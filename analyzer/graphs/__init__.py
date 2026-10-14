"""The graph types (see the graph dropdown)."""
from analyzer.graphs.mega import mega_func
from analyzer.graphs.scatter import scatter_func
from analyzer.graphs.scatter3d import scatter3d_func

GRAPHS = {
    "scatter": scatter_func,
    "3d": scatter3d_func,
    "mega": mega_func,
}

# the axes a graph type needs (others: x and y)
GRAPH_AXES = {
    "3d": ("x", "y", "z"),
    "mega": (),
}
