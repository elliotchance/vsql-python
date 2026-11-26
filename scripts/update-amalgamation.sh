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
# Only what's needed to compile libvsql is copied: libvsql.c, libvsql.h and the
# licenses (not vsql, the command line program, or the files for building with
# make). libvsql.c is one file for every OS and architecture, which defines
# everything it needs itself, so it's compiled as it is by setup.py (with
# -std=gnu11 -fwrapv -w, like the amalgamation's Makefile).
#
# libvsql_flags.json is generated from the libraries in LIBVSQL_FLAGS for each
# OS and architecture in vsql.mk, which are the ones that V used to build
# libvsql, and is read by setup.py.

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

if [ ! -f "$source/vsql.mk" ]; then
  echo "${1:-The latest release} is an older vsql amalgamation (there's no vsql.mk)" >&2
  exit 1
fi

rm -rf "$out"
mkdir -p "$out"
for name in libvsql.c libvsql.h LICENSE LICENSE-V; do
  cp "$source/$name" "$out/"
done

# The libraries for each OS and architecture in vsql.mk, keyed by "OS_ARCH"
# (such as "macos_arm64"). vsql.mk has a block for each one:
#
#   ifeq ($(VSQL_OS)_$(VSQL_ARCH),macos_arm64)
#   LIBVSQL_FLAGS = -ldl -lpthread -lm
#   endif
flags="$root/libvsql_flags.json"
{
  echo "{"
  separator=""
  os_arch=""
  while IFS= read -r line; do
    case "$line" in
      "ifeq (\$(VSQL_OS)_\$(VSQL_ARCH),"*)
        os_arch="${line#*,}"
        os_arch="${os_arch%)}"
        ;;
      "LIBVSQL_FLAGS ="*)
        libraries=""
        for arg in ${line#*=}; do
          case "$arg" in
            # Options for linking libvsql as a shared library.
            -Wl,*) ;;
            -l*) libraries="$libraries${libraries:+, }\"${arg#-l}\"" ;;
            *) echo "Unknown flag for $os_arch in vsql.mk: $arg" >&2; exit 1 ;;
          esac
        done
        printf '%s  "%s": {\n    "libraries": [%s]\n  }' \
          "$separator" "$os_arch" "$libraries"
        separator=$',\n'
        ;;
    esac
  done < "$source/vsql.mk"
  echo
  echo "}"
} > "$flags"
python3 -m json.tool "$flags" > /dev/null

echo "Updated $out and $flags from ${1:-the latest release}"
