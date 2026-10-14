from dash.exceptions import PreventUpdate

from analyzer.graphs.common import (AXIS_LABELS, COLOR_CODES, RECOLOR_FLAGS, build_scatter, legend_labels,
                                    update_legend_labels)


def scatter2_func(x, y, fixgreen, dataset, config, _COLORS, *color_states):
    color_values = color_states[:8]
    color_labels = color_states[8:]

    # Apply recoloring and count (no DOTALL here, as before)
    colors = dataset.recolor(color_values, COLOR_CODES, fixgreen, RECOLOR_FLAGS["scatter2"])
    total = len(colors)

    try:
        fig = build_scatter(
            dataset, x, y, colors, config.jitter,
            labels={
                "color": f"Classification ({total:,})",
                x: f"{x} {AXIS_LABELS.get(x, '')}",
                y: f"{y} {AXIS_LABELS.get(y, '')}",
            },
        )
    except Exception as e:
        print(e)
        raise PreventUpdate

    fig.update_layout(
        title_text="",
        width=1000, height=1000, autosize=False,
        legend=dict(font=dict(size=15), itemsizing="constant"),
        legend_title=dict(font=dict(size=15)),
    )

    if config.x == "x" or config.y == "y":
        fig.update_xaxes(title_standoff=0, side="top")
        fig.update_yaxes(title_standoff=0, autorange="reversed")

    update_legend_labels(fig, legend_labels(colors, color_values, color_labels))

    return fig
