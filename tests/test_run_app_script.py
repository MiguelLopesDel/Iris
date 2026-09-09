from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_launcher_uses_venv_python_when_python3_is_not_in_venv(tmp_path: Path) -> None:
    project = tmp_path / "project"
    venv_bin = project / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    invocation = tmp_path / "invocation.txt"
    fake_python = venv_bin / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$IRIS_TEST_INVOCATION\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    source_script = Path(__file__).parents[1] / "scripts" / "run_app.sh"
    scripts_dir = project / "scripts"
    scripts_dir.mkdir()
    launcher = scripts_dir / "run_app.sh"
    launcher.write_bytes(source_script.read_bytes())
    launcher.chmod(0o755)

    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["IRIS_TEST_INVOCATION"] = str(invocation)
    subprocess.run(
        [str(launcher), "--log-level", "warning"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    args = invocation.read_text(encoding="utf-8").splitlines()
    assert args[:3] == ["-m", "uvicorn", "server:app"]
    assert args[-2:] == ["--log-level", "warning"]
