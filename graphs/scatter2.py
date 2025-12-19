from dash.exceptions import PreventUpdate
import plotly.express as px
import re

# Constants
AXIS_LABELS = {
    "normal": "(v)", "normal_voltage": "(v)",
    "length": "(ns)", "glitch_length": "(ns)",
    "delay": "(ns)", "glitch_delay": "(ns)",
    "power": "(%)", "glitch_power": "(%)",
    "voltage": "(v)", "glitch_voltage": "(v)",
}

COLOR_CODES = ["G", "Y", "M", "O", "C", "B", "Z", "R"]
COLOR_MAP = {
    "P": "pink", "G": "green", "Y": "yellow", "M": "magenta",
    "O": "orange", "C": "cyan", "B": "blue", "Z": "black", "R": "red"
}


def recolor_record(record, regex, new_color, fixgreen):
    """Apply regex-based recoloring to a record."""
    if not regex or (fixgreen and record["color"] == "G"):
        return record["color"]
    return new_color if re.search(regex.encode(), record["response"]) else record["color"]


def scatter2_func(x, y, fixgreen, _RECORDS, config, _COLORS, *color_states):
    color_values = color_states[:8]
    color_labels = color_states[8:]
    
    # Initialize color counts
    color_counts = {code: 0 for code in ["P"] + COLOR_CODES}
    color_map = dict(zip(_COLORS, COLOR_CODES))
    
    # Apply recoloring and count
    for record in _RECORDS:
        for value, color_code in zip(color_values, COLOR_CODES):
            record["color"] = recolor_record(record, value, color_code, fixgreen)
        color_counts[record["color"]] += 1
    
    # Create scatter plot
    try:
        fig = px.scatter(
            _RECORDS, 
            x=x, 
            y=y, 
            color="color", 
            render_mode="webgl",
            labels={
                "color": f"Classification ({len(_RECORDS):,})",
                x: f"{x} {AXIS_LABELS.get(x, '')}",
                y: f"{y} {AXIS_LABELS.get(y, '')}",
            },
            color_discrete_map=COLOR_MAP,
            category_orders={"color": ["P"] + COLOR_CODES}
        )
    except Exception as e:
        print(e)
        raise PreventUpdate
    
    # Configure layout
    fig.update_layout(
        title_text="",
        width=1000, height=1000, autosize=False,
        legend=dict(font=dict(size=15), itemsizing='constant'),
        legend_title=dict(font=dict(size=15))
    )
    
    if config.x == "x" or config.y == "y":
        fig.update_xaxes(title_standoff=0, side="top")
        fig.update_yaxes(title_standoff=0, autorange="reversed")
    
    # Build legend labels with counts and percentages
    total = len(_RECORDS)
    labels = {}
    
    for color_code, value, label in zip(COLOR_CODES, color_values, color_labels):
        count = color_counts[color_code]
        display = label or value
        labels[color_code] = f"{display} ( {count} / {count/total:.1%} )"
    
    labels["P"] = f"timeout ( {color_counts['P']} / {color_counts['P']/total:.1%} )"
    
    # Update legend
    for entry in fig.data:
        if entry["name"] in labels:
            entry["name"] = labels[entry["name"]]
    
    return fig