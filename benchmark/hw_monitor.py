"""Hardware monitor — background sampling of CPU and GPU utilization.

Cross-platform, auto-detects available GPU monitoring:
- macOS Apple Silicon: powermetrics (if sudo), else IOReport framework via subprocess
- Linux NVIDIA: nvidia-smi
- Linux AMD: rocm-smi
- Fallback: 0.0 (no GPU info available)

Usage:
    monitor = HWMonitor(interval=0.5)
    monitor.start()
    # ... run workload ...
    stats = monitor.stop()  # returns HWStats
    print(stats.cpu_avg, stats.gpu_avg)
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass
class HWStats:
    """Aggregated hardware stats from a monitoring session."""
    cpu_avg: float = 0.0       # average CPU % (0-100 per core, or loadavg)
    cpu_max: float = 0.0
    gpu_avg: float = 0.0       # average GPU utilization % (0-100)
    gpu_max: float = 0.0
    gpu_power_avg: float = 0.0  # average GPU power in watts (0 if unavailable)
    gpu_power_max: float = 0.0
    n_samples: int = 0
    gpu_source: str = ""       # which backend provided GPU data


class HWMonitor:
    """Background thread that samples CPU and GPU at regular intervals."""

    def __init__(self, interval: float = 0.5):
        self._interval = interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._cpu_samples: list[float] = []
        self._gpu_samples: list[float] = []
        self._gpu_power_samples: list[float] = []
        self._gpu_sampler = _detect_gpu_sampler()

    def start(self) -> None:
        """Start background sampling."""
        self._stop_event.clear()
        self._cpu_samples.clear()
        self._gpu_samples.clear()
        self._gpu_power_samples.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> HWStats:
        """Stop sampling and return aggregated stats."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        return self._compute_stats()

    def sample_once(self) -> tuple[float, float]:
        """Take a single sample (cpu, gpu). Useful for non-threaded usage."""
        cpu = _sample_cpu()
        gpu, _power = self._gpu_sampler()
        return cpu, gpu

    def _run(self) -> None:
        while not self._stop_event.is_set():
            cpu = _sample_cpu()
            gpu, power = self._gpu_sampler()
            self._cpu_samples.append(cpu)
            self._gpu_samples.append(gpu)
            if power > 0:
                self._gpu_power_samples.append(power)
            self._stop_event.wait(self._interval)

    def _compute_stats(self) -> HWStats:
        cpu = self._cpu_samples
        gpu = self._gpu_samples
        pwr = self._gpu_power_samples
        return HWStats(
            cpu_avg=_avg(cpu),
            cpu_max=max(cpu) if cpu else 0.0,
            gpu_avg=_avg(gpu),
            gpu_max=max(gpu) if gpu else 0.0,
            gpu_power_avg=_avg(pwr),
            gpu_power_max=max(pwr) if pwr else 0.0,
            n_samples=len(cpu),
            gpu_source=getattr(self._gpu_sampler, '_source', 'unknown'),
        )


# ── CPU sampling ─────────────────────────────────────────────────────────

def _sample_cpu() -> float:
    """Return CPU load as a percentage-like value (cross-platform)."""
    try:
        # loadavg is fast and doesn't require psutil (Mac / Linux)
        load1 = os.getloadavg()[0]
        n_cores = os.cpu_count() or 1
        return min(load1 / n_cores * 100, 100.0)
    except (OSError, AttributeError):
        pass
    # Windows fallback: wmic cpu get loadpercentage
    try:
        out = subprocess.run(
            ["wmic", "cpu", "get", "loadpercentage", "/value"],
            capture_output=True, text=True, timeout=3,
        )
        for line in out.stdout.splitlines():
            if "=" in line:
                val = line.split("=", 1)[1].strip()
                if val.isdigit():
                    return float(val)
    except Exception:
        pass
    return 0.0


# ── GPU sampling (auto-detect) ───────────────────────────────────────────

def _detect_gpu_sampler():
    """Return the best available GPU sampler function for this platform."""
    system = platform.system()

    if system == "Darwin":
        sampler = _make_macos_gpu_sampler()
        if sampler:
            return sampler

    if system in ("Linux", "Windows"):
        # NVIDIA
        if shutil.which("nvidia-smi"):
            fn = _sample_gpu_nvidia
            fn._source = "nvidia-smi"
            return fn
        # AMD
        if shutil.which("rocm-smi"):
            fn = _sample_gpu_amd
            fn._source = "rocm-smi"
            return fn

    fn = _sample_gpu_none
    fn._source = "none"
    return fn


def _make_macos_gpu_sampler():
    """Create a macOS GPU sampler using AGXAccelerator PerformanceStatistics.

    Works on Apple Silicon (M1/M2/M3/M4/M5+) without sudo.
    Reads 'Device Utilization %' from ioreg AGXAccelerator node.

    Uses dynamic discovery via `ioreg -l` so it works on any chip generation
    without needing a hardcoded class list.
    """
    # Dynamic discovery: find whatever AGXAccelerator class is present on this chip.
    # Covers M1 (G13), M2 (G14), M3 (G15), M4 (G16), future chips — no hardcoding needed.
    try:
        discovery = subprocess.run(
            ["ioreg", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        if discovery.returncode == 0:
            for line in discovery.stdout.splitlines():
                if "AGXAccelerator" in line and "class" in line:
                    # Extract class name e.g. "class AGXAcceleratorG16X"
                    for token in line.split():
                        if token.startswith("AGXAccelerator"):
                            agx_class = token.rstrip(",>")
                            # Verify it exposes Device Utilization
                            probe = subprocess.run(
                                ["ioreg", "-r", "-c", agx_class, "-d", "1"],
                                capture_output=True, text=True, timeout=2,
                            )
                            if probe.returncode == 0 and "Device Utilization" in probe.stdout:
                                fn = _sample_gpu_macos_agx
                                fn._source = f"ioreg/{agx_class}"
                                fn._agx_class = agx_class
                                return fn
    except Exception:
        pass

    fn = _sample_gpu_none
    fn._source = "none (macOS — AGX not found)"
    return fn


def _sample_gpu_macos_agx() -> tuple[float, float]:
    """Sample GPU utilization from AGXAccelerator PerformanceStatistics.

    Parses: "Device Utilization %"=42, "Renderer Utilization %"=5, "Tiler Utilization %"=5
    Returns (device_utilization_pct, 0.0).
    """
    agx_class = getattr(_sample_gpu_macos_agx, "_agx_class", "AGXAcceleratorG13X")
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-c", agx_class, "-d", "1"],
            capture_output=True, text=True, timeout=2,
        )
        for line in result.stdout.splitlines():
            if "Device Utilization %" in line:
                m = re.search(r'"Device Utilization %"=(\d+)', line)
                if m:
                    return float(m.group(1)), 0.0
        return 0.0, 0.0
    except Exception:
        return 0.0, 0.0


def _sample_gpu_nvidia() -> tuple[float, float]:
    """Sample NVIDIA GPU utilization and power."""
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            utils = []
            powers = []
            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    utils.append(float(parts[0]))
                    powers.append(float(parts[1]))
            return (_avg(utils), _avg(powers))
    except Exception:
        pass
    return 0.0, 0.0


def _sample_gpu_amd() -> tuple[float, float]:
    """Sample AMD GPU utilization via rocm-smi."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showuse", "--csv"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines()[1:]:
                parts = line.split(",")
                if len(parts) >= 2:
                    return float(parts[1].strip().rstrip("%")), 0.0
    except Exception:
        pass
    return 0.0, 0.0


def _sample_gpu_none() -> tuple[float, float]:
    """No GPU monitoring available."""
    return 0.0, 0.0


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
