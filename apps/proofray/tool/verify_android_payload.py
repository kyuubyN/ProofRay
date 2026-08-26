from __future__ import annotations

from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "build/python-app/Android"
ARM64 = ROOT / "build/site-packages/Android/arm64-v8a"


def main() -> None:
    # main.py stays uncompiled and (APP / "main.pyc") absent: dart_bridge's
    # native entrypoint execution (Py_CompileString) cannot load compiled
    # bytecode, only source -- see tool/package_python.sh's own comment.
    # Everything main.py imports (proofray_app/, proofray/, horizon_memory/)
    # still ships compiled.
    if not (APP / "main.py").is_file() or (APP / "main.pyc").exists():
        raise SystemExit("Android app must contain uncompiled main.py only")
    if not (ARM64 / "tzdata/__init__.pyc").is_file():
        raise SystemExit("Android arm64 payload lacks the pinned IANA tzdata package")
    libraries = sorted(ARM64.rglob("*.so"))
    if not libraries:
        raise SystemExit("Android arm64 site-packages contain no native libraries")
    for library in libraries:
        machine, load_alignments = _elf_identity(library)
        if machine != 183:
            raise SystemExit(
                f"non-aarch64 library in arm64 payload: {library.relative_to(ROOT)}")
        if not load_alignments or min(load_alignments) < 16 * 1024:
            raise SystemExit(
                f"library lacks Android 16 KiB LOAD alignment: "
                f"{library.relative_to(ROOT)}")


def _elf_identity(path: Path) -> tuple[int, tuple[int, ...]]:
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:4] != b"\x7fELF" or header[4] != 2:
            raise SystemExit(f"not a 64-bit ELF library: {path.relative_to(ROOT)}")
        byte_order = "<" if header[5] == 1 else ">" if header[5] == 2 else None
        if byte_order is None:
            raise SystemExit(f"invalid ELF byte order: {path.relative_to(ROOT)}")
        machine = struct.unpack(f"{byte_order}H", header[18:20])[0]
        program_offset = struct.unpack(f"{byte_order}Q", header[32:40])[0]
        entry_size = struct.unpack(f"{byte_order}H", header[54:56])[0]
        entry_count = struct.unpack(f"{byte_order}H", header[56:58])[0]
        if entry_size < 56 or entry_count < 1:
            raise SystemExit(f"invalid ELF program headers: {path.relative_to(ROOT)}")
        alignments: list[int] = []
        for index in range(entry_count):
            stream.seek(program_offset + index * entry_size)
            entry = stream.read(entry_size)
            if len(entry) != entry_size:
                raise SystemExit(
                    f"truncated ELF program headers: {path.relative_to(ROOT)}")
            entry_type = struct.unpack(f"{byte_order}I", entry[:4])[0]
            if entry_type == 1:  # PT_LOAD
                alignments.append(struct.unpack(
                    f"{byte_order}Q", entry[48:56])[0])
        return machine, tuple(alignments)


if __name__ == "__main__":
    main()
