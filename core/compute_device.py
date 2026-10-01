"""The one place that picks the compute device, honouring the instance's GPU choice.

Search, indexing, upload processing and faces all ask here. Whether the
container can see an NVIDIA GPU is decided by the installer (the CUDA image
and Docker GPU access); whether Iris uses it is the administrator's choice
in System > Installation (setting ``gpu``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache

_lock = threading.Lock()
_gpu_allowed = True


def set_gpu_allowed(allowed: bool) -> None:
    global _gpu_allowed
    with _lock:
        _gpu_allowed = bool(allowed)


def gpu_allowed() -> bool:
    with _lock:
        return _gpu_allowed


@dataclass(frozen=True)
class GpuStatus:
    available: bool
    name: str | None
    # "cpu_image": this install has no CUDA support (CPU image or CPU wheels);
    # "not_visible": CUDA support is there but no GPU reaches the container.
    reason: str | None


@lru_cache(maxsize=1)
def gpu_status() -> GpuStatus:
    """Probed once per process: the hardware a container sees does not change while it runs."""
    try:
        import torch
    except Exception:
        return GpuStatus(False, None, "cpu_image")
    if torch.version.cuda is None:
        return GpuStatus(False, None, "cpu_image")
    if not torch.cuda.is_available():
        return GpuStatus(False, None, "not_visible")
    try:
        name = torch.cuda.get_device_name(0)
    except Exception:
        name = None
    return GpuStatus(True, name, None)


@lru_cache(maxsize=1)
def _mps_available() -> bool:
    try:
        import torch
    except Exception:
        return False
    return hasattr(torch.backends, "mps") and torch.backends.mps.is_available()


def resolve(requested: str = "auto") -> str:
    """The device to use for ``requested`` ("auto", "cuda", "mps" or "cpu").

    With the GPU turned off, "cuda" and "auto" fall back to the CPU: the
    administrator's choice wins over a per-import request.
    """
    if requested == "cpu":
        return "cpu"
    if requested in {"auto", "cuda"} and gpu_allowed() and gpu_status().available:
        return "cuda"
    if requested in {"auto", "mps"} and _mps_available():
        return "mps"
    return "cpu"
