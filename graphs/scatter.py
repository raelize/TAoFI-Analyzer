from dash.exceptions import PreventUpdate

from graphs.common import (COLOR_CODES, RECOLOR_FLAGS, build_scatter,
                           count_colors, update_legend_labels)


def scatter_func(x, y, fixgreen, dataset, config, _COLORS, *color_states):
    color_values = color_states[:8]
    color_labels = color_states[8:]

    # recolor (vectorized: regexes run on the unique responses only)
    colors = dataset.recolor(color_values, COLOR_CODES, fixgreen, RECOLOR_FLAGS["scatter"])
    counts = count_colors(colors)
    total_records = len(colors)

    try:
        fig = build_scatter(dataset, x, y, colors, config.jitter)
    except Exception as e:
        print(e)
        raise PreventUpdate

    fig.update_layout(
        title_text="",
        width=None,
        height=1000,
        autosize=True,
        margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(
            orientation="h",
            itemsizing="constant",
            yanchor="bottom",
            y=-0.1,
            xanchor="center",
            x=0.5,
            font=dict(size=15),
            title=dict(
                side="top",
                text=f"Classification ( {total_records:,} ):",
                font=dict(size=15),
            ),
        ),
    )

    if config.x == "x" or config.y == "y":
        fig.update_xaxes(title_standoff=0, side="top")
        fig.update_yaxes(title_standoff=0, autorange="reversed")

        # TODO: need to check if this is actually keeping the aspect ratio correctly
        fig.update_layout(width=1000, height=1000, autosize=False)

    # Build legend labels with counts and percentages
    labels = {}
    for color_code, value, label in zip(COLOR_CODES, color_values, color_labels):
        count = counts[color_code]
        display_label = label if label else value
        percentage = count / total_records if total_records else 0
        labels[color_code] = f"{display_label} ( {count} / {percentage:.1%} )"

    # Add special label for timeout
    timeout_count = counts["P"]
    timeout_pct = timeout_count / total_records if total_records else 0
    labels["P"] = f"timeout ( {timeout_count} / {timeout_pct:.1%} )"

    update_legend_labels(fig, labels)

    return fig
