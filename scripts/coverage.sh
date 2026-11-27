#!/bin/bash

# coverage.sh runs the tests and fails unless they cover every line and branch
# of the Python modules (with coverage.py) and every line of the extension
# module, src/vsql/_libvsql.c (with gcov):
#
#   pip install pytest pytest-cov
#   scripts/coverage.sh
#
# The extension module is built in place (src/vsql) with gcov's
# instrumentation, and libvsql (the amalgamation) without it, into
# build/coverage. It needs gcc and gcov on Linux, or clang and llvm-cov (from
# Xcode) on macOS. Run "pip install ." afterwards to use the normal build.

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/build/coverage"
python="${PYTHON:-python3}"
mkdir -p "$out"
cd "$root"

include="$("$python" -c 'import sysconfig; print(sysconfig.get_paths()["include"])')"
suffix="$("$python" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
case "$(uname)" in
  Darwin)
    cc="${CC:-clang}"
    gcov=(xcrun llvm-cov gcov)
    link=(-bundle -undefined dynamic_lookup)
    ;;
  *)
    cc="${CC:-gcc}"
    gcov=(gcov)
    link=(-shared)
    ;;
esac

# libvsql takes a while to compile, so it's only compiled again when it
# changes.
amalgamation=internal/amalgamation/libvsql.c
if [ ! -f "$out/libvsql.o" ] || [ "$amalgamation" -nt "$out/libvsql.o" ]; then
  echo "Compiling libvsql"
  "$cc" -c -O1 -fPIC -std=gnu11 -fwrapv -w -Wno-int-conversion "$amalgamation" \
    -o "$out/libvsql.o"
fi

rm -f "$out"/_libvsql.gc*
"$cc" -c -O0 --coverage -fPIC -I"$include" -Iinternal/amalgamation src/vsql/_libvsql.c \
  -o "$out/_libvsql.o"
"$cc" "${link[@]}" --coverage "$out/_libvsql.o" "$out/libvsql.o" -ldl -lpthread -lm \
  -o "src/vsql/_libvsql$suffix"

# coverage.py's settings (100% of lines and branches) are in pyproject.toml.
PYTHONPATH=src "$python" -m pytest -q tests --cov --cov-report=term-missing

# The tests of running out of memory need Python's free lists to be empty,
# which coverage.py's tracing gets in the way of, so they're run again without
# it. gcov adds up the lines from both runs.
PYTHONPATH=src "$python" -m pytest -q tests/test_libvsql.py

# gcov writes a .gcov file for each source file into the current folder.
"${gcov[@]}" -o "$out" src/vsql/_libvsql.c > "$out/gcov.txt"
mv ./*.gcov "$out/"
summary="$(grep -A1 "^File 'src/vsql/_libvsql.c'" "$out/gcov.txt" | tail -1)"
echo "src/vsql/_libvsql.c: $summary"
if ! grep -q "Lines executed:100.00%" <<< "$summary"; then
  echo "These lines of src/vsql/_libvsql.c aren't covered:"
  grep -E '^ +#####:' "$out/_libvsql.c.gcov"
  exit 1
fi
