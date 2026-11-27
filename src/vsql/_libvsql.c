// _libvsql.c is the vsql._libvsql extension module, the only code that calls
// libvsql (see libvsql.h), which is compiled into the module from the
// amalgamation by setup.py. Each function is a thin wrapper around a libvsql
// routine, with handles as ints. Everything else (such as converting values)
// is in Python.
//
// libvsql can be called from any thread, so the GIL is released while a
// routine runs, and the module doesn't need the GIL on a free-threaded build
// (Py_mod_gil). libvsql runs the routines of an environment one at a time,
// so a Connection (which has its own environment) used from two threads can't
// corrupt libvsql, even though that isn't supported (threadsafety is 1).
// error_factory is only set once, when the vsql package is imported.
//
// When a routine fails, the exception is made by the function given to
// set_error_factory, from the SQLSTATE, message and other fields (such as the
// constraint that was violated) of the first diagnostic record, and every
// record (diagnostics). When a statement completes with a warning
// (SQL_SUCCESS_WITH_INFO), the function given to set_warning_handler is called
// with the SQLSTATE and message of each record.

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdlib.h>
#include <string.h>

#include "libvsql.h"

// Py_NewRef is new in Python 3.10.
#if PY_VERSION_HEX < 0x030A0000
static inline PyObject *Py_NewRef(PyObject *o) {
  Py_INCREF(o);
  return o;
}
#endif

static PyObject *error_factory = NULL;
static PyObject *warning_handler = NULL;

// read_records returns a list of (sqlstate, message) for each diagnostic
// record of the last routine called with the handle, or NULL with an
// exception.
static PyObject *read_records(SQLSMALLINT handle_type, SQLINTEGER handle) {
  PyObject *records = PyList_New(0);
  if (records == NULL) {
    return NULL;
  }

  for (SQLSMALLINT i = 1;; i++) {
    SQLCHAR sqlstate[6];
    SQLSMALLINT size = 512, length = 0;
    SQLCHAR *message = NULL;
    SQLRETURN rc;
    for (;;) {
      message = PyMem_Malloc(size);
      if (message == NULL) {
        Py_DECREF(records);
        return PyErr_NoMemory();
      }
      Py_BEGIN_ALLOW_THREADS
      rc = SQLGetDiagRec(handle_type, handle, i, sqlstate, NULL, message, size, &length);
      Py_END_ALLOW_THREADS
      // The message was truncated.
      if ((rc == SQL_SUCCESS || rc == SQL_SUCCESS_WITH_INFO) && length >= size) {
        size = length + 1;
        PyMem_Free(message);
        continue;
      }
      break;
    }
    if (rc != SQL_SUCCESS && rc != SQL_SUCCESS_WITH_INFO) {
      PyMem_Free(message);
      return records;
    }

    PyObject *record = Py_BuildValue("(sN)", (const char *)sqlstate,
                                     PyUnicode_DecodeUTF8((const char *)message,
                                                          (Py_ssize_t)length, "replace"));
    PyMem_Free(message);
    if (record == NULL || PyList_Append(records, record) < 0) {
      Py_XDECREF(record);
      Py_DECREF(records);
      return NULL;
    }
    Py_DECREF(record);
  }
}

// warn calls the warning handler for each diagnostic record if the routine
// completed with a warning. It returns -1 if the handler raised an exception
// (such as when warnings are errors), otherwise 0.
static int warn(SQLRETURN rc, SQLSMALLINT handle_type, SQLINTEGER handle) {
  if (rc != SQL_SUCCESS_WITH_INFO || warning_handler == NULL) {
    return 0;
  }

  PyObject *records = read_records(handle_type, handle);
  if (records == NULL) {
    return -1;
  }
  for (Py_ssize_t i = 0; i < PyList_GET_SIZE(records); i++) {
    PyObject *result = PyObject_CallObject(warning_handler, PyList_GET_ITEM(records, i));
    if (result == NULL) {
      Py_DECREF(records);
      return -1;
    }
    Py_DECREF(result);
  }
  Py_DECREF(records);

  return 0;
}

// raise_error raises the exception for an SQLSTATE and message, with the other
// fields of its diagnostic record (a dict, or NULL).
static void raise_error_fields(const char *sqlstate, const char *message, Py_ssize_t length,
                               PyObject *fields) {
  if (length < 0) {
    length = (Py_ssize_t)strlen(message);
  }

  if (error_factory == NULL) {
    // The message ends with a NUL. PyErr_Format only has %.*s from Python 3.12.
    PyErr_Format(PyExc_RuntimeError, "vsql: %s (SQLSTATE %s)", message, sqlstate);
    return;
  }
  PyObject *args = Py_BuildValue("(ss#)", sqlstate, message, length);
  if (args == NULL) {
    return;
  }
  PyObject *err = PyObject_Call(error_factory, args, fields);
  Py_DECREF(args);
  if (err != NULL) {
    PyErr_SetObject((PyObject *)Py_TYPE(err), err);
    Py_DECREF(err);
  }
}

static void raise_error(const char *sqlstate, const char *message, Py_ssize_t length) {
  raise_error_fields(sqlstate, message, length, NULL);
}

// The string fields of a diagnostic record that are given to the error factory
// (as keyword arguments) when they aren't empty.
static const struct {
  const char *name;
  SQLSMALLINT field;
} diag_fields[] = {
    {"constraint_catalog", SQL_DIAG_CONSTRAINT_CATALOG},
    {"constraint_schema", SQL_DIAG_CONSTRAINT_SCHEMA},
    {"constraint_name", SQL_DIAG_CONSTRAINT_NAME},
    {"catalog_name", SQL_DIAG_CATALOG_NAME},
    {"schema_name", SQL_DIAG_SCHEMA_NAME},
    {"table_name", SQL_DIAG_TABLE_NAME},
    {"column_name", SQL_DIAG_COLUMN_NAME},
    {"cursor_name", SQL_DIAG_CURSOR_NAME},
    {NULL, 0},
};

// get_diag_fields returns a dict of the non-empty diag_fields of the first
// diagnostic record, or NULL with an exception.
static PyObject *get_diag_fields(SQLSMALLINT handle_type, SQLINTEGER handle) {
  PyObject *fields = PyDict_New();
  if (fields == NULL) {
    return NULL;
  }

  for (int i = 0; diag_fields[i].name != NULL; i++) {
    // An identifier is at most 128 characters (of up to 4 octets each).
    SQLCHAR value[516];
    SQLSMALLINT length = 0;
    SQLRETURN rc;
    Py_BEGIN_ALLOW_THREADS
    rc = SQLGetDiagField(handle_type, handle, 1, diag_fields[i].field, value, sizeof(value),
                         &length);
    Py_END_ALLOW_THREADS
    if ((rc != SQL_SUCCESS && rc != SQL_SUCCESS_WITH_INFO) || value[0] == 0) {
      continue;
    }

    PyObject *s = PyUnicode_DecodeUTF8((const char *)value, (Py_ssize_t)strlen((char *)value),
                                       "replace");
    if (s == NULL || PyDict_SetItemString(fields, diag_fields[i].name, s) < 0) {
      Py_XDECREF(s);
      Py_DECREF(fields);
      return NULL;
    }
    Py_DECREF(s);
  }

  return fields;
}

// check returns 0 for a return code that isn't an error (SQL_NO_DATA isn't),
// otherwise it raises the error from the diagnostics of the handle and returns
// -1.
static int check(SQLRETURN rc, SQLSMALLINT handle_type, SQLINTEGER handle) {
  switch (rc) {
  case SQL_SUCCESS:
  case SQL_SUCCESS_WITH_INFO:
  case SQL_NO_DATA:
    return 0;
  }

  SQLCHAR sqlstate[6];
  SQLSMALLINT size = 512, length = 0;
  SQLCHAR *message = PyMem_Malloc(size);
  for (;;) {
    if (message == NULL) {
      PyErr_NoMemory();
      return -1;
    }

    // An invalid handle (SQL_INVALID_HANDLE) doesn't have diagnostics.
    SQLRETURN diag_rc;
    Py_BEGIN_ALLOW_THREADS
    diag_rc = SQLGetDiagRec(handle_type, handle, 1, sqlstate, NULL, message, size, &length);
    Py_END_ALLOW_THREADS
    if (diag_rc != SQL_SUCCESS && diag_rc != SQL_SUCCESS_WITH_INFO) {
      PyMem_Free(message);
      raise_error("HY000", rc == SQL_INVALID_HANDLE ? "invalid handle" : "unknown error", -1);
      return -1;
    }

    // The message was truncated.
    if (length >= size) {
      size = length + 1;
      PyMem_Free(message);
      message = PyMem_Malloc(size);
      continue;
    }

    PyObject *fields = get_diag_fields(handle_type, handle);
    PyObject *records = fields == NULL ? NULL : read_records(handle_type, handle);
    if (records != NULL) {
      PyObject *diagnostics = PyList_AsTuple(records);
      if (diagnostics != NULL && PyDict_SetItemString(fields, "diagnostics", diagnostics) == 0) {
        raise_error_fields((const char *)sqlstate, (const char *)message, length, fields);
      }
      Py_XDECREF(diagnostics);
      Py_DECREF(records);
    }
    Py_XDECREF(fields);
    PyMem_Free(message);
    return -1;
  }
}

// CALL runs a libvsql routine without the GIL, and returns NULL from the
// function if it fails.
#define CALL(handle_type, handle, routine)                                     \
  do {                                                                         \
    SQLRETURN rc_;                                                             \
    Py_BEGIN_ALLOW_THREADS                                                     \
    rc_ = (routine);                                                           \
    Py_END_ALLOW_THREADS                                                       \
    if (check(rc_, (handle_type), (handle)) < 0) {                             \
      return NULL;                                                             \
    }                                                                          \
  } while (0)

// CALL_WARN is CALL for a routine that can complete with a warning, which is
// given to the warning handler.
#define CALL_WARN(handle_type, handle, routine)                                \
  do {                                                                         \
    SQLRETURN rc_;                                                             \
    Py_BEGIN_ALLOW_THREADS                                                     \
    rc_ = (routine);                                                           \
    Py_END_ALLOW_THREADS                                                       \
    if (check(rc_, (handle_type), (handle)) < 0 ||                             \
        warn(rc_, (handle_type), (handle)) < 0) {                              \
      return NULL;                                                             \
    }                                                                          \
  } while (0)

static PyObject *set_warning_handler(PyObject *self, PyObject *handler) {
  Py_XSETREF(warning_handler, Py_NewRef(handler));
  Py_RETURN_NONE;
}

static PyObject *set_error_factory(PyObject *self, PyObject *factory) {
  Py_XSETREF(error_factory, Py_NewRef(factory));
  Py_RETURN_NONE;
}

// alloc_handle(handle_type, input_handle) -> handle
static PyObject *alloc_handle(PyObject *self, PyObject *args) {
  int handle_type;
  SQLINTEGER input, output = 0;
  if (!PyArg_ParseTuple(args, "ii", &handle_type, &input)) {
    return NULL;
  }

  SQLRETURN rc;
  Py_BEGIN_ALLOW_THREADS
  rc = SQLAllocHandle(handle_type, input, &output);
  Py_END_ALLOW_THREADS

  // The diagnostics are on the input handle (a connection's environment, or a
  // statement's connection). An environment doesn't have one, so it would be
  // an unknown error (but libvsql only fails to allocate one without an
  // OutputHandle).
  if (check(rc, handle_type == SQL_HANDLE_STMT ? SQL_HANDLE_DBC : SQL_HANDLE_ENV, input) < 0) {
    return NULL;
  }

  return PyLong_FromLong(output);
}

// free_handle(handle_type, handle)
static PyObject *free_handle(PyObject *self, PyObject *args) {
  int handle_type;
  SQLINTEGER handle;
  if (!PyArg_ParseTuple(args, "ii", &handle_type, &handle)) {
    return NULL;
  }

  CALL(handle_type, handle, SQLFreeHandle(handle_type, handle));
  Py_RETURN_NONE;
}

// connect(dbc, name)
static PyObject *connect(PyObject *self, PyObject *args) {
  SQLINTEGER dbc;
  const char *name;
  Py_ssize_t length;
  if (!PyArg_ParseTuple(args, "is#", &dbc, &name, &length)) {
    return NULL;
  }

  CALL(SQL_HANDLE_DBC, dbc,
       SQLConnect(dbc, (SQLCHAR *)name, (SQLSMALLINT)length, NULL, 0, NULL, 0));
  Py_RETURN_NONE;
}

// disconnect(dbc)
static PyObject *disconnect(PyObject *self, PyObject *args) {
  SQLINTEGER dbc;
  if (!PyArg_ParseTuple(args, "i", &dbc)) {
    return NULL;
  }

  CALL(SQL_HANDLE_DBC, dbc, SQLDisconnect(dbc));
  Py_RETURN_NONE;
}

// set_autocommit(dbc, on)
static PyObject *set_autocommit(PyObject *self, PyObject *args) {
  SQLINTEGER dbc;
  int on;
  if (!PyArg_ParseTuple(args, "ip", &dbc, &on)) {
    return NULL;
  }

  // libvsql reads the value after SQLSetConnectAttr returns, so it can't be
  // on the stack.
  static SQLINTEGER autocommit_on = SQL_AUTOCOMMIT_ON, autocommit_off = SQL_AUTOCOMMIT_OFF;
  CALL(SQL_HANDLE_DBC, dbc,
       SQLSetConnectAttr(dbc, SQL_ATTR_AUTOCOMMIT,
                         on ? (SQLPOINTER)&autocommit_on : (SQLPOINTER)&autocommit_off, 0));
  Py_RETURN_NONE;
}

// start_tran(dbc, access_mode, isolation_level)
static PyObject *start_tran(PyObject *self, PyObject *args) {
  SQLINTEGER dbc, access_mode, isolation_level;
  if (!PyArg_ParseTuple(args, "iii", &dbc, &access_mode, &isolation_level)) {
    return NULL;
  }

  CALL(SQL_HANDLE_DBC, dbc, SQLStartTran(SQL_HANDLE_DBC, dbc, access_mode, isolation_level));
  Py_RETURN_NONE;
}

// end_tran(dbc, commit)
static PyObject *end_tran(PyObject *self, PyObject *args) {
  SQLINTEGER dbc;
  int commit;
  if (!PyArg_ParseTuple(args, "ip", &dbc, &commit)) {
    return NULL;
  }

  CALL_WARN(SQL_HANDLE_DBC, dbc,
            SQLEndTran(SQL_HANDLE_DBC, dbc, commit ? SQL_COMMIT : SQL_ROLLBACK));
  Py_RETURN_NONE;
}

// get_info(dbc, info_type, is_string) -> str or int reads an SQLGetInfo
// InfoType, which is a string or an SQLSMALLINT.
static PyObject *get_info(PyObject *self, PyObject *args) {
  SQLINTEGER dbc;
  int info_type, is_string;
  if (!PyArg_ParseTuple(args, "iip", &dbc, &info_type, &is_string)) {
    return NULL;
  }

  if (!is_string) {
    SQLSMALLINT value = 0;
    CALL(SQL_HANDLE_DBC, dbc, SQLGetInfo(dbc, (SQLSMALLINT)info_type, &value, 0, NULL));
    return PyLong_FromLong(value);
  }

  SQLCHAR value[256];
  SQLSMALLINT length = 0;
  CALL(SQL_HANDLE_DBC, dbc,
       SQLGetInfo(dbc, (SQLSMALLINT)info_type, value, sizeof(value), &length));
  return PyUnicode_DecodeUTF8((const char *)value, (Py_ssize_t)strlen((char *)value), NULL);
}

// end_savepoint(dbc, name, rollback) rolls back to (or releases) a savepoint of
// the transaction, which is named by the SQL_ATTR_SAVEPOINT_NAME attribute.
static PyObject *end_savepoint(PyObject *self, PyObject *args) {
  SQLINTEGER dbc;
  const char *name;
  Py_ssize_t length;
  int rollback;
  if (!PyArg_ParseTuple(args, "is#p", &dbc, &name, &length, &rollback)) {
    return NULL;
  }

  // libvsql copies the name.
  CALL(SQL_HANDLE_DBC, dbc,
       SQLSetConnectAttr(dbc, SQL_ATTR_SAVEPOINT_NAME, (SQLPOINTER)name, (SQLINTEGER)length));
  CALL(SQL_HANDLE_DBC, dbc,
       SQLEndTran(SQL_HANDLE_DBC, dbc,
                  rollback ? SQL_SAVEPOINT_NAME_ROLLBACK : SQL_SAVEPOINT_NAME_RELEASE));
  Py_RETURN_NONE;
}

// transaction_active(handle_type, handle) -> bool is whether a transaction was
// active after the last routine called with the handle
// (SQL_DIAG_TRANSACTION_ACTIVE).
static PyObject *transaction_active(PyObject *self, PyObject *args) {
  int handle_type;
  SQLINTEGER handle, active = 0;
  if (!PyArg_ParseTuple(args, "ii", &handle_type, &handle)) {
    return NULL;
  }

  CALL(handle_type, handle,
       SQLGetDiagField((SQLSMALLINT)handle_type, handle, 0, SQL_DIAG_TRANSACTION_ACTIVE, &active,
                       0, NULL));
  return PyBool_FromLong(active);
}

// exec_direct(stmt, query)
static PyObject *exec_direct(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  const char *query;
  Py_ssize_t length;
  if (!PyArg_ParseTuple(args, "is#", &stmt, &query, &length)) {
    return NULL;
  }

  CALL_WARN(SQL_HANDLE_STMT, stmt, SQLExecDirect(stmt, (SQLCHAR *)query, (SQLINTEGER)length));
  Py_RETURN_NONE;
}

// prepare(stmt, query)
static PyObject *prepare(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  const char *query;
  Py_ssize_t length;
  if (!PyArg_ParseTuple(args, "is#", &stmt, &query, &length)) {
    return NULL;
  }

  CALL(SQL_HANDLE_STMT, stmt, SQLPrepare(stmt, (SQLCHAR *)query, (SQLINTEGER)length));
  Py_RETURN_NONE;
}

// A parameter's value, which must stay allocated until SQLExecute has read it.
typedef union {
  SQLBIGINT i;
  SQLDOUBLE d;
} number;

// execute(stmt, params) executes the prepared statement with the parameters,
// which are bound for this execution only. Each parameter is a tuple of
// (value_type, parameter_type, column_size, decimal_digits, value), where the
// value is None, an int (SQL_BIGINT), a float (SQL_DOUBLE), UTF-8 bytes
// (SQL_CHAR) or the octets of a binary string (SQL_BINARY).
static PyObject *execute(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  PyObject *params;
  if (!PyArg_ParseTuple(args, "iO!", &stmt, &PyTuple_Type, &params)) {
    return NULL;
  }

  Py_ssize_t n = PyTuple_GET_SIZE(params);
  number *numbers = PyMem_Calloc(n ? n : 1, sizeof(number));
  SQLINTEGER *inds = PyMem_Calloc(n ? n : 1, sizeof(SQLINTEGER));
  if (numbers == NULL || inds == NULL) {
    PyMem_Free(numbers);
    PyMem_Free(inds);
    return PyErr_NoMemory();
  }

  PyObject *result = NULL;
  SQLRETURN rc;
  for (Py_ssize_t i = 0; i < n; i++) {
    int value_type, parameter_type, decimal_digits;
    SQLINTEGER column_size;
    PyObject *value;
    if (!PyArg_ParseTuple(PyTuple_GET_ITEM(params, i), "iiiiO", &value_type, &parameter_type,
                          &column_size, &decimal_digits, &value)) {
      goto done;
    }

    SQLPOINTER pointer = NULL;
    SQLINTEGER buffer_length = 0;
    if (value == Py_None) {
      inds[i] = SQL_NULL_DATA;
    } else if (value_type == SQL_BIGINT) {
      numbers[i].i = PyLong_AsLongLong(value);
      if (numbers[i].i == -1 && PyErr_Occurred()) {
        goto done;
      }
      pointer = &numbers[i].i;
      buffer_length = sizeof(SQLBIGINT);
    } else if (value_type == SQL_DOUBLE) {
      numbers[i].d = PyFloat_AsDouble(value);
      if (numbers[i].d == -1.0 && PyErr_Occurred()) {
        goto done;
      }
      pointer = &numbers[i].d;
      buffer_length = sizeof(SQLDOUBLE);
    } else if (value_type == SQL_BINARY) {
      // The octets, which can be empty (unlike a string), are kept alive by
      // params until SQLExecute returns.
      char *s;
      Py_ssize_t length;
      if (PyBytes_AsStringAndSize(value, &s, &length) < 0) {
        goto done;
      }
      pointer = s;
      buffer_length = (SQLINTEGER)length;
      inds[i] = (SQLINTEGER)length;
    } else {
      // The bytes are kept alive by params until SQLExecute returns.
      char *s;
      Py_ssize_t length;
      if (PyBytes_AsStringAndSize(value, &s, &length) < 0) {
        goto done;
      }
      pointer = s;
      buffer_length = (SQLINTEGER)length + 1;
      // A length must be greater than zero, so an empty string (which is null
      // terminated, like all bytes) is SQL_NTS.
      inds[i] = length ? (SQLINTEGER)length : SQL_NTS;
    }

    Py_BEGIN_ALLOW_THREADS
    rc = SQLBindParameter(stmt, (SQLSMALLINT)(i + 1), SQL_PARAM_MODE_IN, (SQLSMALLINT)value_type,
                          (SQLSMALLINT)parameter_type, column_size, (SQLSMALLINT)decimal_digits,
                          pointer, buffer_length, &inds[i]);
    Py_END_ALLOW_THREADS
    if (check(rc, SQL_HANDLE_STMT, stmt) < 0) {
      goto done;
    }
  }

  Py_BEGIN_ALLOW_THREADS
  rc = SQLExecute(stmt);
  Py_END_ALLOW_THREADS
  if (check(rc, SQL_HANDLE_STMT, stmt) == 0 && warn(rc, SQL_HANDLE_STMT, stmt) == 0) {
    result = Py_NewRef(Py_None);
  }

done:
  // The values are freed, so they can't stay bound for the next execution.
  if (n > 0) {
    Py_BEGIN_ALLOW_THREADS
    SQLFreeStmt(stmt, SQL_RESET_PARAMS);
    Py_END_ALLOW_THREADS
  }
  PyMem_Free(numbers);
  PyMem_Free(inds);

  return result;
}

// row_count(stmt) -> int
static PyObject *row_count(PyObject *self, PyObject *args) {
  SQLINTEGER stmt, n = 0;
  if (!PyArg_ParseTuple(args, "i", &stmt)) {
    return NULL;
  }

  CALL(SQL_HANDLE_STMT, stmt, SQLRowCount(stmt, &n));
  return PyLong_FromLong(n);
}

// num_result_cols(stmt) -> int
static PyObject *num_result_cols(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  SQLSMALLINT n = 0;
  if (!PyArg_ParseTuple(args, "i", &stmt)) {
    return NULL;
  }

  CALL(SQL_HANDLE_STMT, stmt, SQLNumResultCols(stmt, &n));
  return PyLong_FromLong(n);
}

// describe_col(stmt, n) -> (name, data_type, column_size, decimal_digits,
// nullable)
static PyObject *describe_col(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  int n;
  if (!PyArg_ParseTuple(args, "ii", &stmt, &n)) {
    return NULL;
  }

  SQLSMALLINT size = 129, length = 0, data_type = 0, decimal_digits = 0, nullable = 0;
  SQLINTEGER column_size = 0;
  for (;;) {
    SQLCHAR *name = PyMem_Malloc(size);
    if (name == NULL) {
      return PyErr_NoMemory();
    }

    SQLRETURN rc;
    Py_BEGIN_ALLOW_THREADS
    rc = SQLDescribeCol(stmt, (SQLSMALLINT)n, name, size, &length, &data_type, &column_size,
                        &decimal_digits, &nullable);
    Py_END_ALLOW_THREADS
    if (check(rc, SQL_HANDLE_STMT, stmt) < 0) {
      PyMem_Free(name);
      return NULL;
    }

    // The name was truncated.
    if (length >= size) {
      size = length + 1;
      PyMem_Free(name);
      continue;
    }

    PyObject *result = Py_BuildValue("(s#iiii)", name, (Py_ssize_t)length, data_type,
                                     column_size, decimal_digits, nullable);
    PyMem_Free(name);
    return result;
  }
}

// col_attribute(stmt, n, field) -> int reads a numeric field of the IRD for a
// column (such as SQL_DESC_DATETIME_INTERVAL_PRECISION).
static PyObject *col_attribute(PyObject *self, PyObject *args) {
  SQLINTEGER stmt, value = 0;
  int n, field;
  if (!PyArg_ParseTuple(args, "iii", &stmt, &n, &field)) {
    return NULL;
  }

  CALL(SQL_HANDLE_STMT, stmt,
       SQLColAttribute(stmt, (SQLSMALLINT)n, (SQLSMALLINT)field, NULL, 0, NULL, &value));
  return PyLong_FromLong(value);
}

// set_scrollable(stmt, on) makes the cursor of the statement scrollable (or
// not), before it's executed.
static PyObject *set_scrollable(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  int on;
  if (!PyArg_ParseTuple(args, "ip", &stmt, &on)) {
    return NULL;
  }

  static SQLINTEGER scrollable = SQL_SCROLLABLE, nonscrollable = SQL_NONSCROLLABLE;
  CALL(SQL_HANDLE_STMT, stmt,
       SQLSetStmtAttr(stmt, SQL_ATTR_CURSOR_SCROLLABLE,
                      on ? (SQLPOINTER)&scrollable : (SQLPOINTER)&nonscrollable, 0));
  Py_RETURN_NONE;
}

// close_cursor(stmt)
static PyObject *close_cursor(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  if (!PyArg_ParseTuple(args, "i", &stmt)) {
    return NULL;
  }

  CALL(SQL_HANDLE_STMT, stmt, SQLCloseCursor(stmt));
  Py_RETURN_NONE;
}

// get_string reads a column as SQL_CHAR. A string that doesn't fit in the
// buffer is read in pieces, into a buffer that's the size of the rest of it.
static PyObject *get_string(SQLINTEGER stmt, SQLSMALLINT n) {
  char small[256];
  char *buf = small, *large = NULL;
  SQLINTEGER size = sizeof(small), ind = 0;
  size_t length = 0;
  for (;;) {
    SQLRETURN rc;
    Py_BEGIN_ALLOW_THREADS
    rc = SQLGetData(stmt, n, SQL_CHAR, buf + length, size - (SQLINTEGER)length, &ind);
    Py_END_ALLOW_THREADS
    if (check(rc, SQL_HANDLE_STMT, stmt) < 0) {
      PyMem_Free(large);
      return NULL;
    }
    if (ind == SQL_NULL_DATA) {
      PyMem_Free(large);
      Py_RETURN_NONE;
    }

    // The rest of the string (ind) didn't fit, so the buffer is full with a
    // null terminated piece of it. The piece is the whole buffer, since the
    // string can have a NUL in it.
    if (rc == SQL_SUCCESS_WITH_INFO && ind >= size - (SQLINTEGER)length) {
      size_t piece = (size_t)size - length - 1;
      size_t new_size = length + piece + (size_t)ind + 1;
      char *grown = PyMem_Malloc(new_size);
      if (grown == NULL) {
        PyMem_Free(large);
        return PyErr_NoMemory();
      }
      memcpy(grown, buf, length + piece);
      PyMem_Free(large);
      buf = large = grown;
      length += piece;
      size = (SQLINTEGER)new_size;
      continue;
    }

    PyObject *s = PyUnicode_DecodeUTF8(buf, (Py_ssize_t)(length + ind), NULL);
    PyMem_Free(large);
    // vsql's strings are always UTF-8, but it should still be a DataError
    // rather than a UnicodeDecodeError if one isn't.
    if (s == NULL && PyErr_ExceptionMatches(PyExc_UnicodeDecodeError)) {
      PyErr_Clear();
      raise_error("22021", "character not in repertoire: the value isn't UTF-8", -1);
    }
    return s;
  }
}

// get_binary reads a column as SQL_BINARY, in pieces like get_string. The
// octets aren't null terminated, and the length of the rest of the value
// (ind) includes the piece that was read.
static PyObject *get_binary(SQLINTEGER stmt, SQLSMALLINT n) {
  char small[256];
  char *buf = small, *large = NULL;
  SQLINTEGER size = sizeof(small), ind = 0;
  size_t length = 0;
  for (;;) {
    SQLINTEGER available = size - (SQLINTEGER)length;
    SQLRETURN rc;
    Py_BEGIN_ALLOW_THREADS
    rc = SQLGetData(stmt, n, SQL_BINARY, buf + length, available, &ind);
    Py_END_ALLOW_THREADS
    if (check(rc, SQL_HANDLE_STMT, stmt) < 0) {
      PyMem_Free(large);
      return NULL;
    }
    if (ind == SQL_NULL_DATA) {
      PyMem_Free(large);
      Py_RETURN_NONE;
    }

    // The rest of the value (ind) didn't fit, so the buffer is full.
    if (rc == SQL_SUCCESS_WITH_INFO && ind > available) {
      size_t new_size = length + (size_t)ind;
      char *grown = PyMem_Malloc(new_size);
      if (grown == NULL) {
        PyMem_Free(large);
        return PyErr_NoMemory();
      }
      memcpy(grown, buf, length + (size_t)available);
      PyMem_Free(large);
      buf = large = grown;
      length += (size_t)available;
      size = (SQLINTEGER)new_size;
      continue;
    }

    PyObject *b = PyBytes_FromStringAndSize(buf, (Py_ssize_t)(length + (size_t)ind));
    PyMem_Free(large);
    return b;
  }
}

// fetch_row(stmt, kinds[, orientation, offset]) -> tuple or None fetches the
// next row (or the row of a FetchOrientation and FetchOffset for
// SQLFetchScroll), it's None when there isn't a row there. kinds has a character for how each column is read: 'i'
// (SQL_BIGINT), 'd' (SQL_DOUBLE), 'r' (SQL_REAL), 'b' (SQL_BINARY, as bytes)
// or 's' (SQL_CHAR). A NULL is None.
static PyObject *fetch_row(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  const char *kinds;
  Py_ssize_t n;
  int orientation = SQL_FETCH_NEXT;
  SQLINTEGER offset = 0;
  if (!PyArg_ParseTuple(args, "iy#|ii", &stmt, &kinds, &n, &orientation, &offset)) {
    return NULL;
  }

  SQLRETURN rc;
  Py_BEGIN_ALLOW_THREADS
  if (orientation == SQL_FETCH_NEXT) {
    rc = SQLFetch(stmt);
  } else {
    rc = SQLFetchScroll(stmt, (SQLSMALLINT)orientation, offset);
  }
  Py_END_ALLOW_THREADS
  if (rc == SQL_NO_DATA) {
    Py_RETURN_NONE;
  }
  if (check(rc, SQL_HANDLE_STMT, stmt) < 0 || warn(rc, SQL_HANDLE_STMT, stmt) < 0) {
    return NULL;
  }

  PyObject *row = PyTuple_New(n);
  if (row == NULL) {
    return NULL;
  }

  for (Py_ssize_t i = 0; i < n; i++) {
    SQLSMALLINT column = (SQLSMALLINT)(i + 1);
    SQLINTEGER ind = 0;
    PyObject *value = NULL;
    switch (kinds[i]) {
    case 'i': {
      SQLBIGINT v = 0;
      Py_BEGIN_ALLOW_THREADS
      rc = SQLGetData(stmt, column, SQL_BIGINT, &v, sizeof(v), &ind);
      Py_END_ALLOW_THREADS
      if (check(rc, SQL_HANDLE_STMT, stmt) == 0) {
        value = ind == SQL_NULL_DATA ? Py_NewRef(Py_None) : PyLong_FromLongLong(v);
      }
      break;
    }
    case 'd': {
      SQLDOUBLE v = 0;
      Py_BEGIN_ALLOW_THREADS
      rc = SQLGetData(stmt, column, SQL_DOUBLE, &v, sizeof(v), &ind);
      Py_END_ALLOW_THREADS
      if (check(rc, SQL_HANDLE_STMT, stmt) == 0) {
        value = ind == SQL_NULL_DATA ? Py_NewRef(Py_None) : PyFloat_FromDouble(v);
      }
      break;
    }
    case 'r': {
      SQLREAL v = 0;
      Py_BEGIN_ALLOW_THREADS
      rc = SQLGetData(stmt, column, SQL_REAL, &v, sizeof(v), &ind);
      Py_END_ALLOW_THREADS
      if (check(rc, SQL_HANDLE_STMT, stmt) == 0) {
        value = ind == SQL_NULL_DATA ? Py_NewRef(Py_None) : PyFloat_FromDouble(v);
      }
      break;
    }
    case 'b':
      value = get_binary(stmt, column);
      break;
    default:
      value = get_string(stmt, column);
      break;
    }
    if (value == NULL) {
      Py_DECREF(row);
      return NULL;
    }
    PyTuple_SET_ITEM(row, i, value);
  }

  return row;
}

static PyMethodDef methods[] = {
    {"set_error_factory", set_error_factory, METH_O, NULL},
    {"set_warning_handler", set_warning_handler, METH_O, NULL},
    {"alloc_handle", alloc_handle, METH_VARARGS, NULL},
    {"free_handle", free_handle, METH_VARARGS, NULL},
    {"connect", connect, METH_VARARGS, NULL},
    {"disconnect", disconnect, METH_VARARGS, NULL},
    {"set_autocommit", set_autocommit, METH_VARARGS, NULL},
    {"start_tran", start_tran, METH_VARARGS, NULL},
    {"end_tran", end_tran, METH_VARARGS, NULL},
    {"end_savepoint", end_savepoint, METH_VARARGS, NULL},
    {"transaction_active", transaction_active, METH_VARARGS, NULL},
    {"get_info", get_info, METH_VARARGS, NULL},
    {"exec_direct", exec_direct, METH_VARARGS, NULL},
    {"prepare", prepare, METH_VARARGS, NULL},
    {"execute", execute, METH_VARARGS, NULL},
    {"row_count", row_count, METH_VARARGS, NULL},
    {"num_result_cols", num_result_cols, METH_VARARGS, NULL},
    {"describe_col", describe_col, METH_VARARGS, NULL},
    {"col_attribute", col_attribute, METH_VARARGS, NULL},
    {"close_cursor", close_cursor, METH_VARARGS, NULL},
    {"set_scrollable", set_scrollable, METH_VARARGS, NULL},
    {"fetch_row", fetch_row, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL},
};

static int exec_module(PyObject *m);

static PyModuleDef_Slot slots[] = {
    {Py_mod_exec, exec_module},
#if PY_VERSION_HEX >= 0x030D0000
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
#endif
    {0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "vsql._libvsql", NULL, 0, methods, slots,
};

// The constants that the Python code needs.
static const struct {
  const char *name;
  long value;
} constants[] = {
    {"SQL_HANDLE_ENV", SQL_HANDLE_ENV},
    {"SQL_HANDLE_DBC", SQL_HANDLE_DBC},
    {"SQL_HANDLE_STMT", SQL_HANDLE_STMT},
    {"SQL_NULL_HANDLE", SQL_NULL_HANDLE},
    {"SQL_CHAR", SQL_CHAR},
    {"SQL_NUMERIC", SQL_NUMERIC},
    {"SQL_DECIMAL", SQL_DECIMAL},
    {"SQL_INTEGER", SQL_INTEGER},
    {"SQL_SMALLINT", SQL_SMALLINT},
    {"SQL_FLOAT", SQL_FLOAT},
    {"SQL_REAL", SQL_REAL},
    {"SQL_DOUBLE", SQL_DOUBLE},
    {"SQL_VARCHAR", SQL_VARCHAR},
    {"SQL_BOOLEAN", SQL_BOOLEAN},
    {"SQL_BIGINT", SQL_BIGINT},
    {"SQL_TYPE_DATE", SQL_TYPE_DATE},
    {"SQL_TYPE_TIME", SQL_TYPE_TIME},
    {"SQL_TYPE_TIMESTAMP", SQL_TYPE_TIMESTAMP},
    {"SQL_TYPE_TIME_WITH_TIMEZONE", SQL_TYPE_TIME_WITH_TIMEZONE},
    {"SQL_TYPE_TIMESTAMP_WITH_TIMEZONE", SQL_TYPE_TIMESTAMP_WITH_TIMEZONE},
    {"SQL_DECFLOAT", SQL_DECFLOAT},
    {"SQL_BINARY", SQL_BINARY},
    {"SQL_VARBINARY", SQL_VARBINARY},
    {"SQL_BLOB", SQL_BLOB},
    {"SQL_CLOB", SQL_CLOB},
    {"SQL_INTERVAL_YEAR", SQL_INTERVAL_YEAR},
    {"SQL_INTERVAL_MONTH", SQL_INTERVAL_MONTH},
    {"SQL_INTERVAL_DAY", SQL_INTERVAL_DAY},
    {"SQL_INTERVAL_HOUR", SQL_INTERVAL_HOUR},
    {"SQL_INTERVAL_MINUTE", SQL_INTERVAL_MINUTE},
    {"SQL_INTERVAL_SECOND", SQL_INTERVAL_SECOND},
    {"SQL_INTERVAL_YEAR_TO_MONTH", SQL_INTERVAL_YEAR_TO_MONTH},
    {"SQL_INTERVAL_DAY_TO_HOUR", SQL_INTERVAL_DAY_TO_HOUR},
    {"SQL_INTERVAL_DAY_TO_MINUTE", SQL_INTERVAL_DAY_TO_MINUTE},
    {"SQL_INTERVAL_DAY_TO_SECOND", SQL_INTERVAL_DAY_TO_SECOND},
    {"SQL_INTERVAL_HOUR_TO_MINUTE", SQL_INTERVAL_HOUR_TO_MINUTE},
    {"SQL_INTERVAL_HOUR_TO_SECOND", SQL_INTERVAL_HOUR_TO_SECOND},
    {"SQL_INTERVAL_MINUTE_TO_SECOND", SQL_INTERVAL_MINUTE_TO_SECOND},
    {"SQL_DESC_DATETIME_INTERVAL_PRECISION", SQL_DESC_DATETIME_INTERVAL_PRECISION},
    {"SQL_DBMS_VERSION", SQL_DBMS_VERSION},
    {"SQL_VSQL_BUILD_VERSION", SQL_VSQL_BUILD_VERSION},
    {"SQL_VSQL_BUILD_COMMIT", SQL_VSQL_BUILD_COMMIT},
    {"SQL_VSQL_BUILD_DATE", SQL_VSQL_BUILD_DATE},
    {"SQL_VSQL_BUILD_V_VERSION", SQL_VSQL_BUILD_V_VERSION},
    {"SQL_VSQL_BUILD_V_COMMIT", SQL_VSQL_BUILD_V_COMMIT},
    {"SQL_VSQL_BUILD_C_COMPILER", SQL_VSQL_BUILD_C_COMPILER},
    {"SQL_VSQL_OPERATING_SYSTEM", SQL_VSQL_OPERATING_SYSTEM},
    {"SQL_VSQL_MACHINE", SQL_VSQL_MACHINE},
    {"SQL_VSQL_FILE_READ_VERSION", SQL_VSQL_FILE_READ_VERSION},
    {"SQL_VSQL_FILE_WRITE_VERSION", SQL_VSQL_FILE_WRITE_VERSION},
    {"SQL_NO_NULLS", SQL_NO_NULLS},
    {"SQL_FETCH_ABSOLUTE", SQL_FETCH_ABSOLUTE},
    {"SQL_NULLABLE", SQL_NULLABLE},
    {"SQL_TRANSACTION_READ_ONLY", SQL_TRANSACTION_READ_ONLY},
    {"SQL_TRANSACTION_READ_WRITE", SQL_TRANSACTION_READ_WRITE},
    {"SQL_TRANSACTION_READ_UNCOMMITTED", SQL_TRANSACTION_READ_UNCOMMITTED},
    {"SQL_TRANSACTION_READ_COMMITTED", SQL_TRANSACTION_READ_COMMITTED},
    {"SQL_TRANSACTION_REPEATABLE_READ", SQL_TRANSACTION_REPEATABLE_READ},
    {"SQL_TRANSACTION_SERIALIZABLE", SQL_TRANSACTION_SERIALIZABLE},
    {NULL, 0},
};

static int exec_module(PyObject *m) {
  for (int i = 0; constants[i].name != NULL; i++) {
    if (PyModule_AddIntConstant(m, constants[i].name, constants[i].value) < 0) {
      return -1;
    }
  }

  return 0;
}

PyMODINIT_FUNC PyInit__libvsql(void) { return PyModuleDef_Init(&module); }
