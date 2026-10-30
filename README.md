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

vsql doesn't have a binary type, so `bytes` can't be a parameter.

## Columns

| SQL                              | Python              |
|----------------------------------|---------------------|
| `SMALLINT`, `INTEGER`, `BIGINT`  | `int`               |
| `REAL`, `DOUBLE PRECISION`       | `float`             |
| `DECIMAL`, `NUMERIC`             | `decimal.Decimal`   |
| `CHARACTER`, `VARCHAR`           | `str`               |
| `BOOLEAN`                        | `bool`              |
| `DATE`                           | `datetime.date`     |
| `TIME (WITH TIME ZONE)`          | `datetime.time`     |
| `TIMESTAMP (WITH TIME ZONE)`     | `datetime.datetime` |

A `NULL` is `None`. A value `WITH TIME ZONE` is aware (with a fixed offset),
otherwise it's naive. The `type_code` in `cursor.description` is the name of
the type (such as `"INTEGER"`), which compares equal to `vsql.NUMBER`,
`vsql.STRING` or `vsql.DATETIME`.

## Errors

An error from vsql is the PEP 249 exception for its SQLSTATE, with the
SQLSTATE as `sqlstate`:

```python
try:
    cur.execute("SELECT * FROM nope")
except vsql.ProgrammingError as e:
    print(e.sqlstate)  # 42P01
```

| SQLSTATE class                                 | Exception           |
|------------------------------------------------|---------------------|
| `22` data exception                            | `DataError`         |
| `23` integrity constraint violation            | `IntegrityError`    |
| `42` syntax error or access rule violation, `07`, `21`, `24`, `26`, `2B`, `34`, `3D`, `3F` | `ProgrammingError` |
| `0A` feature not supported, `HYC00`            | `NotSupportedError` |
| `08`, `0B`, `25`, `2D`, `40`, `54`, `58`, `HY001` | `OperationalError` |
| `HY` CLI-specific condition                    | `InterfaceError`    |
| `XX` internal error                            | `InternalError`     |
| anything else                                  | `DatabaseError`     |

## Threads

Each connection has its own libvsql environment, so different connections can
be used from different threads at the same time (`threadsafety` is 1: threads
can share the module, but not connections). The GIL is released while libvsql
runs.

## Requirements

Python 3.9 or later. Wheels are built for macOS (ARM64 and x86-64), Linux
(ARM64 and x86-64) and Windows (x86-64), so pip doesn't need to compile
anything.

Building from the source distribution needs a C compiler, and takes about 30
seconds. On Windows, it needs clang-cl (from [LLVM](https://llvm.org), or the
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
