"""The graph types (see the graph dropdown)."""
from analyzer.graphs.scatter import scatter_func
from analyzer.graphs.scatter2 import scatter2_func
from analyzer.graphs.scatter3d import scatter3d_func
from analyzer.graphs.simple import simple_func

GRAPHS = {
    "scatter": scatter_func,
    "scatter2": scatter2_func,
    "simple": simple_func,
    "3d": scatter3d_func,
}

# the axes a graph type needs (others: x and y)
GRAPH_AXES = {
    "3d": ("x", "y", "z"),
}
