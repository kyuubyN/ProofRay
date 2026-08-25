from __future__ import annotations

from io import BytesIO
from pathlib import Path
import struct
import sys
import zipfile


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_android_apk.py path/to/app.apk")
    apk = Path(sys.argv[1])
    if not apk.is_file():
        raise SystemExit("Android APK is absent")
    with zipfile.ZipFile(apk) as archive:
        names = sorted(
            name for name in archive.namelist()
            if name.startswith("lib/arm64-v8a/") and name.endswith(".so"))
        required = {
            "lib/arm64-v8a/libduckdb.so",
            "lib/arm64-v8a/libpython3.so",
        }
        if not required <= set(names):
            raise SystemExit("Android APK lacks required ProofRay native libraries")
        for name in names:
            machine, alignments = _elf_identity(archive.read(name), name)
            if machine != 183:
                raise SystemExit(f"non-aarch64 library in APK arm64 path: {name}")
            if not alignments or min(alignments) < 16 * 1024:
                raise SystemExit(f"APK library lacks Android 16 KiB LOAD alignment: {name}")
        app_assets = [name for name in archive.namelist()
                      if name.endswith("/app.zip") or name == "assets/app.zip"]
        if len(app_assets) != 1:
            raise SystemExit("Android APK must contain exactly one Serious Python app.zip")
        with zipfile.ZipFile(BytesIO(archive.read(app_assets[0]))) as app_zip:
            app_names = set(app_zip.namelist())
            if "main.pyc" not in app_names or "main.py" in app_names:
                raise SystemExit("Android app.zip must contain compiled main.pyc only")
            if not any(name.startswith("proofray_app/") for name in app_names):
                raise SystemExit("Android app.zip lacks the ProofRay backend")
        site_assets = [name for name in archive.namelist()
                       if name.endswith("/sitepackages.zip")
                       or name == "assets/sitepackages.zip"]
        if len(site_assets) != 1:
            raise SystemExit(
                "Android APK must contain exactly one sitepackages.zip")
        with zipfile.ZipFile(BytesIO(archive.read(site_assets[0]))) as site_zip:
            site_names = site_zip.namelist()
            if not any(name.startswith("numpy/") for name in site_names):
                raise SystemExit("Android sitepackages.zip lacks NumPy pure modules")
            if not any(name.startswith("tzdata/") for name in site_names):
                raise SystemExit("Android sitepackages.zip lacks IANA timezone data")


def _elf_identity(value: bytes, name: str) -> tuple[int, tuple[int, ...]]:
    stream = BytesIO(value)
    header = stream.read(64)
    if len(header) < 64 or header[:4] != b"\x7fELF" or header[4] != 2:
        raise SystemExit(f"not a 64-bit ELF library: {name}")
    byte_order = "<" if header[5] == 1 else ">" if header[5] == 2 else None
    if byte_order is None:
        raise SystemExit(f"invalid ELF byte order: {name}")
    machine = struct.unpack(f"{byte_order}H", header[18:20])[0]
    program_offset = struct.unpack(f"{byte_order}Q", header[32:40])[0]
    entry_size = struct.unpack(f"{byte_order}H", header[54:56])[0]
    entry_count = struct.unpack(f"{byte_order}H", header[56:58])[0]
    if entry_size < 56 or entry_count < 1:
        raise SystemExit(f"invalid ELF program headers: {name}")
    alignments: list[int] = []
    for index in range(entry_count):
        stream.seek(program_offset + index * entry_size)
        entry = stream.read(entry_size)
        if len(entry) != entry_size:
            raise SystemExit(f"truncated ELF program headers: {name}")
        if struct.unpack(f"{byte_order}I", entry[:4])[0] == 1:
            alignments.append(struct.unpack(f"{byte_order}Q", entry[48:56])[0])
    return machine, tuple(alignments)


if __name__ == "__main__":
    main()
