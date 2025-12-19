# Guide

---

This is the manual the Raelize TAoFI-Analyzer.

## Graph

---

The graph tab is created from the data read from the database with FI experiments.

You can filter the database before it's fed into the graph engine by extending the query at the top.

Some examples include:

- `color = 'R'`
- `match_string(response, 'ets')`
- `match_hex(response, '661b')`

The input fields are mandatory (i.e., otherwise nothing is shown).

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

You can squeeze the data (i.e., combine experiment with the same responses) or not.

For each row of data, you can:

- show a hex representation of the response or not
- wrap the response in the cell or not
- slice the response or not

...

## Information

---

The information tab lists information related to the selected database.

- arguments if available (i.e., in the `metadata` table of the database)
- points if selected in the graph (i.e., on zoom or selection)
- contents of Dash store used by the analyzer

## Guide

---

The guide tab can be used to execute raw queries on the selected database.