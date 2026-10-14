"""The experiments selected in the graph (box or lasso select), shown by the data tab.

The selection-store keeps the outline of the selection, not the selected points
(those can be a million): the experiments inside it are found here, at the
positions they were plotted at (the jitter is the same every time).
"""

import json

import numpy as np

from analyzer.graphs.common import axis_values

# graph types whose selections the data tab can show (x against y)
SELECTABLE = ("scatter", "scatter2")

# what the plotted positions depend on (besides the selected columns)
_SAME = ("directory", "database", "query", "jitter")


def applies(selection, config):
    """Whether ``selection`` was made in a graph of ``config``'s database (and query and jitter)."""
    return bool(selection) and all(selection.get(name) == getattr(config, name) for name in _SAME)


def selection_key(selection):
    return json.dumps(selection, sort_keys=True) if selection else None


def selected_rows(dataset, config, selection):
    """The rows (experiments) inside ``selection``, or None if it doesn't apply."""
    if not applies(selection, config):
        return None
    df = dataset.df
    x, y = selection.get("x"), selection.get("y")
    if x not in df.columns or y not in df.columns or df[x].dtype.kind not in "iuf" or df[y].dtype.kind not in "iuf":
        return None
    xv = axis_values(dataset, x, config.jitter).astype(float)
    yv = axis_values(dataset, y, config.jitter).astype(float)

    if "box" in selection:
        (x0, x1), (y0, y1) = sorted(selection["box"]["x"]), sorted(selection["box"]["y"])
        return np.flatnonzero((xv >= x0) & (xv <= x1) & (yv >= y0) & (yv <= y1))

    px, py = np.asarray(selection["lasso"]["x"], float), np.asarray(selection["lasso"]["y"], float)
    # only the points within the lasso's bounding box can be inside it
    candidates = np.flatnonzero((xv >= px.min()) & (xv <= px.max()) & (yv >= py.min()) & (yv <= py.max()))
    return candidates[inside_polygon(xv[candidates], yv[candidates], px, py)]


def inside_polygon(x, y, px, py):
    """Whether the points (x, y) are inside the polygon (px, py) (even-odd rule)."""
    inside = np.zeros(len(x), dtype=bool)
    j = len(px) - 1
    for i in range(len(px)):
        crosses = (py[i] > y) != (py[j] > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = (px[j] - px[i]) * (y - py[i]) / (py[j] - py[i]) + px[i]
        inside ^= crosses & (x < at)
        j = i
    return inside


def describe(selection, count):
    """E.g. "the 12 experiments selected in the graph (delay 1100–1500, length 2000–3000)"."""
    if "box" in selection:
        (x0, x1), (y0, y1) = sorted(selection["box"]["x"]), sorted(selection["box"]["y"])
        where = f"{selection['x']} {x0:g}–{x1:g}, {selection['y']} {y0:g}–{y1:g}"
    else:
        where = f"a lasso on {selection['x']} and {selection['y']}"
    return f"the {count} experiments selected in the graph ({where})"
