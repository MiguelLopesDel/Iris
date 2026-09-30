# Android testing workflow

Use the shortest test layer that can prove the change, then widen the scope only
as needed. A green build is not proof of correct runtime behavior.

## Inner loop

From `android/`:

```bash
./scripts/test.sh fast
./gradlew assembleDebug
```

Keep state transformations, permissions, queue policy, and request mapping in
JVM tests where practical. Compose tests should target semantics/content
descriptions and assert visible state, not depend on pixel coordinates.

## Isolated UI smoke test

Start the dedicated AVD and run:

```bash
./scripts/test.sh device-smoke
./scripts/test.sh compose-smoke
./scripts/test.sh maestro-smoke
```

This builds and installs the `lab` variant (`com.iris.app.lab`) plus its test
APK, then runs `InterfaceSmokeTest`. All instrumentation tests target this
variant (`testBuildType = "lab"`). The distinct package has independent
preferences, credentials, Keystore entries, database, and cache from the regular
Iris app. The script selects only an `emulator-*` device and refuses to fall
back to a connected physical phone. The UI test uses a local synthetic API
fixture and temporary media; it must not contact a configured home server or
use personal media.

`./scripts/test.sh compose-smoke` runs the isolated `DeviceMediaSourcePickerTest`
against a dedicated test activity in the lab variant. It drives filters, search,
selection, loading/empty/error states, and completion through Compose semantics.
`./scripts/test.sh maestro-smoke` runs this Compose test and all checked-in
Maestro flows against the explicitly selected emulator. Both steps refuse to
select a physical phone.

The existing `device` and `benchmark-sync` modes also require an emulator.
`device` runs broader instrumentation against the isolated lab package;
`benchmark-sync` additionally requires a disposable server account and uses
synthetic media. Do not run either against a personal phone or the production
library.

## Agent-assisted end-to-end exploration

Install Maestro once for this checkout (the local CLI is intentionally ignored
by Git):

```bash
curl -fsSL https://get.maestro.mobile.dev -o /tmp/maestro-install.sh
MAESTRO_DIR="$PWD/.tools/maestro" PATH="$PWD/.tools/maestro/bin:$PATH" bash /tmp/maestro-install.sh
```

This uses the [official Maestro CLI installer](https://docs.maestro.dev/maestro-cli/how-to-install-maestro-cli).

The project Codex MCP config in `.codex/config.toml` invokes
`scripts/maestro-mcp.sh`. Trust this checkout and restart Codex after installing
the CLI to expose Maestro's device and flow tools. The wrapper disables CLI
analytics and stores its state under ignored `.tools/maestro-home/` rather than
in the user's global config. A checked-in starter flow is
`android/maestro/flows/lab-gallery-launch.yaml`; it launches only the isolated
`com.iris.app.lab` package and clears only that package's state.
See the [Maestro MCP documentation](https://docs.maestro.dev/get-started/maestro-mcp)
for the agent's available device and flow tools.

Use Maestro YAML flows or its MCP integration to inspect and exercise a rendered
user journey. Keep deterministic assertions and business
logic checks in the repository's tests; agent exploration is a way to discover
cases, not a replacement for a repeatable test. Prefer accessibility labels and
Compose semantics; use coordinate taps only where the system UI exposes no
stable element.

For each confirmed bug, capture the initial state, steps, expected result, and
actual result, then add a focused regression test/flow. Re-run the smallest
relevant test first, followed by the Android unit suite and isolated smoke test
for UI or integration changes.

## Physical-device and performance checks

Use a physical device for explicit manual acceptance, OEM/background behavior,
and performance measurements that depend on real hardware or network. Do not
run instrumentation that overwrites app preferences, credentials, or media on
the user's normal Iris installation. Benchmark critical journeys with
Macrobenchmark on a representative physical device and compare repeated runs;
do not infer device performance from a host emulator. Broader API/device
compatibility can run in Firebase Test Lab or equivalent CI after the local
reproducible checks pass.

Keep ADB commands scoped to a known serial. Automated scripts must use an
emulator serial and fail closed if none is available. When the local execution
sandbox blocks the ADB daemon socket, batch the necessary commands into one
approved invocation rather than repeatedly asking for each command.
