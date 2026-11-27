# Type hints for the vsql package (see __init__.py for the documentation).

import builtins
import datetime
import decimal
import os
from collections.abc import Iterable, Iterator, Sequence
from types import TracebackType
from typing import Any, Literal, Optional, Union

from typing_extensions import Self, TypeAlias

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

apilevel: str
threadsafety: int
paramstyle: str
vsql_version: str
vsql_version_info: tuple[int, ...]
build_info: dict[str, str]

# A value that can be a parameter.
_Parameter: TypeAlias = Union[
    None,
    bool,
    int,
    float,
    str,
    decimal.Decimal,
    datetime.datetime,
    datetime.date,
    datetime.time,
    bytes,
    bytearray,
    memoryview,
    datetime.timedelta,
    YearMonthInterval,
]
# A value of a column.
_Value: TypeAlias = Union[
    None,
    bool,
    int,
    float,
    str,
    decimal.Decimal,
    datetime.datetime,
    datetime.date,
    datetime.time,
    bytes,
    datetime.timedelta,
    YearMonthInterval,
]
_Row: TypeAlias = tuple[_Value, ...]
# (name, type_code, display_size, internal_size, precision, scale, null_ok)
_Column: TypeAlias = tuple[str, str, Optional[int], int, Optional[int], Optional[int], bool]
# None, but Any rather than Optional for an attribute that's usually checked
# by the code before (like typeshed's MaybeNone), such as the description of
# a query.
_MaybeNone: TypeAlias = Any
_IsolationLevel: TypeAlias = Literal[
    "READ UNCOMMITTED", "READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"
]

def connect(
    database: Union[str, bytes, os.PathLike[str], os.PathLike[bytes]],
    autocommit: bool = False,
    isolation_level: _IsolationLevel = "READ COMMITTED",
    read_only: bool = False,
) -> Connection: ...

class Connection:
    def __init__(
        self,
        database: Union[str, bytes, os.PathLike[str], os.PathLike[bytes]],
        autocommit: bool = False,
        isolation_level: _IsolationLevel = "READ COMMITTED",
        read_only: bool = False,
    ) -> None: ...
    @property
    def closed(self) -> bool: ...
    @property
    def autocommit(self) -> bool: ...
    @autocommit.setter
    def autocommit(self, value: bool) -> None: ...
    @property
    def in_transaction(self) -> bool: ...
    @property
    def file_version(self) -> tuple[int, int]: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def rollback_to_savepoint(self, name: str) -> None: ...
    def cursor(self) -> Cursor: ...
    def execute(
        self, operation: str, parameters: Optional[Sequence[_Parameter]] = None
    ) -> Cursor: ...
    def close(self) -> None: ...
    def __del__(self) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None: ...

class Cursor:
    connection: Connection
    description: Union[tuple[_Column, ...], _MaybeNone]
    rowcount: int
    arraysize: int
    lastrowid: None
    def __init__(self, connection: Connection) -> None: ...
    def execute(
        self, operation: str, parameters: Optional[Sequence[_Parameter]] = None
    ) -> Self: ...
    def executemany(
        self, operation: str, seq_of_parameters: Iterable[Sequence[_Parameter]]
    ) -> None: ...
    def fetchone(self) -> Optional[_Row]: ...
    def fetchmany(self, size: Optional[int] = None) -> list[_Row]: ...
    def fetchall(self) -> list[_Row]: ...
    def __iter__(self) -> Iterator[_Row]: ...
    def __next__(self) -> _Row: ...
    def setinputsizes(self, sizes: Any) -> None: ...
    def setoutputsize(self, size: Any, column: Optional[int] = None) -> None: ...
    def close(self) -> None: ...
    def __del__(self) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None: ...

class YearMonthInterval:
    def __init__(self, years: int = 0, months: int = 0) -> None: ...
    @property
    def total_months(self) -> int: ...
    @property
    def years(self) -> int: ...
    @property
    def months(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...
    def __hash__(self) -> int: ...
    def __neg__(self) -> YearMonthInterval: ...

Date = datetime.date
Time = datetime.time
Timestamp = datetime.datetime
Binary = bytes

def DateFromTicks(ticks: float) -> datetime.date: ...
def TimeFromTicks(ticks: float) -> datetime.time: ...
def TimestampFromTicks(ticks: float) -> datetime.datetime: ...

class _TypeObject:
    type_names: frozenset[str]
    def __init__(self, *type_names: str) -> None: ...
    def __eq__(self, other: object) -> bool: ...
    def __ne__(self, other: object) -> bool: ...
    def __hash__(self) -> int: ...

STRING: _TypeObject
BINARY: _TypeObject
NUMBER: _TypeObject
DATETIME: _TypeObject
ROWID: _TypeObject

class Warning(builtins.Exception): ...

class Error(builtins.Exception):
    message: str
    sqlstate: Optional[str]
    constraint_catalog: Optional[str]
    constraint_schema: Optional[str]
    constraint_name: Optional[str]
    catalog_name: Optional[str]
    schema_name: Optional[str]
    table_name: Optional[str]
    column_name: Optional[str]
    cursor_name: Optional[str]
    def __init__(
        self,
        message: str,
        sqlstate: Optional[str] = None,
        *,
        constraint_catalog: Optional[str] = None,
        constraint_schema: Optional[str] = None,
        constraint_name: Optional[str] = None,
        catalog_name: Optional[str] = None,
        schema_name: Optional[str] = None,
        table_name: Optional[str] = None,
        column_name: Optional[str] = None,
        cursor_name: Optional[str] = None,
    ) -> None: ...

class InterfaceError(Error): ...
class DatabaseError(Error): ...
class DataError(DatabaseError): ...
class OperationalError(DatabaseError): ...
class IntegrityError(DatabaseError): ...
class InternalError(DatabaseError): ...
class ProgrammingError(DatabaseError): ...
class NotSupportedError(DatabaseError): ...
