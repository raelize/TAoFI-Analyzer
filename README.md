# TAoFI-Analyzer

## v1.0

This is the `Analyzer` that we use for our [The Art of Fault Injection (TAoFI) training](https://raelize.com/taofi/).

## v2.0

This is a new version of the `TAoFI-Analyzer` with additional features that were not available the previous version.

**Important:** This can be considered a development branch and things may not work as expected.

## Usage

It's adviced to run the `TAoFI-Analyzer` from a virtual environment:

```bash
$ python -m venv .venv
$ source .venv/bin/activate
$ pip install -r requirements.txt
$ ./taofi-analyzer 
usage: analyzer [-h] --directory DIRECTORY [--port PORT] [--x X] [--y Y] [--remote]
analyzer: error: the following arguments are required: --directory
```