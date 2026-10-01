#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
cd "$project_dir"

usage() {
    echo "Usage: $0 {fast|build|device|device-smoke|compose-smoke|maestro-smoke|benchmark-sync}" >&2
    echo "  fast    Run deterministic JVM tests (no Android device required)." >&2
    echo "  build   Compile the debug APK after the fast tests." >&2
    echo "  device  Run instrumentation tests on an emulator; never use a personal phone." >&2
    echo "  device-smoke  Run UI smoke tests in the isolated .lab app on an emulator." >&2
    echo "  compose-smoke Run isolated Compose semantics tests on an emulator." >&2
    echo "  maestro-smoke Run Compose semantics tests and checked-in Maestro flows." >&2
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
        ./gradlew assembleLab assembleLabAndroidTest
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/lab/app-lab.apk
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/androidTest/lab/app-lab-androidTest.apk
        "$adb_command" -s "$emulator_serial" shell am instrument -w -r \
            -e class com.iris.app.AuthenticatedMediaTransportTest,com.iris.app.MediaDownloaderTest,com.iris.app.InterfaceSmokeTest,com.iris.app.AccountScopedUploadQueueTest,com.iris.app.AccountScopedSyncSettingsTest,com.iris.app.CloudSyncStatusRepositoryTest,com.iris.app.SyncRunHistoryTest \
            com.iris.app.lab.test/androidx.test.runner.AndroidJUnitRunner
        ;;
    device-smoke)
        if ! command -v adb >/dev/null 2>&1; then
            echo "adb is required for emulator tests." >&2
            exit 1
        fi
        adb_command="$(command -v adb)"
        emulator_serial="$("$adb_command" devices | awk '$1 ~ /^emulator-/ && $2 == "device" { print $1; exit }')"
        if [ -z "$emulator_serial" ]; then
            echo "Start an Android emulator first. This command refuses to run on a personal phone." >&2
            exit 1
        fi
        ./gradlew assembleLab assembleLabAndroidTest
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/lab/app-lab.apk
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/androidTest/lab/app-lab-androidTest.apk
        "$adb_command" -s "$emulator_serial" shell am instrument -w -r \
            -e class com.iris.app.InterfaceSmokeTest \
            com.iris.app.lab.test/androidx.test.runner.AndroidJUnitRunner
        ;;
    compose-smoke)
        if ! command -v adb >/dev/null 2>&1; then
            echo "adb is required for emulator tests." >&2
            exit 1
        fi
        adb_command="$(command -v adb)"
        emulator_serial="$("$adb_command" devices | awk '$1 ~ /^emulator-/ && $2 == "device" { print $1; exit }')"
        if [ -z "$emulator_serial" ]; then
            echo "Start an Android emulator first. This command refuses to run on a personal phone." >&2
            exit 1
        fi
        ./gradlew assembleLab assembleLabAndroidTest
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/lab/app-lab.apk
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/androidTest/lab/app-lab-androidTest.apk
        "$adb_command" -s "$emulator_serial" shell am instrument -w -r \
            -e class com.iris.app.ui.screens.sync.DeviceMediaSourcePickerTest \
            com.iris.app.lab.test/androidx.test.runner.AndroidJUnitRunner
        ;;
    maestro-smoke)
        if ! command -v adb >/dev/null 2>&1; then
            echo "adb is required for emulator tests." >&2
            exit 1
        fi
        maestro_bin="$project_dir/../.tools/maestro/bin/maestro"
        if [[ ! -x "$maestro_bin" ]]; then
            echo "Maestro is not installed for this checkout. See docs/android-testing-workflow.md." >&2
            exit 1
        fi
        adb_command="$(command -v adb)"
        emulator_serial="$("$adb_command" devices | awk '$1 ~ /^emulator-/ && $2 == "device" { print $1; exit }')"
        if [[ -z "$emulator_serial" ]]; then
            echo "Start an Android emulator first. This command refuses to run on a personal phone." >&2
            exit 1
        fi
        ./scripts/test.sh compose-smoke
        mkdir -p "$project_dir/../.tools/maestro-home/.maestro"
        sdk_dir="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
        if [[ -z "$sdk_dir" && -f local.properties ]]; then
            sdk_dir="$(sed -n 's/^sdk\.dir=//p' local.properties | head -n 1)"
        fi
        if [[ -n "$sdk_dir" ]]; then
            export ANDROID_HOME="$sdk_dir" ANDROID_SDK_ROOT="$sdk_dir"
        fi
        export MAESTRO_CLI_NO_ANALYTICS=true
        export MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED=true
        export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:+$JAVA_TOOL_OPTIONS }-Duser.home=$project_dir/../.tools/maestro-home"
        "$maestro_bin" test --device "$emulator_serial" \
            "$project_dir/maestro/flows"
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
        ./gradlew assembleLab assembleLabAndroidTest
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/lab/app-lab.apk
        "$adb_command" -s "$emulator_serial" install -r -t app/build/outputs/apk/androidTest/lab/app-lab-androidTest.apk
        "$adb_command" -s "$emulator_serial" shell am instrument -w -r \
            -e class com.iris.app.AccountScopedUploadQueueTest#isolated_real_server_upload_benchmark_measures_end_to_end_photo_video_payload_path,com.iris.app.BackgroundSyncServiceTest \
            -e irisBenchBaseUrl http://10.0.2.2:8851 \
            -e irisBenchUsername "$IRIS_BENCH_USERNAME" \
            -e irisBenchPassword "$IRIS_BENCH_PASSWORD" \
            com.iris.app.lab.test/androidx.test.runner.AndroidJUnitRunner
        # These runs log the lab app into a real server and change its settings
        # and catalog; reset it so the next suite starts from a clean app.
        "$adb_command" -s "$emulator_serial" shell pm clear com.iris.app.lab
        ;;
    *)
        usage
        exit 2
        ;;
esac
