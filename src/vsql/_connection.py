import os
import weakref

from . import _convert
from . import _libvsql as lib
from ._errors import DatabaseError, InterfaceError, NotSupportedError, ProgrammingError

_isolation_levels = {
    "READ UNCOMMITTED": lib.SQL_TRANSACTION_READ_UNCOMMITTED,
    "READ COMMITTED": lib.SQL_TRANSACTION_READ_COMMITTED,
    "REPEATABLE READ": lib.SQL_TRANSACTION_REPEATABLE_READ,
    "SERIALIZABLE": lib.SQL_TRANSACTION_SERIALIZABLE,
}


class Connection:
    """A connection to a database, see vsql.connect.

    Each connection has its own environment, so connections can be used at the
    same time from different threads (see "Threads" in libvsql.h), but a
    connection (and its cursors) can only be used by one thread at a time.
    """

    def __init__(self, database, autocommit=False, isolation_level="READ COMMITTED",
                 read_only=False):
        level = isolation_level.upper() if isinstance(isolation_level, str) else None
        try:
            self._isolation_level = _isolation_levels[level]
        except KeyError:
            raise ProgrammingError(f"unknown isolation level: {isolation_level!r}") from None
        # A READ UNCOMMITTED transaction must be READ ONLY.
        self._access_mode = (lib.SQL_TRANSACTION_READ_ONLY
                             if read_only or level == "READ UNCOMMITTED"
                             else lib.SQL_TRANSACTION_READ_WRITE)
        self._autocommit = bool(autocommit)
        self._in_transaction = False
        self._cursors = weakref.WeakSet()
        self._dbc = None

        self._env = lib.alloc_handle(lib.SQL_HANDLE_ENV, lib.SQL_NULL_HANDLE)
        try:
            dbc = lib.alloc_handle(lib.SQL_HANDLE_DBC, self._env)
            try:
                lib.connect(dbc, os.fspath(database))
            except BaseException:
                lib.free_handle(lib.SQL_HANDLE_DBC, dbc)
                raise
        except BaseException:
            lib.free_handle(lib.SQL_HANDLE_ENV, self._env)
            raise
        self._dbc = dbc

        # Each statement is committed when it finishes, unless it's in a
        # transaction started by _begin.
        try:
            lib.set_autocommit(self._dbc, True)
        except BaseException:
            self.close()
            raise

    @property
    def closed(self):
        return self._dbc is None

    def _check(self):
        if self._dbc is None:
            raise InterfaceError("the connection is closed")

    @property
    def autocommit(self):
        """Whether each statement is committed when it finishes. Otherwise, a
        transaction is started by the first statement, and ended by commit or
        rollback. Turning it on commits the transaction."""

        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._check()
        if value and self._in_transaction:
            self.commit()
        self._autocommit = bool(value)

    @property
    def in_transaction(self):
        return self._in_transaction

    @property
    def file_version(self):
        """The (read, write) versions of the database file's format, which are
        (0, 0) for ":memory:". vsql can open a file when it knows the read
        version, and change it when it knows the write version."""

        self._check()
        return (lib.get_info(self._dbc, lib.SQL_VSQL_FILE_READ_VERSION, False),
                lib.get_info(self._dbc, lib.SQL_VSQL_FILE_WRITE_VERSION, False))

    def _begin(self):
        """Starts a transaction, unless autocommit is on or there already is
        one."""

        if self._autocommit or self._in_transaction:
            return
        lib.start_tran(self._dbc, self._access_mode, self._isolation_level)
        self._in_transaction = True

    def _check_transaction(self, handle_type, handle):
        """Checks whether the transaction is still active after an error. vsql
        rolls back the whole transaction for some errors (such as a 40001
        serialization failure), which closes the cursors, and then the next
        statement starts a new transaction."""

        if self._in_transaction and not lib.transaction_active(handle_type, handle):
            self._in_transaction = False
            for cursor in list(self._cursors):
                cursor._open = False

    def _end(self, commit):
        self._check()
        if not self._in_transaction:
            return

        # The cursors of the transaction are closed by it.
        for cursor in list(self._cursors):
            cursor._close_results()
        try:
            lib.end_tran(self._dbc, commit)
        finally:
            # A commit that fails may leave the transaction active, or roll
            # it back.
            self._in_transaction = lib.transaction_active(lib.SQL_HANDLE_DBC, self._dbc)

    def commit(self):
        """Commits the transaction, if there is one."""

        self._end(True)

    def rollback(self):
        """Rolls back the transaction, if there is one."""

        self._end(False)

    def rollback_to_savepoint(self, name):
        """Rolls back the transaction to a savepoint, which was made by
        executing SAVEPOINT. vsql doesn't allow ROLLBACK TO SAVEPOINT as a
        statement (like ROLLBACK), so this is how it's done. The cursors of the
        connection are closed. RELEASE SAVEPOINT can be executed as a
        statement.

        The name is an identifier, so a name that isn't quoted is upper case
        (such as "foo", which is FOO), and a quoted name (such as '"foo"') is
        what's inside the quotes."""

        self._check()
        for cursor in list(self._cursors):
            cursor._close_results()
        try:
            lib.end_savepoint(self._dbc, name, True)
        except DatabaseError:
            self._check_transaction(lib.SQL_HANDLE_DBC, self._dbc)
            raise

    def cursor(self, scrollable=False):
        """A new cursor. The rows of a query on a scrollable cursor can be
        fetched in any order (see Cursor.scroll), otherwise they can only be
        fetched once, in order."""

        self._check()
        cursor = Cursor(self, scrollable)
        self._cursors.add(cursor)

        return cursor

    def execute(self, operation, parameters=None):
        """Executes a statement with a new cursor, which is returned."""

        return self.cursor().execute(operation, parameters)

    def executemany(self, operation, seq_of_parameters):
        """Executes a statement for each sequence of parameters with a new
        cursor, which is returned."""

        return self.cursor().executemany(operation, seq_of_parameters)

    def close(self):
        """Closes the connection and its cursors. A transaction that hasn't been
        committed is rolled back. It does nothing if it's already closed."""

        if self._dbc is None:
            return

        try:
            for cursor in list(self._cursors):
                cursor.close()
            if self._in_transaction:
                self._in_transaction = False
                lib.end_tran(self._dbc, False)
            lib.disconnect(self._dbc)
        finally:
            dbc, self._dbc = self._dbc, None
            try:
                lib.free_handle(lib.SQL_HANDLE_DBC, dbc)
            finally:
                lib.free_handle(lib.SQL_HANDLE_ENV, self._env)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Commits the transaction (or rolls it back if there was an exception),
        and closes the connection."""

        try:
            if not self.closed:
                self._end(exc_type is None)
        finally:
            self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class Cursor:
    """A cursor executes statements and reads the rows of a query, see
    Connection.cursor.

    Each cursor has its own statement handle. The statement of the last
    execute with parameters stays prepared, so executing it again (such as with
    executemany) doesn't prepare it again.

    rownumber is the index of the next row of the query (starting at 0), or
    None if the last statement wasn't a query.
    """

    arraysize = 1
    lastrowid = None

    def __init__(self, connection, scrollable=False):
        self.connection = connection
        self.scrollable = bool(scrollable)
        self.description = None
        self.rowcount = -1
        self.rownumber = None
        self._stmt = None
        self._prepared = None
        self._open = False
        self._kinds = b""
        self._converters = None
        self._closed = False

    def _check(self):
        if self._closed:
            raise InterfaceError("the cursor is closed")
        self.connection._check()

    def _close_results(self):
        """Closes the rows of the last query, if they're still open."""

        if self._open:
            self._open = False
            lib.close_cursor(self._stmt)

    def execute(self, operation, parameters=None):
        """Executes a statement, with a "?" for each of the parameters. It
        returns the cursor, so a query can be fetched from it."""

        self._check()
        self._close_results()
        self.description = None
        self.rowcount = -1
        self.rownumber = None
        params = _convert.to_params(parameters)

        self.connection._begin()
        if self._stmt is None:
            self._stmt = lib.alloc_handle(lib.SQL_HANDLE_STMT, self.connection._dbc)
            if self.scrollable:
                lib.set_scrollable(self._stmt, True)

        try:
            if params or operation == self._prepared:
                if operation != self._prepared:
                    self._prepared = None
                    lib.prepare(self._stmt, operation)
                    self._prepared = operation
                lib.execute(self._stmt, params)
            else:
                self._prepared = None
                lib.exec_direct(self._stmt, operation)
        except DatabaseError:
            self.connection._check_transaction(lib.SQL_HANDLE_STMT, self._stmt)
            raise

        n = lib.num_result_cols(self._stmt)
        if n == 0:
            self.rowcount = lib.row_count(self._stmt)
            return self

        self._open = True
        self.rownumber = 0
        description = []
        kinds = []
        converters = []
        for i in range(1, n + 1):
            name, data_type, size, digits, nullable = lib.describe_col(self._stmt, i)
            type_name, kind, converter = _convert.types.get(data_type, ("", "s", None))
            leading = None
            if data_type in _convert.interval_types:
                leading = lib.col_attribute(self._stmt, i,
                                            lib.SQL_DESC_DATETIME_INTERVAL_PRECISION)
            description.append(_column(name, type_name, size, digits, nullable, leading))
            kinds.append(kind)
            converters.append(converter)
        self.description = tuple(description)
        self._kinds = "".join(kinds).encode()
        self._converters = converters if any(converters) else None

        return self

    def executemany(self, operation, seq_of_parameters):
        """Executes a statement for each sequence of parameters. rowcount is
        the total of the rows affected. It returns the cursor."""

        self._check()
        total = 0
        for parameters in seq_of_parameters:
            self.execute(operation, parameters)
            if self._open:
                raise ProgrammingError("executemany can't be used for a query")
            total += max(self.rowcount, 0)
        self.rowcount = total

        return self

    def fetchone(self):
        """The next row as a tuple, or None if there are no more."""

        self._check()
        if not self._open:
            if self.description is None:
                raise ProgrammingError("the last statement wasn't a query")
            return None

        try:
            if self.scrollable:
                # The rows stay open after the last one, since it can scroll
                # back.
                row = lib.fetch_row(self._stmt, self._kinds, lib.SQL_FETCH_ABSOLUTE,
                                    self.rownumber + 1)
            else:
                row = lib.fetch_row(self._stmt, self._kinds)
        except DatabaseError:
            self.connection._check_transaction(lib.SQL_HANDLE_STMT, self._stmt)
            raise
        if row is None:
            if not self.scrollable:
                self._close_results()
            return None
        self.rownumber += 1
        if self._converters is None:
            return row

        return tuple(v if f is None or v is None else f(v) for f, v in zip(self._converters, row))

    def fetchmany(self, size=None):
        """The next rows (arraysize by default)."""

        if size is None:
            size = self.arraysize
        rows = []
        while len(rows) < size:
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)

        return rows

    def fetchall(self):
        """The rest of the rows."""

        rows = []
        while True:
            row = self.fetchone()
            if row is None:
                return rows
            rows.append(row)

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration

        return row

    def scroll(self, value, mode="relative"):
        """Moves to another row of the query, so it's the next one fetched.
        With mode "relative" (the default), value is the number of rows to
        move (which can be negative), and with "absolute" it's the index of
        the row (starting at 0). It's an IndexError if the row isn't in the
        query, but it can move to just after the last row.

        A cursor that isn't scrollable (see Connection.cursor) can only move
        forward (NotSupportedError otherwise), by fetching the rows in between.
        """

        self._check()
        if self.rownumber is None:
            raise ProgrammingError("the last statement wasn't a query")
        if mode == "relative":
            target = self.rownumber + value
        elif mode == "absolute":
            target = value
        else:
            raise ProgrammingError(f"unknown scroll mode: {mode!r}")
        if target < 0:
            raise IndexError(f"row {target} is before the first row")

        if self.scrollable:
            # The row before the target must exist (target - 1, which is the
            # row numbered target from 1).
            if target > 0 and lib.fetch_row(self._stmt, b"", lib.SQL_FETCH_ABSOLUTE,
                                            target) is None:
                raise IndexError(f"row {target} is after the last row")
            self.rownumber = target
            return

        if target < self.rownumber:
            raise NotSupportedError("a cursor can only scroll back if it's scrollable, "
                                    "see Connection.cursor")
        while self.rownumber < target:
            if self.fetchone() is None:
                raise IndexError(f"row {target} is after the last row")

    def setinputsizes(self, sizes):
        pass

    def setoutputsize(self, size, column=None):
        pass

    def close(self):
        """Closes the cursor. It does nothing if it's already closed."""

        if self._closed:
            return
        self._closed = True
        if self._stmt is None or self.connection.closed:
            return

        try:
            self._close_results()
        finally:
            stmt, self._stmt = self._stmt, None
            lib.free_handle(lib.SQL_HANDLE_STMT, stmt)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


# The PEP 249 description of a column: (name, type_code, display_size,
# internal_size, precision, scale, null_ok). The internal_size is the
# ColumnSize from SQLDescribeCol.
def _column(name, type_name, size, digits, nullable, leading=None):
    display_size = precision = scale = None
    if type_name in ("CHARACTER", "CHARACTER VARYING", "CHARACTER LARGE OBJECT"):
        # The size is in octets, with 4 for each character.
        display_size = size // 4 if size > 0 else None
    elif type_name in ("DECIMAL", "NUMERIC"):
        precision, scale = size, digits
    elif type_name == "DECFLOAT":
        precision = size
    elif type_name.startswith(("TIME", "DATE")):
        # The size is the length of the datetime in positions, and the scale
        # is the fractional seconds precision (a DATE has none).
        display_size = size
        if type_name != "DATE":
            scale = digits
    elif leading is not None:
        # An interval's precision is the leading field precision, and its scale
        # is the fractional seconds precision (if it ends with SECOND). The
        # size is its length in positions.
        precision = leading
        display_size = size
        if type_name.endswith("SECOND"):
            scale = digits

    return (name, type_name, display_size, size, precision, scale,
            nullable != lib.SQL_NO_NULLS)


def build_info():
    """The (major, minor, patch) version of vsql, and a dict of how it was
    built, from SQLGetInfo (which needs a connection, so they're read from a
    ":memory:" database)."""

    fields = {
        "version": lib.SQL_VSQL_BUILD_VERSION,
        "commit": lib.SQL_VSQL_BUILD_COMMIT,
        "date": lib.SQL_VSQL_BUILD_DATE,
        "v_version": lib.SQL_VSQL_BUILD_V_VERSION,
        "v_commit": lib.SQL_VSQL_BUILD_V_COMMIT,
        "c_compiler": lib.SQL_VSQL_BUILD_C_COMPILER,
        "operating_system": lib.SQL_VSQL_OPERATING_SYSTEM,
        "machine": lib.SQL_VSQL_MACHINE,
    }
    conn = Connection(":memory:")
    try:
        info = {name: lib.get_info(conn._dbc, code, True) for name, code in fields.items()}
        # SQL_DBMS_VERSION is "01.07.0058", even if it isn't a release.
        dbms_version = lib.get_info(conn._dbc, lib.SQL_DBMS_VERSION, True)
    finally:
        conn.close()

    return tuple(int(part) for part in dbms_version.split(".")), info

