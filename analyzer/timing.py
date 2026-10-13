"""Timing badge in the header: how long the last content update took."""
import time
from functools import wraps

from dash import set_props

_LAST_TIMING = {}


def timed(label, base=None):
    """Report how long a content-generating callback took in the header badge.

    With ``base`` the badge also keeps showing the time of that earlier step,
    e.g. the data table build next to the time of fetching one page of rows.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - started
            _LAST_TIMING[label] = elapsed
            shown = label
            if base in _LAST_TIMING:
                # e.g. the data table build plus the first page of rows
                elapsed += _LAST_TIMING.pop(base)
                shown = base
            # the client-side timer is filled in by assets/timing.js
            set_props("timing-label", {"children": f"{shown}: "})
            set_props("timing", {"children": f"server ({elapsed:.2f} s)"})
            return result

        return wrapper

    return decorator
