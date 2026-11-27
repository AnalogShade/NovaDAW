"""
scripts/build_native_dsp.py - Reproducible MSVC x64 C11 build script for core/bin/nova_dsp.dll.

Usage:
    python scripts/build_native_dsp.py          # Build and verify DLL
    python scripts/build_native_dsp.py --check  # Verify existing DLL loads & ABI == 1
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
C_SOURCE = REPO_ROOT / "core" / "bin" / "nova_dsp.c"
DLL_OUTPUT = REPO_ROOT / "core" / "bin" / "nova_dsp.dll"
EXPECTED_ABI_VERSION = 1

VSWHERE_PATH = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
FALLBACK_VCVARS64 = [
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat"),
    Path(r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"),
    Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"),
    Path(r"C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat"),
    Path(r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat"),
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2019\Community\VC\Auxiliary\Build\vcvars64.bat"),
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2019\Professional\VC\Auxiliary\Build\vcvars64.bat"),
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2019\Enterprise\VC\Auxiliary\Build\vcvars64.bat"),
]


def find_vcvars64() -> Optional[Path]:
    """Locate MSVC 2019+ vcvars64.bat via vswhere.exe -products * or fallback paths."""
    if VSWHERE_PATH.is_file():
        try:
            proc = subprocess.run(
                [
                    str(VSWHERE_PATH),
                    "-products",
                    "*",
                    "-requires",
                    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                    "-latest",
                    "-property",
                    "installationPath",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    install_dir = line.strip()
                    if install_dir:
                        candidate = Path(install_dir) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
                        if candidate.is_file():
                            return candidate
        except OSError:
            pass

    for candidate in FALLBACK_VCVARS64:
        if candidate.is_file():
            return candidate
    return None


def verify_dll(dll_path: Path, quiet: bool = False) -> bool:
    """Verify DLL exists, loads cleanly in 64-bit Python, and exports ABI version 1."""
    if not dll_path.is_file():
        if not quiet:
            print(f"[build_native_dsp] Missing DLL: {dll_path}", file=sys.stderr)
        return False

    try:
        lib = ctypes.CDLL(str(dll_path))
        abi_fn = lib.nova_dsp_abi_version
        abi_fn.argtypes = []
        abi_fn.restype = ctypes.c_int32
        abi = int(abi_fn())
    except Exception as exc:
        if not quiet:
            print(f"[build_native_dsp] Failed to load/verify DLL: {exc}", file=sys.stderr)
        return False

    if abi != EXPECTED_ABI_VERSION:
        if not quiet:
            print(
                f"[build_native_dsp] ABI mismatch: got {abi}, expected {EXPECTED_ABI_VERSION}",
                file=sys.stderr,
            )
        return False

    if not quiet:
        print(f"[build_native_dsp] Verified {dll_path} (ABI v{abi})")
    return True


def build_dll(quiet: bool = False) -> int:
    """Compile core/bin/nova_dsp.c -> core/bin/nova_dsp.dll with zero build residue."""
    if not C_SOURCE.is_file():
        print(f"[build_native_dsp] Source not found: {C_SOURCE}", file=sys.stderr)
        return 1

    vcvars = find_vcvars64()
    if vcvars is None:
        print("[build_native_dsp] Could not locate MSVC vcvars64.bat.", file=sys.stderr)
        return 1

    DLL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="nova_dsp_build_") as tmp_dir:
        tmp = Path(tmp_dir)
        obj_path = tmp / "nova_dsp.obj"
        lib_path = tmp / "nova_dsp.lib"
        tmp_dll_path = tmp / "nova_dsp.dll"

        bat_path = tmp / "build.bat"
        bat_content = (
            "@echo off\r\n"
            f'call "{vcvars}" >nul || exit /b %errorlevel%\r\n'
            f'cl.exe /nologo /TC /std:c11 /O2 /Oi /fp:precise /MT /LD /W3 '
            f'/D_CRT_SECURE_NO_WARNINGS '
            f'/Fo"{obj_path}" /Fe"{tmp_dll_path}" "{C_SOURCE}" '
            f'/link /IMPLIB:"{lib_path}"\r\n'
        )
        bat_path.write_text(bat_content, encoding="utf-8")
        if not quiet:
            print(f"[build_native_dsp] Using vcvars64: {vcvars}")
            print(f"[build_native_dsp] Compiling {C_SOURCE.name} -> {DLL_OUTPUT.name}...")

        proc = subprocess.run(
            ["cmd.exe", "/d", "/c", str(bat_path)],
            cwd=str(tmp),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stdout and (not quiet or proc.returncode != 0):
            print(proc.stdout.rstrip())
        if proc.stderr and (not quiet or proc.returncode != 0):
            print(proc.stderr.rstrip(), file=sys.stderr)

        if proc.returncode != 0:
            print(f"[build_native_dsp] Compilation failed (exit {proc.returncode})", file=sys.stderr)
            return proc.returncode

        if not tmp_dll_path.is_file():
            print(f"[build_native_dsp] Missing compiled DLL: {tmp_dll_path}", file=sys.stderr)
            return 1

        try:
            os.replace(tmp_dll_path, DLL_OUTPUT)
        except PermissionError:
            stale_name = f"nova_dsp_stale_{os.getpid()}.dll"
            stale_path = Path(tempfile.gettempdir()) / stale_name
            try:
                if stale_path.exists():
                    stale_path.unlink()
            except OSError:
                stale_path = Path(tempfile.gettempdir()) / f"nova_dsp_stale_{os.getpid()}_{id(tmp)}.dll"
            os.replace(DLL_OUTPUT, stale_path)
            os.replace(tmp_dll_path, DLL_OUTPUT)

    # Ensure zero .obj/.lib/.exp residue remains in core/bin/
    for ext in (".obj", ".lib", ".exp"):
        stray = DLL_OUTPUT.with_suffix(ext)
        if stray.exists():
            try:
                stray.unlink()
            except OSError:
                pass

    residue = [
        p.name
        for p in DLL_OUTPUT.parent.iterdir()
        if p.name.startswith("nova_dsp") and p.suffix.lower() in (".obj", ".lib", ".exp")
    ]
    if residue:
        print(f"[build_native_dsp] Unexpected build residue in core/bin/: {residue}", file=sys.stderr)
        return 1

    if not verify_dll(DLL_OUTPUT, quiet=quiet):
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or check NovaDAW native C99 DSP DLL.")
    parser.add_argument("--check", action="store_true", help="Only verify that nova_dsp.dll loads with ABI == 1.")
    parser.add_argument("--quiet", action="store_true", help="Suppress informational output.")
    args = parser.parse_args()

    if args.check:
        return 0 if verify_dll(DLL_OUTPUT, quiet=args.quiet) else 1
    return build_dll(quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
