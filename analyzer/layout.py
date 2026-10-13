"""The page layout and the contents of the tabs."""
from dataclasses import asdict

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html

from analyzer.config import COLORS, server_config
from analyzer.database import (data_directory, get_databases,
                               is_local_request)
from analyzer.timing import timed


def create_layout(app):

    app.layout = html.Div([
        dcc.Store(id="config-store", data=asdict(server_config)),
        dcc.Store(id="data-hidden-columns", data=["hex(response)"], storage_type="local"),
        dcc.Store(id="zoom-store"),
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
            style={
                "width": "80%",
                "border-style": "none",
                "margin": "0 auto",
                "display": "flex",
                "justifyContent": "space-between",
                "alignItems": "baseline",
            },
        ),
        html.Div([
            dcc.Tabs(id="tabs", value='tab-graph', children=[
                dcc.Tab(label='Graph', value='tab-graph'),
                dcc.Tab(label='Data', value='tab-data'),
                dcc.Tab(label='Information', value='tab-information'),
                dcc.Tab(label='Database', value='tab-database'),
                dcc.Tab(label='Guide', value='tab-guide'),
            ]),
        ],
        style={"width": "80%", "border-style": "none", "margin": "0 auto"},
        ),

        html.Div(id='tabs-content')
    ], 
        style={
            "width": "100%",
            "border-style": "none",
            "margin-top": "100px",
            "margin-bottom": "100px",
        },
    
    )

    @app.callback(
        Output('tabs-content', 'children'),
        Input('tabs', 'value'),
        State('config-store', 'data')
    )
    @timed("tab")
    def render_content(tab, store):
        if tab == 'tab-graph':

            # apply state when available (the CLI/env presets are the initial store)
            database = store.get('database') if store else None
            x = store.get('x') if store else None
            y = store.get('y') if store else None
            graph = store.get('graph', 'scatter') if store else 'scatter'
            local = is_local_request()
            directory = (store.get('directory') if store and local else None) or server_config.directory
            fixgreen = store.get('fixgreen', True) if store else True
            jitter = (store.get('jitter') if store else None) or 0

            content = html.Div([
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Div(
                                [
                                    html.Button(
                                        "Update",
                                        id="update-button",
                                        n_clicks=0,
                                        style={"width": "100px"},
                                    ),
                                    html.Datalist(
                                        id="examples",
                                        children=[
                                            html.Option(
                                                value="match_string(response, 'ets')"
                                            ),
                                            html.Option(
                                                value="match_hex(response, '661b')"
                                            ),
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
                                        style={
                                            "width": "100%",
                                            "display": "inline-block",
                                        },
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
                        ]
                    )
                ),
                dbc.Card(
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
                                    dbc.Button(
                                        "Browse…",
                                        id="directory-browse",
                                        n_clicks=0,
                                        color="secondary",
                                        disabled=not local,
                                    ),
                                ],
                                size="sm",
                                className="mb-2",
                            ),
                            dcc.Store(id="browse-directory"),
                            dbc.Modal(
                                [
                                    dbc.ModalHeader(dbc.ModalTitle("Select a directory")),
                                    dbc.ModalBody(
                                        [
                                            html.Div(id="browse-path", className="mb-2"),
                                            dbc.ListGroup(
                                                id="browse-list",
                                                style={"maxHeight": "60vh", "overflowY": "auto"},
                                            ),
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
                            dcc.Dropdown(
                                id="database-dropdown",
                                style={"width": "100%"},
                                options=get_databases(data_directory(directory) or server_config.directory),
                                placeholder="database",
                                value=database,
                            ),
                            html.Div(
                                [
                                    dcc.Dropdown(
                                        id="x-dropdown",
                                        style={"width": "100%"},
                                        placeholder="x-axis",
                                        value=x,
                                    ),
                                    dcc.Dropdown(
                                        id="y-dropdown",
                                        style={"width": "100%"},
                                        placeholder="y-axis",
                                        value=y,
                                    ),
                                    dcc.Input(
                                        id="jitter-input",
                                        type="number",
                                        value=jitter,
                                        style={"width": "100%"},
                                    ),
                                ],
                                style=dict(display="flex"),
                            ),
                            
                            dcc.Dropdown(
                                id="graph-dropdown",
                                style={"width": "100%"},
                                options=[
                                    {'label': 'scatter', 'value': 'scatter'},\
                                    {'label': 'scatter2', 'value': 'scatter2'},
                                    {'label': 'simple', 'value': 'simple'},
                                ],
                                # placeholder="option1",
                                value=graph,
                            ),
                        ]
                    )
                ),
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Center(
                                [
                                    dcc.Loading(
                                        dcc.Graph(
                                            id="graph",
                                            config={'displayModeBar': True},
                                            style={"width": "80%"}),
                                        type="circle",
                                        delay_show=150,
                                        overlay_style={"visibility": "visible", "opacity": 0.4},
                                    ),
                                ]
                            ),
                        ]
                    )
                ),
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Span(
                                dbc.Switch(
                                    id="switch-fixgreen",
                                    value=fixgreen,
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
                                    input_component
                                    for color in COLORS
                                    for input_component in [
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
                                            style={
                                                "width": "15%",
                                                "margin-right": "10px",
                                                "margin-bottom": "10px",
                                            },
                                            persistence=True,
                                        ),
                                    ]
                                ],
                                className="mt-2",
                            ),
                        ]
                    )
                ),
            ],
            style={"width": "80%", "border-style": "none", "margin": "0 auto"},
            )
        elif tab == 'tab-data':
            content = html.Div([
                        dbc.Card(
                            dbc.CardBody([
                                    html.Div([
                                        dbc.Switch(
                                            id="switch-squeezedata",
                                            value=True,
                                            label="Squeeze Data",
                                            className="d-inline-block me-3"
                                        ),
                                        dbc.Switch(
                                            id="switch-wraptext",
                                            value=False,
                                            label="Wrap response",
                                            className="d-inline-block me-3"
                                        ),
                                        dcc.Input(
                                            id="response_s",
                                            type="number",
                                            placeholder="start",
                                            className="d-inline-block me-2",
                                            persistence=True,
                                        ),
                                        dcc.Input(
                                            id="response_e",
                                            type="number",
                                            placeholder="end",
                                            className="d-inline-block me-2",
                                            persistence=True,
                                        ),
                                    ], className="mb-0"),
                                    dcc.Dropdown(
                                        id="data-columns",
                                        multi=True,
                                        placeholder="columns to show",
                                        className="mt-2",
                                    ),
                                ]
                            )
                        ),
                        dbc.Card(
                            dbc.CardBody([
                                dcc.Loading(
                                    type="circle",
                                    delay_show=150,
                                    overlay_style={"visibility": "visible", "opacity": 0.4},
                                    children=html.Div(
                                        id="data",
                                        className="border-0 p-2 bg-light rounded",
                                        style={
                                            "minHeight": "400px",
                                            "height": "100%",
                                            "i": "100%",
                                            "fontFamily": "monospace",
                                            "whiteSpace": "pre-wrap",
                                            "overflowY": "auto",
                                            "border-style": "none",
                                        }
                                    ),
                                ),
                            ]),
                        )
                    ],style={"width": "80%", "border-style": "none", "margin": "0 auto"})
            
            return content
        elif tab == 'tab-information':
            content = html.Div([
                dbc.Card(
                    [
                        dbc.CardHeader("Arguments:"),
                        dbc.CardBody(
                            [
                                dcc.Markdown("", id="argv"),
                            ]
                        ),
                    ]
                ),
                dbc.Card(
                    [
                        dbc.CardHeader("Points:"),
                        dbc.CardBody(
                            [
                                dcc.Markdown("", id="points"),
                            ]
                        ),
                    ]
                ),
                dbc.Card(
                    [
                        dbc.CardHeader("Store:"),
                        dbc.CardBody(
                            [
                                dcc.Markdown("", id="printstore"),
                            ]
                        ),
                    ]
                )
            ],style={"width": "80%", "border-style": "none", "margin": "0 auto"})
            return content
        elif tab == 'tab-database':
            content = html.Div([
                dbc.Card([
                    dbc.CardBody([
                        # All on one line
                        html.Div([
                            html.Span("SELECT ", style={"marginRight": "5px", "fontWeight": "bold"}),
                            dbc.Input(
                                id="select-input",
                                type="text",
                                value="*",
                                placeholder="columns",
                                style={"width": "150px", "display": "inline-block", "marginRight": "5px"}
                            ),
                            html.Span(" FROM experiments WHERE ", style={"margin": "0 5px", "fontWeight": "bold"}),
                            dbc.Input(
                                id="where-input",
                                type="text",
                                value="",
                                placeholder="",
                                style={"width": "1200px", "display": "inline-block", "marginRight": "10px"}
                            ),
                            dbc.Button(
                                "Execute",
                                id="query-button",
                                n_clicks=0,
                                color="primary",
                                style={"display": "inline-block"}
                            ),
                            
                        ], style={"display": "flex", "alignItems": "center"}),
                    ])
                ]),
                dcc.Loading(html.Pre(id='query-output'), type="circle", delay_show=150)
            ],style={"width": "80%", "border-style": "none", "margin": "0 auto"})
        elif tab == 'tab-guide':
            with open('pages/guide.md', 'r', encoding='utf-8') as f:
                guide_text = f.read()
            
            content = html.Div([
                dbc.Card([
                    dbc.CardBody([
                         html.Div([
                             dcc.Markdown(guide_text)
                         ])
                    ])
                ])
            ],style={"width": "80%", "border-style": "none", "margin": "0 auto"})

                             

        return content
