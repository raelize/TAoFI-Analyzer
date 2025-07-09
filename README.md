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
./taofi-analyzer
usage: analyzer [-h] [--ip IP] [--port PORT] [--x X] [--y Y] [--refresh-interval REFRESH_INTERVAL] [--debug] [--version] directory
analyzer: error: the following arguments are required: directory
```

For better performance you can use `gunicorn` instead of the built-in server:

```bash
# Activate the virtual environment and install the requirements as befefore
# (Optional) Tweak the gunicorn.conf.py to your needs
# Run the analyzer with gunicorn
$ ANALYZER_DIRECTORY=<DIRECTORY> gunicorn taofi-analyzer:server
```
