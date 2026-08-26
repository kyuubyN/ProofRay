#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: tool/package_python.sh Android|Linux|Windows" >&2
  exit 2
fi

target="$1"
case "$target" in
  Android|Linux|Windows) ;;
  *) echo "unsupported v1 target: $target" >&2; exit 2 ;;
esac

proofray_host="$(uname -s)"
if [[ "$target" == "Linux" && "$proofray_host" != "Linux" ]]; then
  echo "Linux Python packages must be produced on a Linux host" >&2
  exit 3
fi
if [[ "$target" == "Windows" && ! "$proofray_host" =~ ^(MINGW|MSYS|CYGWIN) ]]; then
  echo "Windows Python packages must be produced on a Windows host" >&2
  exit 3
fi

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(cd "$app_dir/../.." && pwd)"
proofray_dart_bin="${PROOFRAY_DART_BIN:-dart}"
stage_dir="$(mktemp -d)"
trap 'rm -rf -- "$stage_dir"' EXIT

mkdir -p "$stage_dir/proofray_app" "$stage_dir/proofray" "$stage_dir/horizon_memory"
cp -R "$app_dir/python/proofray_app/." "$stage_dir/proofray_app/"
cp "$app_dir/python/main.py" "$stage_dir/main.py"
cp -R "$repo_dir/src/proofray/." "$stage_dir/proofray/"
cp -R "$repo_dir/src/horizon_memory/." "$stage_dir/horizon_memory/"
find "$stage_dir" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$stage_dir" -depth -type d -name '__pycache__' -empty -delete

export SERIOUS_PYTHON_VERSION=3.12
export SERIOUS_PYTHON_APP="$app_dir/build/python-app/$target"
export SERIOUS_PYTHON_SITE_PACKAGES="$app_dir/build/site-packages/$target"

requirements="$app_dir/python/requirements-mobile.txt"
"$proofray_dart_bin" run serious_python:main package "$stage_dir" -p "$target" \
  -r -r -r "$requirements" --compile-app --compile-packages --cleanup

# dart_bridge's native SP_RUN_PATH mode (src/serious_python_run.c's
# sp_pyrun_file, see https://github.com/flet-dev/dart-bridge) always feeds
# the resolved entrypoint's raw bytes through Py_CompileString as if they
# were source text -- it never checks for a .pyc magic header or loads
# bytecode via marshal, unlike Python's own import machinery. A compiled
# main.pyc entrypoint therefore crashes the embedded core on every real
# launch with a UTF-8 SyntaxError (confirmed: `python3 main.pyc` runs it
# fine directly through the standard interpreter, which does understand
# .pyc; only dart_bridge's own limited-API reimplementation cannot).
#
# --compile-app above still compiles the whole staged app (including the
# entrypoint) so imported application code -- proofray_app/, proofray/,
# horizon_memory/ -- ships as bytecode, same as before. Only the top-level
# entrypoint is swapped back to its original, trivial, uncompiled source
# afterwards: main.py is a 5-line stub with zero application logic (just
# `from proofray_app.bridge_server import main; main()`), so this doesn't
# meaningfully change what's readable in the shipped bundle. The compiled
# main.pyc is removed rather than left alongside main.py, because
# SeriousPython.run()'s own Dart-side resolution always prefers a .pyc
# over a .py when both exist.
target_main_pyc="$app_dir/build/python-app/$target/main.pyc"
target_main_py="$app_dir/build/python-app/$target/main.py"
if [[ -f "$target_main_pyc" ]]; then
  rm -f "$target_main_pyc"
  cp "$app_dir/python/main.py" "$target_main_py"
fi
