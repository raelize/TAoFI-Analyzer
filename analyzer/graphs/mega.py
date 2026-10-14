import plotly.graph_objects as go

from analyzer.graphs.common import (
    COLOR_MAP,
    axis_values,
    build_scatter,
    classification,
    color_traces,
    compact,
    legend_labels,
    message_figure,
    shown_points,
    update_legend_labels,
)

# columns that aren't parameters of the experiments
NOT_PARAMETERS = {"color", "response"}
PANEL_SIZE = 300  # pixels per plot


def parameters(dataset, chosen=None):
    """The number columns with values: the parameters of the experiments (e.g. id, delay, length).

    ``chosen``: only these columns (in this order), as far as they're number columns.
    """
    df = dataset.df

    def plottable(column):
        return column in df.columns and df[column].dtype.kind in "iuf" and bool(df[column].notna().any())

    return [column for column in chosen or df.columns if column not in NOT_PARAMETERS and plottable(column)]


def mega_func(x, y, fixgreen, dataset, config, _COLORS, *color_states):
    """Every parameter against every other parameter: one plot per pair (the lower half of a scatter matrix)."""
    color_values = color_states[:8]
    color_labels = color_states[8:]

    columns = parameters(dataset, config.mega)
    if len(columns) < 2:
        if config.mega:
            return message_figure(
                f"Select at least two number columns for the mega graph ({', '.join(columns) or 'none'})"
            )
        return message_figure(f"The mega graph needs at least two number columns ({', '.join(columns) or 'none'})")

    colors = dataset.colors_for(config)
    values = {column: axis_values(dataset, column, config.jitter) for column in columns}
    shown = shown_points(colors, config)

    if len(columns) == 2:
        # one pair: plotly can't draw a scatter matrix with only one plot (without the
        # diagonal and the upper half: "Cannot read properties of undefined (reading
        # 'makeCalcdata')"), so the plot itself (the same traces, in the same order)
        fig = build_scatter(dataset, columns[0], columns[1], colors, config.jitter, shown=shown)
    else:
        fig = go.Figure()
        for code, mask in color_traces(colors, shown):
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
            "title": {"side": "top", "text": classification(colors, shown), "font": {"size": 15}},
        },
    )
    update_legend_labels(fig, legend_labels(colors, color_values, color_labels))
    return fig
