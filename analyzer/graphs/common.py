"""Shared helpers for the graph modules.

The analyzer hands the graph functions a ``Dataset`` (see analyzer/dataset.py)
instead of a list of per-experiment dicts. Everything here works on numpy
arrays so that a 500k-experiment database renders in well under a second.
"""
import re

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


# Regex flags each graph type recolors with; graph types not listed don't recolor.
# The data tab uses this too, so it shows the same colors as the graph.
RECOLOR_FLAGS = {
    "scatter": re.DOTALL,
    "scatter2": 0,
}


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
    """Axis values for ``column`` with optional gaussian jitter (never mutates the dataset)."""
    values = dataset.df[column].to_numpy()
    if jitter and values.dtype.kind in "iuf":
        values = values + np.random.normal(0, jitter, len(values))
    return values


def color_masks(colors):
    """Boolean mask per color code present in ``colors`` (hash-based, no sorting)."""
    codes, uniques = pd.factorize(colors)
    return {str(code): codes == index for index, code in enumerate(uniques)}


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
    masks = color_masks(colors)
    fig = go.Figure()
    for code in COLOR_ORDER:
        mask = masks.get(code)
        if mask is None:
            continue
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


def update_legend_labels(fig, labels):
    for entry in fig.data:
        if entry["name"] in labels:
            entry["name"] = labels[entry["name"]]
