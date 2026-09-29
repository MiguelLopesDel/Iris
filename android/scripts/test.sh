#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
cd "$project_dir"

usage() {
    echo "Usage: $0 {fast|build|device|benchmark-sync}" >&2
    echo "  fast    Run deterministic JVM tests (no Android device required)." >&2
    echo "  build   Compile the debug APK after the fast tests." >&2
    echo "  device  Run instrumentation tests on an emulator; never use a personal phone." >&2
    echo "  benchmark-sync  Run the live-server photo/video benchmark on one emulator only." >&2
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
            -e class com.iris.app.AuthenticatedMediaTransportTest,com.iris.app.MediaDownloaderTest,com.iris.app.InterfaceSmokeTest,com.iris.app.AccountScopedUploadQueueTest,com.iris.app.AccountScopedSyncSettingsTest,com.iris.app.CloudSyncStatusRepositoryTest \
            com.iris.app.test/androidx.test.runner.AndroidJUnitRunner
        ;;
    benchmark-sync)
        if ! command -v adb >/dev/null 2>&1; then
            echo "adb is required for emulator tests." >&2
            exit 1
        fi
        : "${IRIS_BENCH_USERNAME:?Set IRIS_BENCH_USERNAME to a disposable lab account.}"
        : "${IRIS_BENCH_PASSWORD:?Set IRIS_BENCH_PASSWORD to its password.}"
        adb_command="$(command -v adb)"
        emulator_serial="$("$adb_command" devices | awk '$1 ~ /^emulator-/ && $2 == "device" { print $1; exit }')"
        if [ -z "$emulator_serial" ]; then
            echo "Start an Android emulator first; this benchmark never selects a physical phone." >&2
            exit 1
        fi
        ./gradlew assembleDebug assembleDebugAndroidTest
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/debug/app-debug.apk
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
        "$adb_command" -s "$emulator_serial" shell am instrument -w -r \
            -e class com.iris.app.AccountScopedUploadQueueTest#isolated_real_server_upload_benchmark_measures_end_to_end_photo_video_payload_path \
            -e irisBenchBaseUrl http://10.0.2.2:8851 \
            -e irisBenchUsername "$IRIS_BENCH_USERNAME" \
            -e irisBenchPassword "$IRIS_BENCH_PASSWORD" \
            com.iris.app.test/androidx.test.runner.AndroidJUnitRunner
        ;;
    *)
        usage
        exit 2
        ;;
esac
