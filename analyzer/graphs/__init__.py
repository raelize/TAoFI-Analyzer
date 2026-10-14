"""The graph types (see the graph dropdown)."""
from analyzer.graphs.mega import mega_func
from analyzer.graphs.scatter import scatter_func
from analyzer.graphs.scatter2 import scatter2_func
from analyzer.graphs.scatter3d import scatter3d_func

GRAPHS = {
    "scatter": scatter_func,
    "scatter2": scatter2_func,
    "3d": scatter3d_func,
    "mega": mega_func,
}

# all axes, as selected on the graph tab (AnalyzerConfig fields)
AXES = ("x", "y", "z", "x2", "y2")
# (as shown in the axis dropdowns)
AXIS_NAMES = {"x": "x-axis", "y": "y-axis", "z": "z-axis", "x2": "x-axis (right)", "y2": "y-axis (right)"}

# the axes a graph type needs (others: x and y); only those are shown
GRAPH_AXES = {
    "scatter2": ("x", "y", "x2", "y2"),
    "3d": ("x", "y", "z"),
    "mega": (),
}


# graph types with more than one plot (each with the traces of color_traces())
GRAPH_PLOTS = {
    "scatter2": 2,
}


def graph_axes(graph):
    return GRAPH_AXES.get(graph, ("x", "y"))
