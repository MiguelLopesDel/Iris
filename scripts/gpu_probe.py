from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import torch


def nvidia_smi() -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return f"nvidia-smi error: {exc}"
    return result.stdout if result.returncode == 0 else result.stderr


def probe() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
        "devices": [],
        "nvidia_smi": nvidia_smi(),
    }
    if torch.cuda.is_available():
        for idx in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(idx)
            payload["devices"].append(
                {
                    "index": idx,
                    "name": torch.cuda.get_device_name(idx),
                    "total_memory_gib": round(props.total_memory / 1024**3, 3),
                    "major": props.major,
                    "minor": props.minor,
                }
            )
        tensor = torch.ones((1,), device="cuda")
        payload["cuda_tensor_test"] = float(tensor.item())
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Confirma se CUDA esta acessivel ao PyTorch.")
    parser.add_argument("--output", default="data/reports/gpu_probe.json")
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()

    payload = probe()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if args.require_cuda and not payload["cuda_available"]:
        raise SystemExit("CUDA nao esta acessivel ao PyTorch neste ambiente.")


if __name__ == "__main__":
    main()
