from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    lock = json.loads((ROOT / "toolchain.lock.json").read_text(encoding="utf-8"))
    pubspec = (ROOT / "pubspec.yaml").read_text(encoding="utf-8")
    cmake = (ROOT / "packages/proofray_duckdb/src/CMakeLists.txt").read_text(
        encoding="utf-8")
    fetch = (ROOT / "tool/fetch_duckdb.sh").read_text(encoding="utf-8")
    android_plugin = (
        ROOT / "packages/proofray_duckdb/android/build.gradle"
    ).read_text(encoding="utf-8")
    workflow = (
        ROOT.parents[1] / ".github/workflows/proofray-app.yml"
    ).read_text(encoding="utf-8")
    package_python = (ROOT / "tool/package_python.sh").read_text(
        encoding="utf-8")
    embedded_runtime = (
        ROOT / "lib/services/runtime/embedded_python_runtime.dart"
    ).read_text(encoding="utf-8")
    vendored = json.loads(json.dumps({
        "version": re.search(
            r"^version:\s*(\S+)",
            (ROOT / "packages/dart_duckdb_core/pubspec.yaml").read_text(
                encoding="utf-8"), re.MULTILINE).group(1),
    }))

    expected_packages = lock["flutter_packages"]
    for package in ("serious_python", "drift", "sqlite3", "flutter_secure_storage"):
        pattern = rf"^\s*{re.escape(package)}:\s*{re.escape(expected_packages[package])}\s*$"
        if re.search(pattern, pubspec, re.MULTILINE) is None:
            raise SystemExit(f"pubspec pin differs from toolchain lock: {package}")
    if vendored["version"] != expected_packages["dart_duckdb_api"]:
        raise SystemExit("vendored Dart DuckDB version differs from toolchain lock")

    duckdb = lock["duckdb"]
    required = (
        duckdb["engine_version"],
        duckdb["linux_x86_64_sha256"],
        duckdb["windows_x86_64_sha256"],
        duckdb["android_source_sha256"],
    )
    for value in required:
        if value not in cmake or value not in fetch:
            raise SystemExit("DuckDB CMake/fetch pins differ from toolchain lock")

    android = lock["android_native"]
    for value in (
            android["ndk_version"], android["cmake_version"],
            str(android["min_sdk"]), str(android["compile_sdk"])):
        if value not in android_plugin:
            raise SystemExit(
                "Android native plugin differs from toolchain lock")
    for value in (android["ndk_version"], android["cmake_version"]):
        if value not in workflow:
            raise SystemExit("Android CI differs from toolchain lock")

    requirements = (ROOT / "python/requirements-mobile.txt").read_text(
        encoding="utf-8").splitlines()
    if not requirements or any(
            not re.fullmatch(r"[A-Za-z0-9_.-]+==[^=\s]+", line)
            for line in requirements):
        raise SystemExit("embedded Python requirements must all be exact pins")
    names = [line.partition("==")[0].casefold() for line in requirements]
    if len(names) != len(set(names)):
        raise SystemExit("embedded Python requirements contain duplicate packages")

    if "--compile-app" not in package_python or "--cleanup" not in package_python:
        raise SystemExit("embedded app must be compiled and source-cleaned")
    if "appFileName:" in embedded_runtime:
        raise SystemExit(
            "Serious Python must auto-detect the packaged entrypoint "
            "(main.py, kept uncompiled -- see tool/package_python.sh); "
            "forcing appFileName breaks that resolution"
        )


if __name__ == "__main__":
    main()
