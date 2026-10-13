#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

from dash import Dash

from analyzer.callbacks import register_callbacks
from analyzer.config import server_config
from analyzer.layout import create_layout

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Backport for <3.11


#
# PROJECT METADATA
#


def get_project_metadata():
    """Load project metadata from pyproject.toml."""
    default_metadata = {
        "version": "2.1.0",
        "description": "A Dash-based web application for analyzing glitch experiment data",
    }

    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        pyproject_path = Path(os.path.join(current_dir, "pyproject.toml"))
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                project = data.get("project", {})
                return {
                    "version": project.get("version", default_metadata["version"]),
                    "description": project.get(
                        "description", default_metadata["description"]
                    ),
                }
        else:
            print(
                f"WARNING: pyproject.toml not found at {pyproject_path}. Using default metadata.",
                file=sys.stderr,
            )
        return default_metadata
    except Exception:
        return default_metadata


PROJECT_METADATA = get_project_metadata()


def check_env() -> None:
    required = ["ANALYZER_DIRECTORY"]
    missing = [var for var in required if var not in os.environ]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")


#
# App
#

# Note: i've added suppress_callback_exceptions=True here as with tabs some elements may be not present.
# Another option would be to use a validated layout which prevents the callback exceptions.

app = Dash(__name__, suppress_callback_exceptions=True)
app.css.config.serve_locally = True
app.scripts.config.serve_locally = True
server = app.server

#
# Main
#

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"Raelize Glitch Analyzer v{PROJECT_METADATA['version']} - {PROJECT_METADATA['description']}",
        prog="analyzer",
    )
    parser.add_argument("--ip", help="Server port", type=str, default="127.0.0.1")
    parser.add_argument("--port", help="Server port", type=int, default=8000)
    parser.add_argument("directory", nargs="+", help="Database directorys", type=str)
    parser.add_argument("--x", required=False, help="Preset the x parameter")
    parser.add_argument("--y", required=False, help="Preset the y parameter")
    parser.add_argument("--database", required=False, help="Database selected")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {PROJECT_METADATA['version']}"
    )

    args = parser.parse_args()

    server_config.directory = os.path.abspath(args.directory[0])
    server_config.x = args.x
    server_config.y = args.y
    server_config.database = args.database 

    register_callbacks(app)
    create_layout(app)

    app.run(host=args.ip, port=args.port, debug=args.debug)
else:
    # this path is taken when started with e.g. unicorn
    check_env()
    server_config.directory = os.path.abspath(os.environ.get("ANALYZER_DIRECTORY", "./databases"))
    if not Path(server_config.directory).absolute().exists():
        raise FileNotFoundError(
            f"Directory {server_config.directory} does not exist. Please set the environment variable ANALYZER_DIRECTORY."
        )
    # same defaults as the command line (None, not ""), optionally preset via env
    server_config.database = os.environ.get("ANALYZER_DATABASE")
    server_config.x = os.environ.get("ANALYZER_X")
    server_config.y = os.environ.get("ANALYZER_Y")

    register_callbacks(app)
    create_layout(app)

    # generate the index page once now: this registers the component script
    # bundles. With several gunicorn workers a worker may otherwise be asked
    # for a bundle before it has served an index page, which fails with
    # "dash is not a registered library" and leaves the browser with a blank app.
    with app.server.test_request_context():
        app._setup_server()
        app.index()
