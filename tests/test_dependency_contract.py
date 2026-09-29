"""Keep the CPU, CUDA and development dependency profiles unambiguous."""

from __future__ import annotations

from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def _pinned_requirements(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    seen = seen or set()
    path = path.resolve()
    if path in seen:
        return {}
    seen.add(path)
    pins = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "--", "-c ")):
            continue
        if line.startswith("-r "):
            included = (path.parent / line[3:].strip()).resolve()
            pins.update(_pinned_requirements(included, seen))
            continue
        requirement = Requirement(line)
        version = str(requirement.specifier)
        assert version.startswith("=="), f"{path.name}: {line} must be pinned"
        pins[canonicalize_name(requirement.name)] = version[2:]
    return pins


def _install_includes(path: Path, included_file: str) -> bool:
    return f"-r {included_file}" in path.read_text().splitlines()


def test_cpu_profile_is_common_direct_dependencies_plus_cpu_runtime() -> None:
    common = _pinned_requirements(ROOT / "requirements-common.txt")
    cpu = _pinned_requirements(ROOT / "requirements.txt")
    assert common.keys() <= cpu.keys()
    assert {
        name: cpu[name] for name in common
    } == common
    assert {name: cpu[name] for name in cpu.keys() - common.keys()} == {
        "torch": "2.7.1+cpu",
        "torchvision": "0.22.1+cpu",
        "torchaudio": "2.7.1+cpu",
        "onnxruntime": "1.20.1",
    }
    assert "https://download.pytorch.org/whl/cpu" in (ROOT / "requirements.txt").read_text()


def test_cuda_profile_selects_cuda_wheels_without_installing_cpu_profile() -> None:
    common = _pinned_requirements(ROOT / "requirements-common.txt")
    cuda = _pinned_requirements(ROOT / "requirements-cuda.txt")
    assert common.keys() <= cuda.keys()
    assert {name: cuda[name] for name in common} == common
    assert {name: cuda[name] for name in cuda.keys() - common.keys()} == {
        "torch": "2.7.1+cu126",
        "torchvision": "0.22.1+cu126",
        "torchaudio": "2.7.1+cu126",
        "onnxruntime-gpu": "1.20.2",
    }
    assert "https://download.pytorch.org/whl/cu126" in (ROOT / "requirements-cuda.txt").read_text()
    assert not _install_includes(ROOT / "requirements-cuda.txt", "requirements.txt")

    dockerfile = (ROOT / "Dockerfile.gpu").read_text()
    assert "COPY requirements-common.txt requirements-cuda.txt constraints-common.txt ./" in dockerfile
    assert "pip install --no-cache-dir -r requirements-cuda.txt" in dockerfile
    assert "pip uninstall -y onnxruntime" not in dockerfile


def test_development_profile_layers_cpu_and_has_test_tools() -> None:
    dev = _pinned_requirements(ROOT / "requirements-dev.txt")
    cpu = _pinned_requirements(ROOT / "requirements.txt")
    assert _install_includes(ROOT / "requirements-dev.txt", "requirements.txt")
    assert {name: dev[name] for name in cpu} == cpu
    assert {name: dev[name] for name in dev.keys() - cpu.keys()} == {
        "pytest": "9.1.1",
        "httpx": "0.28.1",
        "ruff": "0.14.9",
        "pip-audit": "2.10.1",
    }


def test_shared_constraints_only_pin_transitive_dependencies() -> None:
    direct = set(_pinned_requirements(ROOT / "requirements-common.txt"))
    direct.update(_pinned_requirements(ROOT / "requirements.txt"))
    direct.update(_pinned_requirements(ROOT / "requirements-cuda.txt"))
    direct.update(_pinned_requirements(ROOT / "requirements-dev.txt"))
    shared = _pinned_requirements(ROOT / "constraints-common.txt")
    assert direct.isdisjoint(shared)
    assert "-c constraints-common.txt" in (ROOT / "requirements.txt").read_text()
    assert "-c constraints-common.txt" in (ROOT / "requirements-cuda.txt").read_text()
    assert "-c constraints-common.txt" in (ROOT / "requirements-dev.txt").read_text()
    assert "COPY requirements.txt requirements-common.txt constraints-common.txt ./" in (
        ROOT / "Dockerfile"
    ).read_text()


def test_package_metadata_does_not_duplicate_profile_dependencies() -> None:
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "dependencies" not in project
    assert "optional-dependencies" not in project
    assert project["requires-python"] == ">=3.11,<3.13"


def test_cpu_image_checks_vision_and_onnx_runtime_imports() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    requirements = (ROOT / "requirements.txt").read_text()
    assert "g++" in dockerfile
    assert "onnxruntime-gpu" not in requirements
    assert "onnxruntime==1.20.1" in requirements
    assert 'import torch, torchvision, torchaudio, onnxruntime' in dockerfile


def test_vision_extension_loads_with_installed_torch() -> None:
    import torch
    import torchvision

    assert torch.__version__.split("+")[0] == "2.7.1"
    assert torch.__version__.endswith("+cpu")
    assert torch.version.cuda is None
    assert torchvision.__version__.split("+")[0] == "0.22.1"
    assert hasattr(torch.ops.torchvision, "nms")
