#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: tool/build_platform.sh Android|Linux|Windows debug|profile|release" >&2
  exit 2
fi

proofray_target="$1"
proofray_mode="$2"
proofray_app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
proofray_flutter_bin="${PROOFRAY_FLUTTER_BIN:-flutter}"

case "$proofray_target" in
  Android|Linux|Windows) ;;
  *) echo "unsupported v1 target: $proofray_target" >&2; exit 2 ;;
esac
case "$proofray_mode" in
  debug|profile|release) ;;
  *) echo "unsupported build mode: $proofray_mode" >&2; exit 2 ;;
esac

proofray_python_app="$proofray_app_dir/build/python-app/$proofray_target"
proofray_site_packages="$proofray_app_dir/build/site-packages/$proofray_target"
if [[ ! -d "$proofray_python_app" || ! -d "$proofray_site_packages" ]]; then
  echo "package Python for $proofray_target before building" >&2
  exit 3
fi

export SERIOUS_PYTHON_VERSION=3.12
export SERIOUS_PYTHON_APP="$proofray_python_app"
export SERIOUS_PYTHON_SITE_PACKAGES="$proofray_site_packages"

cd "$proofray_app_dir"
case "$proofray_target" in
  Linux)
    "$proofray_flutter_bin" build linux "--$proofray_mode"
    ;;
  Windows)
    "$proofray_flutter_bin" build windows "--$proofray_mode"
    ;;
  Android)
    "$proofray_flutter_bin" build apk "--$proofray_mode" \
      --target-platform android-arm64
    ;;
esac
