#!/usr/bin/env bash
# Packages the built Linux release bundle as an AppImage.
#
# Run tool/build_platform.sh Linux release first: this only wraps what that
# produced, so the AppImage can never contain a differently-built binary than
# the one that was tested.
set -euo pipefail

proofray_app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bundle="$proofray_app_dir/build/linux/x64/release/bundle"
staging="$proofray_app_dir/build/appimage/ProofRay.AppDir"
output_dir="$proofray_app_dir/build/appimage"
appimagetool="${APPIMAGETOOL:-appimagetool}"

if [[ ! -x "$bundle/proofray_app" ]]; then
  echo "no release bundle at $bundle; run tool/build_platform.sh Linux release" >&2
  exit 3
fi
if ! command -v "$appimagetool" >/dev/null 2>&1; then
  echo "appimagetool not found; set APPIMAGETOOL to its path" >&2
  exit 4
fi

version="$(grep -m1 '^version:' "$proofray_app_dir/pubspec.yaml" | cut -d' ' -f2)"
version="${version%%+*}"

rm -rf "$staging"
mkdir -p "$staging/usr/lib/proofray" "$staging/usr/share/applications" \
  "$staging/usr/share/icons/hicolor/512x512/apps"
cp -a "$bundle/." "$staging/usr/lib/proofray/"

# The bundle's own launcher sets LD_PRELOAD relative to itself, which still
# resolves inside the mounted AppDir, so it is left exactly as built rather
# than reimplemented here.
cat > "$staging/AppRun" <<'RUN'
#!/bin/sh
here="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$here/usr/lib/proofray/proofray_app" "$@"
RUN
chmod +x "$staging/AppRun"

cat > "$staging/io.proofray.proofray_app.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ProofRay
Comment=Memory that can show why it remembers
Exec=proofray_app
Icon=io.proofray.proofray_app
Categories=Utility;Office;
Terminal=false
StartupWMClass=proofray_app
DESKTOP
cp "$staging/io.proofray.proofray_app.desktop" \
  "$staging/usr/share/applications/"

icon="$proofray_app_dir/linux/assets/app_icon.png"
cp "$icon" "$staging/io.proofray.proofray_app.png"
cp "$icon" "$staging/usr/share/icons/hicolor/512x512/apps/io.proofray.proofray_app.png"

target="$output_dir/ProofRay-$version-x86_64.AppImage"
rm -f "$target"
# No update information and no signature: neither is claimed, so neither is
# embedded. An unsigned AppImage that pretends otherwise is worse than one that
# says plainly what it is.
ARCH=x86_64 "$appimagetool" --no-appstream "$staging" "$target"

sha256sum "$target" | tee "$target.sha256"
echo "built $target"
