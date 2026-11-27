# setup.py builds the vsql._libvsql extension module, which compiles libvsql
# from the amalgamation in internal/amalgamation (see
# scripts/update-amalgamation.sh) with the libraries that V used for each OS
# and architecture (libvsql_flags.json).

import json
import os
import platform
import shutil
import sys
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

root = Path(__file__).parent


def os_arch():
    os = {"darwin": "macos", "linux": "linux", "win32": "windows"}.get(sys.platform)
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if os is None or arch is None:
        raise RuntimeError(f"vsql isn't supported on {sys.platform} ({machine})")

    return f"{os}_{arch}"


flags = json.loads((root / "libvsql_flags.json").read_text())[os_arch()]


class BuildExt(build_ext):
    """On Windows, the C that V generates uses GNU C (such as statement
    expressions), which MSVC can't compile, so it's compiled with clang-cl
    instead (CC, or clang-cl from the PATH or LLVM's default folder). The rest
    of MSVC (the linker and the headers) is still used."""

    def build_extensions(self):
        if self.compiler.compiler_type == "msvc":
            clang_cl = (os.environ.get("CC") or shutil.which("clang-cl")
                        or shutil.which(r"C:\Program Files\LLVM\bin\clang-cl.exe"))
            if clang_cl is None:
                raise RuntimeError("vsql must be compiled with clang-cl on Windows (see the README)")
            if not self.compiler.initialized:
                self.compiler.initialize()
            self.compiler.cc = clang_cl
        super().build_extensions()


libraries = flags["libraries"]
if sys.platform == "win32":
    # libvsql.c is compiled with -std=gnu11 -fwrapv -w (see the amalgamation's
    # Makefile), since V generates C that relies on signed overflow wrapping and
    # causes many warnings. clang-cl takes clang's own options with /clang:.
    extra_compile_args = ["/w", "/clang:-std=gnu11", "/clang:-fwrapv"]
    # The C that V generates for clang on Windows doesn't include stdio.h. It
    # declares the printf functions (such as fprintf and snprintf) itself, which
    # the Universal CRT defines inline, so they're linked from
    # legacy_stdio_definitions.
    libraries = libraries + ["legacy_stdio_definitions"]
else:
    # V generates C that relies on signed overflow wrapping and causes many
    # warnings (see the amalgamation's Makefile). The C that V has generated for
    # Linux passed a pthread_t as a void*, which is an error from GCC 14 (that
    # -w doesn't turn off) unless -Wno-int-conversion is given.
    extra_compile_args = ["-std=gnu11", "-fwrapv", "-w", "-Wno-int-conversion"]

if sys.platform == "linux":
    # The garbage collector finds the data of the program and its libraries
    # with dl_iterate_phdr, but it only knows that the C library has it for
    # glibc. On musl (Alpine and musllinux) it falls back to the program's own
    # data segment, from the start of libvsql's to the end of Python's, and
    # aborts with "Wrong DATASTART/END pair" (or crashes later). musl has had
    # dl_iterate_phdr since 1.0, and it must be a strong reference since musl
    # leaves a weak one NULL.
    extra_compile_args += ["-DHAVE_DL_ITERATE_PHDR", "-DDL_ITERATE_PHDR_STRONG"]

setup(
    cmdclass={"build_ext": BuildExt},
    ext_modules=[
        Extension(
            "vsql._libvsql",
            sources=[
                "src/vsql/_libvsql.c",
                # One file for every OS and architecture, with the garbage
                # collector and V's helpers.
                "internal/amalgamation/libvsql.c",
            ],
            include_dirs=["internal/amalgamation"],
            libraries=libraries,
            extra_compile_args=extra_compile_args,
        )
    ],
)
