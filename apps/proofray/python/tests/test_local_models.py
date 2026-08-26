import os
from pathlib import Path
import threading
import time
import stat
import sys
import textwrap

import pytest

from proofray_app.local_models import (
    GGUF_MAGIC, LocalModelRuntime, scan_model_directory,
)


def _gguf(path: Path, payload: bytes = b"\x00" * 64) -> Path:
    path.write_bytes(GGUF_MAGIC + payload)
    return path


def test_scan_reports_gguf_and_explains_what_it_cannot_load(tmp_path):
    _gguf(tmp_path / "small.gguf", b"\x00" * 16)
    _gguf(tmp_path / "big.gguf", b"\x00" * 4096)
    (tmp_path / "weights.safetensors").write_bytes(b"not gguf")
    (tmp_path / "notes.txt").write_text("ignored")

    rows = scan_model_directory(str(tmp_path))

    by_name = {item.name: item for item in rows}
    assert by_name["big.gguf"].supported is True
    assert by_name["small.gguf"].supported is True
    # Present but unusable is not the same as absent: saying why is the point.
    assert by_name["weights.safetensors"].supported is False
    assert by_name["weights.safetensors"].reason == "llama.cpp loads GGUF only"
    assert "notes.txt" not in by_name
    # Supported first, then largest, so the real choice is at the top.
    assert [item.name for item in rows][:2] == ["big.gguf", "small.gguf"]


def test_scan_trusts_the_magic_not_the_extension(tmp_path):
    _gguf(tmp_path / "model.bin")
    rows = scan_model_directory(str(tmp_path))
    assert [(item.name, item.file_format, item.supported) for item in rows] == [
        ("model.bin", "gguf", True),
    ]


def test_scan_is_bounded_and_refuses_a_missing_directory(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    _gguf(deep / "buried.gguf")
    assert scan_model_directory(str(tmp_path)) == []
    with pytest.raises(ValueError):
        scan_model_directory(str(tmp_path / "absent"))


def test_load_refuses_a_file_that_is_not_gguf(tmp_path):
    plain = tmp_path / "model.gguf"
    plain.write_bytes(b"nope")
    runtime = LocalModelRuntime()
    with pytest.raises(ValueError, match="GGUF"):
        runtime.load(str(plain), server_binary=sys.executable)


def test_load_waits_for_the_server_then_reports_its_endpoint(tmp_path):
    """A model is ready when the server answers, not when it is spawned.

    Weights reach VRAM before llama.cpp starts serving, so readiness is the only
    honest completion signal -- this stands in a server that deliberately takes
    a moment to open its port.
    """
    model = _gguf(tmp_path / "tiny.gguf")
    fake = tmp_path / "llama-server"
    fake.write_text(textwrap.dedent("""
        import sys, time
        from http.server import BaseHTTPRequestHandler, HTTPServer

        port = int(sys.argv[sys.argv.index("--port") + 1])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data": []}')
            def log_message(self, *args):
                pass

        time.sleep(0.4)
        HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    """).strip())
    launcher = tmp_path / "launch.sh"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)

    runtime = LocalModelRuntime()
    try:
        pending = runtime.load(str(model), server_binary=str(launcher))
        assert pending["state"] == "loading"
        assert pending["endpoint"] is None

        ready = runtime.wait_until_ready(timeout=30)
        assert ready["state"] == "ready", ready
        assert str(ready["endpoint"]).startswith("http://127.0.0.1:")
        assert ready["model_path"] == str(model)
    finally:
        runtime.unload()

    assert runtime.status()["state"] == "idle"
    assert runtime.status()["endpoint"] is None


def test_a_server_that_dies_is_reported_as_failed_not_ready(tmp_path):
    model = _gguf(tmp_path / "tiny.gguf")
    launcher = tmp_path / "launch.sh"
    launcher.write_text("#!/bin/sh\nexit 3\n")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)

    runtime = LocalModelRuntime()
    runtime.load(str(model), server_binary=str(launcher))
    result = runtime.wait_until_ready(timeout=15)

    assert result["state"] == "failed"
    assert "llama_server_exited" in str(result["detail"])
    assert result["endpoint"] is None


def test_a_non_executable_server_path_is_rejected_before_spawning(tmp_path):
    model = _gguf(tmp_path / "tiny.gguf")
    plain = tmp_path / "not-a-binary"
    plain.write_text("#!/bin/sh\n")
    os.chmod(plain, 0o644)
    runtime = LocalModelRuntime()
    with pytest.raises(ValueError, match="executable"):
        runtime.load(str(model), server_binary=str(plain))


def test_the_server_outlives_the_thread_that_started_it(tmp_path):
    """PR_SET_PDEATHSIG is scoped to the spawning THREAD, not the process.

    Measured, not assumed: spawning from a pooled worker killed a healthy model
    the moment that worker exited. Every spawn is funnelled through one
    long-lived thread precisely so the two events cannot be confused.
    """
    model = _gguf(tmp_path / "tiny.gguf")
    launcher = _sleeping_server(tmp_path)
    runtime = LocalModelRuntime()
    holder: list[int] = []

    def spawn() -> None:
        runtime.load(str(model), server_binary=str(launcher))
        holder.append(runtime._process.pid)

    worker = threading.Thread(target=spawn)
    worker.start()
    worker.join()  # the thread that called Popen is now gone

    try:
        time.sleep(1.5)
        assert runtime._process.poll() is None, (
            "the model died with the thread that started it")
    finally:
        runtime.unload()


def test_unload_stops_the_server(tmp_path):
    """A model left running would keep holding the GPU memory it loaded."""
    model = _gguf(tmp_path / "tiny.gguf")
    runtime = LocalModelRuntime()
    runtime.load(str(model), server_binary=str(_sleeping_server(tmp_path)))
    process = runtime._process
    assert process.poll() is None

    runtime.unload()

    assert process.poll() is not None
    assert runtime.status()["state"] == "idle"


def _sleeping_server(tmp_path: Path) -> Path:
    """A stand-in that just stays alive, so lifetime is what is measured."""
    launcher = tmp_path / "sleeper.sh"
    launcher.write_text("#!/bin/sh\nexec sleep 120\n")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return launcher
