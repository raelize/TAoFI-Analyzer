"""The rows of the data tab: squeezing, filtering, sorting and paging."""
import threading
from collections import OrderedDict

import numpy as np
import pandas as pd

from analyzer.dataset import Dataset

# Tables are cached per worker process.
_TABLE_CACHE = OrderedDict()
_TABLE_CACHE_SIZE = 3
_CACHE_LOCK = threading.Lock()


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
            self._text = np.array([self.decode(r) for r in self.responses], dtype=object)
        return self._text

    def hex_column(self):
        if self._hex is None:
            self._hex = np.array([self.hexify(r) for r in self.responses], dtype=object)
        return self._hex

    def column(self, name):
        if name == "response":
            return pd.Series(self.text_column())
        if name == "hex(response)":
            return pd.Series(self.hex_column())
        if name == "rlen":
            return pd.Series([len(r) for r in self.responses])
        return self.df[name].reset_index(drop=True)

    def select(self, sort_model, filter_model):
        """Row indices after applying the grid's filter and sort model (cached)."""
        cache_key = (repr(sort_model), repr(filter_model))
        with self._lock:
            if self._selection is not None and self._selection[0] == cache_key:
                return self._selection[1]

        mask = np.ones(len(self.responses), dtype=bool)
        for name, model in (filter_model or {}).items():
            if name in self.columns:
                mask &= grid_filter_mask(self.column(name), model)
        indices = np.flatnonzero(mask)

        if sort_model:
            frame = pd.DataFrame(
                {s["colId"]: self.column(s["colId"]).to_numpy()[indices] for s in sort_model}
            )
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
        rows = self.df.iloc[page_indices].to_dict("records")
        for row, index in zip(rows, page_indices):
            response = self.responses[index]
            row["rlen"] = len(response)
            row["response"] = self.decode(response)
            row["hex(response)"] = self.hexify(response)
        return rows, int(len(indices))


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
        s = values.astype(str).str.lower()
        f = str(model.get("filter", "")).lower()
        ops = {
            "contains": lambda: s.str.contains(f, regex=False),
            "notContains": lambda: ~s.str.contains(f, regex=False),
            "equals": lambda: s == f,
            "notEqual": lambda: s != f,
            "startsWith": lambda: s.str.startswith(f),
            "endsWith": lambda: s.str.endswith(f),
        }
    if kind not in ops:
        return np.ones(len(values), dtype=bool)
    return ops[kind]().fillna(False).to_numpy(dtype=bool)


def build_table(dataset, colors, squeeze_records, response_s, response_e):
    """Data tab table with the columns of the database (in database order).

    Squeezed, numeric (int/float) columns become a "<column> >" (min) and
    "<column> <" (max) pair; other columns show the value of the first experiment.
    """
    df = dataset.df

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
        responses = np.array([r[response_s:response_e] for r in responses], dtype=object)

    if not squeeze_records:
        out = pd.DataFrame({column: values(column) for column in columns})
        return Table(out, responses, squeezed=False, numeric={c for c in columns if numeric(c)})

    grouped = pd.DataFrame({column: values(column) for column in columns})
    grouped["response"] = responses
    aggregations = {"amount": ("response", "size")}
    ranges = set()
    for column in columns:
        if numeric(column):
            aggregations[f"{column} >"] = (column, "min")
            aggregations[f"{column} <"] = (column, "max")
            ranges |= {f"{column} >", f"{column} <"}
        else:
            aggregations[column] = (column, "first")

    squeezed = grouped.groupby("response", sort=False).agg(**aggregations)
    squeezed = squeezed.sort_values("amount", ascending=False, kind="stable")
    responses = squeezed.index.to_numpy(dtype=object)
    return Table(squeezed.reset_index(drop=True), responses, squeezed=True, numeric=ranges | {"amount"})


def get_table(dataset, config, squeeze_records, response_s, response_e):
    """Cached table for the data tab, colored as the graph for ``config``."""
    key = (dataset.key, Dataset.color_setting(config), bool(squeeze_records), response_s, response_e)
    with _CACHE_LOCK:
        table = _TABLE_CACHE.get(key)
        if table is not None:
            _TABLE_CACHE.move_to_end(key)
            return table
    table = build_table(dataset, dataset.colors_for(config), squeeze_records, response_s, response_e)
    with _CACHE_LOCK:
        _TABLE_CACHE[key] = table
        while len(_TABLE_CACHE) > _TABLE_CACHE_SIZE:
            _TABLE_CACHE.popitem(last=False)
    return table
