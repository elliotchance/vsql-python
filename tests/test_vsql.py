import datetime
import decimal
import threading
import warnings

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
        ("CAST(1.5 AS DECFLOAT)", decimal.Decimal("1.5"), "DECFLOAT"),
        ("CAST(1.5e300 AS DECFLOAT(16))", decimal.Decimal("1.5E+300"), "DECFLOAT"),
        ("CAST('héllo' AS CLOB(10))", "héllo", "CHARACTER LARGE OBJECT"),
        ("X'00ff10'", b"\x00\xff\x10", "BINARY"),
        ("CAST(X'01' AS BINARY(3))", b"\x01\x00\x00", "BINARY"),
        ("CAST(X'0102' AS VARBINARY(10))", b"\x01\x02", "BINARY VARYING"),
        ("CAST(X'' AS VARBINARY(10))", b"", "BINARY VARYING"),
        ("CAST(X'0102' AS BLOB(10))", b"\x01\x02", "BINARY LARGE OBJECT"),
        ("CAST(NULL AS VARBINARY(10))", None, "BINARY VARYING"),
        ("INTERVAL '1-02' YEAR TO MONTH", vsql.YearMonthInterval(1, 2), "INTERVAL YEAR TO MONTH"),
        ("INTERVAL -'1-02' YEAR TO MONTH", vsql.YearMonthInterval(-1, -2),
         "INTERVAL YEAR TO MONTH"),
        ("INTERVAL '100' YEAR(3)", vsql.YearMonthInterval(years=100), "INTERVAL YEAR"),
        ("INTERVAL '14' MONTH", vsql.YearMonthInterval(years=1, months=2), "INTERVAL MONTH"),
        ("INTERVAL '3' DAY", datetime.timedelta(days=3), "INTERVAL DAY"),
        ("INTERVAL '-5' HOUR", datetime.timedelta(hours=-5), "INTERVAL HOUR"),
        ("INTERVAL '90' MINUTE", datetime.timedelta(minutes=90), "INTERVAL MINUTE"),
        ("INTERVAL '12.000005' SECOND", datetime.timedelta(seconds=12, microseconds=5),
         "INTERVAL SECOND"),
        ("INTERVAL '1 02' DAY TO HOUR", datetime.timedelta(days=1, hours=2),
         "INTERVAL DAY TO HOUR"),
        ("INTERVAL '1 02:03' DAY TO MINUTE", datetime.timedelta(days=1, hours=2, minutes=3),
         "INTERVAL DAY TO MINUTE"),
        ("INTERVAL -'12345 04:05:06.5' DAY(5) TO SECOND",
         -datetime.timedelta(days=12345, hours=4, minutes=5, seconds=6, microseconds=500000),
         "INTERVAL DAY TO SECOND"),
        ("INTERVAL '4:05' HOUR TO MINUTE", datetime.timedelta(hours=4, minutes=5),
         "INTERVAL HOUR TO MINUTE"),
        ("INTERVAL '4:05:06' HOUR TO SECOND", datetime.timedelta(hours=4, minutes=5, seconds=6),
         "INTERVAL HOUR TO SECOND"),
        ("INTERVAL '5:06.7' MINUTE TO SECOND",
         datetime.timedelta(minutes=5, seconds=6, microseconds=700000),
         "INTERVAL MINUTE TO SECOND"),
        ("CAST(NULL AS INTERVAL DAY)", None, "INTERVAL DAY"),
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


def test_fractional_seconds_with_leading_zeros(conn):
    assert scalar(conn, "VALUES TIMESTAMP '2022-01-02 15:04:05.000006'") == datetime.datetime(
        2022, 1, 2, 15, 4, 5, 6)


def test_type_objects(conn):
    cur = conn.execute("VALUES ROW(1, 'a', DATE '2022-01-02', TRUE)")
    number, string, date, boolean = (d[1] for d in cur.description)
    assert number == vsql.NUMBER and number != vsql.STRING
    assert string == vsql.STRING
    assert date == vsql.DATETIME
    assert boolean not in (vsql.NUMBER, vsql.STRING, vsql.DATETIME, vsql.BINARY)

    cur = conn.execute("""VALUES ROW(X'01', CAST(X'01' AS BLOB(1)), CAST('a' AS CLOB(1)),
        CAST(1 AS DECFLOAT), INTERVAL '1' DAY, INTERVAL '1-02' YEAR TO MONTH)""")
    binary, blob, clob, decfloat, day, year_month = (d[1] for d in cur.description)
    assert binary == vsql.BINARY and blob == vsql.BINARY and binary != vsql.STRING
    assert clob == vsql.STRING
    assert decfloat == vsql.NUMBER
    assert day == vsql.DATETIME and year_month == vsql.DATETIME


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

    conn.execute("""CREATE TABLE bar (b VARBINARY(10), c CLOB(5), d DECFLOAT(16),
        y INTERVAL YEAR(3) TO MONTH, s INTERVAL DAY(4) TO SECOND(3))""")
    b, c, d, y, s = conn.execute("SELECT * FROM bar").description
    assert b[1:6] == ("BINARY VARYING", None, 10, None, None)
    assert c[1:6] == ("CHARACTER LARGE OBJECT", 5, 20, None, None)
    assert d[1:6] == ("DECFLOAT", None, 16, 16, None)
    # The precision of an interval is its leading field precision, and the
    # scale is the fractional seconds precision. The size is its length.
    assert y[1:6] == ("INTERVAL YEAR TO MONTH", 7, 7, 3, None)
    assert s[1:6] == ("INTERVAL DAY TO SECOND", 18, 18, 4, 3)

    # The scale of a TIME or TIMESTAMP is the fractional seconds precision.
    conn.execute("""CREATE TABLE baz (d DATE, t TIME, tz TIME(6) WITH TIME ZONE,
        ts TIMESTAMP(3), tsz TIMESTAMP WITH TIME ZONE)""")
    d, t, tz, ts, tsz = conn.execute("SELECT * FROM baz").description
    assert d[1:6] == ("DATE", 10, 10, None, None)
    assert t[1:6] == ("TIME", 8, 8, None, 0)
    assert tz[1:6] == ("TIME WITH TIME ZONE", 21, 21, None, 6)
    assert ts[1:6] == ("TIMESTAMP", 23, 23, None, 3)
    assert tsz[1:6] == ("TIMESTAMP WITH TIME ZONE", 32, 32, None, 6)

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
        ("vb", b"\x00\xffab", b"\x00\xffab"),
        ("vb", b"", b""),
        ("vb", bytearray(b"\x01\x02"), b"\x01\x02"),
        ("vb", memoryview(b"abc")[1:], b"bc"),
        ("bi", b"\x01", b"\x01\x00\x00"),
        ("bl", bytes(range(256)) * 4, bytes(range(256)) * 4),
        ("cl", "héllo", "héllo"),
        ("df", decimal.Decimal("1.25E-200"), decimal.Decimal("1.25E-200")),
        ("df", 7, decimal.Decimal("7")),
        ("ds", datetime.timedelta(days=3, hours=4, minutes=5, seconds=6, microseconds=7),
         datetime.timedelta(days=3, hours=4, minutes=5, seconds=6, microseconds=7)),
        ("ds", datetime.timedelta(microseconds=-1), datetime.timedelta(microseconds=-1)),
        ("ds", datetime.timedelta(days=-12345678), datetime.timedelta(days=-12345678)),
        ("ds", datetime.timedelta(0), datetime.timedelta(0)),
        # A cast to an interval that doesn't end with SECOND truncates it.
        ("hm", datetime.timedelta(hours=-5, minutes=-3, seconds=-59),
         datetime.timedelta(hours=-5, minutes=-3)),
        ("ym", vsql.YearMonthInterval(years=-100, months=-11),
         vsql.YearMonthInterval(years=-100, months=-11)),
        ("ym", vsql.YearMonthInterval(months=14), vsql.YearMonthInterval(years=1, months=2)),
        ("vb", None, None),
        ("ds", None, None),
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
        tm TIME(6), tt TIME(6) WITH TIME ZONE, vb VARBINARY(10), bi BINARY(3),
        bl BLOB(2000), cl CLOB(10), df DECFLOAT, ds INTERVAL DAY(8) TO SECOND(6),
        hm INTERVAL HOUR TO MINUTE, ym INTERVAL YEAR(3) TO MONTH)""")
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
        ([b"bytes"], vsql.ProgrammingError, "07006"),
        ([datetime.timedelta(days=10**8)], vsql.DataError, "8 digits"),
        ([vsql.YearMonthInterval(1)], vsql.ProgrammingError, "07006"),
        ([object()], vsql.ProgrammingError, "unsupported type"),
        ([decimal.Decimal("NaN")], vsql.DataError, "finite"),
        (["\ud800"], vsql.DataError, "parameter 1: .* isn't valid Unicode"),
        ([], vsql.ProgrammingError, "dynamic parameter"),
    ],
)
def test_parameter_errors(conn, parameters, error, message):
    with pytest.raises(error, match=message):
        conn.execute("VALUES CAST(? AS INTEGER)", parameters)


def test_binary_parameters_in_where(conn):
    conn.execute("CREATE TABLE foo (b VARBINARY(10), i INTEGER)")
    conn.cursor().executemany("INSERT INTO foo (b, i) VALUES (?, ?)", [(b"\x00", 1), (b"\xff", 2)])
    assert scalar(conn, "SELECT i FROM foo WHERE b = ?", [b"\xff"]) == 2
    assert vsql.Binary(b"\x00") == b"\x00"


def test_long_binary_strings(conn):
    conn.execute("CREATE TABLE foo (b BLOB(10000))")
    for n in [0, 255, 256, 257, 1000, 9999]:
        # SQLGetData reads pieces of 256 octets.
        want = bytes(i % 256 for i in range(n))
        conn.execute("DELETE FROM foo")
        conn.execute("INSERT INTO foo (b) VALUES (?)", [want])
        assert scalar(conn, "SELECT b FROM foo") == want


def test_interval_arithmetic(conn):
    assert scalar(conn, "VALUES TIMESTAMP '2022-01-02 00:00:00' + CAST(? AS INTERVAL DAY)",
                  [datetime.timedelta(days=3)]) == datetime.datetime(2022, 1, 5)
    assert scalar(conn, "VALUES DATE '2022-01-15' - CAST(? AS INTERVAL MONTH)",
                  [vsql.YearMonthInterval(months=2)]) == datetime.date(2021, 11, 15)


def test_year_month_interval():
    i = vsql.YearMonthInterval(years=1, months=14)
    assert (i.years, i.months, i.total_months) == (2, 2, 26)
    assert (-i).years == -2 and (-i).months == -2
    assert str(-i) == "-2-02"
    assert repr(i) == "YearMonthInterval(years=2, months=2)"
    assert i == vsql.YearMonthInterval(months=26)
    assert len({i, vsql.YearMonthInterval(months=26)}) == 1
    assert i != 26


def test_long_strings(conn):
    conn.execute("CREATE TABLE foo (s VARCHAR(10000))")
    for n in [255, 256, 257, 1000, 9999]:
        # Multi-byte characters cross the boundary of each piece that
        # SQLGetData reads.
        want = "é" * (n // 2) + "x" * (n % 2)
        conn.execute("DELETE FROM foo")
        conn.execute("INSERT INTO foo (s) VALUES (?)", [want])
        assert scalar(conn, "SELECT s FROM foo") == want


    # A NUL is part of the string, even in a piece that isn't the last.
    for want in ["a\x00b", "a\x00b" + "c" * 300, "c" * 300 + "\x00" + "é" * 300]:
        assert scalar(conn, "SELECT CAST(? AS VARCHAR(1000))", [want]) == want


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


def test_error_fields(conn):
    conn.execute("""CREATE TABLE foo (id INTEGER CONSTRAINT foo_pk PRIMARY KEY,
        x INTEGER CONSTRAINT x_positive CHECK (x > 0))""")
    conn.execute("INSERT INTO foo (id, x) VALUES (1, 1)")

    for query, constraint in [("INSERT INTO foo (id, x) VALUES (1, 1)", "FOO_PK"),
                              ("INSERT INTO foo (id, x) VALUES (2, -1)", "X_POSITIVE")]:
        with pytest.raises(vsql.IntegrityError) as e:
            conn.execute(query)
        assert (e.value.constraint_catalog, e.value.constraint_schema,
                e.value.constraint_name) == (":memory:", "PUBLIC", constraint)
        assert (e.value.catalog_name, e.value.schema_name,
                e.value.table_name) == (":memory:", "PUBLIC", "FOO")
        assert e.value.column_name is None

    with pytest.raises(vsql.ProgrammingError) as e:
        conn.execute("SELECT * FROM nope")
    assert (e.value.table_name, e.value.constraint_name) == ("NOPE", None)

    with pytest.raises(vsql.ProgrammingError) as e:
        conn.execute("SELECT nope FROM foo")
    assert (e.value.column_name, e.value.table_name) == ("NOPE", None)

    # An error from the module itself doesn't have them.
    e = vsql.ProgrammingError("x")
    assert (e.sqlstate, e.table_name, e.cursor_name) == (None, None, None)



def test_warnings(conn):
    conn.execute("CREATE TABLE foo (x INTEGER)")
    conn.execute("INSERT INTO foo (x) VALUES (1), (NULL)")
    with pytest.warns(vsql.Warning) as record:
        assert scalar(conn, "SELECT COUNT(x) FROM foo") == 1
    assert [(w.message.sqlstate, w.message.message) for w in record] == [
        ("01003", "null value eliminated in set function")]
    assert record[0].filename == __file__
    assert issubclass(vsql.Warning, Exception)

    # A warning can be an error.
    with warnings.catch_warnings():
        warnings.simplefilter("error", vsql.Warning)
        with pytest.raises(vsql.Warning) as e:
            conn.execute("VALUES CAST('abc' AS VARCHAR(2))")
        assert e.value.sqlstate == "01004"


def test_diagnostics(conn):
    with pytest.raises(vsql.ProgrammingError) as e:
        conn.execute("SELECT * FROM nope")
    assert e.value.diagnostics == (("42P01", e.value.message),)
    assert vsql.InterfaceError("x").diagnostics == ()

def test_error_classes():
    assert isinstance(vsql.error("42P01", "x"), vsql.ProgrammingError)
    assert isinstance(vsql.error("23502", "x"), vsql.IntegrityError)
    assert isinstance(vsql.error("0A000", "x"), vsql.NotSupportedError)
    assert isinstance(vsql.error("HY010", "x"), vsql.InterfaceError)
    assert isinstance(vsql.error("HYC00", "x"), vsql.NotSupportedError)
    assert isinstance(vsql.error("3B001", "x"), vsql.ProgrammingError)
    assert vsql.error("23505", "x", constraint_name="PK").constraint_name == "PK"
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



def test_transaction_rolled_back_by_vsql(path):
    """A 40001 rolls back the whole transaction, so the next statement starts a
    new one (which rollback undoes). Other errors only undo the statement."""

    with vsql.connect(path) as conn:
        conn.execute("CREATE TABLE t (x INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO t (x) VALUES (1)")

    a, b = vsql.connect(path), vsql.connect(path)
    a.execute("UPDATE t SET x = 100 WHERE x = 1")
    b.execute("INSERT INTO t (x) VALUES (50)")
    cur = b.execute("SELECT x FROM t")
    with pytest.raises(vsql.OperationalError) as e:
        b.execute("UPDATE t SET x = 200 WHERE x = 1")
    assert e.value.sqlstate == "40001"
    assert not b.in_transaction
    assert cur.fetchone() is None
    a.commit()

    b.execute("INSERT INTO t (x) VALUES (60)")
    assert b.in_transaction
    b.rollback()
    assert not b.in_transaction

    # A unique violation only undoes the statement.
    b.execute("INSERT INTO t (x) VALUES (70)")
    with pytest.raises(vsql.IntegrityError):
        b.execute("INSERT INTO t (x) VALUES (70)")
    assert b.in_transaction
    b.commit()
    a.close()
    b.close()

    with vsql.connect(path) as conn:
        assert conn.execute("SELECT x FROM t ORDER BY x").fetchall() == [(70,), (100,)]

def test_savepoints(path):
    conn = vsql.connect(path)
    conn.execute("CREATE TABLE foo (x INTEGER)")
    conn.commit()

    conn.execute("INSERT INTO foo (x) VALUES (1)")
    conn.execute("SAVEPOINT a")
    conn.execute("INSERT INTO foo (x) VALUES (2)")
    conn.execute('SAVEPOINT "b"')
    conn.execute("INSERT INTO foo (x) VALUES (3)")
    cur = conn.execute("SELECT x FROM foo")
    conn.rollback_to_savepoint('"b"')
    # The rollback closed the cursors.
    assert cur.fetchone() is None
    assert conn.in_transaction
    assert conn.execute("SELECT x FROM foo ORDER BY x").fetchall() == [(1,), (2,)]

    conn.rollback_to_savepoint("a")
    assert conn.execute("SELECT x FROM foo").fetchall() == [(1,)]

    # RELEASE SAVEPOINT is a statement. A savepoint that doesn't exist is
    # 3B001.
    conn.execute("RELEASE SAVEPOINT a")
    with pytest.raises(vsql.ProgrammingError) as e:
        conn.rollback_to_savepoint("a")
    assert e.value.sqlstate == "3B001"
    # ROLLBACK TO SAVEPOINT isn't.
    conn.execute("SAVEPOINT c")
    with pytest.raises(vsql.ProgrammingError) as e:
        conn.execute("ROLLBACK TO SAVEPOINT c")
    assert e.value.sqlstate == "42000"

    conn.commit()
    conn.close()
    with vsql.connect(path) as conn:
        assert conn.execute("SELECT x FROM foo").fetchall() == [(1,)]


def test_commit_closes_cursors(path):
    with vsql.connect(path) as conn:
        cur = conn.execute("VALUES 1")
        conn.commit()
        assert cur.fetchone() is None
        assert cur.execute("VALUES 2").fetchone() == (2,)


def test_isolation_levels(path):
    for level in ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]:
        with vsql.connect(path, isolation_level=level) as conn:
            assert scalar(conn, "VALUES 1") == 1
            conn.execute("CREATE TABLE t (x INT)")
            conn.rollback()

    # READ UNCOMMITTED is always read only.
    for options in [{"isolation_level": "READ UNCOMMITTED"}, {"read_only": True}]:
        with vsql.connect(path, **options) as conn:
            assert scalar(conn, "VALUES 1") == 1
            with pytest.raises(vsql.OperationalError) as e:
                conn.execute("CREATE TABLE t (x INT)")
            assert e.value.sqlstate == "25006"

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


def test_version():
    assert isinstance(vsql.vsql_version, str)
    assert len(vsql.vsql_version_info) == 3
    assert vsql.vsql_version == ".".join(map(str, vsql.vsql_version_info))
    assert vsql.vsql_version_info >= (1, 7, 58)

    info = vsql.build_info
    assert set(info) == {"version", "commit", "date", "v_version", "v_commit", "c_compiler",
                         "operating_system", "machine"}
    # The amalgamation is from a release.
    assert info["version"] == "v" + vsql.vsql_version
    assert len(info["commit"]) == 40
    datetime.date.fromisoformat(info["date"])
    # It's a copy.
    info["version"] = "x"
    assert vsql.build_info["version"] != "x"

    with pytest.raises(AttributeError):
        vsql.nope


def test_file_version(path):
    with vsql.connect(":memory:") as conn:
        assert conn.file_version == (0, 0)
    with vsql.connect(path) as conn:
        read, write = conn.file_version
        assert 0 < read <= write
    with pytest.raises(vsql.InterfaceError):
        conn.file_version
