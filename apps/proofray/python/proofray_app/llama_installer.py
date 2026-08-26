"""Fetches an official llama.cpp build and verifies it before it is used.

ProofRay never invents a binary. It asks GitHub which assets a release has,
downloads one over HTTPS, and checks the SHA-256 that the same API published
for that asset before anything is extracted or made executable. Hardcoding
digests would go stale on every release; reading the release's own digest keeps
the check honest without pinning the app to one build forever.

Nothing here runs automatically. Installing is an explicit action, and the
memory core itself stays offline exactly as before.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlparse
import zipfile


RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
RELEASE_HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")
MAX_RELEASE_BYTES = 4 * 1024 * 1024
MAX_ASSET_BYTES = 2 * 1024 * 1024 * 1024
_CHUNK = 1024 * 256
_TIMEOUT = 60

# Accelerator families, most portable first. Vulkan runs on AMD, NVIDIA and
# Intel alike, so it is the sane default when someone does not know or care
# which stack their GPU speaks.
_VARIANT_ORDER = ("vulkan", "cpu", "rocm", "cuda", "sycl", "openvino", "opencl")


@dataclass(frozen=True)
class LlamaBuild:
    tag: str
    asset: str
    variant: str
    url: str
    size_bytes: int
    sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "asset": self.asset,
            "variant": self.variant,
            "url": self.url,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def host_platform() -> tuple[str, str]:
    """The (os, arch) pair used to match release asset names."""
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    name = {"linux": "ubuntu", "darwin": "macos", "windows": "win"}.get(system, system)
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return name, arch


def _variant_of(asset: str) -> str:
    lowered = asset.casefold()
    for variant in ("rocm", "cuda", "vulkan", "sycl", "openvino", "opencl"):
        if variant in lowered:
            return variant
    return "cpu"


def _read_json(url: str) -> object:
    if _host_of(url) not in RELEASE_HOSTS:
        raise ValueError("unexpected release host")
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ProofRay",
    })
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read(MAX_RELEASE_BYTES + 1).decode("utf-8"))


def available_builds(*, limit: int = 12) -> list[LlamaBuild]:
    """Release assets that match this machine, newest release first."""
    name, arch = host_platform()
    try:
        releases = _read_json(f"{RELEASES_API}?per_page={max(1, min(limit, 30))}")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        raise RuntimeError("llama_release_index_unavailable") from None
    if not isinstance(releases, list):
        raise RuntimeError("llama_release_index_invalid")
    builds: list[LlamaBuild] = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        tag = release.get("tag_name")
        assets = release.get("assets")
        if not isinstance(tag, str) or not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_name = asset.get("name")
            url = asset.get("browser_download_url")
            digest = asset.get("digest")
            size = asset.get("size")
            if (not isinstance(asset_name, str) or not isinstance(url, str)
                    or not isinstance(digest, str) or not isinstance(size, int)):
                continue
            if not digest.startswith("sha256:"):
                # Without a published digest there is nothing to verify against,
                # and an unverified binary is not worth offering.
                continue
            if f"-bin-{name}-" not in asset_name or not asset_name.endswith(
                    (".tar.gz", ".zip")):
                continue
            if not asset_name.endswith((f"-{arch}.tar.gz", f"-{arch}.zip")):
                continue
            builds.append(LlamaBuild(
                tag, asset_name, _variant_of(asset_name), url, size,
                digest.split(":", 1)[1]))
        if builds:
            # One release is enough: mixing builds from different tags would
            # offer combinations nobody tested together.
            break
    builds.sort(key=lambda item: (
        _VARIANT_ORDER.index(item.variant) if item.variant in _VARIANT_ORDER
        else len(_VARIANT_ORDER), item.asset))
    return builds


def install_build(build: LlamaBuild, destination: str, *,
                  on_progress: Callable[[int, int], None] | None = None) -> str:
    """Download, verify and unpack one build; returns the llama-server path.

    The digest is checked before a single byte is extracted. A mismatch deletes
    the download and raises rather than leaving a partial or wrong binary
    somewhere the app would later execute.
    """
    if _host_of(build.url) not in RELEASE_HOSTS:
        raise RuntimeError("llama_unexpected_asset_host")
    root = Path(destination).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(build.url, headers={"User-Agent": "ProofRay"})
    digest = hashlib.sha256()
    received = 0
    with tempfile.NamedTemporaryFile(
            delete=False, dir=root, suffix=".partial") as staging:
        staged = Path(staging.name)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
                total = build.size_bytes
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_ASSET_BYTES:
                        raise RuntimeError("llama_asset_too_large")
                    digest.update(chunk)
                    staging.write(chunk)
                    if on_progress is not None:
                        on_progress(received, total)
        except (urllib.error.URLError, OSError, TimeoutError):
            staged.unlink(missing_ok=True)
            raise RuntimeError("llama_download_failed") from None
    if digest.hexdigest() != build.sha256:
        staged.unlink(missing_ok=True)
        raise RuntimeError("llama_digest_mismatch")
    target = root / build.tag
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    try:
        _extract(staged, build.asset, target)
    finally:
        staged.unlink(missing_ok=True)
    server = _find_server(target)
    if server is None:
        raise RuntimeError("llama_server_missing_from_build")
    os.chmod(server, os.stat(server).st_mode | 0o755)
    return str(server)


def _extract(archive: Path, asset_name: str, target: Path) -> None:
    if asset_name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            _guard_members(bundle.namelist(), target)
            bundle.extractall(target)  # noqa: S202 - members guarded above
        return
    with tarfile.open(archive, "r:gz") as bundle:
        names = bundle.getnames()
        _guard_members(names, target)
        bundle.extractall(target, filter="data")  # noqa: S202 - guarded + filtered


def _guard_members(names: list[str], target: Path) -> None:
    """Refuse archives that would write outside the install directory."""
    root = target.resolve()
    for name in names:
        resolved = (target / name).resolve()
        if resolved != root and root not in resolved.parents:
            raise RuntimeError("llama_archive_path_escape")


def _find_server(root: Path) -> Path | None:
    candidates = ("llama-server", "llama-server.exe")
    for current, _directories, files in os.walk(root):
        for name in files:
            if name in candidates:
                return Path(current, name)
    return None


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").casefold()


__all__ = ["LlamaBuild", "available_builds", "host_platform", "install_build"]
