from dash.exceptions import PreventUpdate
import plotly.express as px

def simple_func(x, y, fixgreen, _RECORDS, config, _COLORS, *color_states):
    try:
        fig = px.scatter(
            _RECORDS,
            x=x,
            y=y,
            render_mode="webgl",
            color="color",
            labels={
                x: f"{x} {"X"}",
                y: f"{y} {"Y"}",
            },
        )
    except Exception as e:
        print(e)
        raise PreventUpdate

    return fig