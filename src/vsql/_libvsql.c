// _libvsql.c is the vsql._libvsql extension module, the only code that calls
// libvsql (see libvsql.h), which is compiled into the module from the
// amalgamation by setup.py. Each function is a thin wrapper around a libvsql
// routine, with handles as ints. Everything else (such as converting values)
// is in Python.
//
// libvsql can be called from any thread, so the GIL is released while a
// routine runs.
//
// When a routine fails, the exception is made by the function given to
// set_error_factory, from the SQLSTATE and message of the first diagnostic
// record.

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

// raise_error raises the exception for an SQLSTATE and message.
static void raise_error(const char *sqlstate, const char *message, Py_ssize_t length) {
  if (error_factory == NULL) {
    PyErr_Format(PyExc_RuntimeError, "vsql: %.*s (SQLSTATE %s)", (int)length, message,
                 sqlstate);
    return;
  }

  PyObject *err = PyObject_CallFunction(error_factory, "ss#", sqlstate, message, length);
  if (err != NULL) {
    PyErr_SetObject((PyObject *)Py_TYPE(err), err);
    Py_DECREF(err);
  }
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
  case SQL_INVALID_HANDLE:
    raise_error("HY000", "invalid handle", -1);
    return -1;
  }

  SQLCHAR sqlstate[6];
  SQLSMALLINT size = 512, length = 0;
  SQLCHAR *message = malloc(size);
  for (;;) {
    if (message == NULL) {
      PyErr_NoMemory();
      return -1;
    }

    Py_BEGIN_ALLOW_THREADS
    rc = SQLGetDiagRec(handle_type, handle, 1, sqlstate, NULL, message, size, &length);
    Py_END_ALLOW_THREADS
    if (rc != SQL_SUCCESS && rc != SQL_SUCCESS_WITH_INFO) {
      free(message);
      raise_error("HY000", "unknown error", -1);
      return -1;
    }

    // The message was truncated.
    if (length >= size) {
      size = length + 1;
      free(message);
      message = malloc(size);
      continue;
    }

    raise_error((const char *)sqlstate, (const char *)message, length);
    free(message);
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

  // An environment doesn't have an input handle with diagnostics.
  if (handle_type == SQL_HANDLE_ENV) {
    if (rc != SQL_SUCCESS) {
      raise_error("HY001", "can't allocate an environment", -1);
      return NULL;
    }
  } else if (check(rc, handle_type == SQL_HANDLE_DBC ? SQL_HANDLE_ENV : SQL_HANDLE_DBC,
                   input) < 0) {
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

  CALL(SQL_HANDLE_DBC, dbc, SQLEndTran(SQL_HANDLE_DBC, dbc, commit ? SQL_COMMIT : SQL_ROLLBACK));
  Py_RETURN_NONE;
}

// exec_direct(stmt, query)
static PyObject *exec_direct(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  const char *query;
  Py_ssize_t length;
  if (!PyArg_ParseTuple(args, "is#", &stmt, &query, &length)) {
    return NULL;
  }

  CALL(SQL_HANDLE_STMT, stmt, SQLExecDirect(stmt, (SQLCHAR *)query, (SQLINTEGER)length));
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
// value is None, an int (SQL_BIGINT), a float (SQL_DOUBLE) or UTF-8 bytes
// (SQL_CHAR).
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
    } else {
      // The bytes are kept alive by params until SQLExecute returns.
      char *s;
      Py_ssize_t length;
      if (PyBytes_AsStringAndSize(value, &s, &length) < 0) {
        goto done;
      }
      pointer = s;
      buffer_length = (SQLINTEGER)length + 1;
      inds[i] = (SQLINTEGER)length;
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
  if (check(rc, SQL_HANDLE_STMT, stmt) == 0) {
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
    SQLCHAR *name = malloc(size);
    if (name == NULL) {
      return PyErr_NoMemory();
    }

    SQLRETURN rc;
    Py_BEGIN_ALLOW_THREADS
    rc = SQLDescribeCol(stmt, (SQLSMALLINT)n, name, size, &length, &data_type, &column_size,
                        &decimal_digits, &nullable);
    Py_END_ALLOW_THREADS
    if (check(rc, SQL_HANDLE_STMT, stmt) < 0) {
      free(name);
      return NULL;
    }

    // The name was truncated.
    if (length >= size) {
      size = length + 1;
      free(name);
      continue;
    }

    PyObject *result = Py_BuildValue("(s#iiii)", name, (Py_ssize_t)length, data_type,
                                     column_size, decimal_digits, nullable);
    free(name);
    return result;
  }
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

    // The rest of the string (ind) didn't fit, so the buffer has a null
    // terminated piece of it.
    if (rc == SQL_SUCCESS_WITH_INFO && ind >= size - (SQLINTEGER)length) {
      size_t piece = strlen(buf + length);
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
    return s;
  }
}

// fetch_row(stmt, kinds) -> tuple or None fetches the next row, it's None
// after the last row. kinds has a character for how each column is read: 'i'
// (SQL_BIGINT), 'd' (SQL_DOUBLE), 'r' (SQL_REAL) or 's' (SQL_CHAR). A NULL is
// None.
static PyObject *fetch_row(PyObject *self, PyObject *args) {
  SQLINTEGER stmt;
  const char *kinds;
  Py_ssize_t n;
  if (!PyArg_ParseTuple(args, "iy#", &stmt, &kinds, &n)) {
    return NULL;
  }

  SQLRETURN rc;
  Py_BEGIN_ALLOW_THREADS
  rc = SQLFetch(stmt);
  Py_END_ALLOW_THREADS
  if (rc == SQL_NO_DATA) {
    Py_RETURN_NONE;
  }
  if (check(rc, SQL_HANDLE_STMT, stmt) < 0) {
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
    {"alloc_handle", alloc_handle, METH_VARARGS, NULL},
    {"free_handle", free_handle, METH_VARARGS, NULL},
    {"connect", connect, METH_VARARGS, NULL},
    {"disconnect", disconnect, METH_VARARGS, NULL},
    {"set_autocommit", set_autocommit, METH_VARARGS, NULL},
    {"start_tran", start_tran, METH_VARARGS, NULL},
    {"end_tran", end_tran, METH_VARARGS, NULL},
    {"exec_direct", exec_direct, METH_VARARGS, NULL},
    {"prepare", prepare, METH_VARARGS, NULL},
    {"execute", execute, METH_VARARGS, NULL},
    {"row_count", row_count, METH_VARARGS, NULL},
    {"num_result_cols", num_result_cols, METH_VARARGS, NULL},
    {"describe_col", describe_col, METH_VARARGS, NULL},
    {"close_cursor", close_cursor, METH_VARARGS, NULL},
    {"fetch_row", fetch_row, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "vsql._libvsql", NULL, -1, methods,
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
    {"SQL_NO_NULLS", SQL_NO_NULLS},
    {"SQL_NULLABLE", SQL_NULLABLE},
    {"SQL_TRANSACTION_READ_ONLY", SQL_TRANSACTION_READ_ONLY},
    {"SQL_TRANSACTION_READ_WRITE", SQL_TRANSACTION_READ_WRITE},
    {"SQL_TRANSACTION_READ_UNCOMMITTED", SQL_TRANSACTION_READ_UNCOMMITTED},
    {"SQL_TRANSACTION_READ_COMMITTED", SQL_TRANSACTION_READ_COMMITTED},
    {"SQL_TRANSACTION_REPEATABLE_READ", SQL_TRANSACTION_REPEATABLE_READ},
    {"SQL_TRANSACTION_SERIALIZABLE", SQL_TRANSACTION_SERIALIZABLE},
    {NULL, 0},
};

PyMODINIT_FUNC PyInit__libvsql(void) {
  PyObject *m = PyModule_Create(&module);
  if (m == NULL) {
    return NULL;
  }

  for (int i = 0; constants[i].name != NULL; i++) {
    if (PyModule_AddIntConstant(m, constants[i].name, constants[i].value) < 0) {
      Py_DECREF(m);
      return NULL;
    }
  }

  return m;
}
