import plotly.graph_objects as go

from analyzer.graphs.common import (COLOR_MAP, axis_values, color_traces, compact, legend_labels, sample,
                                    update_legend_labels)

# points drawn at most (about): a browser needs ~4 kB per point of a 3d scatter
# plot (100000: ~0.4 GB; 1000000 crashed it)
MAX_POINTS = 100_000


def scatter3d_func(x, y, fixgreen, dataset, config, _COLORS, *color_states):
    """The experiments in 3D: x, y and z (config.z) against each other."""
    color_values = color_states[:8]
    color_labels = color_states[8:]
    z = config.z

    colors = dataset.colors_for(config)
    xv, yv, zv = (axis_values(dataset, axis, config.jitter) for axis in (x, y, z))
    shown = sample(len(colors), MAX_POINTS)
    total = f"{len(colors):,}" if shown is None else f"{len(colors):,}; {int(shown.sum()):,} shown"

    fig = go.Figure()
    for code, mask in color_traces(colors, shown):
        fig.add_trace(
            go.Scatter3d(
                x=compact(xv[mask]),
                y=compact(yv[mask]),
                z=compact(zv[mask]),
                mode="markers",
                name=code,
                marker={"color": COLOR_MAP[code], "size": 2},
                hovertemplate=f"color={code}<br>{x}=%{{x}}<br>{y}=%{{y}}<br>{z}=%{{z}}<extra></extra>",
            )
        )

    fig.update_layout(
        height=1000,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        # (the legend below it)
        scene={"xaxis_title_text": x, "yaxis_title_text": y, "zaxis_title_text": z, "domain": {"y": [0.06, 1]}},
        legend={
            "orientation": "h",
            "itemsizing": "constant",
            "yanchor": "bottom",
            "y": 0,
            "xanchor": "center",
            "x": 0.5,
            "font": {"size": 15},
            "title": {"side": "top", "text": f"Classification ( {total} ):", "font": {"size": 15}},
        },
    )
    update_legend_labels(fig, legend_labels(colors, color_values, color_labels))
    return fig
