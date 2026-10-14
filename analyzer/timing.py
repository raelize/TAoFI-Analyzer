"""Timing badge in the header: how long the last content update took."""

import time
from functools import wraps

from dash import Input, Output, State, set_props


def timed(label, base=None):
    """Report how long a content-generating callback took in the header badge.

    With ``base`` the badge adds the time of that earlier step when it came
    right before, e.g. the data table build plus fetching its first page of
    rows. That is done in the browser (see register_timing), so it only
    combines the requests of the same page.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - started
            # the client-side timer is filled in by assets/timing.js
            set_props("timing-store", {"data": {"label": label, "base": base, "elapsed": elapsed}})
            return result

        return wrapper

    return decorator


def register_timing(app):
    """Show the timings sent by timed() in the header badge."""
    app.clientside_callback(
        """
        function (timing, previous) {
            if (!timing) {
                return [dash_clientside.no_update, dash_clientside.no_update, dash_clientside.no_update];
            }
            let label = timing.label;
            let elapsed = timing.elapsed;
            let last = timing;
            if (timing.base && previous && previous.label === timing.base) {
                label = timing.base;
                elapsed += previous.elapsed;
                last = null;
            }
            return [label + ": ", "server (" + elapsed.toFixed(2) + " s)", last];
        }
        """,
        Output("timing-label", "children"),
        Output("timing", "children"),
        Output("timing-last", "data"),
        Input("timing-store", "data"),
        State("timing-last", "data"),
    )
