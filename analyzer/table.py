"""The rows of the data tab: squeezing, filtering, sorting and paging."""

import threading
from collections import OrderedDict

import numpy as np
import pandas as pd

from analyzer.dataset import CACHE_CLEARERS, Dataset
from analyzer.selection import applies, selected_rows, selection_key

# Tables are cached per worker process.
_TABLE_CACHE = OrderedDict()
_TABLE_CACHE_SIZE = 3
_CACHE_LOCK = threading.Lock()
_FILTER_CHUNK = 1000  # distinct responses decoded at a time when filtering on them


def _clear_cache():
    with _CACHE_LOCK:
        cleared = bool(_TABLE_CACHE)
        _TABLE_CACHE.clear()
    return cleared


CACHE_CLEARERS.append(_clear_cache)


class Table:
    """Rows for the data tab (squeezed or not), served page by page to the grid.

    ``df`` holds the plain columns, ``responses`` the (sliced) raw responses;
    the decoded/hex representation is only produced for the rows being shown,
    or for all rows when the grid sorts/filters on them.
    """

    def __init__(self, df, responses, squeezed, numeric=()):
        self.df = df
        self.responses = responses
        self.squeezed = squeezed
        # numeric columns (squeezed: the min/max columns and the amount)
        self.numeric = set(numeric)
        self._text = None
        self._hex = None
        self._distinct = None  # (codes, distinct responses)
        # the number of experiments selected in the graph (None: all experiments)
        self.selected = None
        self._selection = None  # (sort_model, filter_model) -> row indices
        self._lock = threading.Lock()

    @property
    def columns(self):
        plain = list(self.df.columns)
        if self.squeezed:
            return plain + ["response", "rlen", "hex(response)"]
        return plain + ["rlen", "response", "hex(response)"]

    @staticmethod
    def decode(response):
        return response.decode("utf-8", errors="replace")

    @staticmethod
    def hexify(response):
        return response.hex(" ")

    def text_column(self):
        if self._text is None:
            self._text = per_response(self.responses, self.decode)
        return self._text

    def hex_column(self):
        if self._hex is None:
            self._hex = per_response(self.responses, self.hexify)
        return self._hex

    def distinct_responses(self):
        """(code per row, distinct responses)."""
        if self._distinct is None:
            codes, uniques = pd.factorize(self.responses, use_na_sentinel=False)
            self._distinct = (codes, np.asarray(uniques, dtype=object))
        return self._distinct

    def response_mask(self, name, model):
        """Filter mask for the response or hex column: per distinct response, a chunk at a time
        (decoding all of them at once can take gigabytes)."""
        convert = self.decode if name == "response" else self.hexify
        codes, uniques = self.distinct_responses()
        mask = np.empty(len(uniques), dtype=bool)
        for start in range(0, len(uniques), _FILTER_CHUNK):
            chunk = pd.Series([convert(r) for r in uniques[start : start + _FILTER_CHUNK]], dtype=object)
            mask[start : start + len(chunk)] = grid_filter_mask(chunk, model)
        return mask[codes]

    def column(self, name):
        if name == "response":
            return pd.Series(self.text_column())
        if name == "hex(response)":
            return pd.Series(self.hex_column())
        if name == "rlen":
            return pd.Series(per_response(self.responses, len).astype(int))
        return self.df[name].reset_index(drop=True)

    def select(self, sort_model, filter_model):
        """Row indices after applying the grid's filter and sort model (cached)."""
        cache_key = (repr(sort_model), repr(filter_model))
        with self._lock:
            if self._selection is not None and self._selection[0] == cache_key:
                return self._selection[1]

        mask = np.ones(len(self.responses), dtype=bool)
        for name, model in (filter_model or {}).items():
            if name not in self.columns:
                continue
            if name in ("response", "hex(response)"):
                mask &= self.response_mask(name, model)
                continue
            values = self.column(name)
            if values.dtype == object:
                # text: once per distinct value (responses repeat a lot)
                codes, uniques = pd.factorize(values, use_na_sentinel=False)
                mask &= grid_filter_mask(pd.Series(uniques, dtype=object), model)[codes]
            else:
                mask &= grid_filter_mask(values, model)
        indices = np.flatnonzero(mask)

        if sort_model:
            frame = pd.DataFrame({s["colId"]: self.column(s["colId"]).to_numpy()[indices] for s in sort_model})
            order = frame.sort_values(
                by=[s["colId"] for s in sort_model],
                ascending=[s["sort"] == "asc" for s in sort_model],
                kind="stable",
            ).index.to_numpy()
            indices = indices[order]

        with self._lock:
            self._selection = (cache_key, indices)
        return indices

    def page(self, start, end, sort_model, filter_model):
        indices = self.select(sort_model, filter_model)
        page_indices = indices[start:end]
        page = self.df.iloc[page_indices]
        # NULL values as None (null in JSON), not NaN
        rows = page.astype(object).where(page.notna(), None).to_dict("records")
        for row, index in zip(rows, page_indices, strict=True):
            response = self.responses[index]
            row["rlen"] = len(response)
            row["response"] = self.decode(response)
            row["hex(response)"] = self.hexify(response)
        return rows, len(indices)


def per_response(responses, func):
    """``func`` of every response, computed once per distinct response (the results are shared)."""
    codes, uniques = pd.factorize(responses, use_na_sentinel=False)
    results = np.empty(len(uniques), dtype=object)
    results[:] = [func(r) for r in uniques]
    return results[codes]


def grid_filter_mask(values, model):
    """Evaluate an AG Grid text/number filter model against a column."""
    if "conditions" in model or "condition1" in model:
        conditions = model.get("conditions") or [model["condition1"], model["condition2"]]
        masks = [grid_filter_mask(values, c) for c in conditions]
        if model.get("operator", "AND") == "OR":
            return np.logical_or.reduce(masks)
        return np.logical_and.reduce(masks)

    kind = model.get("type", "contains")
    if kind == "blank":
        return values.isna().to_numpy() | (values.astype(str) == "").to_numpy()
    if kind == "notBlank":
        return ~(values.isna().to_numpy() | (values.astype(str) == "").to_numpy())

    if model.get("filterType") == "number":
        a = pd.to_numeric(values, errors="coerce")
        f = model.get("filter")
        t = model.get("filterTo")
        ops = {
            "equals": lambda: a == f,
            "notEqual": lambda: a != f,
            "lessThan": lambda: a < f,
            "lessThanOrEqual": lambda: a <= f,
            "greaterThan": lambda: a > f,
            "greaterThanOrEqual": lambda: a >= f,
            "inRange": lambda: (a >= f) & (a <= t),
        }
    else:
        # plain Python strings: pandas' .str keeps big columns alive in a reference cycle
        s = [str(v).lower() for v in values]
        f = str(model.get("filter", "")).lower()
        ops = {
            "contains": lambda: [f in v for v in s],
            "notContains": lambda: [f not in v for v in s],
            "equals": lambda: [v == f for v in s],
            "notEqual": lambda: [v != f for v in s],
            "startsWith": lambda: [v.startswith(f) for v in s],
            "endsWith": lambda: [v.endswith(f) for v in s],
        }
    if kind not in ops:
        return np.ones(len(values), dtype=bool)
    result = ops[kind]()
    if isinstance(result, pd.Series):
        result = result.fillna(False)
    return np.asarray(result, dtype=bool)


def build_table(dataset, colors, squeeze_records, response_s, response_e, rows=None):
    """Data tab table with the columns of the database (in database order).

    Squeezed, numeric (int/float) columns become a "min(<column>)" and
    "max(<column>)" pair; other columns show the value of the first experiment.
    ``rows``: only these experiments (e.g. those selected in the graph).
    """
    df = dataset.df
    if rows is not None:
        df = df.iloc[rows].reset_index(drop=True)
        colors = colors[rows]

    def shown(column):
        if column in ("response", "amount", "rlen", "hex(response)"):
            return False
        # skip columns without any value (unless there are no rows at all)
        return df.empty or bool(df[column].notna().any())

    def values(column):
        if column == "color":
            # as colored in the graph
            return colors
        values = df[column].to_numpy()
        if values.dtype == object:
            # other blobs can't be sent to the grid as is
            values = np.array([r.hex(" ") if isinstance(r, bytes) else r for r in values], dtype=object)
        return values

    def numeric(column):
        return column != "color" and df[column].dtype.kind in "iuf"

    columns = [column for column in df.columns if shown(column)]

    # slice response
    responses = df["response"].to_numpy(dtype=object)
    if response_s is not None or response_e is not None:
        responses = per_response(responses, lambda r: r[response_s:response_e])

    if not squeeze_records:
        out = pd.DataFrame({column: values(column) for column in columns})
        return Table(out, responses, squeezed=False, numeric={c for c in columns if numeric(c)})

    grouped = pd.DataFrame({column: values(column) for column in columns})
    grouped["response"] = responses
    aggregations = {"amount": ("response", "size")}
    ranges = set()
    for column in columns:
        if numeric(column):
            for function in ("min", "max"):
                aggregations[f"{function}({column})"] = (column, function)
                ranges.add(f"{function}({column})")
        else:
            aggregations[column] = (column, "first")

    squeezed = grouped.groupby("response", sort=False).agg(**aggregations)
    squeezed = squeezed.sort_values("amount", ascending=False, kind="stable")
    responses = squeezed.index.to_numpy(dtype=object)
    return Table(squeezed.reset_index(drop=True), responses, squeezed=True, numeric=ranges | {"amount"})


def get_table(dataset, config, squeeze_records, response_s, response_e, selection=None):
    """Cached table for the data tab, colored as the graph for ``config``; only the
    experiments of ``selection`` (see selection.py) if it applies."""
    key = (
        dataset.key,
        Dataset.color_setting(config),
        bool(squeeze_records),
        response_s,
        response_e,
        selection_key(selection) if applies(selection, config) else None,
    )
    with _CACHE_LOCK:
        table = _TABLE_CACHE.get(key)
        if table is not None:
            _TABLE_CACHE.move_to_end(key)
            return table
    rows = selected_rows(dataset, config, selection)
    table = build_table(dataset, dataset.colors_for(config), squeeze_records, response_s, response_e, rows)
    table.selected = None if rows is None else len(rows)
    with _CACHE_LOCK:
        _TABLE_CACHE[key] = table
        while len(_TABLE_CACHE) > _TABLE_CACHE_SIZE:
            _TABLE_CACHE.popitem(last=False)
    return table
