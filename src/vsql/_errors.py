# The exceptions of PEP 249. An error from vsql is the exception for the class
# of its SQLSTATE (the first two characters), and has the SQLSTATE as its
# sqlstate attribute, and the other fields of its diagnostic record (such as
# the constraint that was violated) as the other attributes.

import builtins
import os
import sys
import warnings

_package = os.path.dirname(__file__)


class Warning(builtins.Warning):  # noqa: A001 (PEP 249 requires this name)
    """A warning from vsql (an SQLSTATE of class 01), such as 01003 (null value
    eliminated in set function). It's issued with warnings.warn (so it's
    printed once by default, and can be filtered or made an error), rather
    than raised, since the statement still completed.

    sqlstate is the SQLSTATE, and message is the message.
    """

    def __init__(self, message, sqlstate=None):
        super().__init__(message)
        self.message = message
        self.sqlstate = sqlstate

    def __str__(self):
        if self.sqlstate is None:
            return self.message

        return f"{self.message} (SQLSTATE {self.sqlstate})"


def warn(sqlstate, message):
    """Issues a Warning for a diagnostic record of a statement that completed
    with a warning. It's for the code that called the cursor (or connection)
    method."""

    # The first frame that isn't in this package (skip_file_prefixes is new in
    # Python 3.12).
    frame, level = sys._getframe(1), 2
    while frame is not None and frame.f_code.co_filename.startswith(_package):
        frame, level = frame.f_back, level + 1
    warnings.warn(Warning(message, sqlstate), stacklevel=level)


class Error(Exception):
    """The base class of every other error.

    sqlstate is the SQLSTATE of an error from vsql (such as "42P01" for an
    undefined table), or None for an error from the module itself.

    These are the object that the error is about, or None if it isn't about
    one (or the error isn't from vsql):

        constraint_catalog, constraint_schema, constraint_name
            the constraint that was violated (such as a PRIMARY KEY or CHECK)
        catalog_name, schema_name, table_name
            the table (such as the table of a constraint, or one that doesn't
            exist)
        column_name
            the column (such as one that doesn't exist)
        cursor_name
            the cursor (for an invalid cursor state)

    diagnostics is a tuple of (sqlstate, message) for each condition that vsql
    raised, starting with this one (or () if the error isn't from vsql).
    """

    constraint_catalog = constraint_schema = constraint_name = None
    catalog_name = schema_name = table_name = column_name = cursor_name = None
    diagnostics = ()

    def __init__(self, message, sqlstate=None, *, constraint_catalog=None,
                 constraint_schema=None, constraint_name=None, catalog_name=None,
                 schema_name=None, table_name=None, column_name=None, cursor_name=None,
                 diagnostics=()):
        super().__init__(message)
        self.message = message
        self.sqlstate = sqlstate
        self.constraint_catalog = constraint_catalog
        self.constraint_schema = constraint_schema
        self.constraint_name = constraint_name
        self.catalog_name = catalog_name
        self.schema_name = schema_name
        self.table_name = table_name
        self.column_name = column_name
        self.cursor_name = cursor_name
        self.diagnostics = tuple(diagnostics)

    def __str__(self):
        if self.sqlstate is None:
            return self.message

        return f"{self.message} (SQLSTATE {self.sqlstate})"


class InterfaceError(Error):
    """An error with the interface to vsql, rather than the database."""


class DatabaseError(Error):
    """An error from the database."""


class DataError(DatabaseError):
    """A problem with a value, such as one that's out of range."""


class OperationalError(DatabaseError):
    """An error with the database's operation, such as a transaction that
    couldn't be committed."""


class IntegrityError(DatabaseError):
    """A constraint, such as NOT NULL or a unique key, was violated."""


class InternalError(DatabaseError):
    """The database had an internal error."""


class ProgrammingError(DatabaseError):
    """A problem with the SQL, such as a syntax error or a table that doesn't
    exist."""


class NotSupportedError(DatabaseError):
    """A feature that vsql doesn't support."""


# The exception for each SQLSTATE class. A class that isn't here is a
# DatabaseError.
_classes = {
    "07": ProgrammingError,  # dynamic SQL error
    "08": OperationalError,  # connection exception
    "0A": NotSupportedError,  # feature not supported
    "0B": OperationalError,  # invalid transaction initiation
    "21": ProgrammingError,  # cardinality violation
    "22": DataError,  # data exception
    "23": IntegrityError,  # integrity constraint violation
    "24": ProgrammingError,  # invalid cursor state
    "25": OperationalError,  # invalid transaction state
    "26": ProgrammingError,  # invalid SQL statement name
    "2B": ProgrammingError,  # dependent privilege descriptors still exist
    "2D": OperationalError,  # invalid transaction termination
    "34": ProgrammingError,  # invalid cursor name
    "3B": ProgrammingError,  # savepoint exception
    "3D": ProgrammingError,  # invalid catalog name
    "3F": ProgrammingError,  # invalid schema name
    "40": OperationalError,  # transaction rollback
    "42": ProgrammingError,  # syntax error or access rule violation
    "54": OperationalError,  # program limit exceeded
    "58": OperationalError,  # system error
    "HY": InterfaceError,  # CLI-specific condition
    "XX": InternalError,  # internal error
}

# SQLSTATEs that aren't the exception for their class.
_states = {
    "HY000": DatabaseError,  # general error
    "HY001": OperationalError,  # memory allocation error
    "HYC00": NotSupportedError,  # optional feature not implemented
}


def error(sqlstate, message, **fields):
    """The exception for an error from vsql, with the fields of its diagnostic
    record (such as constraint_name) that aren't empty."""

    cls = _states.get(sqlstate) or _classes.get(sqlstate[:2], DatabaseError)

    return cls(message, sqlstate, **fields)
