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
