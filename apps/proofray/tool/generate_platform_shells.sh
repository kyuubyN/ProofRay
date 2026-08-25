#!/usr/bin/env bash
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$app_dir"
proofray_flutter_bin="${PROOFRAY_FLUTTER_BIN:-flutter}"
proofray_dart_bin="${PROOFRAY_DART_BIN:-dart}"
./tool/verify_toolchain.sh

for platform_dir in android linux windows; do
  if [[ -e "$platform_dir" ]]; then
    echo "refusing to overwrite existing $platform_dir shell" >&2
    exit 2
  fi
done

stage_dir="$(mktemp -d)"
trap 'rm -rf -- "$stage_dir"' EXIT
"$proofray_flutter_bin" create \
  --platforms=android,linux,windows \
  --org=io.proofray \
  --project-name=proofray_app \
  "$stage_dir/proofray_app"

cp -R "$stage_dir/proofray_app/android" "$app_dir/android"
cp -R "$stage_dir/proofray_app/linux" "$app_dir/linux"
cp -R "$stage_dir/proofray_app/windows" "$app_dir/windows"

gradle_file="$app_dir/android/app/build.gradle.kts"
if [[ ! -f "$gradle_file" ]]; then
  echo "pinned Flutter generated an unexpected Android layout" >&2
  exit 3
fi
sed -i 's/minSdk = flutter.minSdkVersion/minSdk = 29/' "$gradle_file"
if ! rg -q 'minSdk = 29' "$gradle_file"; then
  echo "Android 10 minimum SDK patch did not apply" >&2
  exit 3
fi

manifest="$app_dir/android/app/src/main/AndroidManifest.xml"
sed -i '/<manifest/a\    <uses-permission android:name="android.permission.INTERNET" />' \
  "$manifest"

"$proofray_flutter_bin" pub get
"$proofray_dart_bin" run build_runner build
"$proofray_dart_bin" run flutter_launcher_icons

echo "Platform shells and lockfiles generated. No app was built or launched."
