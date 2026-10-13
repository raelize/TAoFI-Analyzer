from dash.exceptions import PreventUpdate

from analyzer.graphs.common import (AXIS_LABELS, COLOR_CODES, RECOLOR_FLAGS,
                           build_scatter, count_colors, update_legend_labels)


def scatter2_func(x, y, fixgreen, dataset, config, _COLORS, *color_states):
    color_values = color_states[:8]
    color_labels = color_states[8:]

    # Apply recoloring and count (no DOTALL here, as before)
    colors = dataset.recolor(color_values, COLOR_CODES, fixgreen, RECOLOR_FLAGS["scatter2"])
    color_counts = count_colors(colors)
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

    labels = {}
    for color_code, value, label in zip(COLOR_CODES, color_values, color_labels):
        count = color_counts[color_code]
        display = label or value
        labels[color_code] = f"{display} ( {count} / {count / total if total else 0:.1%} )"
    labels["P"] = f"timeout ( {color_counts['P']} / {color_counts['P'] / total if total else 0:.1%} )"

    update_legend_labels(fig, labels)

    return fig
