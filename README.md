# TAoFI-Analyzer

## v1.0

This is the `Analyzer` that we use for our [The Art of Fault Injection (TAoFI) training](https://raelize.com/taofi/).

## v2.0

This is a new version of the `TAoFI-Analyzer` with additional features that were not available the previous version.

**Important:** This can be considered a development branch and things may not work as expected.

## Usage

It's adviced to run the `TAoFI-Analyzer` from a virtual environment:

```bash
# 1.1 Either straight via pip and the provided pyproject.toml
$ python -m venv .venv
$ source .venv/bin/activate
$ pip install -e .
# 1.2 Or via uv
$ uv venv
$ source .venv/bin/activate
$ uv sync
# 2. Run the analyzer:
$ ./taofi-analyzer --help
usage: analyzer [-h] [--ip IP] [--port PORT] [--x X] [--y Y] [--z Z] [--x2 X2]
                [--y2 Y2] [--database DATABASE] [--debug] [--version]
                directory
$ ./taofi-analyzer databases/
```

The analyzer listens on `127.0.0.1:8000` by default (`--ip`/`--port`). Use `--database`, `--x`, `--y`, `--z`, `--x2` and `--y2` to preselect a database and the axes (z: for the 3d graph; x2 and y2: the right plot of the scatter2 graph).

For better performance you can use `gunicorn` instead of the built-in server:

```bash
# Activate the virtual environment and install the requirements as before
# (Optional) Tweak the gunicorn.conf.py to your needs
# Run the analyzer with gunicorn
$ ANALYZER_DIRECTORY=<DIRECTORY> gunicorn taofi-analyzer:server
```

With `gunicorn` the analyzer also listens on `127.0.0.1:8000` (`ANALYZER_IP`/`ANALYZER_PORT`). `ANALYZER_DATABASE`, `ANALYZER_X`, `ANALYZER_Y`, `ANALYZER_Z`, `ANALYZER_X2` and `ANALYZER_Y2` preselect a database and the axes.

## Security

The analyzer is meant to run on your own machine:

- databases are only opened read-only
- only users on the same machine (`127.0.0.1`) can switch to another directory; others only see the directory the analyzer was started with
- queries typed in the analyzer are stopped after 60 seconds

Anyone who can reach the analyzer can still run (read-only) SQL queries on its databases, so think twice before listening on another address than `127.0.0.1`.

## Development

The code lives in the `analyzer` package (`taofi-analyzer.py` only starts it):

- `config.py`: what a browser tab shows (the config store) and the server settings
- `database.py`: which databases may be read, read-only connections and queries
- `dataset.py`: the loaded experiments and their (re)coloring
- `table.py`: the rows of the data tab (squeeze, filter, sort, page)
- `callbacks.py` and `layout.py`: the Dash app
- `graphs/`: the graph types

Run the tests and the linter with `uv`:

```bash
$ uv run pytest
$ uv run ruff check
# the browser tests (headless Chrome, or Playwright's Chromium via: uv run playwright install chromium)
$ uv sync --group browser
$ uv run pytest -m browser
# how long opening databases of 1000 to 1000000 experiments takes (generates up to ~4 GB, see tests/test_profile.py)
$ uv run pytest -m profile -s
```
