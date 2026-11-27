"""A DB-API 2.0 (PEP 249) driver for vsql (https://github.com/elliotchance/vsql),
a single file or in-memory SQL database. libvsql (the vsql library) is compiled
into the package, so nothing else needs to be installed.

    import vsql

    with vsql.connect("test.vsql") as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE foo (x INTEGER)")
        cur.execute("INSERT INTO foo (x) VALUES (?)", [123])
        conn.commit()

The database is the path of the file, which is created if it doesn't exist, or
":memory:" for a database that only exists for the life of the connection.

Transactions

Like PEP 249 requires, autocommit is off: the first statement starts a
transaction, which is ended by commit or rollback (or closing the connection,
which rolls it back). With autocommit on, each statement is committed when it
finishes, or when the rows of a query have all been fetched. START TRANSACTION,
COMMIT and ROLLBACK can't be executed as statements.

A savepoint is made by executing SAVEPOINT, and released by executing RELEASE
SAVEPOINT. ROLLBACK TO SAVEPOINT can't be executed as a statement either, so
it's Connection.rollback_to_savepoint instead.

Parameters

Parameters are "?" (paramstyle is "qmark"). Each Python type is sent as an SQL
type, which is cast to the type it's used as:

    None                 NULL
    bool                 BOOLEAN
    int                  BIGINT (NUMERIC if it's out of range)
    float                DOUBLE PRECISION
    str                  CHARACTER VARYING
    decimal.Decimal      NUMERIC (it must be finite)
    datetime.datetime    TIMESTAMP(6) (WITH TIME ZONE if it's aware)
    datetime.date        DATE
    datetime.time        TIME(6) (WITH TIME ZONE if it's aware)
    bytes, bytearray     BINARY VARYING
      or memoryview
    datetime.timedelta   INTERVAL DAY TO SECOND(6)
    YearMonthInterval    INTERVAL YEAR TO MONTH

Columns

The Python type of each column type:

    SMALLINT, INTEGER, BIGINT          int
    REAL, DOUBLE PRECISION             float
    DECIMAL, NUMERIC, DECFLOAT         decimal.Decimal
    CHARACTER, VARCHAR, CLOB           str
    BINARY, VARBINARY, BLOB            bytes
    BOOLEAN                            bool
    DATE                               datetime.date
    TIME (WITH TIME ZONE)              datetime.time
    TIMESTAMP (WITH TIME ZONE)         datetime.datetime
    INTERVAL YEAR TO MONTH (or YEAR,   YearMonthInterval
      or MONTH)
    any other INTERVAL                 datetime.timedelta

A NULL is None. A value WITH TIME ZONE is aware (with a fixed offset), and
otherwise it's naive. The type_code in a cursor's description is the name of
the type (such as "INTEGER" or "INTERVAL DAY TO SECOND"), which compares equal
to STRING, BINARY, NUMBER or DATETIME.

Errors

An error from vsql is the PEP 249 exception for its SQLSTATE (such as
ProgrammingError for 42P01, an undefined table), with the SQLSTATE as its
sqlstate attribute. The object it's about (such as constraint_name or
table_name) is in the other attributes, see Error.

Version

vsql_version is the version of vsql (such as "1.7.58"), vsql_version_info is
it as a tuple of ints, and build_info is a dict of how it was built (its
"version" tag, "commit", "date", "v_version", "v_commit", "c_compiler",
"operating_system" and "machine").
"""

import datetime
import time

from . import _convert, _libvsql
from ._connection import Connection, Cursor, build_info as _build_info
from ._convert import YearMonthInterval
from ._errors import (
    DatabaseError,
    DataError,
    Error,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
    Warning,
    error,
)

apilevel = "2.0"

# Threads may share the module, but not connections.
threadsafety = 1

paramstyle = "qmark"

_libvsql.set_error_factory(error)


def connect(database, autocommit=False, isolation_level="READ COMMITTED", read_only=False):
    """Opens a connection to a database file (which is created if it doesn't
    exist) or ":memory:".

    isolation_level ("READ UNCOMMITTED", "READ COMMITTED", "REPEATABLE READ"
    or "SERIALIZABLE") and read_only are for the transactions that are started
    when autocommit is off. vsql runs READ UNCOMMITTED as READ COMMITTED, and
    REPEATABLE READ as SERIALIZABLE. A READ UNCOMMITTED transaction is always
    read only.
    """

    return Connection(database, autocommit, isolation_level, read_only)


Date = datetime.date
Time = datetime.time
Timestamp = datetime.datetime


def DateFromTicks(ticks):
    return Date(*time.localtime(ticks)[:3])


def TimeFromTicks(ticks):
    return Time(*time.localtime(ticks)[3:6])


def TimestampFromTicks(ticks):
    return Timestamp(*time.localtime(ticks)[:6])


Binary = bytes


class _TypeObject:
    """Compares equal to the type_code of each of its types."""

    def __init__(self, *type_names):
        self.type_names = frozenset(type_names)

    def __eq__(self, other):
        return other in self.type_names

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(self.type_names)


STRING = _TypeObject("CHARACTER", "CHARACTER VARYING", "CHARACTER LARGE OBJECT")
BINARY = _TypeObject("BINARY", "BINARY VARYING", "BINARY LARGE OBJECT")
NUMBER = _TypeObject("SMALLINT", "INTEGER", "BIGINT", "REAL", "DOUBLE PRECISION", "DECIMAL",
                     "NUMERIC", "DECFLOAT")
DATETIME = _TypeObject("DATE", "TIME", "TIME WITH TIME ZONE", "TIMESTAMP",
                       "TIMESTAMP WITH TIME ZONE",
                       *("INTERVAL " + fields for fields in _convert.interval_types.values()))
ROWID = _TypeObject()

_version = None


def __getattr__(name):
    """vsql_version, vsql_version_info and build_info are read from libvsql the
    first time they're used."""

    global _version
    if name in ("vsql_version", "vsql_version_info", "build_info"):
        if _version is None:
            _version = _build_info()
        version_info, info = _version
        if name == "vsql_version":
            return ".".join(map(str, version_info))
        if name == "vsql_version_info":
            return version_info
        return dict(info)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BINARY",
    "DATETIME",
    "NUMBER",
    "ROWID",
    "STRING",
    "Binary",
    "Connection",
    "Cursor",
    "DataError",
    "DatabaseError",
    "Date",
    "DateFromTicks",
    "Error",
    "IntegrityError",
    "InterfaceError",
    "InternalError",
    "NotSupportedError",
    "OperationalError",
    "ProgrammingError",
    "Time",
    "TimeFromTicks",
    "Timestamp",
    "TimestampFromTicks",
    "Warning",
    "YearMonthInterval",
    "apilevel",
    "build_info",
    "connect",
    "paramstyle",
    "threadsafety",
    "vsql_version",
    "vsql_version_info",
]
