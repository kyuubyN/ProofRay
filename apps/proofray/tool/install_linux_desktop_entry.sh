#!/usr/bin/env bash
set -euo pipefail

# Local-dev-only convenience: registers the already-built Linux debug/profile/
# release bundle with THIS machine's desktop environment (writes a .desktop
# entry + icon under ~/.local/share), so the dock/taskbar shows the real
# ProofRay icon instead of a generic one when the app is running or pinned.
#
# `flutter build linux` (and this repo's own tool/build_platform.sh) never do
# this -- a raw build bundle is meant to be launched directly, not installed,
# and CI has no desktop environment to register with in the first place. A
# real signed release (AppImage/deb) is a separate, later packaging step (see
# apps/proofray/docs/RELEASE_GATES.md's still-open "signed/reproducible
# release artifacts" gate) that would generate its own, portable .desktop
# entry -- this script exists only so a developer running a debug build can
# see accurate desktop integration while testing.

mode="${1:-debug}"
case "$mode" in
  debug|profile|release) ;;
  *) echo "usage: tool/install_linux_desktop_entry.sh [debug|profile|release]" >&2; exit 2 ;;
esac

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bundle="$app_dir/build/linux/x64/$mode/bundle"
binary="$bundle/proofray_app"
icon="$bundle/proofray_app.png"

if [[ ! -x "$binary" ]]; then
  echo "no built bundle at $bundle -- run tool/build_platform.sh Linux $mode first" >&2
  exit 3
fi
if [[ ! -f "$icon" ]]; then
  echo "no bundled icon at $icon -- rebuild with the current linux/CMakeLists.txt" >&2
  exit 3
fi

applications_dir="$HOME/.local/share/applications"
icon_dir="$HOME/.local/share/icons/hicolor/512x512/apps"
mkdir -p "$applications_dir" "$icon_dir"

cp -f "$icon" "$icon_dir/io.proofray.proofray_app.png"

cat > "$applications_dir/io.proofray.proofray_app.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=ProofRay
Comment=Local-first memory and proof engine
Exec=$binary
Icon=io.proofray.proofray_app
Terminal=false
StartupWMClass=io.proofray.proofray_app
Categories=Utility;
DESKTOP
chmod +x "$applications_dir/io.proofray.proofray_app.desktop"

command -v update-desktop-database >/dev/null 2>&1 &&
  update-desktop-database "$applications_dir" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 &&
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" || true

echo "Installed: $applications_dir/io.proofray.proofray_app.desktop"
echo "Icon: $icon_dir/io.proofray.proofray_app.png"
