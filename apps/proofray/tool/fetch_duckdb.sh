#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: tool/fetch_duckdb.sh Linux|Windows|Android" >&2
  exit 2
fi

proofray_target="$1"
proofray_app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
proofray_output="$proofray_app_dir/build/duckdb/$proofray_target"
proofray_version="1.4.2"

case "$proofray_target" in
  Linux)
    proofray_asset="libduckdb-linux-amd64.zip"
    proofray_sha="0a68e623cbdcfa06e10e1b5d6a3092d3fd3679f616008fb1a39b92e4a10d8f43"
    proofray_library="libduckdb.so"
    ;;
  Windows)
    proofray_asset="libduckdb-windows-amd64.zip"
    proofray_sha="4d3e5f2ee009a8fff5b8d43bc50f61c6b246b2b227748bc900f8520ec8fac0b0"
    proofray_library="duckdb.dll"
    ;;
  Android)
    proofray_asset="libduckdb-src.zip"
    proofray_sha="ee7e178341ea8199ad52eabdff07aa89969f9904868eaa94e71efb31eaef7f2d"
    proofray_library="duckdb.cpp"
    ;;
  *)
    echo "unsupported DuckDB target: $proofray_target" >&2
    exit 2
    ;;
esac

proofray_archive="$proofray_output/$proofray_asset"
mkdir -p "$proofray_output"
if [[ ! -f "$proofray_archive" ]]; then
  curl -fL \
    "https://github.com/duckdb/duckdb/releases/download/v$proofray_version/$proofray_asset" \
    -o "$proofray_archive"
fi
printf '%s  %s\n' "$proofray_sha" "$proofray_archive" | sha256sum --check --status
unzip -oq "$proofray_archive" -d "$proofray_output"
if [[ ! -f "$proofray_output/$proofray_library" ]]; then
  echo "verified DuckDB archive lacks $proofray_library" >&2
  exit 3
fi
printf '%s\n' "$proofray_output/$proofray_library"
