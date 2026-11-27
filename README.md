# vsql-python

A [DB-API 2.0 (PEP 249)](https://peps.python.org/pep-0249/) driver for
[vsql](https://github.com/elliotchance/vsql), a single file or in-memory SQL
database. libvsql (the vsql library) is compiled into the package, so there's
nothing else to install.

```sh
pip install vsql-python
```

```python
import vsql

with vsql.connect("test.vsql") as conn:
    cur = conn.cursor()
    cur.execute("CREATE TABLE foo (x INTEGER)")
    cur.execute("INSERT INTO foo (x) VALUES (?)", [123])
    conn.commit()

    cur.execute("SELECT x FROM foo")
    print(cur.fetchall())  # [(123,)]
```

The database is the path of the file (it's created if it doesn't exist), or
`:memory:` for a database that only exists for the life of the connection.
Leaving the `with` block commits (or rolls back if there was an exception) and
closes the connection.

See [example/main.py](example/main.py) for a complete program:

```sh
python example/main.py
```

## Transactions

As PEP 249 requires, autocommit is off: the first statement starts a
transaction, which is ended by `conn.commit()` or `conn.rollback()` (closing
the connection rolls it back). With `vsql.connect(..., autocommit=True)` (or
`conn.autocommit = True`), each statement is committed when it finishes, or
when all the rows of a query have been fetched. `START TRANSACTION`, `COMMIT`
and `ROLLBACK` can't be executed as statements.

vsql's transactions are `READ COMMITTED`.

### Savepoints

A savepoint is made by executing `SAVEPOINT`, and released by executing
`RELEASE SAVEPOINT`. Like `ROLLBACK`, `ROLLBACK TO SAVEPOINT` can't be executed
as a statement, so it's `conn.rollback_to_savepoint(name)` instead (which
closes the cursors of the connection):

```python
cur.execute("INSERT INTO foo (x) VALUES (1)")
cur.execute("SAVEPOINT before_two")
cur.execute("INSERT INTO foo (x) VALUES (2)")
conn.rollback_to_savepoint("before_two")
conn.commit()  # only 1 is inserted
```

The name is an identifier, so `"before_two"` is `BEFORE_TWO`, and a name that
must stay lower case is quoted, such as `'"b"'`.

## Parameters

Parameters are `?` (`paramstyle` is `"qmark"`), given as a sequence. Each
Python type is sent as an SQL type, which is cast to the type it's used as:

| Python                    | SQL                                          |
|---------------------------|----------------------------------------------|
| `None`                    | `NULL`                                       |
| `bool`                    | `BOOLEAN`                                    |
| `int`                     | `BIGINT` (`NUMERIC` if it's out of range)    |
| `float`                   | `DOUBLE PRECISION`                           |
| `str`                     | `CHARACTER VARYING`                          |
| `decimal.Decimal`         | `NUMERIC` (it must be finite)                |
| `datetime.datetime`       | `TIMESTAMP(6)` (`WITH TIME ZONE` if aware)   |
| `datetime.date`           | `DATE`                                       |
| `datetime.time`           | `TIME(6)` (`WITH TIME ZONE` if aware)        |
| `bytes`, `bytearray`, `memoryview` | `BINARY VARYING`                    |
| `datetime.timedelta`      | `INTERVAL DAY TO SECOND(6)`                  |
| `vsql.YearMonthInterval`  | `INTERVAL YEAR TO MONTH`                     |

A string can't be a binary string (or the other way around), and a
year-month interval can't be a day-time interval (`07006`). A
`datetime.timedelta` can have up to 99,999,999 days (`DAY(8)`).

## Columns

| SQL                              | Python              |
|----------------------------------|---------------------|
| `SMALLINT`, `INTEGER`, `BIGINT`  | `int`               |
| `REAL`, `DOUBLE PRECISION`       | `float`             |
| `DECIMAL`, `NUMERIC`, `DECFLOAT` | `decimal.Decimal`   |
| `CHARACTER`, `VARCHAR`, `CLOB`   | `str`               |
| `BINARY`, `VARBINARY`, `BLOB`    | `bytes`             |
| `BOOLEAN`                        | `bool`              |
| `DATE`                           | `datetime.date`     |
| `TIME (WITH TIME ZONE)`          | `datetime.time`     |
| `TIMESTAMP (WITH TIME ZONE)`     | `datetime.datetime` |
| `INTERVAL YEAR`, `INTERVAL MONTH`, `INTERVAL YEAR TO MONTH` | `vsql.YearMonthInterval` |
| any other `INTERVAL` (`DAY` to `SECOND`) | `datetime.timedelta` |

A `NULL` is `None`. A value `WITH TIME ZONE` is aware (with a fixed offset),
otherwise it's naive.

A year-month interval isn't a `datetime.timedelta`, since a month doesn't have
a fixed length. `vsql.YearMonthInterval(years=1, months=2)` is a number of
months (`total_months`), which is also `years` and `months` (with the same
sign):

```python
cur.execute("VALUES INTERVAL -'1-02' YEAR TO MONTH")
i = cur.fetchone()[0]  # YearMonthInterval(years=-1, months=-2)
print(i.total_months)  # -14
```

The `type_code` in `cursor.description` is the name of the type (such as
`"INTEGER"` or `"INTERVAL DAY TO SECOND"`), which compares equal to
`vsql.NUMBER`, `vsql.STRING`, `vsql.BINARY` or `vsql.DATETIME` (which includes
the intervals). The `internal_size` is the length of a string (in octets, 4 for
each character) or a binary string, the precision of a number, or the length of
a datetime or interval (which is also its `display_size`). The `precision` and
`scale` are those of a `DECIMAL` or `NUMERIC`, the precision of a `DECFLOAT`
(16 or 34), or the leading field precision and the fractional seconds
precision of an interval. The `scale` of a `TIME` or `TIMESTAMP` is its
fractional seconds precision.

## Errors

An error from vsql is the PEP 249 exception for its SQLSTATE, with the
SQLSTATE as `sqlstate`:

```python
try:
    cur.execute("SELECT * FROM nope")
except vsql.ProgrammingError as e:
    print(e.sqlstate)  # 42P01
    print(e.table_name)  # NOPE
```

The object that the error is about is in these attributes (or `None`):

| Attribute | |
|---|---|
| `constraint_catalog`, `constraint_schema`, `constraint_name` | The constraint that was violated (such as a `PRIMARY KEY` or `CHECK`) |
| `catalog_name`, `schema_name`, `table_name` | The table (of the constraint, or one that doesn't exist) |
| `column_name` | The column (such as one that doesn't exist) |
| `cursor_name` | The cursor (for an invalid cursor state) |

```python
try:
    cur.execute("INSERT INTO foo (id) VALUES (1)")
except vsql.IntegrityError as e:
    print(e.constraint_name, e.table_name)  # FOO_PK FOO
```

| SQLSTATE class                                 | Exception           |
|------------------------------------------------|---------------------|
| `22` data exception                            | `DataError`         |
| `23` integrity constraint violation            | `IntegrityError`    |
| `42` syntax error or access rule violation, `07`, `21`, `24`, `26`, `2B`, `34`, `3B`, `3D`, `3F` | `ProgrammingError` |
| `0A` feature not supported, `HYC00`            | `NotSupportedError` |
| `08`, `0B`, `25`, `2D`, `40`, `54`, `58`, `HY001` | `OperationalError` |
| `HY` CLI-specific condition                    | `InterfaceError`    |
| `XX` internal error                            | `InternalError`     |
| anything else                                  | `DatabaseError`     |

## Versions

`vsql.vsql_version` is the version of vsql that's compiled into the package
(such as `"1.7.58"`), and `vsql.vsql_version_info` is the same as a tuple
(`(1, 7, 58)`). `vsql.build_info` is a dict of how it was built:

```python
>>> vsql.build_info
{'version': 'v1.7.58', 'commit': '3cafbc3...', 'date': '2026-11-26',
 'v_version': '0.5.2', 'v_commit': 'c0bb949', 'c_compiler': 'clang',
 'operating_system': 'macos', 'machine': 'arm64'}
```

`conn.file_version` is the `(read, write)` version of the database file's
format (`(0, 0)` for `:memory:`).

## Threads

Each connection has its own libvsql environment, so different connections can
be used from different threads at the same time (`threadsafety` is 1: threads
can share the module, but not connections). The GIL is released while libvsql
runs.

## Requirements

Python 3.9 or later. Wheels are built for macOS (ARM64 and x86-64), Linux
(ARM64 and x86-64) and Windows (x86-64), so pip doesn't need to compile
anything.

Building from the source distribution needs a C compiler, and takes a minute or
two. On Windows, it needs clang-cl (from [LLVM](https://llvm.org), or the
"C++ Clang tools for Windows" component of Visual Studio) as well as MSVC, since
the C that V generates doesn't compile with MSVC. clang-cl is found on the
`PATH` (or in `C:\Program Files\LLVM\bin`), or set `CC` to its path.

## Updating vsql

`internal/amalgamation` is the libvsql C from a vsql release
(`vsql-VERSION-amalgamation.zip`). To update it to the latest release:

```sh
scripts/update-amalgamation.sh
pip install -e . && pytest
```

Or give a version (`scripts/update-amalgamation.sh v1.2.3`), or a zip that has
already been downloaded.
