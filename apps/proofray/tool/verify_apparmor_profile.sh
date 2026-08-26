#!/usr/bin/env bash
# Checks the AppArmor profile parses, and that it still confines the two things
# it exists to confine. A profile that no longer names the llama.cpp child, or
# that stops denying it the database, would load cleanly and protect nothing --
# so the parse alone is not the test.
set -euo pipefail

profile_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../packaging/linux/apparmor" && pwd)"
profile="$profile_dir/io.proofray.proofray_app"

if [[ ! -f "$profile" ]]; then
  echo "missing AppArmor profile: $profile" >&2
  exit 1
fi

if ! command -v apparmor_parser >/dev/null 2>&1; then
  echo "apparmor_parser not installed; skipping syntax check" >&2
  exit 0
fi

# -Q parses without loading into the kernel, so this needs no privileges and is
# safe to run in CI.
apparmor_parser -Q -T "$profile"
echo "syntax: ok"

require() {
  if ! grep -qF "$1" "$profile"; then
    echo "AppArmor profile no longer contains: $1" >&2
    exit 1
  fi
}

# The downloaded engine must run under its own profile, not inherit the app's.
require "Px -> io.proofray.llama_server"
require "profile io.proofray.llama_server"
# ...and that profile must keep the conversation database out of its reach.
require "deny owner @{PROOFRAY_DATA}/*.db* rwk"

echo "confinement: llama.cpp runs under its own profile and is denied the store"
