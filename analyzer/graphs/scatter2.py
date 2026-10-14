from plotly.subplots import make_subplots

from analyzer.graphs.common import build_scatter, classification, legend_labels, shown_points, update_legend_labels


def scatter2_func(x, y, fixgreen, dataset, config, _COLORS, *color_states):
    """Two scatter plots next to each other: x against y, and x2 against y2.

    Both have the traces of build_scatter() (one per color, in the same
    order), sharing the legend: hiding a color hides it in both.
    """
    color_values = color_states[:8]
    color_labels = color_states[8:]

    colors = dataset.colors_for(config)
    shown = shown_points(colors, "scatter2")
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.06)
    for col, (xa, ya) in enumerate([(x, y), (config.x2, config.y2)], start=1):
        for trace in build_scatter(dataset, xa, ya, colors, config.jitter, shown=shown).data:
            trace.showlegend = col == 1
            fig.add_trace(trace, row=1, col=col)
        fig.update_xaxes(title_text=xa, row=1, col=col)
        fig.update_yaxes(title_text=ya, row=1, col=col)

    fig.update_layout(
        height=800,
        autosize=True,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        legend={
            "orientation": "h",
            "itemsizing": "constant",
            "yanchor": "top",
            "y": -0.08,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 15},
            "title": {"side": "top", "text": classification(colors, shown), "font": {"size": 15}},
        },
    )
    update_legend_labels(fig, legend_labels(colors, color_values, color_labels))
    return fig
