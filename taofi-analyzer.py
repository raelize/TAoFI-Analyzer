#!/usr/bin/env python3
import argparse
import logging
import os
import sys
import tomllib

from dash import Dash

from analyzer.callbacks import register_callbacks
from analyzer.config import server_config
from analyzer.graphs import AXES, GRAPHS, common
from analyzer.layout import create_layout

#
# PROJECT METADATA
#


def get_project_metadata():
    """Load project metadata from pyproject.toml."""
    metadata = {
        "version": "2.1.0",
        "description": "A Dash-based web application for analyzing glitch experiment data",
    }

    pyproject_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproject.toml")
    try:
        with open(pyproject_path, "rb") as f:
            project = tomllib.load(f).get("project", {})
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"WARNING: can't read {pyproject_path} ({e}). Using default metadata.", file=sys.stderr)
        return metadata

    return {key: project.get(key, default) for key, default in metadata.items()}


PROJECT_METADATA = get_project_metadata()


#
# App
#

# Note: i've added suppress_callback_exceptions=True here as with tabs some elements may be not present.
# Another option would be to use a validated layout which prevents the callback exceptions.

app = Dash(__name__, suppress_callback_exceptions=True)
app.css.config.serve_locally = True
app.scripts.config.serve_locally = True
server = app.server


# the axis presets (see AXES)
AXIS_HELP = {
    "x": "Preset the x parameter",
    "y": "Preset the y parameter",
    "z": "Preset the z parameter (3d graph)",
    "x2": "Preset the x parameter of the right plot (scatter2 graph)",
    "y2": "Preset the y parameter of the right plot (scatter2 graph)",
}


def setup(directory, database=None, graph=None, max_points=None, **axes):
    """Configure the analyzer (directory and presets) and create its page."""
    directory = os.path.abspath(directory)
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"Directory {directory} does not exist.")
    if graph is not None and graph not in GRAPHS:
        raise ValueError(f"Unknown graph type {graph!r} (choose from {', '.join(GRAPHS)})")
    if max_points is not None:
        if not str(max_points).isdigit():
            raise ValueError(f"The max points must be a number (0 for all), not {max_points!r}")
        # (for every graph type, unless set on the graph tab)
        common.DEFAULT_MAX_POINTS = int(max_points)

    server_config.directory = directory
    server_config.database = database
    if graph is not None:
        server_config.graph = graph
    for axis in AXES:
        setattr(server_config, axis, axes.get(axis))

    register_callbacks(app)
    create_layout(app)


def without_development_server_warning(record):
    """Logging filter: removes werkzeug's "This is a development server" warning (keeps the rest)."""
    if isinstance(record.msg, str) and "This is a development server" in record.msg:
        record.msg = "\n".join(line for line in record.msg.splitlines() if "This is a development server" not in line)
    return True


#
# Main
#

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"Raelize Glitch Analyzer v{PROJECT_METADATA['version']} - {PROJECT_METADATA['description']}",
        prog="analyzer",
    )
    parser.add_argument("--ip", help="Address to listen on", type=str, default="127.0.0.1")
    parser.add_argument("--port", help="Server port", type=int, default=8000)
    parser.add_argument("directory", help="Directory with the databases", type=str)
    for axis in AXES:
        parser.add_argument(f"--{axis}", required=False, help=AXIS_HELP[axis])
    parser.add_argument("--database", required=False, help="Preset the database")
    parser.add_argument("--graph", required=False, choices=list(GRAPHS), help="Preset the graph type")
    parser.add_argument("--max-points", required=False, type=int, metavar="N",
                        help="The points a graph draws at most (default: 200000, 100000 for 3d; 0: all)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {PROJECT_METADATA['version']}"
    )

    args = parser.parse_args()

    setup(args.directory, args.database, args.graph, args.max_points, **{axis: getattr(args, axis) for axis in AXES})

    # the built-in server is meant for a single (local) user; gunicorn is
    # documented for anything else, so leave out werkzeug's reminder of that
    logging.getLogger("werkzeug").addFilter(without_development_server_warning)
    app.run(host=args.ip, port=args.port, debug=args.debug)
else:
    # this path is taken when started with e.g. gunicorn, configured via the environment
    if "ANALYZER_DIRECTORY" not in os.environ:
        raise ValueError("Missing required environment variable: ANALYZER_DIRECTORY")

    setup(
        os.environ["ANALYZER_DIRECTORY"],
        os.environ.get("ANALYZER_DATABASE"),
        os.environ.get("ANALYZER_GRAPH"),
        os.environ.get("ANALYZER_MAX_POINTS"),
        **{axis: os.environ.get(f"ANALYZER_{axis.upper()}") for axis in AXES},
    )

    # serve the index page once now: this registers the component script
    # bundles. Otherwise a (restarted) worker may be asked for a bundle before
    # it has served an index page, e.g. by a page that was already open, which
    # fails with "dash is not a registered library" and leaves a blank app.
    app.server.test_client().get("/")
