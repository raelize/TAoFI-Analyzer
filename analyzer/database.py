"""Database access: which files may be read, read-only connections and queries."""
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from os import listdir
from urllib.request import pathname2url

import pandas as pd
from flask import has_request_context, request

from analyzer.config import server_config

_COUNT_CACHE = {}  # path -> (file_key, number of experiments, last rowid)
# large databases are counted in the background (see get_number_of_experiments())
COUNT_IN_BACKGROUND = 50 * 2**20  # bytes
COUNT_CHUNK = 100_000  # rowids counted at a time (with the read lock released in between)
COUNTING = "counting"
_COUNTER = ThreadPoolExecutor(max_workers=2, thread_name_prefix="count")
_COUNTING = set()  # paths being counted
_COUNT_LOCK = threading.Lock()
QUERY_TIMEOUT = 60  # seconds, for queries entered by the user
LOAD_CHUNK = 10_000  # experiments read at a time (with the read lock released in between)
MEMORY_RESERVE = 256 * 2**20  # bytes a database load leaves free (for the rest of the analyzer and the system)


def file_key(path):
    """Identity of a database file: changes whenever the file is (re)written.

    In WAL mode new experiments are written to the -wal file first (the
    database file itself only changes at a checkpoint), so that counts too.
    """
    st = os.stat(path)
    # (path, inode): the same file, not a new one with the same name
    key = (os.path.abspath(path), st.st_ino, st.st_mtime_ns, st.st_size)
    try:
        wal = os.stat(path + "-wal")
        key += (wal.st_mtime_ns, wal.st_size)
    except FileNotFoundError:
        pass
    return key


def is_local_request():
    """True when the current request comes from this machine."""
    return has_request_context() and request.remote_addr in ("127.0.0.1", "::1")


def data_directory(directory):
    """Directory to read databases from, or None if ``directory`` is not a directory.

    Only local users may pick another directory than the one the analyzer was
    started with; for everyone else the server side ``server_config.directory`` is used.
    """
    if not directory or not is_local_request():
        return server_config.directory
    directory = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(directory):
        return None
    return directory


def database_path(directory, database):
    """Path of ``database`` in ``directory``, or None if it is not one of its databases.

    The database name comes from the browser, so it must be a plain ``*.sqlite``
    file name; the directory is checked by data_directory().
    """
    directory = data_directory(directory)
    if directory is None:
        return None
    if not database or os.path.basename(database) != database or not database.endswith(".sqlite"):
        return None
    path = os.path.join(directory, database)
    if not os.path.isfile(path):
        return None
    return path


def list_directory(directory):
    """Subdirectories (name, number of databases) and number of databases in ``directory``."""
    subdirectories = []
    databases = 0
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name.lower())
    except OSError:
        return [], 0
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                try:
                    count = sum(1 for name in os.listdir(entry.path) if name.endswith(".sqlite"))
                except OSError:
                    count = 0
                subdirectories.append((entry.name, count))
            elif entry.name.endswith(".sqlite"):
                databases += 1
        except OSError:
            continue
    return subdirectories, databases


def connect(path):
    """Read-only connection to a database (never creates or modifies a file)."""
    uri = f"file:{pathname2url(os.path.abspath(path))}?mode=ro"
    return sqlite3.connect(uri, uri=True)


# columns the analyzer needs in the experiments table
REQUIRED_COLUMNS = ("color", "response")


def count_rows(connection, after, last):
    """The number of experiments with a rowid in (after, last].

    A range of rowids at a time, each by its own statement: like loading,
    counting all experiments reads the whole file (seconds for a large one),
    and a writer (a glitch script) can get the lock in between.
    """
    count = 0
    while after < last:
        end = min(after + COUNT_CHUNK, last)
        count += connection.execute("SELECT count(*) FROM experiments WHERE rowid > ? AND rowid <= ?", (after, end)).fetchone()[0]
        after = end
    return count


def count_experiments(database_path, key, cached):
    """(number of experiments or None if not an analyzer database, last rowid) of file version ``key``.

    Only the new experiments are counted when the file was appended to since
    the ``cached`` count (see _COUNT_CACHE).
    """
    with closing(connect(database_path)) as connection:
        columns = {row[0] for row in connection.execute("SELECT name FROM pragma_table_info('experiments')")}
        if not all(column in columns for column in REQUIRED_COLUMNS):
            return None, None
        last = connection.execute("SELECT max(rowid) FROM experiments").fetchone()[0] or 0
        if cached is not None and cached[1] is not None and appended_to(cached[0], key) and last >= cached[2]:
            return cached[1] + count_rows(connection, cached[2], last), last
        return count_rows(connection, 0, last), last


def get_number_of_experiments(directory, database, background=False):
    """Number of experiments, or None if the file is not an analyzer database.

    With ``background`` a large database that has to be counted completely is
    counted in the background: COUNTING until it's done.
    """
    database_path = os.path.abspath(os.path.join(directory, database))

    try:
        key = file_key(database_path)
        cached = _COUNT_CACHE.get(key[0])
        if cached is not None and cached[0] == key:
            return cached[1]
        complete = cached is None or cached[1] is None or not appended_to(cached[0], key)
        if background and complete and key[3] > COUNT_IN_BACKGROUND:
            with _COUNT_LOCK:
                if database_path not in _COUNTING:
                    _COUNTING.add(database_path)
                    _COUNTER.submit(_count_in_background, database_path)
            return COUNTING
        count, last = count_experiments(database_path, key, cached)
        # one entry per file (not per version), so a growing database doesn't grow the cache
        _COUNT_CACHE[key[0]] = (key, count, last)
        return count
    except Exception as e:
        print("ERROR (get_number_of_experiments): %s" % (e))


def _count_in_background(database_path):
    try:
        key = file_key(database_path)
        _COUNT_CACHE[key[0]] = (key, *count_experiments(database_path, key, _COUNT_CACHE.get(key[0])))
    except Exception as e:
        print("ERROR (get_number_of_experiments): %s" % (e))
    finally:
        with _COUNT_LOCK:
            _COUNTING.discard(database_path)


def counting():
    """Whether databases are being counted in the background."""
    with _COUNT_LOCK:
        return bool(_COUNTING)


def get_databases(directory):
    # get all databases in directory
    databases = []
    for file in listdir(directory):
        if re.search("^.*\\.sqlite$", file):
            databases.append(file)
    databases.sort(reverse=True)

    # transform to options; other sqlite files are shown, but can't be selected
    databases_options = []
    for database in databases:
        count = get_number_of_experiments(directory, database, background=True)
        if count is COUNTING:
            databases_options.append({"label": f"{database} (counting…)", "value": database})
        elif count is None:
            databases_options.append(
                {"label": f"{database} (not an analyzer database)", "value": database, "disabled": True}
            )
        else:
            databases_options.append({"label": f"{database} ({count})", "value": database})

    return databases_options


def get_argv(database_path):
    try:
        with closing(connect(database_path)) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT argv FROM metadata")
                argvstr = cursor.fetchone()[0]
                return argvstr
    except Exception as e:
        print("ERROR (get_argv): %s" % (e))


def get_parameters(database_path):
    try:
        with closing(connect(database_path)) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT * FROM experiments")
                parameters = list(next(zip(*cursor.description)))
                parameters.remove("response")
                return parameters
    except Exception as e:
        print("ERROR (get_parameters): %s" % (e))


# new function for sqlite3 query
def match_string(response, token):
    # experiments without a response (NULL) don't match
    return response is not None and token.encode() in response


# new function for sqlite3 query
def match_hex(response, token):
    return response is not None and bytes.fromhex(token) in response


def query_connection(path):
    """Read-only connection for user written queries: with the match_* helpers and a time limit."""
    con = connect(path)

    # add some functions to sqlite
    con.create_function("match_string", 2, match_string)
    con.create_function("match_hex", 2, match_hex)

    set_deadline(con)
    return con


def set_deadline(con):
    """Abort queries on ``con`` that run longer than QUERY_TIMEOUT from now (they would
    block a server worker); sqlite raises "interrupted" when the handler returns True."""
    deadline = time.monotonic() + QUERY_TIMEOUT
    con.set_progress_handler(lambda: time.monotonic() > deadline, 100_000)


def available_memory():
    """Memory available to new allocations in bytes, or None if unknown (not Linux)."""
    try:
        with open("/proc/meminfo") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def query_error(e):
    """Message for a failed user query."""
    # pandas wraps the sqlite error in its own exception
    cause = e
    while cause is not None:
        if isinstance(cause, sqlite3.OperationalError) and str(cause) == "interrupted":
            return f"the query took longer than {QUERY_TIMEOUT} s"
        cause = cause.__cause__ or cause.__context__
    return str(e)


def load_experiments(database_path, query, after=0, responses=None):
    """The experiments after rowid ``after`` matching ``query``: (DataFrame, last rowid).

    Read LOAD_CHUNK experiments at a time, each chunk by its own statement:
    sqlite's read lock is released in between, so a glitch script writing to
    the database (in rollback journal mode) only waits for one chunk instead of
    the whole load (which would crash it once that takes longer than its timeout).

    ``responses``: the distinct responses so far (response -> response); new
    experiments share these, and new responses are added to it.
    """
    con = query_connection(database_path)
    # on its own line, so that a -- comment in the query doesn't hide the rest
    condition = f"AND (\n{query}\n)" if query else ""
    sql = f"SELECT rowid, * FROM experiments WHERE rowid > ? {condition} ORDER BY rowid LIMIT {LOAD_CHUNK}"

    # one copy per distinct response: experiments mostly repeat a few
    # responses, so memory depends on the distinct ones, not on the rows
    if responses is None:
        responses = {}
    responses.setdefault(None, b"")
    last = after
    try:
        cursor = con.execute(sql, (last,))
        # the first column is the rowid
        columns = [d[0] for d in cursor.description][1:]
        values = [[] for _ in columns]
        response = columns.index("response") if "response" in columns else None
        while rows := cursor.fetchall():
            if rows[0][0] <= last:
                # e.g. "1) OR (1": not one condition, it escaped "rowid > ?" (and would load forever)
                raise ValueError("the query must be a condition on the experiments (e.g. delay > 100)")
            last = rows[-1][0]
            for index, chunk in enumerate(list(zip(*rows))[1:]):
                if index == response:
                    chunk = [responses.setdefault(r, r) for r in chunk]
                values[index].extend(chunk)
            # stop before the system runs out of memory (and kills the analyzer)
            available = available_memory()
            if available is not None and available < MEMORY_RESERVE:
                raise MemoryError(f"not enough memory: stopped after {len(values[0])} experiments "
                                  f"({available // 2**20} MB left); use a query to load fewer experiments")
            if len(rows) < LOAD_CHUNK:
                break
            # a new deadline per chunk: QUERY_TIMEOUT is about a single (runaway) query
            set_deadline(con)
            cursor = con.execute(sql, (last,))
    finally:
        con.close()

    return pd.DataFrame(dict(zip(columns, values)), columns=columns), last


def load_dataframe(database_path, query):
    """All experiments matching ``query``."""
    return load_experiments(database_path, query)[0]


def max_rowid(database_path):
    """The highest rowid of the experiments (0 if there are none)."""
    with closing(connect(database_path)) as connection:
        return connection.execute("SELECT max(rowid) FROM experiments").fetchone()[0] or 0


def appended_to(previous, current):
    """Whether file version ``current`` (a file_key) can only differ from ``previous``
    by experiments added to it: the same file (not replaced), that didn't shrink.

    Experiments are only ever added by a glitch script; a database rewritten
    in place (e.g. rows deleted) without shrinking isn't noticed.
    """
    return previous[:2] == current[:2] and current[3] >= previous[3]
