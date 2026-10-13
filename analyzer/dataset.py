"""The experiments of a database (filtered by the query) and their colors."""
import re
import threading
from collections import OrderedDict

import numpy as np
import pandas as pd
from dash.exceptions import PreventUpdate

from analyzer.config import COLORS
from analyzer.database import REQUIRED_COLUMNS, database_path, file_key, load_dataframe, query_error
from graphs.common import COLOR_CODES, RECOLOR_FLAGS

# Loaded databases are cached per worker process.
_DATASET_CACHE = OrderedDict()
_DATASET_CACHE_SIZE = 3
_CACHE_LOCK = threading.Lock()
_RECOLOR_CACHE_SIZE = 8


class Dataset:
    """The experiments of one database (optionally filtered by the query), as a DataFrame.

    Responses are factorized once so that recolor regexes only run on the
    unique responses; the resulting masks are cached per regex.
    """

    def __init__(self, key, df):
        self.key = key
        self.df = df
        responses = df["response"]
        self.resp_codes, self.resp_uniques = pd.factorize(responses, use_na_sentinel=False)
        self.resp_uniques = np.asarray(self.resp_uniques, dtype=object)
        self.base_colors = df["color"].to_numpy(dtype=object)
        self.base_colors.flags.writeable = False
        self._mask_cache = {}
        self._recolor_cache = OrderedDict()
        self._lock = threading.Lock()

    def match_mask(self, regex, flags=0):
        """Boolean mask over all experiments whose response matches ``regex``."""
        cache_key = (regex, flags)
        with self._lock:
            mask = self._mask_cache.get(cache_key)
        if mask is None:
            pattern = re.compile(regex.encode(), flags)
            unique_mask = np.fromiter(
                (pattern.search(r) is not None for r in self.resp_uniques),
                dtype=bool,
                count=len(self.resp_uniques),
            )
            mask = unique_mask[self.resp_codes]
            with self._lock:
                self._mask_cache[cache_key] = mask
        return mask

    def recolor(self, regexes, color_codes, fixgreen, flags=0):
        """Apply the recolor fields in order (same semantics as the old per-record loop).

        The result only depends on the arguments (never on what an earlier
        request did), so the graph and the data tab agree on every worker.
        """
        cache_key = (tuple(regexes), tuple(color_codes), bool(fixgreen), flags)
        with self._lock:
            colors = self._recolor_cache.get(cache_key)
            if colors is not None:
                self._recolor_cache.move_to_end(cache_key)
                return colors

        colors = self.base_colors.copy()
        for regex, code in zip(regexes, color_codes):
            if regex in [None, ""]:
                continue
            try:
                mask = self.match_mask(regex, flags)
            except re.error as e:
                print(f"WARNING: invalid recolor regex {regex!r}: {e}")
                continue
            if fixgreen:
                mask = mask & (colors != "G")
            colors[mask] = code
        colors.flags.writeable = False
        with self._lock:
            self._recolor_cache[cache_key] = colors
            while len(self._recolor_cache) > _RECOLOR_CACHE_SIZE:
                self._recolor_cache.popitem(last=False)
        return colors

    @staticmethod
    def color_setting(config):
        """The parts of ``config`` that decide the colors: (regexes, fixgreen, flags) or None."""
        flags = RECOLOR_FLAGS.get(config.graph)
        if flags is None:
            # graph type without recoloring (e.g. simple)
            return None
        regexes = tuple(config.colors.get(color, [None, None])[0] for color in COLORS)
        return (regexes, bool(config.fixgreen), flags)

    def colors_for(self, config):
        """Colors as shown by the graph for ``config`` (graph type, fixgreen and recolor fields)."""
        setting = self.color_setting(config)
        if setting is None:
            return self.base_colors
        regexes, fixgreen, flags = setting
        return self.recolor(regexes, COLOR_CODES, fixgreen, flags)


def get_dataset(config):
    """Cached, loaded database for the given database/query."""
    path = database_path(config.directory, config.database)
    if path is None:
        raise PreventUpdate

    key = file_key(path) + (config.query or "",)

    with _CACHE_LOCK:
        dataset = _DATASET_CACHE.get(key)
        if dataset is not None:
            _DATASET_CACHE.move_to_end(key)
            return dataset

    try:
        df = load_dataframe(path, config.query or "")
    except Exception as e:
        print("ERROR (get_dataset): %s" % query_error(e))
        raise PreventUpdate

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        print("ERROR (get_dataset): %s has no %s column" % (config.database, ", ".join(missing)))
        raise PreventUpdate

    dataset = Dataset(key, df)
    with _CACHE_LOCK:
        _DATASET_CACHE[key] = dataset
        while len(_DATASET_CACHE) > _DATASET_CACHE_SIZE:
            _DATASET_CACHE.popitem(last=False)
    return dataset
