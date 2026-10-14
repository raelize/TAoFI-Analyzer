"""The page layout and the contents of the tabs."""
import os
from dataclasses import asdict

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html
from dash.exceptions import PreventUpdate

from analyzer.config import COLORS, AnalyzerConfig, server_config
from analyzer.database import data_directory, get_databases, is_local_request
from analyzer.graphs import GRAPHS
from analyzer.timing import timed

# the guide tab (relative to the code, not the current directory)
GUIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pages", "guide.md")

# centered column used by the header, the tabs and their contents
PAGE_STYLE = {"width": "80%", "border-style": "none", "margin": "0 auto"}

LOADING = {"type": "circle", "delay_show": 150}


def create_layout(app):
    app.layout = html.Div(
        [
            dcc.Store(id="config-store", data=asdict(server_config)),
            dcc.Store(id="data-hidden-columns", data=["hex(response)"], storage_type="local"),
            dcc.Store(id="zoom-store"),
            # timings of the server callbacks (see timing.py)
            dcc.Store(id="timing-store"),
            dcc.Store(id="timing-last"),
            html.Div(
                [
                    html.H4("Research by Raelize"),
                    html.Span(
                        [
                            html.Span("", id="timing-label"),
                            html.Span("", id="timing-client", title="time from sending the request until the browser rendered the result"),
                            html.Span("", id="timing", title="time spent in the server callback"),
                        ],
                        style={"fontFamily": "monospace", "color": "#666"},
                    ),
                ],
                style={**PAGE_STYLE, "display": "flex", "justifyContent": "space-between", "alignItems": "baseline"},
            ),
            html.Div(
                [
                    dcc.Tabs(
                        id="tabs",
                        value="tab-graph",
                        children=[
                            dcc.Tab(label="Graph", value="tab-graph"),
                            dcc.Tab(label="Data", value="tab-data"),
                            dcc.Tab(label="Information", value="tab-information"),
                            dcc.Tab(label="Database", value="tab-database"),
                            dcc.Tab(label="Guide", value="tab-guide"),
                        ],
                    ),
                ],
                style=PAGE_STYLE,
            ),
            html.Div(id="tabs-content"),
        ],
        style={
            "width": "100%",
            "border-style": "none",
            "margin-top": "100px",
            "margin-bottom": "100px",
        },
    )

    @app.callback(
        Output("tabs-content", "children"),
        Input("tabs", "value"),
        State("config-store", "data"),
    )
    @timed("tab")
    def render_content(tab, store):
        if tab not in TABS:
            raise PreventUpdate
        return html.Div(TABS[tab](AnalyzerConfig.from_store(store)), style=PAGE_STYLE)


def graph_tab(config):
    # the CLI/env presets are the initial store, so everything comes from the store
    local = is_local_request()
    directory = (config.directory if local else None) or server_config.directory

    query = dbc.Card(
        dbc.CardBody(
            html.Div(
                [
                    html.Button("Update", id="update-button", n_clicks=0, style={"width": "100px"}),
                    html.Datalist(
                        id="examples",
                        children=[
                            html.Option(value="match_string(response, 'ets')"),
                            html.Option(value="match_hex(response, '661b')"),
                            html.Option(value="color = 'G'"),
                            html.Option(value="delay > 100"),
                            html.Option(value="length > 100"),
                        ],
                    ),
                    dcc.Input(
                        id="query-input",
                        type="text",
                        list="examples",
                        value="",
                        style={"width": "100%", "display": "inline-block"},
                        placeholder="SELECT * FROM experiments WHERE",
                        persistence=True,
                    ),
                    dbc.Switch(
                        id="switch-autorefresh",
                        value=False,
                        label="Auto refresh",
                        className="ms-3 mb-0 text-nowrap",
                        persistence=True,
                    ),
                    dcc.Input(
                        id="refresh-seconds",
                        type="number",
                        value=10,
                        min=1,
                        step=1,
                        placeholder="sec",
                        style={"width": "70px", "marginLeft": "5px"},
                        persistence=True,
                    ),
                    dcc.Interval(id="refresh-timer", interval=10000, disabled=True),
                ],
                style={"display": "flex", "alignItems": "center"},
            )
        )
    )

    selection = dbc.Card(
        dbc.CardBody(
            [
                # only local users may pick another directory (see data_directory())
                dbc.InputGroup(
                    [
                        dbc.InputGroupText("directory"),
                        dbc.Input(
                            id="directory-input",
                            type="text",
                            value=directory,
                            debounce=True,
                            disabled=not local,
                            placeholder="directory with databases (press enter to apply)",
                        ),
                        dbc.Button("Browse…", id="directory-browse", n_clicks=0, color="secondary", disabled=not local),
                    ],
                    size="sm",
                    className="mb-2",
                ),
                directory_browser(),
                dcc.Dropdown(
                    id="database-dropdown",
                    style={"width": "100%"},
                    options=get_databases(data_directory(directory) or server_config.directory),
                    placeholder="database",
                    value=config.database,
                ),
                html.Div(
                    [
                        dcc.Dropdown(id="x-dropdown", style={"width": "100%"}, placeholder="x-axis", value=config.x),
                        dcc.Dropdown(id="y-dropdown", style={"width": "100%"}, placeholder="y-axis", value=config.y),
                        dcc.Dropdown(id="z-dropdown", style={"width": "100%"}, placeholder="z-axis (3d)", value=config.z),
                        dcc.Input(id="jitter-input", type="number", value=config.jitter or 0, style={"width": "100%"}),
                    ],
                    style={"display": "flex"},
                ),
                dcc.Dropdown(
                    id="graph-dropdown",
                    style={"width": "100%"},
                    options=[{"label": name, "value": name} for name in GRAPHS],
                    value=config.graph,
                ),
            ]
        )
    )

    graph = dbc.Card(
        dbc.CardBody(
            html.Center(
                dcc.Loading(
                    dcc.Graph(id="graph", config={"displayModeBar": True}, style={"width": "80%"}),
                    overlay_style={"visibility": "visible", "opacity": 0.4},
                    **LOADING,
                ),
            )
        )
    )

    recolor = dbc.Card(
        dbc.CardBody(
            [
                html.Span(
                    dbc.Switch(
                        id="switch-fixgreen",
                        value=config.fixgreen,
                        label="Do not recolor green experiments",
                        className="d-inline-block me-3",
                    )
                ),
                html.Span(
                    dbc.Switch(
                        id="switch-hover",
                        value=True,
                        label="Show hover info on the graph",
                        className="d-inline-block me-3",
                    )
                ),
                html.Div(
                    [
                        component
                        for color in COLORS
                        for component in [
                            dcc.Input(
                                id=f"recolor-{color}",
                                type="text",
                                placeholder=f"{color}",
                                style={"width": "15%"},
                                persistence=True,
                            ),
                            dcc.Input(
                                id=f"recolor-{color}-label",
                                type="text",
                                placeholder=f"{color}-label",
                                style={"width": "15%", "margin-right": "10px", "margin-bottom": "10px"},
                                persistence=True,
                            ),
                        ]
                    ],
                    className="mt-2",
                ),
            ]
        )
    )

    return [query, selection, graph, recolor]


def directory_browser():
    """Dialog to pick a directory (see browse_directory() and update_browser())."""
    return html.Div(
        [
            dcc.Store(id="browse-directory"),
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle("Select a directory")),
                    dbc.ModalBody(
                        [
                            html.Div(id="browse-path", className="mb-2"),
                            dbc.ListGroup(id="browse-list", style={"maxHeight": "60vh", "overflowY": "auto"}),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Cancel", id="browse-cancel", n_clicks=0, color="secondary"),
                            dbc.Button("Select", id="browse-select", n_clicks=0, color="primary"),
                        ]
                    ),
                ],
                id="directory-modal",
                is_open=False,
                size="lg",
                scrollable=True,
            ),
        ]
    )


def data_tab(config):
    controls = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        dbc.Switch(id="switch-squeezedata", value=True, label="Squeeze Data", className="d-inline-block me-3"),
                        dbc.Switch(id="switch-wraptext", value=False, label="Wrap response", className="d-inline-block me-3"),
                        dcc.Input(id="response_s", type="number", placeholder="start", className="d-inline-block me-2", persistence=True),
                        dcc.Input(id="response_e", type="number", placeholder="end", className="d-inline-block me-2", persistence=True),
                    ],
                    className="mb-0",
                ),
                dcc.Dropdown(id="data-columns", multi=True, placeholder="columns to show", className="mt-2"),
            ]
        )
    )

    grid = dbc.Card(
        dbc.CardBody(
            dcc.Loading(
                html.Div(
                    id="data",
                    className="border-0 p-2 bg-light rounded",
                    style={
                        "minHeight": "400px",
                        "height": "100%",
                        "fontFamily": "monospace",
                        "whiteSpace": "pre-wrap",
                        "overflowY": "auto",
                        "border-style": "none",
                    },
                ),
                overlay_style={"visibility": "visible", "opacity": 0.4},
                **LOADING,
            ),
        )
    )

    return [controls, grid]


def information_tab(config):
    def card(header, id):
        return dbc.Card([dbc.CardHeader(header), dbc.CardBody(dcc.Markdown("", id=id))])

    return [card("Arguments:", "argv"), card("Points:", "points"), card("Store:", "printstore")]


def database_tab(config):
    query = dbc.Card(
        dbc.CardBody(
            # all on one line
            html.Div(
                [
                    html.Span("SELECT ", style={"marginRight": "5px", "fontWeight": "bold"}),
                    dbc.Input(
                        id="select-input",
                        type="text",
                        value="*",
                        placeholder="columns",
                        style={"width": "150px", "display": "inline-block", "marginRight": "5px"},
                    ),
                    html.Span(" FROM experiments WHERE ", style={"margin": "0 5px", "fontWeight": "bold"}),
                    dbc.Input(
                        id="where-input",
                        type="text",
                        value="",
                        placeholder="",
                        style={"width": "1200px", "display": "inline-block", "marginRight": "10px"},
                    ),
                    dbc.Button("Execute", id="query-button", n_clicks=0, color="primary", style={"display": "inline-block"}),
                ],
                style={"display": "flex", "alignItems": "center"},
            ),
        )
    )
    return [query, dcc.Loading(html.Pre(id="query-output"), **LOADING)]


def guide_tab(config):
    with open(GUIDE, "r", encoding="utf-8") as f:
        guide_text = f.read()
    return [dbc.Card(dbc.CardBody(dcc.Markdown(guide_text)))]


TABS = {
    "tab-graph": graph_tab,
    "tab-data": data_tab,
    "tab-information": information_tab,
    "tab-database": database_tab,
    "tab-guide": guide_tab,
}
