#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
cd "$project_dir"

usage() {
    echo "Usage: $0 {fast|build|device}" >&2
    echo "  fast    Run deterministic JVM tests (no Android device required)." >&2
    echo "  build   Compile the debug APK after the fast tests." >&2
    echo "  device  Run instrumentation tests on an emulator; never use a personal phone." >&2
}

case "${1:-fast}" in
    fast)
        ./gradlew testDebugUnitTest
        ;;
    build)
        ./gradlew testDebugUnitTest assembleDebug
        ;;
    device)
        if ! command -v adb >/dev/null 2>&1; then
            echo "adb is required for emulator tests." >&2
            exit 1
        fi
        adb_command="$(command -v adb)"
        emulator_serial="$($adb_command devices | awk '$1 ~ /^emulator-/ && $2 == "device" { print $1; exit }')"
        if [ -z "$emulator_serial" ]; then
            echo "Start an Android emulator first. This command intentionally refuses to run on a personal phone." >&2
            exit 1
        fi
        ./gradlew assembleDebug assembleDebugAndroidTest
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/debug/app-debug.apk
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
        "$adb_command" -s "$emulator_serial" shell am instrument -w -r \
            -e class com.iris.app.AuthenticatedMediaTransportTest \
            com.iris.app.test/androidx.test.runner.AndroidJUnitRunner
        ;;
    *)
        usage
        exit 2
        ;;
esac
