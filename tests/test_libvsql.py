"""Tests of the extension module (_libvsql.c) that call it directly, for the
errors that the vsql package never causes (such as the wrong arguments or an
invalid handle) and for running out of memory."""

import subprocess
import sys

import pytest

import vsql
from vsql import _libvsql as lib

_testcapi = pytest.importorskip("_testcapi")

INVALID = 99999

FUNCTIONS = [
    "alloc_handle",
    "free_handle",
    "connect",
    "disconnect",
    "set_autocommit",
    "start_tran",
    "end_tran",
    "get_info",
    "end_savepoint",
    "exec_direct",
    "prepare",
    "execute",
    "row_count",
    "num_result_cols",
    "describe_col",
    "col_attribute",
    "close_cursor",
    "set_scrollable",
    "fetch_row",
    "transaction_active",
]


@pytest.fixture
def conn():
    """A connection, with its own statement handles (freed before it's
    closed), so that the tests can leave them in any state."""

    c = vsql.connect(":memory:", autocommit=True)
    c.statements = []
    yield c
    for stmt in c.statements:
        lib.free_handle(lib.SQL_HANDLE_STMT, stmt)
    c.close()


def statement(conn, query=None):
    """A new statement handle, that has executed query."""

    stmt = lib.alloc_handle(lib.SQL_HANDLE_STMT, conn._dbc)
    conn.statements.append(stmt)
    if query is not None:
        lib.exec_direct(stmt, query)
    return stmt


@pytest.mark.parametrize("name", FUNCTIONS)
def test_wrong_arguments(name):
    with pytest.raises(TypeError):
        getattr(lib, name)()


@pytest.mark.parametrize(
    "call",
    [
        lambda: lib.alloc_handle(lib.SQL_HANDLE_STMT, INVALID),
        lambda: lib.free_handle(lib.SQL_HANDLE_STMT, INVALID),
        lambda: lib.connect(INVALID, ":memory:"),
        lambda: lib.disconnect(INVALID),
        lambda: lib.set_autocommit(INVALID, True),
        lambda: lib.start_tran(INVALID, lib.SQL_TRANSACTION_READ_WRITE,
                               lib.SQL_TRANSACTION_SERIALIZABLE),
        lambda: lib.end_tran(INVALID, True),
        lambda: lib.end_savepoint(INVALID, "a", True),
        lambda: lib.exec_direct(INVALID, "VALUES 1"),
        lambda: lib.prepare(INVALID, "VALUES 1"),
        lambda: lib.execute(INVALID, ()),
        lambda: lib.row_count(INVALID),
        lambda: lib.num_result_cols(INVALID),
        lambda: lib.col_attribute(INVALID, 1, lib.SQL_DESC_DATETIME_INTERVAL_PRECISION),
        lambda: lib.close_cursor(INVALID),
        lambda: lib.set_scrollable(INVALID, True),
        lambda: lib.fetch_row(INVALID, b"i"),
        lambda: lib.transaction_active(lib.SQL_HANDLE_STMT, INVALID),
    ],
)
def test_invalid_handle(call):
    with pytest.raises(vsql.DatabaseError, match="invalid handle") as e:
        call()
    assert e.value.sqlstate == "HY000"


def test_errors(conn):
    # A string or an int can't be read as a binary string, or the other way
    # around.
    with pytest.raises(vsql.ProgrammingError, match="07006"):
        lib.fetch_row(statement(conn, "VALUES 1"), b"b")
    with pytest.raises(vsql.ProgrammingError, match="07006"):
        lib.fetch_row(statement(conn, "VALUES X'00'"), b"s")
    for kind, value in [(b"i", "'x'"), (b"d", "'x'"), (b"r", "'x'")]:
        with pytest.raises(vsql.DataError):
            lib.fetch_row(statement(conn, f"VALUES {value}"), kind)

    stmt = statement(conn, "VALUES 1")
    with pytest.raises(vsql.ProgrammingError, match="07009"):
        lib.describe_col(stmt, 2)
    with pytest.raises(vsql.InterfaceError, match="HY091"):
        lib.col_attribute(stmt, 1, -1)
    with pytest.raises(vsql.InterfaceError, match="HY096"):
        lib.get_info(conn._dbc, -1, True)
    with pytest.raises(vsql.InterfaceError, match="HY096"):
        lib.get_info(conn._dbc, -1, False)

    # Fetching after the cursor is closed.
    lib.close_cursor(stmt)
    with pytest.raises(vsql.ProgrammingError, match="24000"):
        lib.fetch_row(stmt, b"i")


@pytest.mark.parametrize(
    "param, error",
    [
        ((1,), TypeError),
        ((lib.SQL_BIGINT, lib.SQL_BIGINT, 0, 0, 2**64), OverflowError),
        ((lib.SQL_DOUBLE, lib.SQL_DOUBLE, 0, 0, "x"), TypeError),
        ((lib.SQL_BINARY, lib.SQL_VARBINARY, 1, 0, "x"), TypeError),
        ((lib.SQL_CHAR, lib.SQL_VARCHAR, 1, 0, 1), TypeError),
        ((-1, lib.SQL_VARCHAR, 1, 0, b"x"), vsql.InterfaceError),
    ],
)
def test_bad_parameters(conn, param, error):
    stmt = statement(conn)
    lib.prepare(stmt, "VALUES CAST(? AS VARCHAR(10))")
    with pytest.raises(error):
        lib.execute(stmt, (param,))


def test_long_names(conn):
    """A message or column name that doesn't fit in the first buffer."""

    name = '"' + "é" * 128 + '"'
    conn.execute(f"CREATE SCHEMA {name}")
    with pytest.raises(vsql.ProgrammingError) as e:
        conn.execute(f"SELECT * FROM {name}.{name}")
    assert len(e.value.message.encode()) > 512
    assert e.value.message.count("é" * 128) == 2

    cur = conn.execute(f"SELECT x AS {name} FROM (VALUES 1) AS t (x)")
    assert cur.description[0][0] == "é" * 128


def without_memory(start, call):
    """Calls call with every allocation failing from the start'th one. The
    allocations work again before an exception is handled, since Python can't
    handle one without memory."""

    # Python keeps freed dicts, lists and small tuples to use again, so a new
    # one doesn't always need to be allocated. These empty the free lists of
    # dicts and lists (a tuple of more than 20 items is never in a free list).
    dicts = [{} for _ in range(1000)]  # noqa: F841
    lists = [[] for _ in range(1000)]  # noqa: F841
    _testcapi.set_nomemory(start)
    try:
        return call()
    finally:
        _testcapi.remove_mem_hooks()


def fail_each_allocation(call, expected=()):
    """Calls call with the first allocation failing, then the second and so on,
    until it doesn't raise MemoryError (it can raise expected instead). It
    returns the number of allocations."""

    for start in range(1000):
        try:
            without_memory(start, call)
        except MemoryError:
            continue
        except expected:
            pass
        return start
    raise AssertionError("call never succeeded")


def test_out_of_memory(conn):
    # An error (the diagnostics, the exception and its fields).
    stmt = statement(conn)
    assert fail_each_allocation(lambda: lib.exec_direct(stmt, "SELECT * FROM nope"),
                                vsql.ProgrammingError) > 0
    assert fail_each_allocation(lambda: lib.free_handle(lib.SQL_HANDLE_STMT, INVALID),
                                vsql.DatabaseError) > 0

    # A warning (its diagnostics). The warning handler is a builtin, so that
    # no Python code runs without memory.
    lib.exec_direct(stmt, "CREATE TABLE w (x VARCHAR(1))")
    lib.set_warning_handler(max)
    try:
        assert fail_each_allocation(lambda: lib.exec_direct(
            stmt, "INSERT INTO w (x) VALUES (CAST('ab' AS VARCHAR(1)))")) > 0
    finally:
        lib.set_warning_handler(vsql._warn)

    # Parameters.
    lib.prepare(stmt, "VALUES CAST(? AS VARCHAR(10))")
    param = (lib.SQL_CHAR, lib.SQL_VARCHAR, 1, 0, b"x")
    assert fail_each_allocation(lambda: lib.execute(stmt, (param,))) > 0
    lib.close_cursor(stmt)

    # A row of more than 20 columns.
    lib.exec_direct(stmt, "VALUES (" + ", ".join(["1"] * 25) + ")")
    assert fail_each_allocation(lambda: lib.fetch_row(stmt, b"i" * 25)) > 0
    lib.close_cursor(stmt)

    # Describing a column.
    lib.exec_direct(stmt, "VALUES 1")
    assert fail_each_allocation(lambda: lib.describe_col(stmt, 1)) > 0
    lib.close_cursor(stmt)

    # Fetching a row with a long string and a long binary string (which are
    # read in pieces).
    for kind, value in [(b"s", "CAST('" + "x" * 1000 + "' AS VARCHAR(1000))"),
                        (b"b", "CAST(X'" + "00" * 1000 + "' AS VARBINARY(1000))")]:
        lib.exec_direct(stmt, f"VALUES {value}, {value}")
        # Each fetch that fails moves to the next row, so there are as many
        # rows as it takes.
        assert fail_each_allocation(lambda: lib.fetch_row(stmt, kind)) > 0
        lib.close_cursor(stmt)


def test_module_init():
    """Running out of memory when the module is created, and the module without
    the vsql package (so there's no error factory). PyInit__libvsql is called
    directly, since Python's import crashes if it runs out of memory after the
    module is created."""

    script = f"""
import ctypes, _testcapi

init = ctypes.PyDLL({lib.__file__!r}).PyInit__libvsql
init.restype = ctypes.py_object

def attempt(start):
    _testcapi.set_nomemory(start)
    try:
        return init()
    finally:
        _testcapi.remove_mem_hooks()

for start in range(1000):
    try:
        m = attempt(start)
    except MemoryError:
        continue
    break
assert start > 0

try:
    m.free_handle(m.SQL_HANDLE_STMT, {INVALID})
except RuntimeError as e:
    print(e)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            check=True)
    assert result.stdout == "vsql: invalid handle (SQLSTATE HY000)\n"
