"""Local GGUF models served by a llama.cpp build on loopback.

ProofRay does not implement inference. It discovers model files, starts a
`llama-server` process bound to loopback, and then talks to it through the
OpenAI-compatible provider it already ships -- llama.cpp exposes exactly the
`/v1/models` and `/v1/chat/completions` surface that path expects, so nothing
about the chat, memory or proof pipeline changes for a local model.

Only GGUF is claimed. llama.cpp loads GGUF and nothing else: `.safetensors`,
`.pt` and fp8 checkpoints belong to a different runtime entirely, so they are
reported as present-but-unsupported rather than silently listed as choices that
would fail at load time.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


GGUF_MAGIC = b"GGUF"
MAX_SCAN_ENTRIES = 512
MAX_SCAN_DEPTH = 3
# Checkpoint formats people are likely to have next to their GGUF files. Naming
# them lets the UI say why they are not offered instead of hiding them.
UNSUPPORTED_SUFFIXES = {
    ".safetensors": "safetensors",
    ".pt": "pytorch",
    ".pth": "pytorch",
    ".bin": "pytorch",
    ".onnx": "onnx",
    ".npz": "numpy",
}
_READY_TIMEOUT_SECONDS = 600
_STOP_GRACE_SECONDS = 10
# prctl(PR_SET_PDEATHSIG, ...) -- see _die_with_parent below.
_PR_SET_PDEATHSIG = 1
_HAS_PDEATHSIG = sys.platform.startswith("linux")


@dataclass(frozen=True)
class LocalModelFile:
    path: str
    name: str
    size_bytes: int
    file_format: str
    supported: bool
    reason: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "format": self.file_format,
            "supported": self.supported,
            "reason": self.reason,
        }


def _is_gguf(path: Path) -> bool:
    """Read the magic rather than trusting the extension.

    A renamed file would otherwise be offered as a model and fail only once the
    server had already been started for it.
    """
    try:
        with path.open("rb") as stream:
            return stream.read(4) == GGUF_MAGIC
    except OSError:
        return False


def scan_model_directory(directory: str) -> list[LocalModelFile]:
    """List candidate model files under a directory, newest-largest first.

    Bounded in both breadth and depth: a model directory is a model directory,
    not a filesystem crawl, and someone pointing this at their home folder must
    not hang the app.
    """
    if not isinstance(directory, str) or not directory:
        raise ValueError("a model directory is required")
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise ValueError("model directory does not exist")
    found: list[LocalModelFile] = []
    for current, directories, files in os.walk(root, followlinks=False):
        depth = len(Path(current).relative_to(root).parts)
        if depth >= MAX_SCAN_DEPTH:
            directories[:] = []
        for name in files:
            if len(found) >= MAX_SCAN_ENTRIES:
                directories[:] = []
                break
            candidate = Path(current, name)
            suffix = candidate.suffix.casefold()
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            if suffix == ".gguf" or _is_gguf(candidate):
                found.append(LocalModelFile(
                    str(candidate), name, size, "gguf", True))
            elif suffix in UNSUPPORTED_SUFFIXES:
                found.append(LocalModelFile(
                    str(candidate), name, size, UNSUPPORTED_SUFFIXES[suffix],
                    False, "llama.cpp loads GGUF only"))
    found.sort(key=lambda item: (not item.supported, -item.size_bytes, item.name))
    return found


def _die_with_parent() -> None:
    """Ask the kernel to kill this child when its parent dies.

    The graceful path -- unloading on bridge shutdown -- only runs when the
    interpreter is allowed to shut down. It is not: the app terminates the
    embedded runtime outright, so a llama-server started from it survived the
    app and kept holding VRAM with nothing left to stop it.

    PR_SET_PDEATHSIG moves that guarantee to the kernel, where it holds however
    the parent goes away, including a crash or a kill -9. Best effort by
    design: on a platform without it the process simply starts normally and the
    shutdown unload still applies.

    The signal fires when the parent THREAD exits, not the parent process --
    measured, not assumed. Spawning from a pooled worker would therefore kill a
    perfectly healthy model the moment that worker was recycled, which is worse
    than the leak this exists to prevent. All spawning is funnelled through one
    thread that lives as long as the runtime does; see `_spawn_in_owner_thread`.
    """
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    except Exception:  # pragma: no cover - platform dependent
        pass


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LocalModelRuntime:
    """Owns at most one llama-server process.

    One model is loaded at a time on purpose: a second one would compete for the
    same VRAM, and the failure mode of that is an allocation error deep inside
    the server rather than anything this layer could report usefully.
    """

    def __init__(self, *, server_binary: str | None = None):
        self._binary = server_binary
        self._spawn_requests: queue.SimpleQueue = queue.SimpleQueue()
        self._spawn_thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._model_path: str | None = None
        self._port: int | None = None
        self._state = "idle"
        self._detail: str | None = None
        self._lock = threading.RLock()

    @property
    def endpoint(self) -> str | None:
        with self._lock:
            if self._port is None or self._state != "ready":
                return None
            return f"http://127.0.0.1:{self._port}/v1"

    def resolve_binary(self, explicit: str | None = None) -> str:
        """Find the llama.cpp server, preferring an explicitly configured path."""
        for candidate in (explicit, self._binary):
            if candidate:
                path = Path(candidate).expanduser()
                if path.is_file() and os.access(path, os.X_OK):
                    return str(path)
                raise ValueError("configured llama.cpp server is not executable")
        for name in ("llama-server", "llama-cpp-server", "server"):
            found = _which(name)
            if found:
                return found
        raise ValueError("llama_server_not_found")

    def status(self) -> dict[str, object]:
        with self._lock:
            alive = self._process is not None and self._process.poll() is None
            if self._state == "ready" and not alive:
                # The server died after reporting ready; say so rather than
                # keeping an endpoint that now refuses every connection.
                self._state = "failed"
                self._detail = "llama_server_exited"
            return {
                "state": self._state,
                "model_path": self._model_path,
                "endpoint": self.endpoint,
                "detail": self._detail,
            }

    def load(self, model_path: str, *, server_binary: str | None = None,
             context_size: int = 8192, gpu_layers: int = 999,
             extra_arguments: tuple[str, ...] = ()) -> dict[str, object]:
        if not isinstance(model_path, str) or not model_path:
            raise ValueError("a model path is required")
        model = Path(model_path).expanduser()
        if not model.is_file():
            raise ValueError("model file does not exist")
        if not _is_gguf(model):
            raise ValueError("model is not a GGUF file")
        binary = self.resolve_binary(server_binary)
        with self._lock:
            if self._model_path == str(model) and self._state == "ready":
                return self.status()
            self._stop_locked()
            port = _free_loopback_port()
            command = [
                binary, "--model", str(model), "--host", "127.0.0.1",
                "--port", str(port), "--ctx-size", str(context_size),
                "--n-gpu-layers", str(gpu_layers), *extra_arguments,
            ]
            try:
                self._process = self._spawn_in_owner_thread(command)
            except OSError as error:
                self._state = "failed"
                self._detail = f"spawn_failed:{errno.errorcode.get(error.errno, '')}"
                raise RuntimeError("local_model_spawn_failed") from None
            self._model_path = str(model)
            self._port = port
            self._state = "loading"
            self._detail = None
        return self.status()

    def _spawn_in_owner_thread(self, command: list[str]) -> subprocess.Popen:
        """Start the server from one thread that outlives every caller.

        PR_SET_PDEATHSIG is scoped to the spawning thread, so the thread that
        calls Popen has to last as long as the model is meant to. This one
        blocks on its queue forever and is never joined, which makes "parent
        thread exits" and "process exits" the same event.
        """
        if self._spawn_thread is None:
            self._spawn_thread = threading.Thread(
                target=self._serve_spawn_requests,
                name="proofray-local-model-spawner",
                daemon=True,
            )
            self._spawn_thread.start()
        answer: queue.SimpleQueue = queue.SimpleQueue()
        self._spawn_requests.put((command, answer))
        outcome = answer.get()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def _serve_spawn_requests(self) -> None:
        while True:
            command, answer = self._spawn_requests.get()
            try:
                answer.put(subprocess.Popen(  # noqa: S603 - explicit local binary
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    preexec_fn=_die_with_parent if _HAS_PDEATHSIG else None,
                ))
            except BaseException as error:  # noqa: BLE001 - relayed to the caller
                answer.put(error)

    def wait_until_ready(self, *, timeout: float = _READY_TIMEOUT_SECONDS) -> dict[str, object]:
        """Block until the server answers, it dies, or the deadline passes.

        Loading weights into VRAM is the slow part and llama.cpp does it before
        it starts answering, so readiness is the only honest completion signal.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                process = self._process
                port = self._port
                state = self._state
            if state in ("idle", "failed") or process is None or port is None:
                return self.status()
            if process.poll() is not None:
                with self._lock:
                    self._state = "failed"
                    self._detail = f"llama_server_exited:{process.returncode}"
                return self.status()
            if _probe_ready(port):
                with self._lock:
                    self._state = "ready"
                    self._detail = None
                return self.status()
            time.sleep(0.25)
        with self._lock:
            self._state = "failed"
            self._detail = "local_model_load_timeout"
        return self.status()

    def unload(self) -> dict[str, object]:
        with self._lock:
            self._stop_locked()
        return self.status()

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        self._model_path = None
        self._port = None
        self._state = "idle"
        self._detail = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=_STOP_GRACE_SECONDS)


def _which(name: str) -> str | None:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry, name)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _probe_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed loopback URL
            f"http://127.0.0.1:{port}/v1/models", timeout=2
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


__all__ = [
    "GGUF_MAGIC", "LocalModelFile", "LocalModelRuntime", "scan_model_directory",
]
