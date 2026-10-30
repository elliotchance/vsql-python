#!/bin/bash

# update-amalgamation.sh replaces the libvsql C in internal/amalgamation with
# the C from a vsql amalgamation (vsql-VERSION-amalgamation.zip from a vsql
# release, or the folder it unzips to). With no arguments, or a version, the zip
# is downloaded from the latest (or that) vsql release:
#
#   scripts/update-amalgamation.sh
#   scripts/update-amalgamation.sh v1.2.3
#   scripts/update-amalgamation.sh vsql-v1.2.3-amalgamation.zip
#
# Only what's needed to compile libvsql is copied (not vsql, the command line
# program). libvsql_flags.json is generated from the defines and libraries in
# each vsql_OS_ARCH.mk, which are the ones that V used to build libvsql, and is
# read by setup.py.
#
# The amalgamation isn't compiled file by file. Instead, src/vsql/_libvsql.c
# includes libvsql.c and vsql_runtime.c from internal/amalgamation.

set -euo pipefail

source="${1:-latest}"
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/internal/amalgamation"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if [ ! -e "$source" ]; then
  version="$source"
  if [ "$version" = latest ]; then
    version="$(curl -fsSL https://api.github.com/repos/elliotchance/vsql/releases/latest |
      sed -n 's/^ *"tag_name": *"\(.*\)",$/\1/p')"
  fi
  zip="vsql-$version-amalgamation.zip"
  echo "Downloading $zip"
  curl -fsSL -o "$tmp/$zip" \
    "https://github.com/elliotchance/vsql/releases/download/$version/$zip"
  source="$tmp/$zip"
fi

if [ -f "$source" ]; then
  unzip -q "$source" -d "$tmp/unzipped"
  source="$(dirname "$(find "$tmp/unzipped" -name libvsql.h | head -1)")"
fi

if [ ! -f "$source/libvsql.h" ] || [ ! -f "$source/libvsql.c" ]; then
  echo "$1 isn't a vsql amalgamation (there's no libvsql.h or libvsql.c)" >&2
  exit 1
fi

rm -rf "$out"
mkdir -p "$out"
for file in "$source"/*; do
  name="$(basename "$file")"
  case "$name" in
    # vsql, the command line program, and the files for building with make.
    vsql.c | vsql_*_amd64.c | vsql_*_arm64.c | Makefile | *.mk | example.c | libvsql.def | libvsql.exports | libvsql.map) ;;
    *) cp -R "$file" "$out/" ;;
  esac
done

# The defines and libraries for each vsql_OS_ARCH.mk, keyed by "OS_ARCH" (such
# as "macos_arm64").
flags="$root/libvsql_flags.json"
{
  echo "{"
  separator=""
  for mk in "$source"/vsql_*_*.mk; do
    os_arch="$(basename "$mk" .mk)"
    os_arch="${os_arch#vsql_}"

    defines=""
    libraries=""
    for arg in $(sed -n 's/^LIBVSQL_FLAGS *= *//p' "$mk"); do
      case "$arg" in
        # Options for linking libvsql as a shared library.
        -Wl,*) ;;
        -D*) defines="$defines${defines:+, }\"${arg#-D}\"" ;;
        -l*) libraries="$libraries${libraries:+, }\"${arg#-l}\"" ;;
        *) echo "Unknown flag in $(basename "$mk"): $arg" >&2; exit 1 ;;
      esac
    done
    printf '%s  "%s": {\n    "defines": [%s],\n    "libraries": [%s]\n  }' \
      "$separator" "$os_arch" "$defines" "$libraries"
    separator=$',\n'
  done
  echo
  echo "}"
} > "$flags"
python3 -m json.tool "$flags" > /dev/null

echo "Updated $out and $flags from ${1:-the latest release}"
