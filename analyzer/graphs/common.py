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
    "normal": "(v)", "normal_voltage": "(v)",
    "length": "(ns)", "glitch_length": "(ns)",
    "delay": "(ns)", "glitch_delay": "(ns)",
    "power": "(%)", "glitch_power": "(%)",
    "voltage": "(v)", "glitch_voltage": "(v)",
}

# Recolor targets, in the order of the recolor input fields (green .. red).
COLOR_CODES = ["G", "Y", "M", "O", "C", "B", "Z", "R"]
# Legend/trace order (timeout first, as before).
COLOR_ORDER = ["P"] + COLOR_CODES
COLOR_MAP = {
    "P": "pink", "G": "green", "Y": "yellow", "M": "magenta",
    "O": "orange", "C": "cyan", "B": "blue", "Z": "black", "R": "red",
}


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


def color_traces(colors):
    """(color code, mask) of the traces of a graph, in their order (COLOR_ORDER, the codes present)."""
    masks = color_masks(colors)
    return [(code, masks[code]) for code in COLOR_ORDER if code in masks]


def clicked_experiment(colors, curve, point, plots=1):
    """The experiment (row) of point ``point`` of trace ``curve``, or None (not in this graph).

    A graph with several ``plots`` (scatter2) has these traces for each plot.
    """
    traces = color_traces(colors)
    if not 0 <= curve < len(traces) * plots:
        return None
    curve %= len(traces)
    rows = np.flatnonzero(traces[curve][1])
    return int(rows[point]) if 0 <= point < len(rows) else None


def count_colors(colors):
    counts = {code: 0 for code in COLOR_ORDER}
    for code, mask in color_masks(colors).items():
        counts[code] = int(mask.sum())
    return counts


def build_scatter(dataset, x, y, colors, jitter, labels=None):
    """One WebGL scatter trace per color, in COLOR_ORDER, mimicking px.scatter's look."""
    labels = labels or {}
    xv = axis_values(dataset, x, jitter)
    yv = axis_values(dataset, y, jitter)
    fig = go.Figure()
    for code, mask in color_traces(colors):
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
        code: f"{label or regex or COLOR_MAP[code]} {share(counts[code])}"
        for code, regex, label in zip(COLOR_CODES, regexes, labels)
    }
    result["P"] = f"timeout {share(counts['P'])}"
    return result


def message_figure(message):
    """An empty graph showing ``message`` (e.g. why the database can't be shown)."""
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, font={"size": 16, "color": "#c0392b"},
                       xref="paper", yref="paper", x=0.5, y=0.5)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def update_legend_labels(fig, labels):
    for entry in fig.data:
        if entry["name"] in labels:
            entry["name"] = labels[entry["name"]]
