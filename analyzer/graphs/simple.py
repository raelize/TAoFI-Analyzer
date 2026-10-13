from dash.exceptions import PreventUpdate

from analyzer.graphs.common import build_scatter


def simple_func(x, y, fixgreen, dataset, config, _COLORS, *color_states):
    try:
        fig = build_scatter(
            dataset, x, y, dataset.base_colors, config.jitter,
            labels={x: f"{x} X", y: f"{y} Y"},
        )
    except Exception as e:
        print(e)
        raise PreventUpdate

    return fig
