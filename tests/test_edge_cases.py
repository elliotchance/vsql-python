"""Tests of the Python modules for the cases that the other tests don't reach,
such as errors that vsql never returns."""

import datetime
import gc

import pytest

import vsql
from vsql import _connection, _convert


def test_ticks():
    assert vsql.DateFromTicks(0) == datetime.date.fromtimestamp(0)
    assert vsql.TimeFromTicks(0) == datetime.datetime.fromtimestamp(0).time()


def test_type_objects():
    assert vsql.NUMBER != "BOOLEAN"
    assert not (vsql.NUMBER != "INTEGER")
    assert len({vsql.NUMBER, vsql.STRING, vsql.NUMBER}) == 2


def test_connect_errors(tmp_path, monkeypatch):
    # A file that can't be created.
    with pytest.raises(vsql.OperationalError) as e:
        vsql.connect(tmp_path / "missing" / "test.vsql")
    assert e.value.sqlstate == "08001"

    # Turning on autocommit fails (which vsql doesn't do).
    def fail(dbc, on):
        raise vsql.InternalError("autocommit failed")

    monkeypatch.setattr(_connection.lib, "set_autocommit", fail)
    with pytest.raises(vsql.InternalError, match="autocommit failed"):
        vsql.connect(":memory:")


def test_closed_in_with():
    with vsql.connect(":memory:") as conn:
        conn.close()
    assert conn.closed


def test_cursor_methods():
    with vsql.connect(":memory:", autocommit=True) as conn:
        conn.execute("CREATE TABLE foo (x INTEGER)")
        cur = conn.cursor()
        cur.setinputsizes([None])
        cur.setoutputsize(10)
        cur.setoutputsize(10, 1)
        cur.executemany("INSERT INTO foo (x) VALUES (?)", [[1], [2]])
        with pytest.raises(vsql.ProgrammingError, match="query"):
            cur.executemany("SELECT x FROM foo WHERE x = ?", [[1]])
        cur.execute("SELECT x FROM foo ORDER BY x")
        assert cur.fetchmany(5) == [(1,), (2,)]


def test_del_ignores_errors(monkeypatch):
    conn = vsql.connect(":memory:")
    cur = conn.execute("VALUES 1")

    def fail(*args):
        raise vsql.InternalError("can't free")

    monkeypatch.setattr(_connection.lib, "free_handle", fail)
    del cur, conn
    gc.collect()


@pytest.mark.parametrize(
    "parse, s",
    [
        (_convert.parse_date, "DATE 'x'"),
        (_convert.parse_interval, "INTERVAL"),
        (_convert.parse_interval, "INTERVAL '1 02' DAY TO MINUTE"),
        (_convert.parse_interval, "INTERVAL '1-x' YEAR TO MONTH"),
        (_convert.parse_boolean, "MAYBE"),
    ],
)
def test_invalid_values(parse, s):
    """Values from vsql that aren't valid literals (which vsql doesn't
    return)."""

    with pytest.raises(vsql.DataError, match="invalid"):
        parse(s)


def test_fetch_error():
    """An error from fetching a row leaves the transaction as it was. vsql
    evaluates a query when it's executed, but a string is only decoded when
    it's fetched, and part of a character isn't UTF-8."""

    conn = vsql.connect(":memory:")
    cur = conn.execute("VALUES SUBSTRING('é' FROM 1 FOR 1 USING OCTETS)")
    with pytest.raises(vsql.DataError, match="22021"):
        cur.fetchone()
    assert conn.in_transaction


def test_warning_without_sqlstate():
    assert str(vsql.Warning("careful")) == "careful"
    assert str(vsql.Warning("careful", "01000")) == "careful (SQLSTATE 01000)"
