# Manual

This is the manual the Raelize TAoFI-Analyzer.

## Graph

The graph is created from the data read from the database with FI experiments.

You can filter the database before it's fed into the graph engine by extending the query at the top.

Some examples include:

- `color = 'R'`
- `match_string(response, 'ets')`
- `match_hex(response, '661b')`

The input fields are mandatory (i.e., otherwise nothing is shown).

At the bottom of the graph you can recolor the experiments fed into the graph engine.

The first field is the qualifier (i.e., `re.search(<qualifier>, response))`) and the second field is the label.

## Data

The data is synced with the graph configuration.

You can squeeze the data (i.e., combine experiment with the same responses) or not.

For each row of data, you can:

- show a hex representation of the response or not
- wrap the response in the cell or not
- slice the response or not

...

## Information

...

## Database

...