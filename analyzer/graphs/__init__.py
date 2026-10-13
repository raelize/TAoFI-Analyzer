"""The graph types (see the graph dropdown)."""
from analyzer.graphs.scatter import scatter_func
from analyzer.graphs.scatter2 import scatter2_func
from analyzer.graphs.simple import simple_func

GRAPHS = {
    "scatter": scatter_func,
    "scatter2": scatter2_func,
    "simple": simple_func,
}
