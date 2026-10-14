"""The callbacks of the app (except the tab contents, see layout.py)."""

import json
import os
import zlib
from dataclasses import asdict
from typing import Any

import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, MATCH, Input, Output, State, html, no_update
from dash import callback_context as ctx
from dash.exceptions import PreventUpdate
from dash_ag_grid import AgGrid

from analyzer.config import COLORS, AnalyzerConfig, server_config
from analyzer.database import (
    counting,
    data_directory,
    database_path,
    file_key,
    get_argv,
    get_databases,
    get_parameters,
    is_local_request,
    list_directory,
    query_error,
    run_query,
)
from analyzer.dataset import DatasetError, get_dataset
from analyzer.graphs import AXES, AXIS_NAMES, GRAPHS, PLOT_AXES, graph_axes
from analyzer.graphs.common import COLOR_ORDER, clicked_experiment, message_figure, plotted_at, shown_points, themed
from analyzer.layout import EXPERIMENT_HIDDEN, EXPERIMENT_SHOWN, axis_style, mega_style, recolor_dot
from analyzer.selection import SELECTABLE, describe, selection_key
from analyzer.table import get_table
from analyzer.timing import register_timing, timed

# the text fields of the graph tab; applied by Update or enter (see update_store())
TEXT_FIELDS = (
    ["query-input", "jitter-input", "max-points-input"]
    + [f"recolor-{color}" for color in COLORS]
    + [f"recolor-{color}-label" for color in COLORS]
)

_PAGE_SIZE = 100
_MAX_ROWS = 1000  # per request of the data grid
QUERY_ROWS = 100  # rows of a query shown on the database tab


def register_callbacks(app):
    register_timing(app)

    # the config-store for the graph tab's callbacks: a callback with inputs on
    # the graph tab and outside of it (the config-store) fails when the store
    # changes while another tab is shown (e.g. switching tabs during an update)
    # (and the page's theme: the graph is drawn in it)
    app.clientside_callback(
        "function (store, theme) { return { ...store, theme: theme || 'light' }; }",
        Output("graph-store", "data"),
        Input("config-store", "data"),
        Input("theme-store", "data"),
    )

    # the theme button in the header: light or dark (kept by the browser)
    app.clientside_callback(
        """
        function (n_clicks, theme) {
            return n_clicks ? (theme === "dark" ? "light" : "dark") : dash_clientside.no_update;
        }
        """,
        Output("theme-store", "data"),
        Input("theme-button", "n_clicks"),
        State("theme-store", "data"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """
        function (theme) {
            const dark = theme === "dark";
            // bootstrap's dark mode (see also assets/raelize.css)
            document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light");
            return dark ? "☀ light" : "☾ dark";
        }
        """,
        Output("theme-button", "children"),
        Input("theme-store", "data"),
    )

    # callback for remembering the zoomed area of the graph (shown on the information tab);
    # runs in the browser and reads the plot's current axes, so quick successive
    # zooms can't be merged into an outdated area
    app.clientside_callback(
        """
        function (relayout, store, zoomed) {
            const triggered = dash_clientside.callback_context.triggered.map((t) => t.prop_id);
            const plot = document.querySelector("#graph .js-plotly-plot");
            // the graph keeps its zoom while this stays the same (see graph_revision())
            const graph = ["directory", "database", "x", "y", "z", "x2", "y2", "graph"];
            const revision = store ? graph.map((name) => store[name]).join("|") : null;
            if (triggered.includes("graph-store.data")) {
                // e.g. auto refresh: still zoomed in the same graph; otherwise another graph, not zoomed
                return zoomed && zoomed.revision === revision ? dash_clientside.no_update : null;
            }
            if (!plot || !plot.layout || ["3d", "mega"].includes(store && store.graph)) {
                // not a graph of x against y
                return null;
            }
            const zoom = {};
            // (xaxis2/yaxis2: the second plot of scatter2)
            for (const axis of ["xaxis", "yaxis", "xaxis2", "yaxis2"]) {
                const layout = plot.layout[axis];
                if (layout && !layout.autorange && layout.range) {
                    zoom[axis] = layout.range.slice();
                }
            }
            return Object.keys(zoom).length ? { ...zoom, revision: revision } : null;
        }
        """,
        Output("zoom-store", "data"),
        Input("graph", "relayoutData"),
        Input("graph-store", "data"),
        State("zoom-store", "data"),
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

        names = {"xaxis": store.get("x"), "yaxis": store.get("y"), "xaxis2": store.get("x2"), "yaxis2": store.get("y2")}
        axes = ["xaxis", "yaxis"] + (["xaxis2", "yaxis2"] if store.get("graph") == "scatter2" else [])
        p = ""
        for axis in axes:
            if axis in zoom:
                low, high = (f"{v:g}" if isinstance(v, int | float) else str(v) for v in zoom[axis])
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
        Output("count-timer", "disabled"),
        Input("update-button", "n_clicks"),
        Input("refresh-timer", "n_intervals"),
        Input("directory-input", "value"),
        Input("count-timer", "n_intervals"),
    )
    def update_database_list(nr_of_clicks, n_intervals, directory, counted):
        directory = data_directory(directory)
        if directory is None:
            return [], True, True
        # large databases are counted in the background: check again until they're done
        options = get_databases(directory)
        return options, False, not counting()

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
            items.append(dbc.ListGroupItem("..", id={"type": "browse-entry", "index": parent}, action=True, n_clicks=0))
        for name, count in subdirectories:
            items.append(
                dbc.ListGroupItem(
                    [
                        name,
                        dbc.Badge(
                            count,
                            color="primary" if count else "light",
                            text_color=None if count else "secondary",
                            className="ms-2",
                        ),
                    ],
                    id={"type": "browse-entry", "index": os.path.join(directory, name)},
                    action=True,
                    n_clicks=0,
                )
            )
        path = [html.Code(directory), html.Span(f"  ({databases} databases here)", className="text-muted")]
        return path, items

    # callback for the dots of the recolor fields: filled when the color is recolored (as applied)
    @app.callback(
        [Output(f"recolor-{color}-dot", "style") for color in COLORS],
        Input("graph-store", "data"),
    )
    def show_recolored(store):
        config = AnalyzerConfig.from_store(store)
        return [recolor_dot(color, bool(config.colors.get(color, [None])[0])) for color in COLORS]

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
        output={
            "store": Output("config-store", "data"),
            "database": Output("database-dropdown", "value"),
            "axes": [Output(f"{axis}-dropdown", "value") for axis in AXES],
            "mega": Output("mega-dropdown", "value"),
            "applied": Output("applied-fields", "data"),
            "fields": [Output(field, "value") for field in TEXT_FIELDS],
        },
        inputs={
            "update": Input("update-button", "n_clicks"),
            "database": Input("database-dropdown", "value"),
            "axes": [Input(f"{axis}-dropdown", "value") for axis in AXES],
            "mega": Input("mega-dropdown", "value"),
            "n_intervals": Input("refresh-timer", "n_intervals"),
            "fixgreen": Input("switch-fixgreen", "value"),
            "graph": Input("graph-dropdown", "value"),
            "directory": Input("directory-input", "value"),
            # enter in one of the text fields applies them (like Update)
            "submits": [Input(field, "n_submit") for field in TEXT_FIELDS],
        },
        state={
            "query": State("query-input", "value"),
            "store": State("config-store", "data"),
            "jitter": State("jitter-input", "value"),
            "max_points": State("max-points-input", "value"),
            "regexes": [State(f"recolor-{color}", "value") for color in COLORS],
            "labels": [State(f"recolor-{color}-label", "value") for color in COLORS],
            "applied": State("applied-fields", "data"),
        },
    )
    def update_store(
        update,
        database,
        axes,
        mega,
        n_intervals,
        fixgreen,
        graph,
        directory,
        submits,
        query,
        store,
        jitter,
        max_points,
        regexes,
        labels,
        applied,
    ):
        config = AnalyzerConfig.from_store(store)

        def result(database=no_update, applied=no_update, fields=no_update):
            if fields is no_update:
                fields = [no_update] * len(TEXT_FIELDS)
            return {
                "store": asdict(config),
                "database": database,
                "axes": [getattr(config, axis) for axis in AXES],
                "mega": config.mega,
                "applied": applied,
                "fields": fields,
            }

        directory = data_directory(directory)
        if directory is None:
            raise PreventUpdate

        # a new page: the text fields as applied before (e.g. before a reload of the
        # page), shown and applied; not when the graph tab is shown again (the text
        # in them then is what was typed, maybe not applied)
        restored = no_update
        if not config.fields_restored:
            config.fields_restored = True
            applied = applied or {}
            config.query = applied.get("query") or ""
            config.jitter = applied.get("jitter") or 0
            config.max_points = applied.get("max_points")
            for color in COLORS:
                config.colors[color] = list((applied.get("colors") or {}).get(color) or [None, None])
            restored = text_fields(config)

        if ctx.triggered_id == "directory-input" and directory != config.directory:
            # another directory: its databases are listed by update_database_list(), start over
            config.directory = directory
            config.database = None
            config.dbkey = ""
            config.argv = ""
            return result(None, fields=restored)

        if database is None:
            if restored is not no_update:
                return result(fields=restored)
            raise PreventUpdate

        # check if database exists
        path = database_path(directory, database)
        if path is None:
            print("database does not exist")
            if restored is not no_update:
                return result(fields=restored)
            raise PreventUpdate

        # auto refresh: only re-render when the database file has changed
        dbkey = repr(file_key(path))
        if ctx.triggered_id == "refresh-timer" and dbkey == config.dbkey:
            raise PreventUpdate
        config.dbkey = dbkey

        config.directory = directory
        config.database = database
        for axis, value in zip(AXES, axes, strict=True):
            setattr(config, axis, value)
        config.mega = mega or None
        config.fixgreen = fixgreen
        config.graph = graph
        config.argv = get_argv(path)

        # the text fields only with Update or enter: not e.g. a query that is
        # still being typed at an auto refresh. (When the graph tab is shown, all
        # its inputs count as triggered: those of a real click or enter are > 0.)
        applied = no_update
        if any(t["value"] and t["prop_id"].split(".")[0] in ("update-button", *TEXT_FIELDS) for t in ctx.triggered):
            config.query = query
            config.jitter = jitter
            config.max_points = None if max_points is None else max(0, int(max_points))
            for color, regex, label in zip(COLORS, regexes, labels, strict=True):
                config.colors[color] = [regex, label]
            # (kept by the browser)
            applied = {
                "query": config.query,
                "jitter": config.jitter,
                "max_points": config.max_points,
                "colors": config.colors,
            }

        return result(config.database, applied, restored)

    # callback for printing the argv string at the bottom; also runs when the
    # information tab is opened (its input already exists, only the output is new)
    @app.callback(
        Output("argv", "children"),
        Input("config-store", "data"),
    )
    def update_argv(store):
        config = AnalyzerConfig.from_store(store)
        return config.argv

    # callback for the axis lists
    @app.callback(
        [Output(f"{axis}-dropdown", "options") for axis in AXES] + [Output("mega-dropdown", "options")],
        Input("database-dropdown", "value"),
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def update_dropdown_xy(database, store):
        path = database_path(store.get("directory"), database)
        if path is None:
            raise PreventUpdate
        parameters = get_parameters(path)
        return [parameters] * (len(AXES) + 1)

    # callback for showing the axis dropdowns the graph type uses
    @app.callback(
        [Output(f"{axis}-dropdown", "style") for axis in AXES] + [Output("mega-dropdown", "style")],
        Input("graph-dropdown", "value"),
        prevent_initial_call=True,
    )
    def show_axes(graph):
        return [axis_style(graph, axis) for axis in AXES] + [mega_style(graph)]

    # callback graph; chained from update_store()
    @app.callback(
        Output("graph", "figure"),
        Input("graph-store", "data"),
        Input("switch-hover", "value"),
        prevent_initial_call=True,
    )
    @timed("graph")
    def update_graph(store, hover):
        return themed(draw_graph(AnalyzerConfig.from_store(store), hover), (store or {}).get("theme"))

    def draw_graph(config, hover):
        """The graph of ``config`` (or a message why it can't be drawn)."""
        x, y = config.x, config.y

        # the recolor fields as stored by update_store(), so that the data tab
        # (which colors from the store as well) shows the same colors
        regexes = [config.colors.get(color, [None, None])[0] for color in COLORS]
        labels = [config.colors.get(color, [None, None])[1] for color in COLORS]

        if not config.database:
            # (e.g. after switching to another directory: not the graph of the previous database)
            return message_figure("Select a database")
        axes = graph_axes(config.graph)
        missing = [axis for axis in axes if getattr(config, axis) is None]
        if missing:
            names = " and ".join(AXIS_NAMES[axis] for axis in missing)
            return message_figure(f"Select the {names} to show the {config.graph} graph")

        try:
            dataset = get_dataset(config)
        except DatasetError as e:
            return message_figure(str(e))
        # e.g. another database without the selected columns
        missing = [getattr(config, axis) for axis in axes if getattr(config, axis) not in dataset.df.columns]
        if missing:
            return message_figure(
                f"{config.database} has no {', '.join(missing)} column{'s' if len(missing) > 1 else ''}"
            )

        # create graph
        if config.graph in GRAPHS:
            callback_func = GRAPHS[config.graph]
            result = callback_func(x, y, config.fixgreen, dataset, config, COLORS, *regexes, *labels)
            if not hover:
                # no hover lookups or hover labels at all (cheaper on huge plots)
                result.update_layout(hovermode=False)
            # keep the zoom and the colors hidden via the legend when the graph is
            # drawn again (e.g. auto refresh, another query), but not for another graph
            result.update_layout(uirevision=graph_revision(config))
            return result
        raise PreventUpdate

    # callback for the selection in the graph (box or lasso select): only its
    # outline, the selected points can be a million (see selection.py)
    app.clientside_callback(
        """
        function (selected, store) {
            if (!selected || !store || !SELECTABLE.includes(store.graph)) {
                return null;
            }
            const same = {
                directory: store.directory, database: store.database, query: store.query, jitter: store.jitter,
            };
            // the plots of the graph: their axes in the event, and the columns they show
            const plots = [["x", "y", store.x, store.y], ["x2", "y2", store.x2, store.y2]];
            for (const [xaxis, yaxis, x, y] of plots) {
                if (selected.range && selected.range[xaxis] && selected.range[yaxis]) {
                    return { ...same, x: x, y: y, box: { x: selected.range[xaxis], y: selected.range[yaxis] } };
                }
                if (selected.lassoPoints && selected.lassoPoints[xaxis] && selected.lassoPoints[yaxis]) {
                    const lasso = { x: selected.lassoPoints[xaxis], y: selected.lassoPoints[yaxis] };
                    return { ...same, x: x, y: y, lasso: lasso };
                }
            }
            return null;
        }
        """.replace("SELECTABLE", json.dumps(list(SELECTABLE))),
        Output("selection-store", "data"),
        Input("graph", "selectedData"),
        State("graph-store", "data"),
        prevent_initial_call=True,
    )

    # callback for the experiment clicked in the graph
    @app.callback(
        Output("experiment", "children"),
        Output("experiment-panel", "style"),
        Input("graph", "clickData"),
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def show_experiment(click, store):
        return clicked_card(click, store), EXPERIMENT_SHOWN

    @app.callback(
        Output("experiment-panel", "style", allow_duplicate=True),
        Input("experiment-close", "n_clicks"),
        prevent_initial_call=True,
    )
    def close_experiment(n_clicks):
        # (also called when the graph tab is shown: the button is new then)
        if not n_clicks:
            raise PreventUpdate
        return EXPERIMENT_HIDDEN

    # the graph takes the width the experiment panel leaves (its height is the graph type's)
    app.clientside_callback(
        """
        function (style) {
            const plot = document.querySelector("#graph .js-plotly-plot");
            if (plot && plot._fullLayout) {
                setTimeout(() => Plotly.Plots.resize(plot), 0);
            }
            return dash_clientside.no_update;
        }
        """,
        Output("graph-resized", "data"),
        Input("experiment-panel", "style"),
        prevent_initial_call=True,
    )

    def clicked_card(click, store):
        """The experiment of the point clicked in the graph (or why it can't be shown)."""
        points = (click or {}).get("points") or []
        if not points or not isinstance(points[0].get("pointNumber"), int):
            raise PreventUpdate

        config = AnalyzerConfig.from_store(store)
        try:
            dataset = get_dataset(config)
        except DatasetError as e:
            return html.Div(str(e), className="text-danger")
        # the traces are the colors of the graph, see color_traces()
        plots = PLOT_AXES.get(config.graph, [{}])
        colors = dataset.colors_for(config)
        clicked = clicked_experiment(
            colors, points[0].get("curveNumber", 0), points[0]["pointNumber"], len(plots), shown_points(colors, config)
        )
        if clicked is not None:
            index, plot = clicked
            columns = {coordinate: getattr(config, axis) for coordinate, axis in plots[plot].items()}
        if clicked is None or not plotted_at(dataset, index, columns, config.jitter, points[0]):
            # (a graph of another version of the database: it was drawn again in the meantime)
            return "The database changed: click the point again."
        return experiment_card(dataset, config, index)

    # callback for auto refresh on the data tab: the grid fetches its rows again
    # when the database changed (from its latest version, see get_dataset())
    @app.callback(
        Output("data-dbkey", "data"),
        Input("refresh-timer", "n_intervals"),
        State("tabs", "value"),
        State("data-dbkey", "data"),
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_data(n_intervals, tab, shown, store):
        if tab != "tab-data":
            # (the data tab is made again when it's shown)
            raise PreventUpdate
        config = AnalyzerConfig.from_store(store)
        path = database_path(config.directory, config.database)
        if path is None:
            raise PreventUpdate
        dbkey = repr(file_key(path))
        if dbkey == shown:
            raise PreventUpdate
        return dbkey

    # (the grid keeps its page, sorting and filters)
    app.clientside_callback(
        """
        function (dbkey) {
            for (const element of document.querySelectorAll('[id*="data-grid"]')) {
                try {
                    dash_ag_grid.getApi(element.id).refreshInfiniteCache();
                } catch (e) {
                    // (not the grid itself, or not ready yet)
                }
            }
            return dash_clientside.no_update;
        }
        """,
        Output("data-refresh", "data"),
        Input("data-dbkey", "data"),
        prevent_initial_call=True,
    )

    # callback data; chained from update_graph()
    @app.callback(
        Output("data", "children"),
        Output("data-columns", "options"),
        Output("data-columns", "value"),
        Output("data-selection", "children"),
        Output("data-selection-bar", "style"),
        Input("switch-squeezedata", "value"),
        Input("switch-wraptext", "value"),
        Input("response_s", "value"),
        Input("response_e", "value"),
        Input("tabs", "value"),
        Input("data-show-all", "n_clicks"),
        State("config-store", "data"),
        State("data-hidden-columns", "data"),
        State("selection-store", "data"),
        prevent_initial_call=False,
    )
    @timed("data")
    def update_data(squeeze, wraptext, response_s, response_e, tab, show_all, store, hidden, selection):
        if tab != "tab-data":
            # (made when the data tab is shown)
            raise PreventUpdate
        config = AnalyzerConfig.from_store(store)

        if not config.database:
            raise PreventUpdate

        if ctx.triggered_id == "data-show-all" and show_all:
            # (the selection-store is emptied by show_all())
            selection = None

        # squeeze data (or not); only the column layout is needed here, the
        # rows are fetched page by page by get_rows()
        try:
            dataset = get_dataset(config)
        except DatasetError as e:
            return html.Div(str(e), className="text-danger m-3"), [], [], "", {"display": "none"}
        table = get_table(dataset, config, squeeze, response_s, response_e, selection)
        hidden = set(hidden or [])

        # a new id per grid configuration makes the grid remount and re-request its rows
        selected = zlib.crc32(selection_key(selection).encode()) if table.selected is not None else ""
        grid_id = {
            "type": "data-grid",
            "index": f"{int(bool(squeeze))}-{int(bool(wraptext))}-{response_s}-{response_e}-{selected}",
        }
        grid = data_grid(grid_id, column_defs(table, wraptext, hidden), wraptext)

        if table.selected is None:
            note, bar = "", {"display": "none"}
        else:
            note, bar = f"Showing {describe(selection, table.selected)}.", {}

        shown = [column for column in table.columns if column not in hidden]
        return grid, table.columns, shown, note, bar

    @app.callback(
        Output("selection-store", "data", allow_duplicate=True),
        Input("data-show-all", "n_clicks"),
        prevent_initial_call=True,
    )
    def show_all(n_clicks):
        # (also called when the data tab is shown: the button is new then)
        if not n_clicks:
            raise PreventUpdate
        return

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
        State("selection-store", "data"),
        prevent_initial_call=True,
    )
    @timed("rows", base="data")
    def get_rows(request, store, squeeze, response_s, response_e, selection):
        if request is None:
            raise PreventUpdate

        config = AnalyzerConfig.from_store(store)
        if not config.database:
            raise PreventUpdate

        table = get_table(get_dataset(config), config, squeeze, response_s, response_e, selection)
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
        State("config-store", "data"),
        prevent_initial_call=True,
    )
    @timed("query")
    def execute_query(n_clicks, n_submit1, n_submit2, select_cols, where_condition, data):
        path = database_path(data.get("directory"), data.get("database"))
        if path is None:
            return "ERROR: select a database on the Graph tab first"
        try:
            df, count = run_query(path, select_cols or "*", where_condition, QUERY_ROWS)
        except Exception as e:
            print(e)
            return f"ERROR: {query_error(e)}"

        if count is None:
            note = f"{QUERY_ROWS} or more rows, the first {QUERY_ROWS} are shown (counting them took too long)"
        else:
            note = f"{count} rows" + (f"; the first {QUERY_ROWS} are shown" if count > QUERY_ROWS else "")
        return [html.Div(note, className="mb-2"), query_grid(df)]


def text_fields(config):
    """The values of the TEXT_FIELDS for ``config``."""
    return (
        [config.query, config.jitter, config.max_points]
        + [config.colors[color][0] for color in COLORS]
        + [config.colors[color][1] for color in COLORS]
    )


def query_grid(df):
    """The rows of a query of the database tab."""
    return AgGrid(
        columnDefs=[{"field": column, "headerName": column} for column in df.columns],
        rowData=df.astype(object).where(df.notna(), None).to_dict("records"),
        defaultColDef={"resizable": True, "sortable": True, "filter": True},
        className="ag-theme-quartz",
        dashGridOptions={
            # (column names like "count(*)" or "a.b" are names, not paths)
            "suppressFieldDotNotation": True,
            "enableCellTextSelection": True,
            "domLayout": "autoHeight",
        },
        columnSize="autoSize",
    )


def experiment_card(dataset, config, index):
    """All columns of an experiment, its response (as text and hex) and how many experiments share it."""
    row = dataset.df.iloc[index]
    response = row["response"]
    shown = dataset.colors_for(config)[index]
    same = int((dataset.resp_codes == dataset.resp_codes[index]).sum())

    values = []
    for column, value in row.items():
        if column == "response":
            continue
        if isinstance(value, bytes):
            text = value.hex(" ")
        elif column == "color" and shown != value:
            text = f"{value} (shown as {shown})"
        else:
            text = "" if pd.isna(value) else str(value)
        values.append(html.Tr([html.Th(column, className="pe-3"), html.Td(text)]))
    values.append(
        html.Tr(
            [
                html.Th("response", className="pe-3"),
                html.Td(f"{len(response)} bytes, the same for {same} of {len(dataset.df)} experiments"),
            ]
        )
    )

    def block(text, **style):
        return html.Pre(
            text,
            style={"whiteSpace": "pre-wrap", "wordBreak": "break-all", "maxHeight": "300px", **style},
            className="border rounded p-2 mb-2",
        )

    return [
        html.Table(html.Tbody(values), className="mb-2"),
        block(response.decode("utf-8", errors="replace")),
        block(response.hex(" "), fontFamily="monospace"),
    ]


def graph_revision(config):
    """What makes it another graph (with its own zoom); the same in the zoom-store callback."""
    values = (
        config.directory,
        config.database,
        *[getattr(config, axis) for axis in AXES],
        config.graph,
        ",".join(config.mega or []),
    )
    return "|".join("" if v is None else str(v) for v in values)


def column_defs(table, wraptext, hidden):
    """AG Grid column definitions for the columns of ``table``."""
    centered = {"cellStyle": {"textAlign": "center"}, "headerClass": "header-center-aligned", "pinned": "left"}
    numeric = table.numeric | {"rlen"}

    definitions = []
    for column in table.columns:
        definition: dict[str, Any]
        if column in table.numeric and table.squeezed:
            # min/max columns (and amount)
            definition = {"maxWidth": 150, **centered}
        elif column in table.numeric or column in ["color", "rlen"]:
            definition = {"maxWidth": 100, **centered}
        elif column == "response":
            definition = (
                {"width": 500, "wrapText": True, "autoHeight": True} if wraptext else {"flex": 2, "minWidth": 300}
            )
        elif column == "hex(response)":
            definition = (
                {"width": 500, "wrapText": True, "autoHeight": True} if wraptext else {"flex": 3, "minWidth": 300}
            )
            definition["cellStyle"] = {"font-family": "monospace"}
        else:
            definition = {}

        if column in table.numeric and table.squeezed and column != "amount":
            # min(power) -> Min(Power) (AG Grid only capitalizes the first letter of a header)
            function, name = column[:-1].split("(", 1)
            definition["headerName"] = f"{function.capitalize()}({name[:1].upper()}{name[1:]})"

        definitions.append(
            {
                "field": column,
                **definition,
                # number filters (greater than, in range, ...) for numbers; text filters otherwise
                "filter": "agNumberColumnFilter" if column in numeric else "agTextColumnFilter",
                "hide": column in hidden,
            }
        )
    return definitions


def data_grid(grid_id, columns, wraptext):
    """The data tab's grid; its rows are requested page by page (see get_rows())."""
    # a background per color code: the classes row-G etc. (assets/raelize.css; light and dark)
    # (params.data: not for the rows that are still being loaded)
    row_classes = {f"row-{code}": f"params.data && params.data.color == '{code}'" for code in COLOR_ORDER}

    return AgGrid(
        id=grid_id,
        columnDefs=columns,
        rowModelType="infinite",
        defaultColDef={"resizable": True, "sortable": True, "filter": True, "checkboxSelection": False},
        className="ag-theme-quartz",
        rowClassRules=row_classes,
        dashGridOptions={
            "pagination": True,
            "paginationPageSize": _PAGE_SIZE,
            "cacheBlockSize": _PAGE_SIZE,
            "maxBlocksInCache": 10,
            "rowBuffer": 0,
            "animateRows": False,
            "alwaysShowHorizontalScroll": True,
            "autoSizeStrategy": {"type": "fitGridWidth"} if wraptext else {"type": "fitCellContents"},
            "ensureDomOrder": True,
            "enableCellTextSelection": True,
        },
        style={"height": "1000px"},
    )
