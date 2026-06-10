from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def detect_hardware() -> dict:
    """Detect GPU/CPU/RAM and return capabilities. Result cached for lifetime of process."""
    import psutil
    import torch

    gpu_name: str | None = None
    vram_gb: float = 0.0
    cuda_available = torch.cuda.is_available()

    if cuda_available:
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1_073_741_824

    ram_gb = psutil.virtual_memory().total / 1_073_741_824
    cpu_cores = psutil.cpu_count(logical=False) or 1

    return {
        "gpu_name": gpu_name,
        "vram_gb": round(vram_gb, 1),
        "ram_gb": round(ram_gb, 1),
        "cpu_cores": cpu_cores,
        "cuda": cuda_available,
    }


def optimal_settings(hw: dict | None = None) -> dict:
    """Return optimal indexing settings for the detected hardware."""
    if hw is None:
        hw = detect_hardware()

    vram = hw["vram_gb"]
    ram = hw["ram_gb"]
    cuda = hw["cuda"]

    # ── GPU tiers ─────────────────────────────────────────────────────────────
    if cuda and vram >= 12:
        return {
            "device": "cuda",
            "batch_size": 8,
            "max_dim": 1920,
            "caption_model": "microsoft/Florence-2-large",
            "whisper_model": "small",
            "fp16": True,
            "tier": "high",
            "reason": f"GPU {hw['gpu_name']} ({vram:.1f} GB VRAM) — configurações máximas",
        }

    if cuda and vram >= 8:
        return {
            "device": "cuda",
            "batch_size": 6,
            "max_dim": 1536,
            "caption_model": "microsoft/Florence-2-large",
            "whisper_model": "small",
            "fp16": True,
            "tier": "high",
            "reason": f"GPU {hw['gpu_name']} ({vram:.1f} GB VRAM)",
        }

    if cuda and vram >= 5:
        return {
            "device": "cuda",
            "batch_size": 4,
            "max_dim": 1024,
            "caption_model": "microsoft/Florence-2-large",
            "whisper_model": "tiny",
            "fp16": True,
            "tier": "mid",
            "reason": f"GPU {hw['gpu_name']} ({vram:.1f} GB VRAM) — compartilhando com engine de busca",
        }

    if cuda and vram >= 3:
        return {
            "device": "cuda",
            "batch_size": 2,
            "max_dim": 768,
            "caption_model": "microsoft/Florence-2-base",
            "whisper_model": "tiny",
            "fp16": True,
            "tier": "low",
            "reason": f"GPU {hw['gpu_name']} ({vram:.1f} GB VRAM) — VRAM limitada, usando Florence-2-base",
        }

    if cuda and vram > 0:
        return {
            "device": "cuda",
            "batch_size": 1,
            "max_dim": 640,
            "caption_model": "none",
            "whisper_model": "none",
            "fp16": True,
            "tier": "minimal",
            "reason": f"GPU {hw['gpu_name']} ({vram:.1f} GB VRAM) — VRAM insuficiente para caption/whisper",
        }

    # ── CPU fallback ──────────────────────────────────────────────────────────
    if ram >= 16:
        return {
            "device": "cpu",
            "batch_size": 2,
            "max_dim": 768,
            "caption_model": "none",
            "whisper_model": "none",
            "fp16": False,
            "tier": "cpu",
            "reason": f"CPU ({hw['cpu_cores']} núcleos, {ram:.0f} GB RAM) — sem GPU detectada",
        }

    return {
        "device": "cpu",
        "batch_size": 1,
        "max_dim": 512,
        "caption_model": "none",
        "whisper_model": "none",
        "fp16": False,
        "tier": "cpu_low",
        "reason": f"CPU ({hw['cpu_cores']} núcleos, {ram:.0f} GB RAM) — RAM limitada",
    }


def hardware_summary(hw: dict | None = None) -> str:
    """Short human-readable hardware summary."""
    if hw is None:
        hw = detect_hardware()
    if hw["cuda"]:
        return f"🎮 {hw['gpu_name']} · {hw['vram_gb']:.1f} GB VRAM · {hw['ram_gb']:.0f} GB RAM"
    return f"💻 CPU · {hw['cpu_cores']} núcleos · {hw['ram_gb']:.0f} GB RAM"


def apply_env_optimizations(hw: dict | None = None) -> None:
    """Set env-level torch/CUDA optimizations based on hardware."""
    if hw is None:
        hw = detect_hardware()
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    if hw["cuda"]:
        try:
            import torch
            torch.backends.cudnn.benchmark = True
            # Enable TF32 on Ampere+ GPUs for faster matmul with minimal precision loss
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        except Exception:
            pass
