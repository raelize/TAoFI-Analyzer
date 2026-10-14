"""The graph types (see the graph dropdown)."""
from analyzer.graphs.mega import mega_func
from analyzer.graphs.scatter import scatter_func
from analyzer.graphs.scatter2 import scatter2_func
from analyzer.graphs.scatter3d import MAX_POINTS as MAX_POINTS_3D
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


# the number of points a graph type draws at most (about; see sample())
GRAPH_MAX_POINTS = {
    "3d": MAX_POINTS_3D,
}

# the plots of a graph type (each with the traces of color_traces()): the axis
# of each coordinate of a clicked point (see show_experiment()); a graph type
# not listed has one plot, without these coordinates
PLOT_AXES = {
    "scatter": [{"x": "x", "y": "y"}],
    "scatter2": [{"x": "x", "y": "y"}, {"x": "x2", "y": "y2"}],
    "3d": [{"x": "x", "y": "y", "z": "z"}],
}


def graph_axes(graph):
    return GRAPH_AXES.get(graph, ("x", "y"))
