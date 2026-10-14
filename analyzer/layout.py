"""The page layout and the contents of the tabs."""

import os
from dataclasses import asdict

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html
from dash.exceptions import PreventUpdate

from analyzer.config import COLORS, AnalyzerConfig, server_config
from analyzer.database import data_directory, get_databases, is_local_request
from analyzer.graphs import AXES, AXIS_NAMES, GRAPHS, graph_axes
from analyzer.timing import timed

# the guide tab (relative to the code, not the current directory)
GUIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pages", "guide.md")

# centered column used by the header, the tabs and their contents
PAGE_STYLE = {"width": "80%", "border-style": "none", "margin": "0 auto"}


def loading(child, **kwargs):
    """``child`` with a loading indicator (shown after 150 ms)."""
    return dcc.Loading(child, type="circle", delay_show=150, **kwargs)


# the panel of the experiment clicked in the graph
EXPERIMENT_SHOWN = {"width": "420px", "flexShrink": 0, "maxHeight": "1000px", "overflowY": "auto"}
EXPERIMENT_HIDDEN = {**EXPERIMENT_SHOWN, "display": "none"}


def create_layout(app):
    app.layout = html.Div(
        [
            dcc.Store(id="config-store", data=asdict(server_config)),
            # the text fields of the graph tab as last applied, kept by the browser (see update_store())
            dcc.Store(id="applied-fields", storage_type="local"),
            dcc.Store(id="data-hidden-columns", data=["hex(response)"], storage_type="local"),
            dcc.Store(id="zoom-store"),
            # timings of the server callbacks (see timing.py)
            dcc.Store(id="timing-store"),
            dcc.Store(id="timing-last"),
            # the auto refresh settings of the graph tab, for the timers of the tabs
            dcc.Store(id="refresh-settings"),
            dcc.Store(id="data-refresh"),
            # the outline of the selection in the graph (see selection.py)
            dcc.Store(id="selection-store"),
            html.Div(
                [
                    html.H4("Research by Raelize"),
                    html.Span(
                        [
                            html.Span("", id="timing-label"),
                            html.Span(
                                "",
                                id="timing-client",
                                title="time from sending the request until the browser rendered the result",
                            ),
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
        State("refresh-settings", "data"),
    )
    @timed("tab")
    def render_content(tab, store, refresh):
        if tab not in TABS:
            raise PreventUpdate
        return html.Div(TABS[tab](AnalyzerConfig.from_store(store), refresh or {}), style=PAGE_STYLE)


def refresh_timer(timer_id, refresh):
    """An auto refresh timer of a tab, as set on the graph tab (see update_refresh_timer()).

    Every tab that refreshes has its own: a callback with inputs on several
    tabs (e.g. one timer for all of them) fails on the other tabs.
    """
    seconds = max(1, int(refresh.get("seconds") or 10))
    return dcc.Interval(id=timer_id, interval=seconds * 1000, disabled=not refresh.get("enabled"))


def graph_tab(config, refresh):
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
                        # (as applied; see update_store())
                        value=config.query or "",
                        style={"width": "100%", "display": "inline-block"},
                        placeholder="SELECT * FROM experiments WHERE",
                    ),
                    dbc.Switch(
                        id="switch-autorefresh",
                        value=False,
                        label="Auto refresh",
                        # (centered like the fields next to it: by default bootstrap puts it a bit higher)
                        className="ms-3 mb-0 text-nowrap d-flex align-items-center",
                        input_style={"marginTop": 0},
                        label_style={"marginLeft": "0.5em", "marginBottom": 0},
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
                    refresh_timer("refresh-timer", refresh),
                    # while databases are counted in the background (see update_database_list())
                    dcc.Interval(id="count-timer", interval=1000, disabled=True),
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
                        # (a plain button, like Update)
                        html.Button("Browse…", id="directory-browse", n_clicks=0, disabled=not local),
                    ],
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
                        *[
                            dcc.Dropdown(
                                id=f"{axis}-dropdown",
                                placeholder=AXIS_NAMES[axis],
                                value=getattr(config, axis),
                                style=axis_style(config.graph, axis),
                            )
                            for axis in AXES
                        ],
                        html.Span("jitter", className="mx-2 text-muted align-self-center"),
                        dcc.Input(id="jitter-input", type="number", value=config.jitter or 0, style={"width": "100px"}),
                    ],
                    style={"display": "flex"},
                ),
                html.Div(
                    [
                        dcc.Dropdown(
                            id="graph-dropdown",
                            style={"width": "100%"},
                            options=[{"label": name, "value": name} for name in GRAPHS],
                            value=config.graph,
                        ),
                        # the parameters of the mega graph (only shown for it)
                        dcc.Dropdown(
                            id="mega-dropdown",
                            multi=True,
                            placeholder="parameters (all number columns)",
                            value=config.mega,
                            style=mega_style(config.graph),
                        ),
                        # (applied like the other fields; see graphs.common.max_points())
                        html.Span("max points", className="mx-2 text-muted align-self-center text-nowrap"),
                        html.Span(
                            dcc.Input(
                                id="max-points-input",
                                type="number",
                                min=0,
                                step=1,
                                value=config.max_points,
                                placeholder="default",
                                style={"width": "100px", "height": "100%"},
                            ),
                            title="The points the graph draws at most (a sample of the experiments): "
                            "empty for the default, 0 for all of them",
                        ),
                    ],
                    style={"display": "flex"},
                ),
            ]
        )
    )

    # the experiment clicked in the graph, next to it (see show_experiment()); hidden until then
    experiment = html.Div(
        [
            html.Div(
                [
                    html.Strong("Experiment"),
                    html.Button(id="experiment-close", n_clicks=0, className="btn-close", title="Close"),
                ],
                className="d-flex justify-content-between align-items-center mb-2",
            ),
            html.Div(id="experiment"),
        ],
        id="experiment-panel",
        className="border rounded p-2 ms-2",
        style=EXPERIMENT_HIDDEN,
    )

    graph = dbc.Card(
        dbc.CardBody(
            html.Div(
                [
                    html.Div(
                        loading(
                            # (resized when the experiment is shown next to it, see resize_graph)
                            dcc.Graph(id="graph", config={"displayModeBar": True}),
                            overlay_style={"visibility": "visible", "opacity": 0.4},
                        ),
                        style={"flex": 1, "minWidth": 0},
                    ),
                    experiment,
                ],
                style={"display": "flex", "alignItems": "flex-start"},
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
                                value=config.colors.get(color, [None, None])[0],
                                placeholder=f"{color}",
                                style={"width": "15%"},
                            ),
                            dcc.Input(
                                id=f"recolor-{color}-label",
                                type="text",
                                value=config.colors.get(color, [None, None])[1],
                                placeholder=f"{color}-label",
                                style={"width": "15%", "margin-right": "10px", "margin-bottom": "10px"},
                            ),
                        ]
                    ],
                    className="mt-2",
                ),
            ]
        )
    )

    # (see the graph-store callback)
    return [query, selection, graph, recolor, dcc.Store(id="graph-store"), dcc.Store(id="graph-resized")]


def mega_style(graph):
    """The parameters of the mega graph are only shown for it."""
    return {"width": "100%"} if graph == "mega" else {"display": "none"}


def axis_style(graph, axis):
    """Axis dropdowns are only shown for the graph types that use them."""
    return {"width": "100%"} if axis in graph_axes(graph) else {"display": "none"}


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


def data_tab(config, refresh):
    controls = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        dbc.Switch(
                            id="switch-squeezedata", value=True, label="Squeeze Data", className="d-inline-block me-3"
                        ),
                        dbc.Switch(
                            id="switch-wraptext", value=False, label="Wrap response", className="d-inline-block me-3"
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
                    ],
                    className="mb-0",
                ),
                dcc.Dropdown(id="data-columns", multi=True, placeholder="columns to show", className="mt-2"),
                # the experiments selected in the graph (if any)
                html.Div(
                    [
                        html.Span(id="data-selection", className="me-2"),
                        dbc.Button("Show all", id="data-show-all", n_clicks=0, size="sm", color="secondary"),
                    ],
                    id="data-selection-bar",
                    className="mt-2",
                    style={"display": "none"},
                ),
            ]
        )
    )

    grid = dbc.Card(
        dbc.CardBody(
            loading(
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
            ),
        )
    )

    # auto refresh: the version of the database the grid shows (see refresh_data())
    timer = html.Div([refresh_timer("data-refresh-timer", refresh), dcc.Store(id="data-dbkey", data=config.dbkey)])

    return [controls, grid, timer]


def information_tab(config, refresh):
    def card(header, id):
        return dbc.Card([dbc.CardHeader(header), dbc.CardBody(dcc.Markdown("", id=id))])

    return [card("Arguments:", "argv"), card("Points:", "points"), card("Store:", "printstore")]


def database_tab(config, refresh):
    query = dbc.Card(
        dbc.CardBody(
            # all on one line
            html.Div(
                [
                    html.Span("SELECT ", style={"marginRight": "5px", "fontWeight": "bold"}, className="text-nowrap"),
                    dbc.Input(
                        id="select-input",
                        type="text",
                        value="*",
                        placeholder="columns",
                        style={"width": "150px", "display": "inline-block", "marginRight": "5px"},
                    ),
                    html.Span(
                        " FROM experiments WHERE ",
                        style={"margin": "0 5px", "fontWeight": "bold"},
                        className="text-nowrap",
                    ),
                    dbc.Input(
                        id="where-input",
                        type="text",
                        value="",
                        placeholder="",
                        style={"flex": 1, "display": "inline-block", "marginRight": "10px"},
                    ),
                    dbc.Button(
                        "Execute", id="query-button", n_clicks=0, color="primary", style={"display": "inline-block"}
                    ),
                ],
                style={"display": "flex", "alignItems": "center"},
            ),
        )
    )
    return [query, loading(html.Div(id="query-output", className="mt-2"))]


def guide_tab(config, refresh):
    with open(GUIDE, encoding="utf-8") as f:
        guide_text = f.read()
    return [dbc.Card(dbc.CardBody(dcc.Markdown(guide_text)))]


TABS = {
    "tab-graph": graph_tab,
    "tab-data": data_tab,
    "tab-information": information_tab,
    "tab-database": database_tab,
    "tab-guide": guide_tab,
}
