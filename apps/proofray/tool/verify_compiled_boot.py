from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SCHEMA = "proofray.app.bridge.v1"


def main() -> None:
    target = sys.argv[1] if len(sys.argv) == 2 else _host_target()
    app_dir = ROOT / "build" / "python-app" / target
    site_packages = ROOT / "build" / "site-packages" / target
    # main.py stays uncompiled -- dart_bridge's native entrypoint execution
    # (Py_CompileString) cannot load a compiled main.pyc, only source; see
    # tool/package_python.sh's own comment. Application code it imports
    # still ships compiled.
    entrypoint = app_dir / "main.py"
    if not entrypoint.is_file() or (app_dir / "main.pyc").exists():
        raise SystemExit("compiled package must contain main.py and no main.pyc")

    with tempfile.TemporaryDirectory(prefix="proofray-compiled-boot-") as raw:
        runtime_dir = Path(raw)
        bootstrap = runtime_dir / "bootstrap.json"
        bootstrap.write_text(
            json.dumps({
                "schema": "proofray.app.bootstrap.v1",
                "profile_name": "Compiled boot verifier",
                "timezone": "UTC",
            }, separators=(",", ":")),
            encoding="utf-8",
        )
        token = "a" * 64
        env = os.environ.copy()
        env.update({
            "PROOFRAY_APP_BOOTSTRAP": str(bootstrap),
            "PROOFRAY_APP_TOKEN": token,
            "PYTHONPATH": os.pathsep.join((str(site_packages), str(app_dir))),
            "PYTHONUTF8": "1",
        })
        process = subprocess.Popen(
            [sys.executable, str(entrypoint)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            runtime = _wait_for_runtime(runtime_dir / "runtime.json", process)
            with socket.create_connection(
                ("127.0.0.1", runtime["port"]), timeout=5,
            ) as stream:
                request = json.dumps({
                    "schema": BRIDGE_SCHEMA,
                    "request_id": "compiled_boot",
                    "method": "bridge.authenticate",
                    "payload": {"token": token},
                }, separators=(",", ":")).encode("utf-8") + b"\n"
                stream.sendall(request)
                response = json.loads(_read_line(stream))
            expected = {
                "schema": BRIDGE_SCHEMA,
                "request_id": "compiled_boot",
                "event": "authenticated",
                "payload": {},
            }
            if response != expected:
                raise RuntimeError(
                    "compiled bridge authentication response differs")
        finally:
            process.terminate()
            try:
                _, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _, stderr = process.communicate(timeout=5)
            if process.returncode not in (0, -15, 15) and stderr:
                raise RuntimeError("compiled runtime exited unexpectedly")


def _host_target() -> str:
    if sys.platform.startswith("linux"):
        return "Linux"
    if sys.platform == "win32":
        return "Windows"
    raise SystemExit("compiled boot verifier supports Linux and Windows hosts")


def _wait_for_runtime(
        path: Path, process: subprocess.Popen[bytes]) -> dict[str, int | str]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"compiled runtime stopped before ready: {stderr[:500]}")
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                value = None
            if (isinstance(value, dict)
                    and value.get("schema") == "proofray.app.runtime.v1"
                    and isinstance(value.get("port"), int)):
                return value
        time.sleep(0.05)
    raise TimeoutError("compiled runtime did not become ready")


def _read_line(stream: socket.socket) -> bytes:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = stream.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > 1024 * 1024:
            raise RuntimeError(
                "compiled bridge response exceeded protocol limit")
    if not data.endswith(b"\n"):
        raise RuntimeError("compiled bridge returned an incomplete frame")
    return bytes(data[:-1])


if __name__ == "__main__":
    main()
