import os
import sqlite3
import time

import pytest

from analyzer import database
from analyzer.config import server_config
from analyzer.database import (connect, data_directory, database_path, file_key, get_argv, get_databases,
                               get_number_of_experiments, get_parameters, list_directory, load_dataframe,
                               match_hex, match_string, query_connection, query_error)
from tests.conftest import EXAMPLE_ARGV, EXAMPLE_DATABASE, EXAMPLE_DIRECTORY, EXAMPLE_EXPERIMENTS, create_database

COLUMNS = "id integer, delay integer, length integer, color text, response blob"


#
# which files may be read
#


@pytest.mark.parametrize("name", [
    "../databases/" + EXAMPLE_DATABASE,
    "/etc/passwd",
    "..",
    "missing.sqlite",
    "not-a-database.txt",
    "",
    None,
])
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
        con.execute("WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM r WHERE i < 20000000) SELECT count(*) FROM r").fetchall()
    assert time.monotonic() - started < 5
    assert query_error(error.value) == "the query took longer than 0.5 s"


def test_query_error_unwraps_pandas_errors():
    try:
        try:
            raise sqlite3.OperationalError("interrupted")
        except sqlite3.OperationalError as e:
            raise RuntimeError("Execution failed on sql ...") from e
    except RuntimeError as e:
        assert query_error(e) == f"the query took longer than {database.QUERY_TIMEOUT} s"
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


def test_match_helpers():
    assert match_string(b"Hello", "ell")
    assert not match_string(b"Hello", "x")
    assert match_hex(b"\x00\x66\x1b", "661b")
    assert not match_hex(b"\x00", "661b")
