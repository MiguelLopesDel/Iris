"""Keep the published package metadata and deployment profiles in sync."""

from __future__ import annotations

from pathlib import Path

import tomllib
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def _pinned_requirements(path: Path) -> dict[str, str]:
    pins = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "--", "-c ")):
            continue
        requirement = Requirement(line)
        version = str(requirement.specifier)
        assert version.startswith("=="), f"{path.name}: {line} must be pinned"
        pins[canonicalize_name(requirement.name)] = version[2:]
    return pins


def test_project_metadata_matches_cpu_install() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    metadata = {}
    for requirement in (
        project["dependencies"]
        + project["optional-dependencies"]["dev"]
        + project["optional-dependencies"]["cpu-runtime"]
    ):
        parsed = Requirement(requirement)
        metadata[canonicalize_name(parsed.name)] = str(parsed.specifier)[2:]

    cpu = _pinned_requirements(ROOT / "requirements.txt")
    assert set(cpu) == set(metadata)
    assert {
        name: Version(version).base_version for name, version in cpu.items()
    } == {
        name: Version(version).base_version for name, version in metadata.items()
    }
    nvidia = Requirement(project["optional-dependencies"]["nvidia-runtime"][0])
    assert canonicalize_name(nvidia.name) == "onnxruntime-gpu"
    assert str(nvidia.specifier) == "==1.20.2"


def test_cuda_profile_matches_pytorch_family_and_is_copied_into_image() -> None:
    cpu = _pinned_requirements(ROOT / "requirements.txt")
    cuda = _pinned_requirements(ROOT / "requirements-cuda.txt")
    for name in ("torch", "torchvision", "torchaudio"):
        assert Version(cuda[name]).base_version == Version(cpu[name]).base_version
        assert cuda[name].endswith("+cu126")

    dockerfile = (ROOT / "Dockerfile.gpu").read_text()
    assert "COPY requirements.txt requirements-cuda.txt constraints-common.txt ./" in dockerfile
    assert dockerfile.index("pip uninstall -y onnxruntime") < dockerfile.index(
        "pip install --no-cache-dir --force-reinstall -r requirements-cuda.txt"
    )


def test_shared_constraints_cover_every_non_variant_direct_dependency() -> None:
    cpu = _pinned_requirements(ROOT / "requirements.txt")
    shared = _pinned_requirements(ROOT / "constraints-common.txt")
    excluded = {"torch", "torchvision", "torchaudio", "onnxruntime"}
    assert excluded.isdisjoint(shared)
    assert shared.items() >= (cpu.items() - {(name, cpu[name]) for name in excluded})
    assert "-c constraints-common.txt" in (ROOT / "requirements.txt").read_text()
    assert "-c constraints-common.txt" in (ROOT / "requirements-cuda.txt").read_text()
    assert "COPY requirements.txt constraints-common.txt ./" in (ROOT / "Dockerfile").read_text()


def test_vision_extension_loads_with_installed_torch() -> None:
    import torch
    import torchvision

    assert torch.__version__.split("+")[0] == "2.7.1"
    assert torchvision.__version__.split("+")[0] == "0.22.1"
    assert hasattr(torch.ops.torchvision, "nms")
