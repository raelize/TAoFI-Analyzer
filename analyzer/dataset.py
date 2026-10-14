"""The experiments of a database (filtered by the query) and their colors."""

import gc
import re
import threading
from collections import OrderedDict

import numpy as np
import pandas as pd
from dash.exceptions import PreventUpdate

from analyzer.config import COLORS
from analyzer.database import (
    REQUIRED_COLUMNS,
    appended_to,
    database_path,
    file_key,
    load_experiments,
    max_rowid,
    query_error,
)
from analyzer.graphs.common import COLOR_CODES, RECOLOR_FLAGS


class DatasetError(PreventUpdate):
    """A database that can't be shown (bad query, not enough memory, ...); the message is for the user."""


# Loaded databases are cached per worker process: (path, query) -> Dataset of
# the latest version of the file.
_DATASET_CACHE = OrderedDict()
_DATASET_CACHE_SIZE = 3
_CACHE_LOCK = threading.Lock()
_LOAD_LOCKS = {}  # (path, query) -> lock held while loading it
_RECOLOR_CACHE_SIZE = 8

# functions that empty a cache (and return whether there was anything in it),
# called when a database doesn't fit in memory; see free_memory()
CACHE_CLEARERS = []


def _clear_cache():
    with _CACHE_LOCK:
        cleared = bool(_DATASET_CACHE)
        _DATASET_CACHE.clear()
    return cleared


CACHE_CLEARERS.append(_clear_cache)


def free_memory():
    """Empty the caches; whether there was anything to free."""
    cleared = [clear() for clear in CACHE_CLEARERS]
    gc.collect()
    return any(cleared)


def with_free_memory(load):
    """``load()``, again after emptying the caches if there wasn't enough memory for it."""
    try:
        return load()
    except MemoryError:
        if not free_memory():
            raise
        print("WARNING: not enough memory, emptied the caches")
        return load()


class Dataset:
    """The experiments of one database (optionally filtered by the query), as a DataFrame.

    Responses are factorized once so that recolor regexes only run on the
    unique responses; the resulting masks are cached per regex.
    """

    def __init__(self, key, df, last_rowid=0):
        # (file_key, query): changes with every new version of the file
        self.key = key
        self.df = df
        # the rowid of the last experiment loaded, to load only newer ones later
        self.last_rowid = last_rowid
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
        """The parts of ``config`` that decide the colors: (regexes, fixgreen)."""
        regexes = tuple(config.colors.get(color, [None, None])[0] for color in COLORS)
        return (regexes, bool(config.fixgreen))

    def colors_for(self, config):
        """Colors as shown by the graph (and the data tab) for ``config``: fixgreen and the recolor fields."""
        regexes, fixgreen = self.color_setting(config)
        return self.recolor(regexes, COLOR_CODES, fixgreen, RECOLOR_FLAGS)


def get_dataset(config):
    """Cached, loaded database for the given database/query.

    When the file only got new experiments since it was loaded (e.g. a running
    glitch script), only those are loaded and added.
    """
    path = database_path(config.directory, config.database)
    if path is None:
        raise PreventUpdate

    query = config.query or ""
    key = file_key(path) + (query,)
    cache_key = (path, query)

    with _CACHE_LOCK:
        dataset = _DATASET_CACHE.get(cache_key)
        if dataset is not None and dataset.key == key:
            _DATASET_CACHE.move_to_end(cache_key)
            return dataset
        load_lock = _LOAD_LOCKS.setdefault(cache_key, threading.Lock())

    # one load per database/query at a time: e.g. the graph and the data tab
    # ask for the same database at the same time, the second uses the first's
    with load_lock:
        # (the file may have changed while waiting)
        key = file_key(path) + (query,)
        with _CACHE_LOCK:
            dataset = _DATASET_CACHE.get(cache_key)
            if dataset is not None and dataset.key == key:
                return dataset
        dataset = load_dataset(config, path, query, key, dataset)
        with _CACHE_LOCK:
            _DATASET_CACHE[cache_key] = dataset
            _DATASET_CACHE.move_to_end(cache_key)
            while len(_DATASET_CACHE) > _DATASET_CACHE_SIZE:
                _DATASET_CACHE.popitem(last=False)
    return dataset


def load_dataset(config, path, query, key, dataset):
    """Load ``key`` (a version of the file and query), from ``dataset`` (an earlier version) if possible."""
    try:
        if dataset is not None and appended_to(dataset.key[:-1], key[:-1]) and max_rowid(path) >= dataset.last_rowid:
            return with_free_memory(lambda: extended(dataset, key, path, query))
        # another file, or changed otherwise: the old version is of no use (free it first)
        with _CACHE_LOCK:
            _DATASET_CACHE.pop((path, query), None)
        dataset = None
        df, last_rowid = with_free_memory(lambda: load_experiments(path, query))
    except Exception as e:
        message = f"{config.database}: {query_error(e)}"
        print("ERROR (get_dataset): %s" % message)
        raise DatasetError(message)

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        message = f"{config.database} has no {', '.join(missing)} column"
        print("ERROR (get_dataset): %s" % message)
        raise DatasetError(message)
    return Dataset(key, df, last_rowid)


def extended(dataset, key, path, query):
    """``dataset`` with the experiments added to the database since it was loaded."""
    # new experiments share the responses already loaded
    responses = {r: r for r in dataset.resp_uniques}
    df, last_rowid = load_experiments(path, query, dataset.last_rowid, responses)
    if df.empty:
        return Dataset(key, dataset.df, dataset.last_rowid)
    return Dataset(key, pd.concat([dataset.df, df], ignore_index=True), last_rowid)
