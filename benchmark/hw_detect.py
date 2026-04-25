"""
hw_detect.py — Cross-platform hardware detection for benchmark metadata.

Populates runtime_cpu, runtime_ram_gb, runtime_gpu, runtime_gpu_cores, runtime_os.
Works on macOS (sysctl, system_profiler) and Linux (/proc, lspci, nvidia-smi, rocm-smi).
"""
from __future__ import annotations

import platform
import subprocess


def detect_hw() -> dict[str, str]:
    """Detect hardware and return metadata dict."""
    meta: dict[str, str] = {}
    _is_mac = platform.system() == "Darwin"

    # ── OS ────────────────────────────────────────────────────────────
    try:
        if _is_mac:
            ver = subprocess.check_output(
                ["sw_vers", "-productVersion"], text=True
            ).strip()
            meta["runtime_os"] = f"macOS {ver}"
        else:
            meta["runtime_os"] = platform.platform()
    except Exception:
        meta["runtime_os"] = platform.platform()

    # ── CPU ───────────────────────────────────────────────────────────
    try:
        if _is_mac:
            cpu = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
        else:
            cpu = ""
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        cpu = line.split(":", 1)[1].strip()
                        break
            if not cpu:
                cpu = platform.processor() or "unknown"
        meta["runtime_cpu"] = cpu
    except Exception:
        meta["runtime_cpu"] = platform.processor() or "unknown"

    # ── RAM ───────────────────────────────────────────────────────────
    ram_bytes = 0
    try:
        if _is_mac:
            ram_bytes = int(subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True
            ).strip())
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        ram_bytes = int(line.split()[1]) * 1024
                        break
        meta["runtime_ram_gb"] = str(round(ram_bytes / (1024**3)))
    except Exception:
        meta["runtime_ram_gb"] = "?"

    # ── GPU ───────────────────────────────────────────────────────────
    gpu = ""
    gpu_cores = ""
    try:
        if _is_mac:
            gpu_info = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType"], text=True
            )
            for line in gpu_info.splitlines():
                if "Chipset Model" in line:
                    gpu = line.split(":")[-1].strip()
                    break
            for line in gpu_info.splitlines():
                if "Total Number of Cores" in line:
                    gpu_cores = line.split(":")[-1].strip()
                    break
        else:
            # Linux: nvidia-smi → rocm-smi → lspci (priority order)
            try:
                gpu = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    text=True, stderr=subprocess.DEVNULL
                ).strip().split("\n")[0]
                # GPU cores from nvidia-smi
                try:
                    gpu_cores = subprocess.check_output(
                        ["nvidia-smi", "--query-gpu=count", "--format=csv,noheader"],
                        text=True, stderr=subprocess.DEVNULL
                    ).strip()
                except Exception:
                    pass
            except Exception:
                pass

            if not gpu:
                try:
                    rsmi = subprocess.check_output(
                        ["rocm-smi", "--showproductname"],
                        text=True, stderr=subprocess.DEVNULL
                    )
                    for line in rsmi.splitlines():
                        if "Card series" in line:
                            gpu = line.split(":")[-1].strip()
                            break
                    if not gpu:
                        for line in rsmi.splitlines():
                            if "Card model" in line:
                                gpu = line.split(":")[-1].strip()
                                break
                except Exception:
                    pass

            if not gpu:
                try:
                    lspci = subprocess.check_output(
                        ["lspci"], text=True, stderr=subprocess.DEVNULL
                    )
                    # Collect all VGA/3D devices, prefer discrete over integrated
                    _candidates = []
                    for line in lspci.splitlines():
                        if "VGA" in line or "3D controller" in line:
                            _candidates.append(line.split(":")[-1].strip())
                    if len(_candidates) == 1:
                        gpu = _candidates[0]
                    elif len(_candidates) > 1:
                        # Prefer discrete GPU: skip known integrated chipsets
                        _integrated = {"Caicos", "Cedar", "Turks", "HD 7470", "HD 8470",
                                       "R5 235", "R5 310", "UHD Graphics", "HD Graphics",
                                       "Iris", "Xe Graphics"}
                        for c in _candidates:
                            if not any(ig in c for ig in _integrated):
                                gpu = c
                                break
                        if not gpu:
                            gpu = _candidates[0]  # fallback to first
                except Exception:
                    pass
    except Exception:
        pass

    meta["runtime_gpu"] = gpu or "?"
    meta["runtime_gpu_cores"] = gpu_cores or "?"

    # ── Hostname ──────────────────────────────────────────────────────
    meta["runtime_hostname"] = platform.node()

    return meta
