from dash.exceptions import PreventUpdate
import plotly.express as px
import re

def give_xy_label(parameter):
    labels = {
        "normal": "(v)",
        "normal_voltage": "(v)",
        "length": "(ns)",
        "glitch_length": "(ns)",
        "delay": "(ns)",
        "glitch_delay": "(ns)",
        "power": "(%)",
        "glitch_power": "(%)",
        "voltage": "(v)",
        "glitch_voltage": "(v)",
    }
    return labels.get(parameter, "")


def recolor(record, regex, new_color, fixgreen):
    if regex in [None, ""]:
        return record["color"]
    if fixgreen and record["color"] == "G":
        return record["color"]
    elif re.search(regex.encode(), record["response"]):
        return new_color
    else:
        return record["color"]

def update_legend_labels(fig, labels):
    for entry in fig.data:
        if entry["name"] in labels:
            entry["name"] = labels[entry["name"]]


def scatter_func(x, y, fixgreen, _RECORDS, config, _COLORS, *color_states):
    color_values = color_states[:8]
    color_labels = color_states[8:]

    # color amounts
    colors = {
        "P": 0,
        "G": 0,
        "Y": 0,
        "M": 0,
        "O": 0,
        "C": 0,
        "B": 0,
        "Z": 0,
        "R": 0,
    }

    color_map = dict(zip(_COLORS, ["G", "Y", "M", "O", "C", "B", "Z", "R"]))

    # recolor if needed
    for record in _RECORDS:
        for value, color_code in zip(color_values, color_map.values()):
            record["color"] = recolor(record, value, color_code, fixgreen)
        colors[record["color"]] += 1

    try:
        fig = px.scatter(
            _RECORDS,
            x=x,
            y=y,
            render_mode="webgl",
            color="color",
            labels={
                "color": f"Classification ({len(_RECORDS):,})",
                x: f"{x} {give_xy_label(x)}",
                y: f"{y} {give_xy_label(y)}",
            },
            color_discrete_map={
                "P": "pink",
                "G": "green",
                "Y": "yellow",
                "M": "magenta",
                "O": "orange",
                "C": "cyan",
                "B": "blue",
                "Z": "black",
                "R": "red",
            },
            category_orders={
                "color": ["P", "G", "Y", "M", "O", "C", "B", "Z", "R"]
            },
        )
    except Exception as e:
        print(e)
        
        raise PreventUpdate

    # update title of graph
    # fig.update_layout(title_text=config.database[:-7], title_x=0.5, title_y=0.95)
    fig.update_layout(title_text="")
    fig.update_layout(width=1000, height=1000, autosize=False)
    fig.update_layout(
        legend = dict(font = dict(size = 15)),
        legend_title = dict(font = dict(size = 15)),
    )
    fig.update_layout(legend= {'itemsizing': 'constant'})

    if config.x == "x" or config.y == "y":
        fig.update_xaxes(title_standoff=0, side="top")
        fig.update_yaxes(title_standoff=0, autorange="reversed")

    # Build legend labels with counts and percentages
    labels = {}
    total_records = len(_RECORDS)

    # Create labels for each color category
    for color_code, value, label in zip(color_map.values(), color_values, color_labels):
        count = colors[color_code]
        display_label = label if label else value
        percentage = count / total_records
        labels[color_code] = f"{display_label} ( {count} / {percentage:.1%} )"

    # Add special label for timeout
    timeout_count = colors['P']
    timeout_pct = timeout_count / total_records
    labels["P"] = f"timeout ( {timeout_count} / {timeout_pct:.1%} )"

    update_legend_labels(fig, labels)

    return fig