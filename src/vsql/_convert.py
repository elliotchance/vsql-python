# Converting parameters to the values that _libvsql.execute binds, and the
# values of columns to Python.

import datetime
import decimal
import re

from . import _libvsql as lib
from ._errors import DataError, Error, NotSupportedError, ProgrammingError

_BIGINT_MIN, _BIGINT_MAX = -(2**63), 2**63 - 1


def to_params(parameters):
    """The parameters for _libvsql.execute, from a sequence of values."""

    if parameters is None:
        return ()
    if isinstance(parameters, dict) or hasattr(parameters, "keys"):
        raise NotSupportedError("named parameters are not supported, use ? with a sequence")
    if isinstance(parameters, (str, bytes)):
        raise ProgrammingError("parameters must be a sequence, not a string")

    params = []
    for i, value in enumerate(parameters, 1):
        try:
            params.append(to_param(value))
        except Error as e:
            e.message = f"parameter {i}: {e.message}"
            e.args = (e.message,)
            raise

    return tuple(params)


def to_param(value):
    """A tuple of (value_type, parameter_type, column_size, decimal_digits,
    value) for _libvsql.execute. The value is sent as an SQL type, which is cast
    to the type it's used as."""

    # The type doesn't matter for a NULL, since it's cast to the type it's
    # used as.
    if value is None:
        return (lib.SQL_CHAR, lib.SQL_VARCHAR, 1, 0, None)

    # bool is an int, so it's first.
    if isinstance(value, bool):
        return (lib.SQL_CHAR, lib.SQL_BOOLEAN, 0, 0, b"TRUE" if value else b"FALSE")

    if isinstance(value, int):
        if _BIGINT_MIN <= value <= _BIGINT_MAX:
            return (lib.SQL_BIGINT, lib.SQL_BIGINT, 0, 0, value)
        return _numeric(str(value), 0)

    if isinstance(value, float):
        return (lib.SQL_DOUBLE, lib.SQL_DOUBLE, 0, 0, value)

    if isinstance(value, str):
        # A CHARACTER VARYING as long as the string (which can't be 0).
        return (lib.SQL_CHAR, lib.SQL_VARCHAR, max(len(value), 1), 0, value.encode())

    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            raise DataError(f"{value} isn't a finite decimal")
        exponent = value.as_tuple().exponent
        return _numeric(format(value, "f"), max(-exponent, 0))

    # datetime is a date, so it's first.
    if isinstance(value, datetime.datetime):
        s = value.isoformat(sep=" ", timespec="microseconds")
        if value.utcoffset() is None:
            return _datetime(lib.SQL_TYPE_TIMESTAMP, s)
        return _datetime(lib.SQL_TYPE_TIMESTAMP_WITH_TIMEZONE, s)

    if isinstance(value, datetime.date):
        return _datetime(lib.SQL_TYPE_DATE, value.isoformat())

    if isinstance(value, datetime.time):
        s = value.isoformat(timespec="microseconds")
        if value.utcoffset() is None:
            return _datetime(lib.SQL_TYPE_TIME, s)
        return _datetime(lib.SQL_TYPE_TIME_WITH_TIMEZONE, s)

    if isinstance(value, (bytes, bytearray, memoryview)):
        raise NotSupportedError("vsql doesn't have a binary type")

    raise ProgrammingError(f"unsupported type {type(value).__name__}")


def _numeric(s, scale):
    """A NUMERIC with the precision of the digits in s. vsql counts at least one
    digit before the decimal point (so 0.25 is NUMERIC(3, 2))."""

    integer = s.lstrip("-").split(".")[0].lstrip("0")

    return (lib.SQL_CHAR, lib.SQL_NUMERIC, max(len(integer), 1) + scale, scale, s.encode())


def _datetime(parameter_type, s):
    """A DATE, TIME or TIMESTAMP, with microseconds (except a DATE)."""

    digits = 0 if parameter_type == lib.SQL_TYPE_DATE else 6

    return (lib.SQL_CHAR, parameter_type, len(s), digits, s.encode())


# The datetimes from SQLGetData, such as "2022-01-02T15:04:05.123456+0530".
# There are at most 6 fractional digits.
_time = r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:([+-])(\d{2})(\d{2}))?"
_date_re = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_time_re = re.compile(_time)
_timestamp_re = re.compile(r"(\d{4})-(\d{2})-(\d{2})T" + _time)


def _match(regex, s, type_name):
    m = regex.fullmatch(s)
    if m is None:
        raise DataError(f"invalid {type_name}: {s!r}")

    return m.groups()


def _time_parts(hour, minute, second, fraction, sign, tz_hour, tz_minute):
    microsecond = int(fraction.ljust(6, "0")) if fraction else 0
    tzinfo = None
    if sign:
        offset = datetime.timedelta(hours=int(tz_hour), minutes=int(tz_minute))
        tzinfo = datetime.timezone(-offset if sign == "-" else offset)

    return int(hour), int(minute), int(second), microsecond, tzinfo


def parse_date(s):
    year, month, day = _match(_date_re, s, "DATE")

    return datetime.date(int(year), int(month), int(day))


def parse_time(s):
    hour, minute, second, microsecond, tzinfo = _time_parts(*_match(_time_re, s, "TIME"))

    return datetime.time(hour, minute, second, microsecond, tzinfo)


def parse_timestamp(s):
    year, month, day, *time = _match(_timestamp_re, s, "TIMESTAMP")
    hour, minute, second, microsecond, tzinfo = _time_parts(*time)

    return datetime.datetime(int(year), int(month), int(day), hour, minute, second, microsecond,
                             tzinfo)


_booleans = {"TRUE": True, "FALSE": False}


def parse_boolean(s):
    try:
        return _booleans[s]
    except KeyError:
        raise DataError(f"invalid BOOLEAN: {s!r}") from None


# For each data type (from SQLDescribeCol): its name, how _libvsql.fetch_row
# reads it ('i' as an int, 'd' or 'r' as a float, 's' as a str) and the
# function that converts that to its Python value (None if it doesn't need to
# be converted).
types = {
    lib.SQL_SMALLINT: ("SMALLINT", "i", None),
    lib.SQL_INTEGER: ("INTEGER", "i", None),
    lib.SQL_BIGINT: ("BIGINT", "i", None),
    lib.SQL_REAL: ("REAL", "r", None),
    lib.SQL_FLOAT: ("DOUBLE PRECISION", "d", None),
    lib.SQL_DOUBLE: ("DOUBLE PRECISION", "d", None),
    lib.SQL_DECIMAL: ("DECIMAL", "s", decimal.Decimal),
    lib.SQL_NUMERIC: ("NUMERIC", "s", decimal.Decimal),
    lib.SQL_CHAR: ("CHARACTER", "s", None),
    lib.SQL_VARCHAR: ("CHARACTER VARYING", "s", None),
    lib.SQL_BOOLEAN: ("BOOLEAN", "s", parse_boolean),
    lib.SQL_TYPE_DATE: ("DATE", "s", parse_date),
    lib.SQL_TYPE_TIME: ("TIME", "s", parse_time),
    lib.SQL_TYPE_TIME_WITH_TIMEZONE: ("TIME WITH TIME ZONE", "s", parse_time),
    lib.SQL_TYPE_TIMESTAMP: ("TIMESTAMP", "s", parse_timestamp),
    lib.SQL_TYPE_TIMESTAMP_WITH_TIMEZONE: ("TIMESTAMP WITH TIME ZONE", "s", parse_timestamp),
}
