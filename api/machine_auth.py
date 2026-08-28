# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A local, single-operator ProofRay access token bound to this machine.

ProofRay's HTTP API has no multi-tenant concept and is meant to run on the operator's own machine
(default bind `127.0.0.1`, see `server.py`). The property this module provides is narrower than a
real multi-user auth system, and is deliberately not presented as one: a random token is generated
once, on first run, and persisted locally alongside a best-effort OS machine identifier recomputed
on every request -- so a copy of the credentials file moved to a different machine stops working
there. Nothing here can stop code already running on THIS machine from reading its own token file;
that is the same limit any locally-stored secret has, not a defect specific to this design. See
`api/README.md`'s "Authentication" section for why a heavier scheme (OAuth/JWT) was not chosen for
this stage of the project.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import stat
import subprocess
from pathlib import Path


def credentials_path() -> Path:
    override = os.environ.get("PROOFRAY_API_CREDENTIALS_PATH") or os.environ.get(
        "HORIZON_API_CREDENTIALS_PATH")
    if override:
        return Path(override)
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    proofray_path = base / "proofray" / "api_credentials.json"
    legacy_path = base / "horizon-memory" / "api_credentials.json"
    # Reuse an existing alpha credential rather than silently rotating its bearer token.
    if not proofray_path.exists() and legacy_path.exists():
        return legacy_path
    return proofray_path


def _read_linux_machine_id() -> str | None:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def _read_macos_hardware_uuid() -> str | None:
    try:
        output = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in output.splitlines():
        if "IOPlatformUUID" in line:
            parts = line.split('"')
            if len(parts) >= 4:
                return parts[3]
    return None


def _read_windows_machine_guid() -> str | None:
    try:
        import winreg  # Windows-only stdlib module.
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _kind = winreg.QueryValueEx(key, "MachineGuid")
    except OSError:
        return None
    return value or None


def raw_machine_identifier() -> str | None:
    """Best-effort, no-privilege-required OS machine identifier. `None` when unavailable (some
    containers/CI images) -- callers fall back to a persisted random value in that case, see
    `ensure_local_credentials`."""
    system = platform.system()
    if system == "Linux":
        return _read_linux_machine_id()
    if system == "Darwin":
        return _read_macos_hardware_uuid()
    if system == "Windows":
        return _read_windows_machine_guid()
    return None


def machine_fingerprint(raw_identifier: str) -> str:
    return hashlib.sha256(raw_identifier.encode("utf-8")).hexdigest()


def ensure_local_credentials(path: Path | None = None) -> dict:
    """Idempotent: returns the existing credentials unchanged on every call after the first.
    Creates the parent directory and the file (mode 0600 on POSIX) only if missing."""
    path = path or credentials_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    raw_identifier = raw_machine_identifier()
    if raw_identifier is None:
        # No OS machine-id reachable (some containers/CI images) -- fall back to a random,
        # locally-persisted identifier. Still "this installation", just not tied to hardware.
        raw_identifier = secrets.token_hex(16)

    credentials = {
        "token": secrets.token_hex(32),
        "machine_fingerprint": machine_fingerprint(raw_identifier),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(credentials, indent=2) + "\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600: owner read/write only.
    return credentials


def current_machine_matches(stored_fingerprint: str) -> bool:
    raw_identifier = raw_machine_identifier()
    if raw_identifier is None:
        # No OS machine-id reachable at verification time either -- cannot prove a mismatch, so
        # this check is skipped; the bearer token itself is still required regardless.
        return True
    return secrets.compare_digest(machine_fingerprint(raw_identifier), stored_fingerprint)


def verify_bearer_token(authorization_header: str | None, credentials: dict) -> bool:
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return False
    presented = authorization_header[len("Bearer "):].strip()
    # Encoded to bytes because compare_digest raises TypeError on two `str` operands unless both
    # are pure ASCII -- a client sending a non-ASCII bearer token (malformed or malicious) would
    # otherwise crash this check with a 500 instead of failing it with a clean 401.
    if not secrets.compare_digest(presented.encode("utf-8"), credentials["token"].encode("utf-8")):
        return False
    return current_machine_matches(credentials["machine_fingerprint"])
