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

Columns

The Python type of each column type:

    SMALLINT, INTEGER, BIGINT    int
    REAL, DOUBLE PRECISION       float
    DECIMAL, NUMERIC             decimal.Decimal
    CHARACTER, VARCHAR           str
    BOOLEAN                      bool
    DATE                         datetime.date
    TIME (WITH TIME ZONE)        datetime.time
    TIMESTAMP (WITH TIME ZONE)   datetime.datetime

A NULL is None. A value WITH TIME ZONE is aware (with a fixed offset), and
otherwise it's naive. The type_code in a cursor's description is the name of
the type (such as "INTEGER" or "CHARACTER VARYING"), which compares equal to
STRING, NUMBER or DATETIME.

Errors

An error from vsql is the PEP 249 exception for its SQLSTATE (such as
ProgrammingError for 42P01, an undefined table), with the SQLSTATE as its
sqlstate attribute.
"""

import datetime
import time

from . import _libvsql
from ._connection import Connection, Cursor
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

    isolation_level and read_only are for the transactions that are started
    when autocommit is off. vsql's transactions are READ COMMITTED, so
    "READ UNCOMMITTED" and "READ COMMITTED" are the only isolation levels that
    can be used, and read_only can't be used (the first statement is a
    NotSupportedError).
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


# vsql doesn't have a binary type, so a Binary can't be a parameter.
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


STRING = _TypeObject("CHARACTER", "CHARACTER VARYING")
BINARY = _TypeObject()
NUMBER = _TypeObject("SMALLINT", "INTEGER", "BIGINT", "REAL", "DOUBLE PRECISION", "DECIMAL",
                     "NUMERIC")
DATETIME = _TypeObject("DATE", "TIME", "TIME WITH TIME ZONE", "TIMESTAMP",
                       "TIMESTAMP WITH TIME ZONE")
ROWID = _TypeObject()

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
    "apilevel",
    "connect",
    "paramstyle",
    "threadsafety",
]
