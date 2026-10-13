"""The callbacks of the app (except the tab contents, see layout.py)."""
import os
from dataclasses import asdict

import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, MATCH, Input, Output, State, html
from dash import callback_context as ctx
from dash import no_update
from dash.exceptions import PreventUpdate
from dash_ag_grid import AgGrid

from analyzer.config import COLORS, ROW_COLORS, AnalyzerConfig, server_config
from analyzer.database import (data_directory, database_path, file_key, get_argv, get_databases,
                               get_parameters, is_local_request, list_directory, query_connection, query_error)
from analyzer.dataset import get_dataset
from analyzer.table import get_table
from analyzer.timing import timed
from graphs.scatter import scatter_func
from graphs.scatter2 import scatter2_func
from graphs.simple import simple_func

# graph types (see the graph dropdown)
GRAPHS = {
    "scatter": scatter_func,
    "scatter2": scatter2_func,
    "simple": simple_func,
}

_PAGE_SIZE = 100
_MAX_ROWS = 1000  # per request of the data grid


def register_callbacks(app):
    # callback for remembering the zoomed area of the graph (shown on the information tab);
    # runs in the browser and reads the plot's current axes, so quick successive
    # zooms can't be merged into an outdated area
    app.clientside_callback(
        """
        function (relayout, store) {
            const triggered = dash_clientside.callback_context.triggered.map((t) => t.prop_id);
            const plot = document.querySelector("#graph .js-plotly-plot");
            if (triggered.includes("config-store.data") || !plot || !plot.layout) {
                // another graph: nothing zoomed (yet)
                return null;
            }
            const zoom = {};
            for (const axis of ["xaxis", "yaxis"]) {
                const layout = plot.layout[axis];
                if (layout && !layout.autorange && layout.range) {
                    zoom[axis] = layout.range.slice();
                }
            }
            return Object.keys(zoom).length ? zoom : null;
        }
        """,
        Output("zoom-store", "data"),
        Input("graph", "relayoutData"),
        Input("config-store", "data"),
        prevent_initial_call=True,
    )

    # callback for printing the zoomed area
    @app.callback(
        Output("points", "children"),
        Input("zoom-store", "data"),
        State("config-store", "data"),
    )
    def zoomed_points(zoom, store):
        if not zoom:
            return "Zoom into the graph to see the selected range here."

        names = {"xaxis": store.get("x"), "yaxis": store.get("y")}
        p = ""
        for axis in ("xaxis", "yaxis"):
            if axis in zoom:
                low, high = (f"{v:g}" if isinstance(v, (int, float)) else str(v) for v in zoom[axis])
                p += f"* {names[axis]}: {low} – {high}\n"
            else:
                p += f"* {names[axis]}: (not zoomed)\n"
        return p

    # callback for printing store at the bottom
    @app.callback(Output("printstore", "children"), Input("config-store", "data"))
    def printstore(store):
        p = ""
        for key, value in store.items():
            p += f"* {key}:{value}\n"
        return p

    # callback for updating database list after clickin the button
    @app.callback(
        Output("database-dropdown", "options"),
        Output("directory-input", "invalid"),
        Input("update-button", "n_clicks"),
        Input("refresh-timer", "n_intervals"),
        Input("directory-input", "value"),
    )
    def update_database_list(nr_of_clicks, n_intervals, directory):
        directory = data_directory(directory)
        if directory is None:
            return [], True
        return get_databases(directory), False

    # callback for the directory browser: open/close it, walk the tree, pick a directory
    @app.callback(
        Output("directory-modal", "is_open"),
        Output("browse-directory", "data"),
        Output("directory-input", "value"),
        Input("directory-browse", "n_clicks"),
        Input("browse-select", "n_clicks"),
        Input("browse-cancel", "n_clicks"),
        Input({"type": "browse-entry", "index": ALL}, "n_clicks"),
        State("directory-input", "value"),
        State("browse-directory", "data"),
        prevent_initial_call=True,
    )
    def browse_directory(browse, select, cancel, entries, directory, browsed):
        if not is_local_request():
            raise PreventUpdate

        trigger = ctx.triggered_id
        if trigger == "directory-browse":
            start = data_directory(directory) or server_config.directory
            return True, os.path.abspath(start), no_update
        if trigger == "browse-cancel":
            return False, no_update, no_update
        if trigger == "browse-select":
            return False, no_update, browsed
        if isinstance(trigger, dict) and ctx.triggered[0]["value"]:
            # entries are re-created (n_clicks=0) every time the list changes; only real clicks count
            return no_update, os.path.abspath(trigger["index"]), no_update
        raise PreventUpdate

    # callback for the contents of the directory browser
    @app.callback(
        Output("browse-path", "children"),
        Output("browse-list", "children"),
        Input("browse-directory", "data"),
        prevent_initial_call=True,
    )
    def update_browser(directory):
        if not directory or not is_local_request():
            raise PreventUpdate

        subdirectories, databases = list_directory(directory)
        parent = os.path.dirname(directory)
        items = []
        if parent != directory:
            items.append(
                dbc.ListGroupItem(
                    "..", id={"type": "browse-entry", "index": parent}, action=True, n_clicks=0
                )
            )
        for name, count in subdirectories:
            items.append(
                dbc.ListGroupItem(
                    [
                        name,
                        dbc.Badge(count, color="primary" if count else "light", text_color=None if count else "secondary", className="ms-2"),
                    ],
                    id={"type": "browse-entry", "index": os.path.join(directory, name)},
                    action=True,
                    n_clicks=0,
                )
            )
        path = [html.Code(directory), html.Span(f"  ({databases} databases here)", className="text-muted")]
        return path, items

    # callback for the auto refresh timer
    @app.callback(
        Output("refresh-timer", "disabled"),
        Output("refresh-timer", "interval"),
        Input("switch-autorefresh", "value"),
        Input("refresh-seconds", "value"),
    )
    def update_refresh_timer(enabled, seconds):
        seconds = max(1, int(seconds or 10))
        return not enabled, seconds * 1000

    # callback for updating store
    @app.callback(
        Output("config-store", "data"),
        Output("database-dropdown", "value"),
        Output("x-dropdown", "value"),
        Output("y-dropdown", "value"),
        [
            Input("update-button", "n_clicks"),
            Input("database-dropdown", "value"),
            Input("x-dropdown", "value"),
            Input("y-dropdown", "value"),
            Input("refresh-timer", "n_intervals"),
            Input("switch-fixgreen", "value"),
            Input("graph-dropdown", "value"),
            Input("directory-input", "value"),
        ],
        State("query-input", "value"),
        State("config-store", "data"),
        State("jitter-input", "value"),
        [State(f"recolor-{color}", "value") for color in COLORS]
        + [State(f"recolor-{color}-label", "value") for color in COLORS],
    )
    # def update_store(nr_of_clicks, contents, query, database, x, y, store, *color_states):
    def update_store(nr_of_clicks, database, x, y, n_intervals, fixgreen, graph, directory, query, store, jitter, *color_states):
        config = AnalyzerConfig.from_store(store)

        directory = data_directory(directory)
        if directory is None:
            raise PreventUpdate

        if ctx.triggered_id == "directory-input" and directory != config.directory:
            # another directory: its databases are listed by update_database_list(), start over
            config.directory = directory
            config.database = None
            config.dbkey = ""
            config.argv = ""
            return asdict(config), None, config.x, config.y

        if database is None:
            raise PreventUpdate

        # check if database exists
        path = database_path(directory, database)
        if path is None:
            print("database does not exist")
            raise PreventUpdate

        # auto refresh: only re-render when the database file has changed
        dbkey = repr(file_key(path))
        if ctx.triggered_id == "refresh-timer" and dbkey == config.dbkey:
            raise PreventUpdate
        config.dbkey = dbkey

        config.directory = directory
        config.database = database
        config.x = x
        config.y = y
        config.jitter = jitter
        config.query = query
        config.fixgreen = fixgreen
        config.graph = graph
        config.argv = get_argv(path)

        # Update color in config
        for color, regex, label in zip(COLORS, color_states[:8], color_states[8:]):
            config.colors[color] = [regex, label]

        return asdict(config), config.database, config.x, config.y

    # callback for printing the argv string at the bottom; also runs when the
    # information tab is opened (its input already exists, only the output is new)
    @app.callback(
        Output("argv", "children"),
        Input("config-store", "data"),
    )
    def update_argv(store):
        config = AnalyzerConfig.from_store(store)
        return config.argv

    # callback for the x and y lists
    @app.callback(
        Output("x-dropdown", "options"),
        Output("y-dropdown", "options"),
        Input("database-dropdown", "value"),
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_dropdown_xy(database, store):
        path = database_path(store.get("directory"), database)
        if path is None:
            raise PreventUpdate
        parameters = get_parameters(path)
        return parameters, parameters



    # callback graph; chained from update_store()
    @app.callback(
        Output("graph", "figure"),
        Input("config-store", "data"),
        Input("switch-hover", "value"),
        prevent_initial_call=True,
    )
    @timed("graph")
    def update_graph(store, hover):
        config = AnalyzerConfig.from_store(store)
        x, y = config.x, config.y

        # the recolor fields as stored by update_store(), so that the data tab
        # (which colors from the store as well) shows the same colors
        regexes = [config.colors.get(color, [None, None])[0] for color in COLORS]
        labels = [config.colors.get(color, [None, None])[1] for color in COLORS]

        # prevent update
        if any(v is None for v in [x, y]):
            raise PreventUpdate

        dataset = get_dataset(config)

        # create graph
        if config.graph in GRAPHS:
            callback_func = GRAPHS[config.graph]
            result = callback_func(x, y, config.fixgreen, dataset, config, COLORS, *regexes, *labels)
            if not hover:
                # no hover lookups or hover labels at all (cheaper on huge plots)
                result.update_layout(hovermode=False)
            return result
        raise PreventUpdate

    # callback data; chained from update_graph()
    @app.callback(
        Output("data", "children"),
        Output("data-columns", "options"),
        Output("data-columns", "value"),
        [
            # Input("config-store", "data"),
            # Input("graph", "figure"),
            Input("switch-squeezedata", "value"),
            Input("switch-wraptext", "value"),
            Input("response_s", "value"),
            Input("response_e", "value"),
            Input('tabs', 'value')
        ],
        State("config-store", "data"),
        State("data-hidden-columns", "data"),
        prevent_initial_call=False,
    )
    @timed("data")
    def update_data(squeeze, wraptext, response_s, response_e, tab, store, hidden):
        config = AnalyzerConfig.from_store(store)

        if not config.database:
            raise PreventUpdate

        # squeeze data (or not); only the column layout is needed here, the
        # rows are fetched page by page by get_rows()
        table = get_table(get_dataset(config), config, squeeze, response_s, response_e)
        columns = table.columns

        fields = []
        configs = []

        for column in columns:
            fields.append(column)
            if column in table.numeric and table.squeezed:
                # min/max columns (and amount)
                configs.append(
                    {
                        "maxWidth": 150,
                        "cellStyle": {"textAlign": "center"},
                        "headerClass": "header-center-aligned",
                        "pinned": "left",
                    }
                )
            elif column in table.numeric or column in ["color", "rlen"]:
                configs.append(
                    {
                        "maxWidth": 100,
                        "cellStyle": {"textAlign": "center"},
                        "headerClass": "header-center-aligned",
                        "pinned": "left",
                    }
                )
            elif column in ["response"]:
                if wraptext:
                    configs.append(
                        {
                            "width": 500,
                            "wrapText": True,
                            "autoHeight": True,
                        }
                    )
                else:
                    configs.append(
                        {
                            "flex": 2,
                            "minWidth": 300,
                        }
                    )
            elif column in ["hex(response)"]:
                if wraptext:
                    configs.append(
                        {
                            "width": 500,
                            "wrapText": True,
                            "autoHeight": True,
                            "cellStyle": {"font-family": "monospace"},
                        }
                    )
                else:
                    configs.append(
                        {
                            "flex": 3,
                            "minWidth": 300,
                            "cellStyle": {"font-family": "monospace"},
                        }
                    )
            else:
                configs.append({})

        hidden = set(hidden or [])
        numeric = table.numeric | {"rlen"}
        columnDefs = [
            {
                "field": field,
                **config,
                # number filters (greater than, in range, ...) for numbers; text filters otherwise
                "filter": "agNumberColumnFilter" if field in numeric else "agTextColumnFilter",
                "hide": field in hidden,
            }
            for field, config in zip(fields, configs)
        ]

        rowstyles = {
            "styleConditions": [
                {
                    "condition": f"params.data.color == '{code}'",
                    "style": {"backgroundColor": background},
                }
                for code, background in ROW_COLORS.items()
            ],
            "defaultStyle": {"backgroundColor": "white", "color": "black"},
        }

        if wraptext:
            resize_strategy = {"type": "fitGridWidth"}
        else:
            resize_strategy = {"type": "fitCellContents"}

        # a new id per grid configuration makes the grid remount and re-request its rows
        grid_id = {"type": "data-grid", "index": f"{int(bool(squeeze))}-{int(bool(wraptext))}-{response_s}-{response_e}"}

        data = AgGrid(
            id=grid_id,
            columnDefs=columnDefs,
            rowModelType="infinite",
            defaultColDef={
                "resizable": True,
                "sortable": True,
                "filter": True,
                "checkboxSelection": False,
            },
            className="ag-theme-quartz",
            getRowStyle=rowstyles,
            dashGridOptions={
                "pagination": True,
                "paginationPageSize": _PAGE_SIZE,
                "cacheBlockSize": _PAGE_SIZE,
                "maxBlocksInCache": 10,
                "rowBuffer": 0,
                "animateRows": False,
                "alwaysShowHorizontalScroll": True,
                "autoSizeStrategy": resize_strategy,
                "ensureDomOrder": True,


                "enableCellTextSelection": True,
            },
            style={"height": "1000px"}

        )

        shown = [column for column in columns if column not in hidden]
        return data, list(columns), shown

    # callback for the column selector of the data tab: show/hide columns without rebuilding the grid
    @app.callback(
        Output({"type": "data-grid", "index": ALL}, "columnDefs"),
        Output("data-hidden-columns", "data"),
        Input("data-columns", "value"),
        State("data-columns", "options"),
        State({"type": "data-grid", "index": ALL}, "columnDefs"),
        State("data-hidden-columns", "data"),
        prevent_initial_call=True,
    )
    def update_data_columns(shown, columns, grids, hidden):
        if shown is None or not columns:
            raise PreventUpdate

        # keep columns hidden that this table doesn't have (e.g. squeezed only columns)
        hidden = {column for column in (hidden or []) if column not in columns}
        hidden |= {column for column in columns if column not in shown}

        grids = [
            [{**definition, "hide": definition["field"] in hidden} for definition in definitions]
            for definitions in grids
        ]
        return grids, sorted(hidden)

    # callback rows of the data grid; requested page by page by AG Grid
    @app.callback(
        Output({"type": "data-grid", "index": MATCH}, "getRowsResponse"),
        Input({"type": "data-grid", "index": MATCH}, "getRowsRequest"),
        State("config-store", "data"),
        State("switch-squeezedata", "value"),
        State("response_s", "value"),
        State("response_e", "value"),
        prevent_initial_call=True,
    )
    @timed("rows", base="data")
    def get_rows(request, store, squeeze, response_s, response_e):
        if request is None:
            raise PreventUpdate

        config = AnalyzerConfig.from_store(store)
        if not config.database:
            raise PreventUpdate

        table = get_table(get_dataset(config), config, squeeze, response_s, response_e)
        # the grid asks for _PAGE_SIZE rows at a time; never send more than _MAX_ROWS at once
        start = max(0, int(request.get("startRow") or 0))
        end = min(int(request.get("endRow") or start + _PAGE_SIZE), start + _MAX_ROWS)
        rows, count = table.page(
            start,
            end,
            request.get("sortModel") or [],
            request.get("filterModel") or {},
        )
        return {"rowData": rows, "rowCount": count}

    @app.callback(
        Output("query-output", "children"),
        Input("query-button", "n_clicks"),
        Input("select-input", "n_submit"),
        Input("where-input", "n_submit"),
        State("select-input", "value"),
        State("where-input", "value"),
        State('config-store', 'data'),
        prevent_initial_call=True
    )
    @timed("query")
    def execute_query(n_clicks, n_submit1, n_submit2, select_cols, where_condition, data):
        path = database_path(data.get("directory"), data.get("database"))
        if path is None:
            return "ERROR: select a database on the Graph tab first"

        con = query_connection(path)

        if not where_condition:
            query = f"SELECT {select_cols} FROM experiments"
        else:
            query = f"SELECT {select_cols} FROM experiments WHERE {where_condition}"

        # Build query; only the first 100 rows are shown, so only fetch those
        try:
            df = pd.read_sql(f"SELECT * FROM ({query}) LIMIT 100;", con)
        except Exception as e:
            print(e)
            return f"ERROR: {query_error(e)}"
        finally:
            con.close()

        return df.to_string(index=False)
