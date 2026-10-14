import os
import sqlite3
import threading
import time

import pytest

from analyzer import database
from analyzer.config import server_config
from analyzer.database import (
    appended_to,
    available_memory,
    being_written,
    connect,
    data_directory,
    database_path,
    file_key,
    get_argv,
    get_databases,
    get_number_of_experiments,
    get_parameters,
    list_directory,
    load_dataframe,
    load_experiments,
    match_hex,
    match_string,
    query_connection,
    query_error,
    run_query,
)
from tests.conftest import (
    EXAMPLE_ARGV,
    EXAMPLE_DATABASE,
    EXAMPLE_DIRECTORY,
    EXAMPLE_EXPERIMENTS,
    SLOW_CONDITION,
    create_database,
)

COLUMNS = "id integer, delay integer, length integer, color text, response blob"


#
# which files may be read
#


@pytest.mark.parametrize(
    "name",
    [
        "../databases/" + EXAMPLE_DATABASE,
        "/etc/passwd",
        "..",
        "missing.sqlite",
        "not-a-database.txt",
        "",
        None,
    ],
)
def test_database_path_rejects(request_from, name):
    with request_from("127.0.0.1"):
        assert database_path(EXAMPLE_DIRECTORY, name) is None


def test_database_path_accepts_a_database_in_the_directory(request_from):
    with request_from("127.0.0.1"):
        assert database_path(EXAMPLE_DIRECTORY, EXAMPLE_DATABASE) == os.path.join(EXAMPLE_DIRECTORY, EXAMPLE_DATABASE)


def test_local_users_can_pick_another_directory(request_from, tmp_path):
    with request_from("127.0.0.1"):
        assert data_directory(str(tmp_path)) == str(tmp_path)
        assert data_directory(str(tmp_path / "missing")) is None
    with request_from("::1"):
        assert data_directory(str(tmp_path)) == str(tmp_path)


def test_remote_users_always_get_the_server_directory(request_from, tmp_path, make_database):
    make_database("other.sqlite")
    with request_from("10.1.2.3"):
        assert data_directory(str(tmp_path)) == server_config.directory
        assert data_directory("/nonexistent") == server_config.directory
        # a database in another directory is looked up in the server directory instead
        assert database_path(str(tmp_path), "other.sqlite") is None


def test_outside_a_request_only_the_server_directory(tmp_path):
    assert data_directory(str(tmp_path)) == server_config.directory


def test_list_directory(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.sqlite").touch()
    (tmp_path / "a" / "y.sqlite").touch()
    (tmp_path / "B").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "here.sqlite").touch()
    (tmp_path / "notes.txt").touch()
    assert list_directory(str(tmp_path)) == ([("a", 2), ("B", 0)], 1)
    assert list_directory(str(tmp_path / "missing")) == ([], 0)


#
# connections
#


def test_connections_are_read_only(tmp_path, make_database):
    make_database()
    con = connect(str(tmp_path / "test.sqlite"))
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("CREATE TABLE t(x)")
    con.close()


def test_connecting_to_a_missing_file_does_not_create_it(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        connect(str(tmp_path / "missing.sqlite"))
    assert not (tmp_path / "missing.sqlite").exists()


# the slow queries take several seconds without the time limit (so the test fails, not hangs)
def test_slow_queries_are_interrupted(tmp_path, make_database, monkeypatch):
    make_database()
    monkeypatch.setattr(database, "QUERY_TIMEOUT", 0.5)
    con = query_connection(str(tmp_path / "test.sqlite"))
    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError) as error:
        con.execute(f"SELECT {SLOW_CONDITION}").fetchall()
    assert time.monotonic() - started < 5
    assert query_error(error.value) == "the query took longer than 0.5 s"


def test_query_error_unwraps_pandas_errors():
    # as pandas raises it
    error = RuntimeError("Execution failed on sql ...")
    error.__cause__ = sqlite3.OperationalError("interrupted")
    assert query_error(error) == f"the query took longer than {database.QUERY_TIMEOUT} s"
    assert query_error(ValueError("no such column: x")) == "no such column: x"


#
# file versions
#


def test_file_key_changes_with_wal_writes(tmp_path):
    path = str(tmp_path / "wal.sqlite")
    writer = create_database(path, COLUMNS, wal=True)
    before = file_key(path)
    writer.execute("INSERT INTO experiments VALUES (1, 2, 3, 'G', x'00')")
    writer.commit()
    # still in the -wal file: the database file itself is unchanged
    assert os.path.exists(path + "-wal")
    assert file_key(path) != before
    writer.close()


#
# databases in a directory
#


def test_get_databases(tmp_path, make_database):
    make_database("b.sqlite", rows=[(1, 2, 3, "G", b"x"), (2, 2, 3, "R", b"y")])
    make_database("a.sqlite")
    make_database("no-response.sqlite", columns="id integer, color text")
    con = sqlite3.connect(tmp_path / "other-app.sqlite")
    con.execute("CREATE TABLE t(x)")
    con.close()
    (tmp_path / "broken.sqlite").write_bytes(b"not a database")
    (tmp_path / "notes.txt").touch()

    options = {o["value"]: o for o in get_databases(str(tmp_path))}
    assert options["b.sqlite"] == {"label": "b.sqlite (2)", "value": "b.sqlite"}
    assert options["a.sqlite"] == {"label": "a.sqlite (0)", "value": "a.sqlite"}
    for name in ("no-response.sqlite", "other-app.sqlite", "broken.sqlite"):
        assert options[name]["disabled"] is True
        assert "not an analyzer database" in options[name]["label"]
    assert "notes.txt" not in options
    # newest (highest) name first
    assert [o["value"] for o in get_databases(str(tmp_path))][:2] == ["other-app.sqlite", "no-response.sqlite"]


def test_count_cache_keeps_one_entry_per_file(tmp_path):
    path = str(tmp_path / "growing.sqlite")
    writer = create_database(path, COLUMNS)
    for i in range(5):
        writer.execute("INSERT INTO experiments VALUES (?, 2, 3, 'G', x'00')", (i,))
        writer.commit()
        assert get_number_of_experiments(str(tmp_path), "growing.sqlite") == i + 1
    assert len(database._COUNT_CACHE) == 1
    writer.close()


def test_counting_in_chunks(tmp_path, make_database, monkeypatch):
    monkeypatch.setattr(database, "COUNT_CHUNK", 3)
    make_database(rows=[(i, 2, 3, "G", b"x") for i in range(10)])
    assert get_number_of_experiments(str(tmp_path), "test.sqlite") == 10
    # removed experiments (not only new ones): counted again
    con = sqlite3.connect(tmp_path / "test.sqlite")
    con.execute("DELETE FROM experiments WHERE rowid > 4")
    con.commit()
    con.close()
    assert get_number_of_experiments(str(tmp_path), "test.sqlite") == 4


def test_large_databases_are_counted_in_the_background(tmp_path, make_database, monkeypatch):
    monkeypatch.setattr(database, "COUNT_IN_BACKGROUND", 0)
    make_database("big.sqlite", rows=[(i, 2, 3, "G", b"x") for i in range(5)])
    make_database("other.sqlite", columns="id integer")
    started = threading.Event()
    count_experiments = database.count_experiments

    def slow(*args):
        started.wait(5)
        return count_experiments(*args)

    monkeypatch.setattr(database, "count_experiments", slow)
    options = {o["value"]: o["label"] for o in get_databases(str(tmp_path))}
    assert options == {"big.sqlite": "big.sqlite (counting…)", "other.sqlite": "other.sqlite (counting…)"}
    assert database.counting()
    started.set()
    for _ in range(100):
        if not database.counting():
            break
        time.sleep(0.05)
    options = {o["value"]: o["label"] for o in get_databases(str(tmp_path))}
    assert options == {"big.sqlite": "big.sqlite (5)", "other.sqlite": "other.sqlite (not an analyzer database)"}


def test_count_after_the_file_is_replaced(tmp_path):
    path = str(tmp_path / "test.sqlite")
    create_database(path, COLUMNS, [(i, 2, 3, "G", b"x") for i in range(5)]).close()
    assert get_number_of_experiments(str(tmp_path), "test.sqlite") == 5
    # a new file with the same name (and more experiments than the first had)
    create_database(path + ".new", COLUMNS, [(i, 2, 3, "G", b"x") for i in range(7)]).close()
    os.replace(path + ".new", path)
    assert get_number_of_experiments(str(tmp_path), "test.sqlite") == 7


def test_appended_to():
    key = ("/db", 12, 1000, 4096)
    assert appended_to(key, ("/db", 12, 2000, 8192))
    assert appended_to(key, ("/db", 12, 2000, 4096, 3000, 512))  # WAL
    assert not appended_to(key, ("/db", 13, 2000, 8192))  # another file
    assert not appended_to(key, ("/db", 12, 2000, 2048))  # shrunk


def test_example_database():
    path = os.path.join(EXAMPLE_DIRECTORY, EXAMPLE_DATABASE)
    assert get_number_of_experiments(EXAMPLE_DIRECTORY, EXAMPLE_DATABASE) == EXAMPLE_EXPERIMENTS
    assert get_argv(path) == EXAMPLE_ARGV
    assert get_parameters(path) == ["id", "delay", "length", "color"]


#
# queries
#


def test_load_dataframe(tmp_path, make_database):
    make_database(rows=[(1, 10, 3, "G", b"ok"), (2, 20, 3, "R", None), (3, 30, 3, "R", b"\x66\x1b")])
    path = str(tmp_path / "test.sqlite")
    assert len(load_dataframe(path, "")) == 3
    df = load_dataframe(path, "delay > 15")
    assert list(df["id"]) == [2, 3]
    # NULL responses become empty
    assert df["response"].iloc[0] == b""
    assert list(load_dataframe(path, "match_string(response, 'ok')")["id"]) == [1]
    assert list(load_dataframe(path, "match_hex(response, '661b')")["id"]) == [3]


def test_load_dataframe_keeps_one_copy_per_response(tmp_path, make_database, monkeypatch):
    monkeypatch.setattr(database, "LOAD_CHUNK", 2)  # the same response in different chunks
    make_database(rows=[(i, i, 3, "G", b"same" if i % 2 else b"other") for i in range(7)])
    df = load_dataframe(str(tmp_path / "test.sqlite"), "")
    assert list(df["id"]) == list(range(7))
    assert len({id(r) for r in df["response"]}) == 2
    assert list(df["response"][:2]) == [b"other", b"same"]
    assert len(load_dataframe(str(tmp_path / "test.sqlite"), "id > 100")) == 0


def test_load_dataframe_stops_before_running_out_of_memory(tmp_path, make_database, monkeypatch):
    monkeypatch.setattr(database, "LOAD_CHUNK", 2)
    make_database(rows=[(i, i, 3, "G", b"x") for i in range(7)])
    path = str(tmp_path / "test.sqlite")
    monkeypatch.setattr(database, "available_memory", lambda: None)  # unknown: no check
    assert len(load_dataframe(path, "")) == 7
    monkeypatch.setattr(database, "available_memory", lambda: database.MEMORY_RESERVE - 1)
    with pytest.raises(MemoryError, match="stopped after 2 experiments"):
        load_dataframe(path, "")


def test_load_experiments_in_chunks(tmp_path, make_database, monkeypatch):
    monkeypatch.setattr(database, "LOAD_CHUNK", 2)
    make_database(rows=[(i, i, 3, "RGY"[i % 3], b"r%d" % (i % 2)) for i in range(1, 8)])
    path = str(tmp_path / "test.sqlite")
    df, last = load_experiments(path, "")
    assert list(df.columns) == ["id", "delay", "length", "color", "response"]
    assert list(df["id"]) == list(range(1, 8)) and last == 7
    # only the experiments after a rowid; responses shared with the earlier ones
    responses = {}
    first, last = load_experiments(path, "", 0, responses)
    more, _ = load_experiments(path, "", 5, responses)
    assert list(more["id"]) == [6, 7]
    assert more["response"][0] is first["response"][5]
    # the query is one condition (OR doesn't escape the rowid range), comments are fine
    df, last = load_experiments(path, "color = 'R' OR color = 'G' -- comment", 4)
    assert list(df["id"]) == [6, 7] and last == 7
    # the last rowid read (not the last one matching): the next load starts after it
    df, last = load_experiments(path, "id > 100", 0)
    assert len(df) == 0 and next(iter(df.columns)) == "id" and last == 7


@pytest.mark.parametrize("query", ["1) OR (1", "color = 'R') OR (color = 'G'"])
def test_a_query_cannot_escape_the_chunks(tmp_path, make_database, monkeypatch, query):
    monkeypatch.setattr(database, "LOAD_CHUNK", 2)
    make_database(rows=[(i, i, 3, "RG"[i % 2], b"x") for i in range(1, 8)])
    with pytest.raises(ValueError, match="the query must be a condition"):
        load_experiments(str(tmp_path / "test.sqlite"), query)
    # loading only the new experiments
    monkeypatch.setattr(database, "LOAD_CHUNK", 10)
    with pytest.raises(ValueError, match="the query must be a condition"):
        load_experiments(str(tmp_path / "test.sqlite"), query, 5)


def test_a_chunk_reads_a_range_of_rowids(tmp_path, make_database, monkeypatch):
    """Also with a query that matches few experiments: a statement never reads more than LOAD_CHUNK of them."""
    monkeypatch.setattr(database, "LOAD_CHUNK", 3)
    make_database(rows=[(i, i, 3, "R" if i == 9 else "G", b"x") for i in range(1, 11)])
    statements = []
    connection = database.query_connection

    def spy(path):
        con = connection(path)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(database, "query_connection", spy)
    df, last = load_experiments(str(tmp_path / "test.sqlite"), "color = 'R'")
    assert list(df["id"]) == [9] and last == 10
    ranges = [s.split("rowid > ")[1].split(" AND (")[0] for s in statements if "rowid > " in s]
    assert ranges == ["0 AND rowid <= 3", "3 AND rowid <= 6", "6 AND rowid <= 9", "9 AND rowid <= 10"]


def test_being_written(tmp_path):
    path = str(tmp_path / "test.sqlite")
    create_database(path, COLUMNS).close()
    assert being_written(path)
    # not changed for a while
    os.utime(path, (time.time() - 120, time.time() - 120))
    assert not being_written(path)
    # in WAL mode readers don't block the writer
    create_database(str(tmp_path / "wal.sqlite"), COLUMNS, wal=True).close()
    assert not being_written(str(tmp_path / "wal.sqlite"))


def test_run_query_counts_only_when_needed(tmp_path, make_database, monkeypatch):
    make_database(rows=[(i, i, 3, "G", b"ok") for i in range(10)])
    path = str(tmp_path / "test.sqlite")
    statements = []
    connection = database.query_connection

    def spy(*args):
        con = connection(*args)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(database, "query_connection", spy)
    df, count = run_query(path, "id, response", "id < 5", 100)
    assert count == 5 and list(df["response"]) == ["ok"] * 5
    assert not any("count(*)" in statement for statement in statements)
    df, count = run_query(path, "*", "", 3)
    assert (len(df), count) == (3, 10)


def test_run_query_on_a_database_being_written_to(tmp_path, make_database, monkeypatch):
    make_database(rows=[(i, i, 3, "G", b"ok") for i in range(10)])
    path = str(tmp_path / "test.sqlite")
    deadlines = []
    set_deadline = database.set_deadline

    def stop_counting(con, seconds=None):
        deadlines.append(seconds)
        set_deadline(con, seconds)
        if len(deadlines) == 2:
            # the count takes too long
            con.set_progress_handler(lambda: True, 1)

    monkeypatch.setattr(database, "set_deadline", stop_counting)
    df, count = run_query(path, "*", "", 3)
    assert (len(df), count) == (3, None)
    assert deadlines == [database.LIVE_QUERY_TIMEOUT] * 2
    # not being written to: the usual time limit
    os.utime(path, (time.time() - 120, time.time() - 120))
    deadlines.clear()
    run_query(path, "*", "", 3)
    assert deadlines == [database.QUERY_TIMEOUT] * 2


def test_slow_queries_on_a_database_being_written_to(tmp_path, make_database, monkeypatch):
    make_database(rows=[(1, 1, 3, "G", b"ok")])
    monkeypatch.setattr(database, "LIVE_QUERY_TIMEOUT", 0.2)
    with pytest.raises(TimeoutError) as error:
        run_query(str(tmp_path / "test.sqlite"), "*", SLOW_CONDITION, 100)
    assert query_error(error.value).startswith("the query took longer than 0.2 s: stopped, because a glitch script")


def test_writes_can_happen_while_loading(tmp_path, make_database, monkeypatch):
    """A glitch script writing to the database (rollback journal) gets the lock between chunks."""
    monkeypatch.setattr(database, "LOAD_CHUNK", 2)
    make_database(rows=[(i, i, 3, "G", b"x") for i in range(7)])
    path = str(tmp_path / "test.sqlite")
    writer = sqlite3.connect(path, timeout=0)
    writes = []

    def write_between_chunks():
        writer.execute("INSERT INTO experiments VALUES (100, 1, 1, 'R', x'00')")
        writer.commit()
        writes.append(1)

    monkeypatch.setattr(database, "available_memory", write_between_chunks)
    df = load_dataframe(path, "")
    writer.close()
    assert len(writes) >= 3
    # experiments written during the load are loaded the next time
    assert len(df) == 7
    monkeypatch.setattr(database, "available_memory", lambda: None)
    assert len(load_experiments(path, "", 7)[0]) == len(writes)


def test_available_memory():
    assert available_memory() is None or available_memory() > 0


def test_match_helpers():
    assert match_string(b"Hello", "ell")
    assert not match_string(b"Hello", "x")
    assert match_hex(b"\x00\x66\x1b", "661b")
    assert not match_hex(b"\x00", "661b")
