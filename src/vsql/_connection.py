import os
import weakref

from . import _convert
from . import _libvsql as lib
from ._errors import InterfaceError, ProgrammingError

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
        try:
            self._isolation_level = _isolation_levels[isolation_level]
        except KeyError:
            raise ProgrammingError(f"unknown isolation level: {isolation_level!r}") from None
        self._access_mode = (lib.SQL_TRANSACTION_READ_ONLY if read_only
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

    def _begin(self):
        """Starts a transaction, unless autocommit is on or there already is
        one."""

        if self._autocommit or self._in_transaction:
            return
        lib.start_tran(self._dbc, self._access_mode, self._isolation_level)
        self._in_transaction = True

    def _end(self, commit):
        self._check()
        if not self._in_transaction:
            return

        # The cursors of the transaction are closed by it.
        for cursor in list(self._cursors):
            cursor._close_results()
        self._in_transaction = False
        lib.end_tran(self._dbc, commit)

    def commit(self):
        """Commits the transaction, if there is one."""

        self._end(True)

    def rollback(self):
        """Rolls back the transaction, if there is one."""

        self._end(False)

    def cursor(self):
        self._check()
        cursor = Cursor(self)
        self._cursors.add(cursor)

        return cursor

    def execute(self, operation, parameters=None):
        """Executes a statement with a new cursor, which is returned."""

        return self.cursor().execute(operation, parameters)

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
    """

    arraysize = 1
    lastrowid = None

    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rowcount = -1
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
        params = _convert.to_params(parameters)

        self.connection._begin()
        if self._stmt is None:
            self._stmt = lib.alloc_handle(lib.SQL_HANDLE_STMT, self.connection._dbc)

        if params or operation == self._prepared:
            if operation != self._prepared:
                self._prepared = None
                lib.prepare(self._stmt, operation)
                self._prepared = operation
            lib.execute(self._stmt, params)
        else:
            self._prepared = None
            lib.exec_direct(self._stmt, operation)

        n = lib.num_result_cols(self._stmt)
        if n == 0:
            self.rowcount = lib.row_count(self._stmt)
            return self

        self._open = True
        description = []
        kinds = []
        converters = []
        for i in range(1, n + 1):
            name, data_type, size, digits, nullable = lib.describe_col(self._stmt, i)
            type_name, kind, converter = _convert.types.get(data_type, ("", "s", None))
            description.append(_column(name, type_name, size, digits, nullable))
            kinds.append(kind)
            converters.append(converter)
        self.description = tuple(description)
        self._kinds = "".join(kinds).encode()
        self._converters = converters if any(converters) else None

        return self

    def executemany(self, operation, seq_of_parameters):
        """Executes a statement for each sequence of parameters. rowcount is
        the total of the rows affected."""

        total = 0
        for parameters in seq_of_parameters:
            self.execute(operation, parameters)
            if self._open:
                raise ProgrammingError("executemany can't be used for a query")
            total += max(self.rowcount, 0)
        self.rowcount = total

    def fetchone(self):
        """The next row as a tuple, or None if there are no more."""

        self._check()
        if not self._open:
            if self.description is None:
                raise ProgrammingError("the last statement wasn't a query")
            return None

        row = lib.fetch_row(self._stmt, self._kinds)
        if row is None:
            self._close_results()
            return None
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
# internal_size, precision, scale, null_ok).
def _column(name, type_name, size, digits, nullable):
    display_size = precision = scale = None
    if type_name in ("CHARACTER", "CHARACTER VARYING"):
        # The size is in octets, with 4 for each character.
        display_size = size // 4 if size > 0 else None
    elif type_name in ("DECIMAL", "NUMERIC"):
        precision, scale = size, digits

    return (name, type_name, display_size, size, precision, scale,
            nullable != lib.SQL_NO_NULLS)

