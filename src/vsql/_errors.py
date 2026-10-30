# The exceptions of PEP 249. An error from vsql is the exception for the class
# of its SQLSTATE (the first two characters), and has the SQLSTATE as its
# sqlstate attribute.


class Warning(Exception):  # noqa: A001 (PEP 249 requires this name)
    """An important warning, such as data truncation."""


class Error(Exception):
    """The base class of every other error.

    sqlstate is the SQLSTATE of an error from vsql (such as "42P01" for an
    undefined table), or None for an error from the module itself.
    """

    def __init__(self, message, sqlstate=None):
        super().__init__(message)
        self.message = message
        self.sqlstate = sqlstate

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


def error(sqlstate, message):
    """The exception for an error from vsql."""

    cls = _states.get(sqlstate) or _classes.get(sqlstate[:2], DatabaseError)

    return cls(message, sqlstate)
