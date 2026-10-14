"""Shared helpers for the graph modules.

The analyzer hands the graph functions a ``Dataset`` (see analyzer/dataset.py)
instead of a list of per-experiment dicts. Everything here works on numpy
arrays so that a 500k-experiment database renders in well under a second.
"""

import re
import zlib

import numpy as np
import pandas as pd
import plotly.graph_objects as go

AXIS_LABELS = {
    "normal": "(v)",
    "normal_voltage": "(v)",
    "length": "(ns)",
    "glitch_length": "(ns)",
    "delay": "(ns)",
    "glitch_delay": "(ns)",
    "power": "(%)",
    "glitch_power": "(%)",
    "voltage": "(v)",
    "glitch_voltage": "(v)",
}

# Recolor targets, in the order of the recolor input fields (green .. red).
COLOR_CODES = ["G", "Y", "M", "O", "C", "B", "Z", "R"]
# Legend/trace order (timeout first, as before).
COLOR_ORDER = ["P", *COLOR_CODES]
# The names of the colors (as in the recolor fields and the legend).
COLOR_NAMES = {
    "P": "pink",
    "G": "green",
    "Y": "yellow",
    "M": "magenta",
    "O": "orange",
    "C": "cyan",
    "B": "blue",
    "Z": "black",
    "R": "red",
}
# The colors drawn: easy to tell apart (after Tableau's/D3's category colors;
# e.g. "yellow" is gold: plain yellow hardly shows on a light background).
COLOR_MAP = {
    "P": "#ff9ec4",
    "G": "#2ca02c",
    "Y": "#e6b400",
    "M": "#d81b9a",
    "O": "#ff7f0e",
    "C": "#17becf",
    "B": "#1f6fd1",
    "Z": "#1a1a1a",
    "R": "#d62728",
}


# Black is drawn light on a dark background (dark mode; see themed()).
DARK_BLACK = "#e0e0e0"


def themed(fig, theme):
    """``fig`` in the page's theme (light or dark, the button in the header)."""
    if theme == "dark":
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)")
        for trace in fig.data:
            if getattr(trace, "marker", None) is not None and trace.marker.color == COLOR_MAP["Z"]:
                trace.marker.color = DARK_BLACK
    return fig


# Regex flags of the recolor regexes: . also matches a newline.
RECOLOR_FLAGS = re.DOTALL


def give_xy_label(parameter):
    return AXIS_LABELS.get(parameter, "")


def compact(values):
    """Return ``values`` as the smallest numpy dtype plotly can ship as a typed array.

    plotly>=6 sends numpy arrays to the browser as base64 typed arrays, so an
    int16 axis costs 2 bytes per point instead of ~20 bytes of JSON text.
    """
    a = np.asarray(values)
    if a.size == 0:
        return a
    if a.dtype.kind in "iu":
        lo, hi = int(a.min()), int(a.max())
        for dtype in (np.int16, np.int32):
            info = np.iinfo(dtype)
            if info.min <= lo and hi <= info.max:
                return a.astype(dtype)
        return a
    if a.dtype.kind == "f":
        finite = a[np.isfinite(a)]
        # float32 keeps ~7 significant digits; stay in float64 beyond that
        if finite.size == 0 or np.abs(finite).max() < 2**24:
            return a.astype(np.float32)
        return a
    return a


def axis_values(dataset, column, jitter):
    """Axis values for ``column`` with optional gaussian jitter (never mutates the dataset).

    The jitter of an experiment stays the same when the graph is drawn again
    (e.g. auto refresh): the same pseudo-random sequence per column every
    time, and new experiments are added at the end.
    """
    values = dataset.df[column].to_numpy()
    if jitter and values.dtype.kind in "iuf":
        rng = np.random.default_rng(zlib.crc32(column.encode()))
        values = values + jitter * rng.standard_normal(len(values))
    return values


def color_masks(colors):
    """Boolean mask per color code present in ``colors`` (hash-based, no sorting)."""
    codes, uniques = pd.factorize(colors)
    return {str(code): codes == index for index, code in enumerate(uniques)}


# The points a graph type draws at most (about): a browser takes long to draw
# more (a million: ~20 s for scatter, and a 3d plot needs ~4 kB per point: a
# million crashed the browser). With more experiments a sample is drawn.
MAX_POINTS = {
    "scatter": 200_000,
    "scatter2": 200_000,
    "3d": 100_000,
    "mega": 200_000,
}
# instead of MAX_POINTS for every graph type (--max-points; 0: all)
DEFAULT_MAX_POINTS: int | None = None


def max_points(config):
    """The points the graph of ``config`` draws at most (None: all): the max points
    field of the graph tab, else the --max-points option, else MAX_POINTS."""
    for limit in (config.max_points, DEFAULT_MAX_POINTS):
        if limit is not None:
            return int(limit) or None
    return MAX_POINTS.get(config.graph)


def sample(colors, limit):
    """Which experiments a graph that can show about ``limit`` points draws (a mask; None: all).

    Only the large classes (colors) are thinned out: every experiment of a
    small class is drawn (those are often the interesting ones, e.g. a few
    successful glitches), the rest of ``limit`` is shared by the larger ones.
    Within a class a fixed pseudo-random choice per experiment, the same every
    time the graph is drawn (new experiments are added at the end); not every
    n-th experiment, which would skip a regular pattern of glitch parameters.
    """
    count = len(colors)
    if limit is None or count <= limit:
        return None
    codes, uniques = pd.factorize(np.asarray(colors, dtype=object))
    sizes = np.bincount(codes, minlength=len(uniques))
    drawn = np.zeros(len(uniques))
    left = float(limit)
    # smallest first: those that fit their share are drawn completely
    for done, index in enumerate(np.argsort(sizes)):
        drawn[index] = min(sizes[index], left / (len(sizes) - done))
        left -= drawn[index]
    return np.random.default_rng(0).random(count) < (drawn / sizes)[codes]


def shown_points(colors, config):
    """The experiments the graph of ``config`` draws (see sample())."""
    return sample(colors, max_points(config))


def classification(colors, shown):
    """The legend title: the number of experiments (and how many are drawn)."""
    total = f"{len(colors):,}" if shown is None else f"{len(colors):,}; {int(shown.sum()):,} shown"
    return f"Classification ( {total} ):"


def color_traces(colors, shown=None):
    """(color code, mask) of the traces of a graph, in their order (COLOR_ORDER, the codes present);
    only the experiments ``shown`` (see sample())."""
    masks = color_masks(colors)
    traces = [(code, masks[code]) for code in COLOR_ORDER if code in masks]
    if shown is not None:
        traces = [(code, mask & shown) for code, mask in traces]
    return traces


def clicked_experiment(colors, curve, point, plots=1, shown=None):
    """(experiment (row), plot) of point ``point`` of trace ``curve``, or None (not in this graph).

    A graph with several ``plots`` (scatter2) has these traces for each plot;
    ``shown``: the experiments the graph draws (see sample()).
    """
    traces = color_traces(colors, shown)
    if not 0 <= curve < len(traces) * plots:
        return None
    plot, curve = divmod(curve, len(traces))
    rows = np.flatnonzero(traces[curve][1])
    return (int(rows[point]), plot) if 0 <= point < len(rows) else None


def plotted_at(dataset, row, columns, jitter, clicked):
    """Whether experiment ``row`` is plotted at ``clicked`` (coordinate -> value, e.g. x -> 1100),
    ``columns``: the column of each coordinate."""
    for coordinate, column in columns.items():
        if coordinate not in clicked or column not in dataset.df.columns:
            continue
        # (as sent to the browser: compact() makes floats float32)
        value = compact(axis_values(dataset, column, jitter)[row : row + 1])[0]
        try:
            if not np.isclose(float(value), float(clicked[coordinate]), rtol=1e-6, atol=0):
                return False
        except (TypeError, ValueError):
            # e.g. a text column (categories)
            if str(value) != str(clicked[coordinate]):
                return False
    return True


def count_colors(colors):
    counts = dict.fromkeys(COLOR_ORDER, 0)
    for code, mask in color_masks(colors).items():
        counts[code] = int(mask.sum())
    return counts


def build_scatter(dataset, x, y, colors, jitter, labels=None, shown=None):
    """One WebGL scatter trace per color, in COLOR_ORDER, mimicking px.scatter's look;
    only the experiments ``shown`` (see sample())."""
    labels = labels or {}
    xv = axis_values(dataset, x, jitter)
    yv = axis_values(dataset, y, jitter)
    fig = go.Figure()
    for code, mask in color_traces(colors, shown):
        fig.add_trace(
            go.Scattergl(
                x=compact(xv[mask]),
                y=compact(yv[mask]),
                mode="markers",
                name=code,
                legendgroup=code,
                marker={"color": COLOR_MAP[code], "symbol": "circle"},
                hovertemplate=f"color={code}<br>{x}=%{{x}}<br>{y}=%{{y}}<extra></extra>",
            )
        )
    fig.update_layout(
        xaxis_title_text=labels.get(x, x),
        yaxis_title_text=labels.get(y, y),
        legend={"title": {"text": labels.get("color", "color")}, "tracegroupgap": 0},
    )
    return fig


def legend_labels(colors, regexes, labels):
    """Legend label per color code: the recolor label (or regex, or the color) with its count and share."""
    counts = count_colors(colors)
    total = len(colors)

    def share(count):
        return f"( {count} / {count / total if total else 0:.1%} )"

    result = {
        code: f"{label or regex or COLOR_NAMES[code]} {share(counts[code])}"
        for code, regex, label in zip(COLOR_CODES, regexes, labels, strict=True)
    }
    result["P"] = f"timeout {share(counts['P'])}"
    return result


def message_figure(message):
    """An empty graph showing ``message`` (e.g. why the database can't be shown)."""
    fig = go.Figure()
    fig.add_annotation(
        text=message, showarrow=False, font={"size": 16, "color": "#c0392b"}, xref="paper", yref="paper", x=0.5, y=0.5
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def update_legend_labels(fig, labels):
    for entry in fig.data:
        if entry["name"] in labels:
            entry["name"] = labels[entry["name"]]
