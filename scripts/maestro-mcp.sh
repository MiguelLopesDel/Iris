#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
maestro_bin="$repo_root/.tools/maestro/bin/maestro"

if [[ ! -x "$maestro_bin" ]]; then
    echo "Maestro is not installed for this checkout. See docs/android-testing-workflow.md." >&2
    exit 1
fi

export MAESTRO_CLI_NO_ANALYTICS=true
export MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED=true
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:+$JAVA_TOOL_OPTIONS }-Duser.home=$repo_root/.tools/maestro-home"
mkdir -p "$repo_root/.tools/maestro-home/.maestro"
exec "$maestro_bin" mcp
