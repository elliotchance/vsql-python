# Converting parameters to the values that _libvsql.execute binds, and the
# values of columns to Python.

import datetime
import decimal
import re

from . import _libvsql as lib
from ._errors import DataError, Error, NotSupportedError, ProgrammingError

_BIGINT_MIN, _BIGINT_MAX = -(2**63), 2**63 - 1


class YearMonthInterval:
    """A year-month interval (such as INTERVAL '1-02' YEAR TO MONTH), which
    datetime.timedelta can't be, since the length of a month varies. It's a
    number of months, which is also years and months (that have the same
    sign):

        >>> YearMonthInterval(years=1, months=2)
        YearMonthInterval(years=1, months=2)
        >>> YearMonthInterval(months=-14).total_months
        -14
    """

    __slots__ = ("_months",)

    def __init__(self, years=0, months=0):
        self._months = int(years) * 12 + int(months)

    @property
    def total_months(self):
        return self._months

    @property
    def years(self):
        years = abs(self._months) // 12

        return -years if self._months < 0 else years

    @property
    def months(self):
        return self._months - self.years * 12

    def __eq__(self, other):
        if not isinstance(other, YearMonthInterval):
            return NotImplemented

        return self._months == other._months

    def __hash__(self):
        return hash((YearMonthInterval, self._months))

    def __neg__(self):
        return YearMonthInterval(months=-self._months)

    def __repr__(self):
        return f"YearMonthInterval(years={self.years}, months={self.months})"

    def __str__(self):
        sign = "-" if self._months < 0 else ""

        return f"{sign}{abs(self.years)}-{abs(self.months):02d}"


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
        # A CHARACTER VARYING as long as the string (which can't be 0). A lone
        # surrogate (such as from surrogateescape) can't be UTF-8.
        try:
            encoded = value.encode()
        except UnicodeEncodeError as e:
            raise DataError(f"{value!r} isn't valid Unicode: {e.reason}") from None
        return (lib.SQL_CHAR, lib.SQL_VARCHAR, max(len(value), 1), 0, encoded)

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
        # A BINARY VARYING as long as the value (which can't be 0, but the
        # value can be empty).
        value = bytes(value)
        return (lib.SQL_BINARY, lib.SQL_VARBINARY, max(len(value), 1), 0, value)

    if isinstance(value, datetime.timedelta):
        return _day_time_interval(value)

    if isinstance(value, YearMonthInterval):
        s = str(value)
        # The ColumnSize of an interval is its length in positions, which is
        # the sign, the leading field and "-MM".
        return (lib.SQL_CHAR, lib.SQL_INTERVAL_YEAR_TO_MONTH, len(s), 0, s.encode())

    raise ProgrammingError(f"unsupported type {type(value).__name__}")


def _numeric(s, scale):
    """A NUMERIC with the precision of the digits in s. vsql counts at least one
    digit before the decimal point (so 0.25 is NUMERIC(3, 2))."""

    integer = s.lstrip("-").split(".")[0].lstrip("0")

    return (lib.SQL_CHAR, lib.SQL_NUMERIC, max(len(integer), 1) + scale, scale, s.encode())


# The most digits of an interval's leading field.
_MAX_LEADING_PRECISION = 8


def _day_time_interval(value):
    """An INTERVAL DAY TO SECOND(6), with as many digits for the days as it
    needs."""

    us = value // datetime.timedelta(microseconds=1)
    sign = "-" if us < 0 else ""
    seconds, us = divmod(abs(us), 1_000_000)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    days, hour = divmod(hours, 24)
    if len(str(days)) > _MAX_LEADING_PRECISION:
        raise DataError(f"{value!r} has more than {_MAX_LEADING_PRECISION} digits of days")
    s = f"{sign}{days} {hour:02d}:{minute:02d}:{second:02d}.{us:06d}"

    # The ColumnSize of an interval is its length in positions (the sign, the
    # days, " HH:MM:SS" and the 6 fractional digits after a period).
    return (lib.SQL_CHAR, lib.SQL_INTERVAL_DAY_TO_SECOND, len(s), 6, s.encode())


def _datetime(parameter_type, s):
    """A DATE, TIME or TIMESTAMP, with microseconds (except a DATE)."""

    digits = 0 if parameter_type == lib.SQL_TYPE_DATE else 6

    return (lib.SQL_CHAR, parameter_type, len(s), digits, s.encode())


# The datetimes from SQLGetData are literals (the result of a CAST to
# CHARACTER VARYING), such as "TIMESTAMP '2022-01-02 15:04:05.123456+05:30'".
# There are at most 6 fractional digits.
_time = r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:([+-])(\d{2}):(\d{2}))?"
_date_re = re.compile(r"DATE '(\d{4})-(\d{2})-(\d{2})'")
_time_re = re.compile(r"TIME '" + _time + "'")
_timestamp_re = re.compile(r"TIMESTAMP '(\d{4})-(\d{2})-(\d{2}) " + _time + "'")


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


# An interval from SQLGetData is a literal, such as
# "INTERVAL '-1-02' YEAR(2) TO MONTH" or
# "INTERVAL '3 04:05:06.500000' DAY(2) TO SECOND(6)". The fields are YEAR TO
# MONTH, or between DAY and SECOND.
_interval_re = re.compile(r"INTERVAL '([+-]?)([0-9 :.-]+)' ([A-Z]+)(?:\([0-9, ]+\))?"
                          r"(?: TO ([A-Z]+)(?:\([0-9]+\))?)?")
_day_time_fields = ("DAY", "HOUR", "MINUTE", "SECOND")
_day_time_units = {
    "DAY": datetime.timedelta(days=1),
    "HOUR": datetime.timedelta(hours=1),
    "MINUTE": datetime.timedelta(minutes=1),
}


def parse_interval(s):
    """A YearMonthInterval for a year-month interval, otherwise a
    datetime.timedelta."""

    m = _interval_re.fullmatch(s)
    if m is None:
        raise DataError(f"invalid INTERVAL: {s!r}")
    sign, body, start, end = m.groups()
    end = end or start

    try:
        if start in ("YEAR", "MONTH"):
            if start == "YEAR" and end == "MONTH":
                years, months = body.split("-")
                value = YearMonthInterval(int(years), int(months))
            elif start == "YEAR":
                value = YearMonthInterval(years=int(body))
            else:
                value = YearMonthInterval(months=int(body))
            return -value if sign == "-" else value

        fields = _day_time_fields[_day_time_fields.index(start):_day_time_fields.index(end) + 1]
        parts = re.split(r"[ :]", body)
        if len(parts) != len(fields):
            raise ValueError
        value = datetime.timedelta()
        for field, part in zip(fields, parts):
            if field == "SECOND":
                seconds, _, fraction = part.partition(".")
                value += datetime.timedelta(seconds=int(seconds),
                                            microseconds=int(fraction.ljust(6, "0")[:6] or 0))
            else:
                value += int(part) * _day_time_units[field]
    except ValueError:
        raise DataError(f"invalid INTERVAL: {s!r}") from None

    return -value if sign == "-" else value


_booleans = {"TRUE": True, "FALSE": False}


def parse_boolean(s):
    try:
        return _booleans[s]
    except KeyError:
        raise DataError(f"invalid BOOLEAN: {s!r}") from None


# For each data type (from SQLDescribeCol): its name, how _libvsql.fetch_row
# reads it ('i' as an int, 'd' or 'r' as a float, 'b' as bytes, 's' as a str) and the
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
    lib.SQL_DECFLOAT: ("DECFLOAT", "s", decimal.Decimal),
    lib.SQL_CHAR: ("CHARACTER", "s", None),
    lib.SQL_VARCHAR: ("CHARACTER VARYING", "s", None),
    lib.SQL_CLOB: ("CHARACTER LARGE OBJECT", "s", None),
    lib.SQL_BINARY: ("BINARY", "b", None),
    lib.SQL_VARBINARY: ("BINARY VARYING", "b", None),
    lib.SQL_BLOB: ("BINARY LARGE OBJECT", "b", None),
    lib.SQL_BOOLEAN: ("BOOLEAN", "s", parse_boolean),
    lib.SQL_TYPE_DATE: ("DATE", "s", parse_date),
    lib.SQL_TYPE_TIME: ("TIME", "s", parse_time),
    lib.SQL_TYPE_TIME_WITH_TIMEZONE: ("TIME WITH TIME ZONE", "s", parse_time),
    lib.SQL_TYPE_TIMESTAMP: ("TIMESTAMP", "s", parse_timestamp),
    lib.SQL_TYPE_TIMESTAMP_WITH_TIMEZONE: ("TIMESTAMP WITH TIME ZONE", "s", parse_timestamp),
}

# The interval types, such as "INTERVAL YEAR TO MONTH".
interval_types = {
    lib.SQL_INTERVAL_YEAR: "YEAR",
    lib.SQL_INTERVAL_MONTH: "MONTH",
    lib.SQL_INTERVAL_DAY: "DAY",
    lib.SQL_INTERVAL_HOUR: "HOUR",
    lib.SQL_INTERVAL_MINUTE: "MINUTE",
    lib.SQL_INTERVAL_SECOND: "SECOND",
    lib.SQL_INTERVAL_YEAR_TO_MONTH: "YEAR TO MONTH",
    lib.SQL_INTERVAL_DAY_TO_HOUR: "DAY TO HOUR",
    lib.SQL_INTERVAL_DAY_TO_MINUTE: "DAY TO MINUTE",
    lib.SQL_INTERVAL_DAY_TO_SECOND: "DAY TO SECOND",
    lib.SQL_INTERVAL_HOUR_TO_MINUTE: "HOUR TO MINUTE",
    lib.SQL_INTERVAL_HOUR_TO_SECOND: "HOUR TO SECOND",
    lib.SQL_INTERVAL_MINUTE_TO_SECOND: "MINUTE TO SECOND",
}
for _code, _fields in interval_types.items():
    types[_code] = ("INTERVAL " + _fields, "s", parse_interval)
