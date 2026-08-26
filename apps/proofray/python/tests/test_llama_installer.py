import hashlib
from pathlib import Path
import tarfile
import zipfile

import pytest

from proofray_app.llama_installer import (
    LlamaBuild, host_platform, install_build,
)


def _archive(tmp_path: Path, members: dict[str, bytes], *, zipped: bool = False) -> Path:
    if zipped:
        path = tmp_path / "build.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            for name, payload in members.items():
                bundle.writestr(name, payload)
        return path
    path = tmp_path / "build.tar.gz"
    with tarfile.open(path, "w:gz") as bundle:
        for name, payload in members.items():
            blob = tmp_path / Path(name).name
            blob.write_bytes(payload)
            bundle.add(blob, arcname=name)
    return path


def _build(archive: Path, *, sha256: str | None = None) -> LlamaBuild:
    payload = archive.read_bytes()
    return LlamaBuild(
        tag="btest",
        asset=archive.name,
        variant="cpu",
        url=f"https://github.com/ggml-org/llama.cpp/releases/download/btest/{archive.name}",
        size_bytes=len(payload),
        sha256=sha256 or hashlib.sha256(payload).hexdigest(),
    )


def test_host_platform_names_a_real_asset_shape():
    name, arch = host_platform()
    assert name in {"ubuntu", "macos", "win"}
    assert arch in {"x64", "arm64"}


def test_a_wrong_digest_is_never_extracted(tmp_path, monkeypatch):
    """The published SHA-256 is the whole reason this is safe to run.

    A mismatch has to stop before anything lands on disk that the app would
    later execute -- reporting the failure afterwards would be too late.
    """
    archive = _archive(tmp_path, {"llama-server": b"#!/bin/sh\necho hi\n"})
    build = _build(archive, sha256="0" * 64)
    _serve(monkeypatch, archive)
    destination = tmp_path / "install"

    with pytest.raises(RuntimeError, match="digest_mismatch"):
        install_build(build, str(destination))

    assert list(destination.rglob("llama-server")) == []


def test_a_verified_build_yields_an_executable_server(tmp_path, monkeypatch):
    archive = _archive(tmp_path, {"inner/llama-server": b"#!/bin/sh\nexit 0\n"})
    build = _build(archive)
    _serve(monkeypatch, archive)
    seen: list[tuple[int, int]] = []

    server = install_build(
        build, str(tmp_path / "install"), on_progress=lambda a, b: seen.append((a, b)))

    assert Path(server).name == "llama-server"
    assert Path(server).stat().st_mode & 0o111
    assert seen and seen[-1][0] == build.size_bytes


def test_an_archive_that_escapes_its_directory_is_refused(tmp_path, monkeypatch):
    """A build is untrusted input until it is unpacked, not after."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escaped.txt", b"nope")
    build = _build(archive)
    _serve(monkeypatch, archive)

    with pytest.raises(RuntimeError, match="path_escape"):
        install_build(build, str(tmp_path / "install"))

    assert not (tmp_path / "escaped.txt").exists()


def test_an_asset_from_another_host_is_refused_before_any_request(tmp_path):
    archive = _archive(tmp_path, {"llama-server": b"x"})
    build = _build(archive)
    hijacked = LlamaBuild(
        build.tag, build.asset, build.variant,
        "https://example.invalid/llama-server.tar.gz",
        build.size_bytes, build.sha256,
    )
    with pytest.raises(RuntimeError, match="unexpected_asset_host"):
        install_build(hijacked, str(tmp_path / "install"))


def test_a_build_without_a_server_binary_is_reported(tmp_path, monkeypatch):
    archive = _archive(tmp_path, {"docs/readme.txt": b"no binary here"})
    build = _build(archive)
    _serve(monkeypatch, archive)
    with pytest.raises(RuntimeError, match="server_missing"):
        install_build(build, str(tmp_path / "install"))


def _serve(monkeypatch, archive: Path) -> None:
    """Answer the download from disk, so no test touches the network."""
    class _Response:
        def __init__(self, payload: bytes):
            self._payload = payload
            self._offset = 0

        def read(self, size: int = -1) -> bytes:
            if size is None or size < 0:
                size = len(self._payload) - self._offset
            chunk = self._payload[self._offset:self._offset + size]
            self._offset += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *exception):
            return False

    payload = archive.read_bytes()
    monkeypatch.setattr(
        "proofray_app.llama_installer.urllib.request.urlopen",
        lambda *args, **kwargs: _Response(payload),
    )
