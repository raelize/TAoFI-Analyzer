# Guide

---

This is the manual the Raelize TAoFI-Analyzer.

## Graph

---

The graph tab is created from the data read from the database with FI experiments.

Select a database from the directory shown at the top. When the analyzer runs on your own machine you can switch to another directory: type it (press enter) or use `Browse…`. Files that are not analyzer databases are listed, but can't be selected.

With `Auto refresh` the graph is updated when the database changes (e.g., while experiments are still running). The zoom and the colors hidden via the legend are kept. The data tab is refreshed as well (keeping its page, sorting and filters).

You can filter the database before it's fed into the graph engine by extending the query at the top.

The query, jitter and recolor fields are applied with `Update` or by pressing enter in one of them. The browser remembers them as applied (e.g. after a reload of the page).

Some examples include:

- `color = 'R'`
- `match_string(response, 'ets')`
- `match_hex(response, '661b')`

The input fields are mandatory (i.e., otherwise nothing is shown).

The graph types:

- `scatter`: the experiments with the x and y parameters against each other, colored by their classification
  With a column named `x` on the x-axis or `y` on the y-axis (e.g. a scan over the positions of a chip), the graph is shown like an image: the x-axis at the top, the y-axis going down, in a fixed size of 1000 by 1000 pixels.
- `scatter2`: two scatter plots next to each other, each with its own x and y parameter (the right one: `x-axis (right)`, `y-axis (right)`); hiding a color in the legend hides it in both
- `3d`: the x, y and z parameters against each other (the z axis is only shown for this graph); drag to rotate, scroll to zoom
- `mega`: every parameter (number column, except `id`) against every other one, one plot per pair; the axes aren't used

A browser takes long to draw many points: with more than 200000 experiments (100000 for `3d`) a sample is drawn, shown in the legend title (e.g. `Classification ( 1,000,000; 200,000 shown )`). Only the large classes are thinned out: every experiment of a small class (e.g. the few successful glitches) is drawn. The counts in the legend are of all experiments, and a selection in the graph includes the experiments that aren't drawn.

Click a point in the graph to see its experiment below the graph: all its columns, its response (as text and hex) and how many experiments have the same response. This needs `Show hover info on the graph` to be on.

To see the experiments in a region of the graph, select it with `Box Select` or `Lasso Select` (in the toolbar of the graph; `scatter` and `scatter2`) and open the data tab: it shows only those experiments, until you click `Show all` there or select another region. Double click the graph to clear the selection.

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

You can squeeze the data (i.e., combine experiment with the same responses) or not. Squeezed, every number column is shown as a pair: `Min(<column>)` (the smallest value) and `Max(<column>)` (the largest value); other columns show the value of the first experiment.

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

The database tab can be used to execute raw queries on the selected database: it shows the number of rows and the first 100 of them (blobs, like the response, as text; use e.g. `hex(response)` for hex). Queries that take longer than 60 seconds are stopped.