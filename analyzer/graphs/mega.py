import plotly.graph_objects as go

from analyzer.graphs.common import (COLOR_MAP, axis_values, color_traces,
                                    compact, legend_labels, message_figure, update_legend_labels)

# columns that aren't parameters of the experiments
NOT_PARAMETERS = {"id", "color", "response"}
PANEL_SIZE = 300  # pixels per plot


def parameters(dataset):
    """The number columns with values: the parameters of the experiments (e.g. delay, length)."""
    df = dataset.df
    return [
        column for column in df.columns
        if column not in NOT_PARAMETERS and df[column].dtype.kind in "iuf" and df[column].notna().any()
    ]


def mega_func(x, y, fixgreen, dataset, config, _COLORS, *color_states):
    """Every parameter against every other parameter: one plot per pair (the lower half of a scatter matrix)."""
    color_values = color_states[:8]
    color_labels = color_states[8:]

    columns = parameters(dataset)
    if len(columns) < 2:
        return message_figure(f"The mega graph needs at least two number columns besides id ({', '.join(columns) or 'none'})")

    colors = dataset.colors_for(config)
    values = {column: axis_values(dataset, column, config.jitter) for column in columns}

    fig = go.Figure()
    for code, mask in color_traces(colors):
        fig.add_trace(
            go.Splom(
                dimensions=[{"label": column, "values": compact(values[column][mask])} for column in columns],
                name=code,
                marker={"color": COLOR_MAP[code], "size": 3},
                # every pair once, without the diagonal (a parameter against itself)
                showupperhalf=False,
                diagonal={"visible": False},
            )
        )

    size = max(1000, PANEL_SIZE * (len(columns) - 1))
    fig.update_layout(
        height=size,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        dragmode="zoom",
        legend={
            "orientation": "h",
            "itemsizing": "constant",
            "yanchor": "top",
            "y": 1,
            "xanchor": "right",
            "x": 1,
            "font": {"size": 15},
            "title": {"side": "top", "text": f"Classification ( {len(colors):,} ):", "font": {"size": 15}},
        },
    )
    update_legend_labels(fig, legend_labels(colors, color_values, color_labels))
    return fig
