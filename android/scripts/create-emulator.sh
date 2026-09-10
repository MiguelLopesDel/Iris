#!/usr/bin/env bash
set -euo pipefail

avd_name="${1:-Iris-Test-API35}"
sdk_root="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"

if [ -z "$sdk_root" ]; then
    echo "Set ANDROID_SDK_ROOT or ANDROID_HOME before creating an emulator." >&2
    exit 1
fi

sdkmanager="$sdk_root/cmdline-tools/latest/bin/sdkmanager"
avdmanager="$sdk_root/cmdline-tools/latest/bin/avdmanager"
if [ ! -x "$sdkmanager" ] || [ ! -x "$avdmanager" ]; then
    echo "Android command-line tools are required in $sdk_root/cmdline-tools/latest." >&2
    exit 1
fi

image="system-images;android-35;google_apis;x86_64"
yes | "$sdkmanager" --sdk_root="$sdk_root" "emulator" "$image"

if "$avdmanager" list avd | grep -Fq "Name: $avd_name"; then
    echo "Android emulator '$avd_name' already exists."
    exit 0
fi

printf 'no\n' | "$avdmanager" create avd --force --name "$avd_name" --package "$image" --device "pixel_6"
echo "Created '$avd_name'. Start it with:"
echo "  $sdk_root/emulator/emulator -avd $avd_name"
