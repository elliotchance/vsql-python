import datetime
import decimal
import threading

import pytest

import vsql

plus0530 = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
minus4 = datetime.timezone(datetime.timedelta(hours=-4))


@pytest.fixture
def conn():
    """A connection to a new in-memory database, with autocommit on."""

    c = vsql.connect(":memory:", autocommit=True)
    yield c
    c.close()


@pytest.fixture
def path(tmp_path):
    """The path of a new database file."""

    return tmp_path / "test.vsql"


def scalar(conn, query, parameters=None):
    return conn.execute(query, parameters).fetchone()[0]


@pytest.mark.parametrize(
    "expr, want, type_name",
    [
        ("CAST(12 AS SMALLINT)", 12, "SMALLINT"),
        ("CAST(-12 AS INTEGER)", -12, "INTEGER"),
        ("CAST(9223372036854775807 AS BIGINT)", 9223372036854775807, "BIGINT"),
        ("CAST(1.5 AS REAL)", 1.5, "REAL"),
        ("CAST(1.25 AS DOUBLE PRECISION)", 1.25, "DOUBLE PRECISION"),
        ("CAST(1.5 AS DECIMAL(10, 3))", decimal.Decimal("1.500"), "DECIMAL"),
        ("CAST(12 AS NUMERIC(10, 2))", decimal.Decimal("12.00"), "NUMERIC"),
        ("CAST('ab' AS CHARACTER(4))", "ab  ", "CHARACTER"),
        ("CAST('héllo' AS VARCHAR(10))", "héllo", "CHARACTER VARYING"),
        ("TRUE", True, "BOOLEAN"),
        ("FALSE", False, "BOOLEAN"),
        ("DATE '2022-01-02'", datetime.date(2022, 1, 2), "DATE"),
        ("TIME '15:04:05.123456'", datetime.time(15, 4, 5, 123456), "TIME"),
        ("TIME '15:04:05+05:30'", datetime.time(15, 4, 5, tzinfo=plus0530), "TIME WITH TIME ZONE"),
        ("TIMESTAMP '2022-01-02 15:04:05.5'", datetime.datetime(2022, 1, 2, 15, 4, 5, 500000),
         "TIMESTAMP"),
        ("TIMESTAMP '2022-01-02 15:04:05.123+05:30'",
         datetime.datetime(2022, 1, 2, 15, 4, 5, 123000, tzinfo=plus0530),
         "TIMESTAMP WITH TIME ZONE"),
        ("CAST(NULL AS INTEGER)", None, "INTEGER"),
        ("CAST(NULL AS BOOLEAN)", None, "BOOLEAN"),
        ("CAST(NULL AS DATE)", None, "DATE"),
    ],
)
def test_column_values(conn, expr, want, type_name):
    cur = conn.execute("VALUES " + expr)
    assert cur.description[0][1] == type_name
    got = cur.fetchone()[0]
    assert got == want
    assert type(got) is type(want)
    if isinstance(want, (datetime.time, datetime.datetime)):
        assert got.utcoffset() == want.utcoffset()


@pytest.mark.xfail(reason="vsql drops the leading zeros of fractional seconds", strict=True)
def test_fractional_seconds_with_leading_zeros(conn):
    assert scalar(conn, "VALUES TIMESTAMP '2022-01-02 15:04:05.000006'") == datetime.datetime(
        2022, 1, 2, 15, 4, 5, 6)


def test_type_objects(conn):
    cur = conn.execute("VALUES ROW(1, 'a', DATE '2022-01-02', TRUE)")
    number, string, date, boolean = (d[1] for d in cur.description)
    assert number == vsql.NUMBER and number != vsql.STRING
    assert string == vsql.STRING
    assert date == vsql.DATETIME
    assert boolean not in (vsql.NUMBER, vsql.STRING, vsql.DATETIME)


def test_description(conn):
    conn.execute("CREATE TABLE foo (a INTEGER NOT NULL, b VARCHAR(20), c NUMERIC(10, 3))")

    cur = conn.execute("SELECT * FROM foo")
    assert [d[0] for d in cur.description] == ["A", "B", "C"]
    assert all(len(d) == 7 for d in cur.description)
    a, b, c = cur.description
    assert a[6] is False
    assert b[6] is True
    assert b[2] == 20
    assert (c[4], c[5]) == (10, 3)

    # A statement that isn't a query has no description.
    conn.execute("DELETE FROM foo")
    cur = conn.execute("DELETE FROM foo")
    assert cur.description is None


@pytest.mark.parametrize(
    "column, arg, want",
    [
        ("s", 12, 12),
        ("i", -12, -12),
        ("b", 9223372036854775807, 9223372036854775807),
        ("r", 1.5, 1.5),
        ("d", 1.25, 1.25),
        ("n", 7, decimal.Decimal("7.00000")),
        ("n", 1.5, decimal.Decimal("1.50000")),
        ("n", decimal.Decimal("123"), decimal.Decimal("123.00000")),
        ("n", decimal.Decimal("-1.25"), decimal.Decimal("-1.25000")),
        ("n", decimal.Decimal("1E+2"), decimal.Decimal("100.00000")),
        ("n", decimal.Decimal("0.25"), decimal.Decimal("0.25000")),
        ("n", decimal.Decimal("-0.00005"), decimal.Decimal("-0.00005")),
        ("n", decimal.Decimal("0"), decimal.Decimal("0.00000")),
        ("n", 10**14, decimal.Decimal("100000000000000.00000")),
        ("n", decimal.Decimal("123456789012345.12345"), decimal.Decimal("123456789012345.12345")),
        ("c", "héllo", "héllo"),
        ("c", "", ""),
        ("bo", True, True),
        ("bo", False, False),
        ("ts", datetime.datetime(2022, 1, 2, 15, 4, 5, 123456),
         datetime.datetime(2022, 1, 2, 15, 4, 5, 123456)),
        ("tz", datetime.datetime(2022, 1, 2, 15, 4, 5, 123456, tzinfo=minus4),
         datetime.datetime(2022, 1, 2, 15, 4, 5, 123456, tzinfo=minus4)),
        ("dt", datetime.date(2022, 1, 2), datetime.date(2022, 1, 2)),
        ("tm", datetime.time(15, 4, 5, 123456), datetime.time(15, 4, 5, 123456)),
        ("tt", datetime.time(15, 4, 5, tzinfo=plus0530), datetime.time(15, 4, 5, tzinfo=plus0530)),
        ("i", None, None),
        ("c", None, None),
        ("bo", None, None),
        ("dt", None, None),
    ],
)
def test_parameters(conn, column, arg, want):
    conn.execute("""CREATE TABLE foo (
        s SMALLINT, i INTEGER, b BIGINT, r REAL, d DOUBLE PRECISION,
        n NUMERIC(20, 5), c VARCHAR(100), bo BOOLEAN,
        ts TIMESTAMP(6), tz TIMESTAMP(6) WITH TIME ZONE, dt DATE,
        tm TIME(6), tt TIME(6) WITH TIME ZONE)""")
    conn.execute(f"INSERT INTO foo ({column}) VALUES (?)", [arg])

    got = scalar(conn, f"SELECT {column} FROM foo")
    assert got == want
    assert type(got) is type(want)


def test_bigint_out_of_range_is_numeric(conn):
    assert scalar(conn, "VALUES CAST(? AS NUMERIC(30))", [10**25]) == decimal.Decimal(10**25)


def test_parameters_in_where(conn):
    conn.execute("CREATE TABLE foo (i INTEGER, c VARCHAR(10))")
    conn.execute("INSERT INTO foo (i, c) VALUES (?, ?)", (1, "a"))
    conn.execute("INSERT INTO foo (i, c) VALUES (?, ?)", (2, "b"))
    assert scalar(conn, "SELECT c FROM foo WHERE i = ?", [2]) == "b"


@pytest.mark.parametrize(
    "parameters, error, message",
    [
        ({"i": 1}, vsql.NotSupportedError, "named parameters"),
        ("1", vsql.ProgrammingError, "sequence"),
        ([b"bytes"], vsql.NotSupportedError, "binary"),
        ([object()], vsql.ProgrammingError, "unsupported type"),
        ([decimal.Decimal("NaN")], vsql.DataError, "finite"),
        ([], vsql.ProgrammingError, "dynamic parameter"),
    ],
)
def test_parameter_errors(conn, parameters, error, message):
    with pytest.raises(error, match=message):
        conn.execute("VALUES CAST(? AS INTEGER)", parameters)


def test_long_strings(conn):
    conn.execute("CREATE TABLE foo (s VARCHAR(10000))")
    for n in [255, 256, 257, 1000, 9999]:
        # Multi-byte characters cross the boundary of each piece that
        # SQLGetData reads.
        want = "é" * (n // 2) + "x" * (n % 2)
        conn.execute("DELETE FROM foo")
        conn.execute("INSERT INTO foo (s) VALUES (?)", [want])
        assert scalar(conn, "SELECT s FROM foo") == want


@pytest.mark.parametrize(
    "query, error, sqlstate",
    [
        ("SELECT * FROM nope", vsql.ProgrammingError, "42P01"),
        ("SELEC 1", vsql.ProgrammingError, "42601"),
        # Transactions are ended with commit and rollback.
        ("COMMIT", vsql.ProgrammingError, "42000"),
        ("VALUES 1 / 0", vsql.DataError, "22012"),
    ],
)
def test_errors(conn, query, error, sqlstate):
    with pytest.raises(error) as e:
        conn.execute(query)
    assert e.value.sqlstate == sqlstate
    assert f"(SQLSTATE {sqlstate})" in str(e.value)
    assert isinstance(e.value, vsql.DatabaseError)

    # The connection still works after an error.
    assert scalar(conn, "VALUES 1") == 1


def test_integrity_error(conn):
    conn.execute("CREATE TABLE foo (x INTEGER NOT NULL)")
    with pytest.raises(vsql.IntegrityError) as e:
        conn.execute("INSERT INTO foo (x) VALUES (NULL)")
    assert e.value.sqlstate.startswith("23")


def test_error_classes():
    assert isinstance(vsql.error("42P01", "x"), vsql.ProgrammingError)
    assert isinstance(vsql.error("23502", "x"), vsql.IntegrityError)
    assert isinstance(vsql.error("0A000", "x"), vsql.NotSupportedError)
    assert isinstance(vsql.error("HY010", "x"), vsql.InterfaceError)
    assert isinstance(vsql.error("HYC00", "x"), vsql.NotSupportedError)
    assert type(vsql.error("HY000", "x")) is vsql.DatabaseError
    assert type(vsql.error("99999", "x")) is vsql.DatabaseError
    for cls in [vsql.InterfaceError, vsql.DatabaseError, vsql.DataError, vsql.OperationalError,
                vsql.IntegrityError, vsql.InternalError, vsql.ProgrammingError,
                vsql.NotSupportedError]:
        assert issubclass(cls, vsql.Error)
    assert issubclass(vsql.Error, Exception) and issubclass(vsql.Warning, Exception)


def test_rowcount(conn):
    conn.execute("CREATE TABLE foo (x INTEGER)")
    conn.execute("INSERT INTO foo (x) VALUES (1)")
    conn.execute("INSERT INTO foo (x) VALUES (2)")

    cur = conn.execute("UPDATE foo SET x = x + 1")
    assert cur.rowcount == 2
    assert cur.lastrowid is None
    assert conn.execute("SELECT * FROM foo").rowcount == -1

    cur.executemany("INSERT INTO foo (x) VALUES (?)", [[3], [4], [5]])
    assert cur.rowcount == 3


def test_fetch(conn):
    conn.execute("CREATE TABLE foo (x INTEGER)")
    conn.cursor().executemany("INSERT INTO foo (x) VALUES (?)", [[i] for i in range(10)])

    cur = conn.cursor()
    cur.execute("SELECT x FROM foo")
    assert cur.fetchone() == (0,)
    assert cur.fetchmany() == [(1,)]
    assert cur.fetchmany(3) == [(2,), (3,), (4,)]
    cur.arraysize = 2
    assert cur.fetchmany() == [(5,), (6,)]
    assert cur.fetchall() == [(7,), (8,), (9,)]
    assert cur.fetchone() is None
    assert cur.fetchall() == []

    assert [x for x, in conn.execute("SELECT x FROM foo")] == list(range(10))

    # Executing again closes the rows that weren't fetched.
    cur.execute("SELECT x FROM foo")
    cur.fetchone()
    assert cur.execute("SELECT COUNT(*) FROM foo").fetchall() == [(10,)]

    with pytest.raises(vsql.ProgrammingError):
        cur.execute("DELETE FROM foo WHERE x > 100").fetchone()


def test_prepared_statements(conn):
    conn.execute("CREATE TABLE foo (x INTEGER)")
    cur = conn.cursor()
    for i in range(1, 11):
        cur.execute("INSERT INTO foo (x) VALUES (?)", [i])

    # Each cursor has its own statement, so the rows of one are still open
    # while another is executed.
    rows1 = conn.execute("SELECT x FROM foo WHERE x > ?", [5])
    rows2 = conn.execute("SELECT x FROM foo WHERE x > ?", [8])
    assert len(rows2.fetchall()) == 2
    assert len(rows1.fetchall()) == 5

    # A statement that fails to prepare isn't reused.
    with pytest.raises(vsql.ProgrammingError):
        cur.execute("SELEC ?", [1])
    with pytest.raises(vsql.ProgrammingError):
        cur.execute("SELEC ?", [1])


def test_cursor_close(conn):
    cur = conn.cursor()
    cur.execute("VALUES 1")
    cur.close()
    cur.close()
    with pytest.raises(vsql.InterfaceError):
        cur.execute("VALUES 1")
    with pytest.raises(vsql.InterfaceError):
        cur.fetchone()

    with conn.cursor() as cur:
        cur.execute("VALUES 1")
    with pytest.raises(vsql.InterfaceError):
        cur.fetchone()


def test_connection_close(path):
    conn = vsql.connect(path)
    cur = conn.cursor()
    cur.execute("VALUES 1")
    conn.close()
    conn.close()
    assert conn.closed
    with pytest.raises(vsql.InterfaceError):
        conn.cursor()
    with pytest.raises(vsql.InterfaceError):
        cur.execute("VALUES 1")
    with pytest.raises(vsql.InterfaceError):
        conn.commit()
    cur.close()


def test_transactions(path):
    with vsql.connect(path, autocommit=True) as conn:
        conn.execute("CREATE TABLE foo (x INTEGER)")

    def count():
        with vsql.connect(path) as other:
            return scalar(other, "SELECT COUNT(*) FROM foo")

    conn = vsql.connect(path)
    assert not conn.autocommit and not conn.in_transaction

    # commit
    conn.execute("INSERT INTO foo (x) VALUES (1)")
    assert conn.in_transaction
    assert count() == 0
    conn.commit()
    assert not conn.in_transaction
    assert count() == 1

    # rollback
    conn.execute("INSERT INTO foo (x) VALUES (2)")
    assert scalar(conn, "SELECT COUNT(*) FROM foo") == 2
    conn.rollback()
    assert count() == 1

    # Commit and rollback without a transaction do nothing.
    conn.commit()
    conn.rollback()

    # Turning autocommit on commits the transaction.
    conn.execute("INSERT INTO foo (x) VALUES (3)")
    conn.autocommit = True
    assert not conn.in_transaction
    assert count() == 2
    conn.execute("INSERT INTO foo (x) VALUES (4)")
    assert not conn.in_transaction
    assert count() == 3

    # Closing the connection rolls back the transaction.
    conn.autocommit = False
    conn.execute("INSERT INTO foo (x) VALUES (5)")
    conn.close()
    assert count() == 3

    # The context manager commits, or rolls back for an exception.
    with vsql.connect(path) as conn:
        conn.execute("INSERT INTO foo (x) VALUES (6)")
    assert conn.closed
    assert count() == 4
    with pytest.raises(ZeroDivisionError):
        with vsql.connect(path) as conn:
            conn.execute("INSERT INTO foo (x) VALUES (7)")
            1 / 0
    assert count() == 4


def test_commit_closes_cursors(path):
    with vsql.connect(path) as conn:
        cur = conn.execute("VALUES 1")
        conn.commit()
        assert cur.fetchone() is None
        assert cur.execute("VALUES 2").fetchone() == (2,)


def test_isolation_levels(path):
    for level in ["READ UNCOMMITTED", "READ COMMITTED"]:
        with vsql.connect(path, isolation_level=level) as conn:
            assert scalar(conn, "VALUES 1") == 1

    for options in [{"isolation_level": "SERIALIZABLE"}, {"isolation_level": "REPEATABLE READ"},
                    {"read_only": True}]:
        with vsql.connect(path, **options) as conn:
            with pytest.raises(vsql.NotSupportedError) as e:
                conn.execute("VALUES 1")
            assert e.value.sqlstate == "HYC00"

    with pytest.raises(vsql.ProgrammingError):
        vsql.connect(path, isolation_level="SNAPSHOT")


def test_persistence(path):
    with vsql.connect(path) as conn:
        conn.execute("CREATE TABLE foo (x VARCHAR(10))")
        conn.execute("INSERT INTO foo (x) VALUES ('saved')")

    with vsql.connect(str(path)) as conn:
        assert scalar(conn, "SELECT x FROM foo") == "saved"


def test_memory_databases_are_separate():
    with vsql.connect(":memory:") as a, vsql.connect(":memory:") as b:
        a.execute("CREATE TABLE foo (x INTEGER)")
        with pytest.raises(vsql.ProgrammingError):
            b.execute("SELECT * FROM foo")


def test_concurrency(path):
    """Many connections from many threads, with enough work for the garbage
    collector to run."""

    with vsql.connect(path, autocommit=True) as conn:
        conn.execute("CREATE TABLE foo (g INTEGER, x INTEGER)")

    threads, inserts = 8, 50
    errors = []

    def work(g):
        try:
            with vsql.connect(path, autocommit=True) as conn:
                for i in range(inserts):
                    conn.execute("INSERT INTO foo (g, x) VALUES (?, ?)", [g, i])
                    n = scalar(conn, "SELECT COUNT(*) FROM foo WHERE g = ?", [g])
                    assert n == i + 1, f"thread {g}: got {n} rows, want {i + 1}"
        except BaseException as e:
            errors.append(e)

    ts = [threading.Thread(target=work, args=(g,)) for g in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []

    with vsql.connect(path) as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM foo") == threads * inserts


def test_module_globals():
    assert vsql.apilevel == "2.0"
    assert vsql.threadsafety == 1
    assert vsql.paramstyle == "qmark"
    assert vsql.Date(2022, 1, 2) == datetime.date(2022, 1, 2)
    assert isinstance(vsql.TimestampFromTicks(0), datetime.datetime)
