import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("runtime", ["podman", "docker"])
@pytest.mark.parametrize("label_mode", ["private", "disable"])
def test_launcher_keeps_data_and_forwards_arguments(tmp_path, runtime, label_mode):
    executable = tmp_path / runtime
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        'with open(os.environ["RUNTIME_CALLS"], "a") as stream:\n'
        '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
    )
    executable.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    data = tmp_path / "data with spaces"
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "WAT_CONTAINER_RUNTIME": runtime,
        "WAT_DATA_DIR": str(data),
        "WAT_SELINUX_LABEL": label_mode,
        "RUNTIME_CALLS": str(calls),
    }
    launcher = Path(__file__).resolve().parents[1] / "scripts/wat-container.sh"
    subprocess.run(["bash", str(launcher), "--demo"], env=environment, check=True)
    build, run = [json.loads(line) for line in calls.read_text().splitlines()]
    assert build[:3] == ["build", "--tag", "localhost/whatsapp-terminal:local"]
    assert run[:3] == ["run", "--rm", "-it"]
    mount = f"{data}:/data" + (":Z" if label_mode == "private" else "")
    assert run[run.index("--volume") + 1] == mount
    if label_mode == "disable":
        assert run[run.index("--security-opt") + 1] == "label=disable"
    else:
        assert "--security-opt" not in run
    assert run[run.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert ("--userns=keep-id" in run) == (runtime == "podman")
    assert run[-1] == "--demo"
    assert data.stat().st_mode & 0o777 == 0o700


def test_invalid_label_mode_stops_before_build(tmp_path):
    launcher = Path(__file__).resolve().parents[1] / "scripts/wat-container.sh"
    result = subprocess.run(
        ["bash", str(launcher)],
        env={**os.environ, "WAT_CONTAINER_RUNTIME": "podman", "WAT_SELINUX_LABEL": "invalid"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "WAT_SELINUX_LABEL must be private or disable" in result.stderr
