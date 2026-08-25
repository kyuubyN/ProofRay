#!/usr/bin/env bash
set -euo pipefail

expected_flutter="6655482ec06e547f90abf8ae7590466f4415978d"
expected_dart="3.13.1"
proofray_flutter_bin="${PROOFRAY_FLUTTER_BIN:-flutter}"
proofray_python_bin="${PROOFRAY_PYTHON_BIN:-python3}"

if ! command -v "$proofray_python_bin" >/dev/null 2>&1; then
  proofray_python_bin="python"
fi

if ! command -v "$proofray_flutter_bin" >/dev/null 2>&1; then
  echo "Flutter 3.47.1 is not installed." >&2
  exit 1
fi

machine="$("$proofray_flutter_bin" --version --machine)"
revision="$(printf '%s' "$machine" | "$proofray_python_bin" -c 'import json,sys; print(json.load(sys.stdin)["frameworkRevision"])')"
dart_version="$(printf '%s' "$machine" | "$proofray_python_bin" -c 'import json,sys; print(json.load(sys.stdin)["dartSdkVersion"])')"

if [[ "$revision" != "$expected_flutter" || "$dart_version" != "$expected_dart" ]]; then
  echo "Toolchain mismatch: Flutter=$revision Dart=$dart_version" >&2
  exit 1
fi
