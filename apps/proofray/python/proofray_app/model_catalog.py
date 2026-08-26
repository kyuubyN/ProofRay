"""A short starter catalogue of GGUF models, resolved live.

Only the repositories are named here. Their actual files, sizes, quantisations
and checksums come from Hugging Face at request time, so this list does not go
stale every time a repo publishes a new quant -- and every download is verified
against the SHA-256 that Hugging Face itself records for the file (an LFS oid).

This is a starting point, never a boundary: anyone can drop their own GGUF into
the same folder and it is picked up by the ordinary directory scan.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlparse


HUGGINGFACE_HOSTS = ("huggingface.co", "cdn-lfs.huggingface.co",
                     "cdn-lfs-us-1.hf.co", "cas-bridge.xethub.hf.co")
MAX_TREE_BYTES = 4 * 1024 * 1024
MAX_MODEL_BYTES = 64 * 1024 * 1024 * 1024
_CHUNK = 1024 * 512
_TIMEOUT = 60


# Families offered as one-tap filters. These are search shortcuts, not the
# boundary of what can be installed: the search box reaches every GGUF
# repository on Hugging Face, and this list only saves typing for the ones
# people ask for most.
FAMILIES: tuple[dict[str, str], ...] = (
    {"key": "", "label": "Trending"},
    {"key": "qwen", "label": "Qwen"},
    {"key": "llama", "label": "Llama"},
    {"key": "gemma", "label": "Gemma"},
    {"key": "granite", "label": "Granite"},
    {"key": "glm", "label": "GLM"},
    {"key": "mistral", "label": "Mistral"},
    {"key": "phi", "label": "Phi"},
    {"key": "deepseek", "label": "DeepSeek"},
)


@dataclass(frozen=True)
class ModelSummary:
    repository: str
    downloads: int
    likes: int
    updated: str

    def payload(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "downloads": self.downloads,
            "likes": self.likes,
            "updated": self.updated,
        }


def families() -> list[dict[str, str]]:
    return [dict(item) for item in FAMILIES]


def search_models(query: str = "", *, limit: int = 40) -> list[ModelSummary]:
    """GGUF repositories on Hugging Face, most downloaded first.

    Live rather than curated: a fixed list would be wrong within weeks, and the
    interesting model is often the one released yesterday.
    """
    parameters = [
        ("filter", "gguf"),
        ("sort", "downloads"),
        ("direction", "-1"),
        ("limit", str(max(1, min(limit, 100)))),
    ]
    if isinstance(query, str) and query.strip():
        parameters.append(("search", query.strip()[:120]))
    url = "https://huggingface.co/api/models?" + urlencode(parameters)
    request = urllib.request.Request(url, headers={"User-Agent": "ProofRay"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
            listing = json.loads(response.read(MAX_TREE_BYTES + 1).decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
        raise RuntimeError("model_search_unavailable") from None
    if not isinstance(listing, list):
        raise RuntimeError("model_search_invalid")
    results: list[ModelSummary] = []
    for item in listing:
        if not isinstance(item, dict):
            continue
        repository = item.get("modelId") or item.get("id")
        if not isinstance(repository, str) or "/" not in repository:
            continue
        results.append(ModelSummary(
            repository,
            int(item.get("downloads") or 0),
            int(item.get("likes") or 0),
            str(item.get("lastModified") or "")[:10],
        ))
    return results


@dataclass(frozen=True)
class CatalogFile:
    repository: str
    filename: str
    size_bytes: int
    sha256: str

    @property
    def url(self) -> str:
        return (f"https://huggingface.co/{self.repository}/resolve/main/"
                f"{quote(self.filename)}?download=true")

    def payload(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "url": self.url,
        }


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").casefold()


def repository_files(repository: str) -> list[CatalogFile]:
    """GGUF files a repository actually publishes right now, smallest first."""
    if not isinstance(repository, str) or "/" not in repository:
        raise ValueError("a huggingface repository is required")
    url = f"https://huggingface.co/api/models/{repository}/tree/main"
    request = urllib.request.Request(url, headers={"User-Agent": "ProofRay"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
            listing = json.loads(response.read(MAX_TREE_BYTES + 1).decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
        raise RuntimeError("model_index_unavailable") from None
    if not isinstance(listing, list):
        raise RuntimeError("model_index_invalid")
    files: list[CatalogFile] = []
    for item in listing:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        size = item.get("size")
        lfs = item.get("lfs")
        if (not isinstance(path, str) or not path.casefold().endswith(".gguf")
                or not isinstance(size, int)):
            continue
        # Without a recorded checksum there is nothing to verify the download
        # against, so the file is not offered rather than trusted blindly.
        oid = lfs.get("oid") if isinstance(lfs, dict) else None
        if not isinstance(oid, str) or len(oid) != 64:
            continue
        files.append(CatalogFile(repository, path, size, oid))
    files.sort(key=lambda item: item.size_bytes)
    return files


def download_model(file: CatalogFile, destination: str, *,
                   on_progress: Callable[[int, int], None] | None = None) -> str:
    """Fetch one GGUF into the model folder, verified before it is kept.

    Downloads land on a temporary name in the same folder and are only renamed
    into place once the checksum matches, so an interrupted or corrupted
    download can never be picked up by the model scan as if it were usable.
    """
    if _host_of(file.url) not in HUGGINGFACE_HOSTS:
        raise RuntimeError("model_unexpected_host")
    root = Path(destination).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    target = root / Path(file.filename).name
    if target.exists() and target.stat().st_size == file.size_bytes:
        return str(target)
    request = urllib.request.Request(file.url, headers={"User-Agent": "ProofRay"})
    digest = hashlib.sha256()
    received = 0
    with tempfile.NamedTemporaryFile(
            delete=False, dir=root, suffix=".partial") as staging:
        staged = Path(staging.name)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_MODEL_BYTES:
                        raise RuntimeError("model_too_large")
                    digest.update(chunk)
                    staging.write(chunk)
                    if on_progress is not None:
                        on_progress(received, file.size_bytes)
        except (urllib.error.URLError, OSError, TimeoutError):
            staged.unlink(missing_ok=True)
            raise RuntimeError("model_download_failed") from None
    if digest.hexdigest() != file.sha256:
        staged.unlink(missing_ok=True)
        raise RuntimeError("model_digest_mismatch")
    staged.replace(target)
    return str(target)


__all__ = [
    "FAMILIES", "CatalogFile", "ModelSummary", "download_model", "families",
    "repository_files", "search_models",
]
