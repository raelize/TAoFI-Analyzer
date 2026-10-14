# Guide

---

This is the manual the Raelize TAoFI-Analyzer.

## Graph

---

The graph tab is created from the data read from the database with FI experiments.

Select a database from the directory shown at the top. When the analyzer runs on your own machine you can switch to another directory: type it (press enter) or use `Browse…`. Files that are not analyzer databases are listed, but can't be selected.

With `Auto refresh` the graph is updated when the database changes (e.g., while experiments are still running). The zoom and the colors hidden via the legend are kept.

You can filter the database before it's fed into the graph engine by extending the query at the top.

The query, jitter and recolor fields are applied with `Update` or by pressing enter in one of them.

Some examples include:

- `color = 'R'`
- `match_string(response, 'ets')`
- `match_hex(response, '661b')`

The input fields are mandatory (i.e., otherwise nothing is shown).

The graph types:

- `scatter`: the experiments with the x and y parameters against each other, colored by their classification
- `scatter2`: the same, in a fixed size (the recolor qualifiers don't match newlines with `.`)
- `simple`: the experiments as classified by the glitch script (no recoloring)
- `3d`: the x, y and z parameters against each other; drag to rotate, scroll to zoom
- `mega`: every parameter (number column, except `id`) against every other one, one plot per pair; the axes aren't used

Click a point in the graph to see its experiment below the graph: all its columns, its response (as text and hex) and how many experiments have the same response. This needs `Show hover info on the graph` to be on.

At the bottom of the graph you can recolor the experiments fed into the graph engine.

The first field is the qualifier (i.e., `re.search(<qualifier>, response))`) and the second field is the label.

- `bla`: response contains `bla`
- `\x41\x41`: response contains `AA`
- `^.{0,1000}$`: response length between 0 an 1000
- `^.{1000,}$`: response length larger than 1000
- `.*`: catch all (useful for recoloring everything to yellow before applying other colors)

## Data

---

The data tab is synced with the graph configuration.

The columns are the columns of the `experiments` table of the database.

You can squeeze the data (i.e., combine experiment with the same responses) or not. Squeezed, every number column is shown as a pair: `<column> >` (the smallest value) and `<column> <` (the largest value); other columns show the value of the first experiment.

Every column can be sorted and filtered; number columns have number filters (e.g., greater than, in range).

For each row of data, you can:

- wrap the response in the cell or not
- slice the response or not

Use the column selector below the switches to choose which columns are shown (e.g., add `hex(response)` for a hex representation of the response). The selection is remembered by the browser.

...

## Information

---

The information tab lists information related to the selected database.

- arguments if available (i.e., in the `metadata` table of the database)
- the zoomed area of the graph (double click the graph to reset it)
- contents of Dash store used by the analyzer

## Database

---

The database tab can be used to execute raw queries on the selected database (the first 100 rows are shown). Queries that take longer than 60 seconds are stopped.