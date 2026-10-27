"""Best-effort, identity-free hardware metadata for crash diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import sys


_MAX_HARDWARE_TEXT = 160
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    total: int | None = None
    available: int | None = None
    usable: int | None = None


@dataclass(frozen=True, slots=True)
class DisplaySnapshot:
    width: int | None = None
    height: int | None = None
    dpi: int | None = None


@dataclass(frozen=True, slots=True)
class GpuSnapshot:
    name: str
    vendor_name: str | None = None
    driver_version: str | None = None
    memory_size_mb: int | None = None
    api_type: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    processor_count: int | None
    cpu_description: str | None
    processor_frequency_mhz: int | None
    memory: MemorySnapshot
    process_memory_bytes: int | None
    display: DisplaySnapshot
    gpu: GpuSnapshot | None


def _safe_hardware_text(value: object) -> str | None:
    """Normalize a local hardware label without accepting structured data."""
    candidate = " ".join(str(value or "").split())
    if not candidate or _CONTROL_CHARACTERS.search(candidate):
        return None
    return candidate[:_MAX_HARDWARE_TEXT]


def os_family() -> str:
    if hasattr(sys, "getandroidapilevel"):
        return "Android"
    if sys.platform == "ios":
        return "iOS"
    return platform.system() or "unknown"


def device_class(family: str) -> str:
    if family in {"Android", "iOS"}:
        return "mobile"
    if family in {"Windows", "Linux", "Darwin"}:
        return "desktop"
    return "unknown"


def _windows_memory_snapshot() -> MemorySnapshot | None:
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        total = int(status.total_physical)
        available = int(status.available_physical)
        return MemorySnapshot(total=total, available=available, usable=total)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _posix_memory_snapshot() -> MemorySnapshot | None:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(
            encoding="ascii", errors="ignore"
        ).splitlines()[:64]:
            key, separator, raw_value = line.partition(":")
            if separator and raw_value.strip().endswith("kB"):
                values[key] = int(raw_value.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        values = {}
    total = values.get("MemTotal")
    available = values.get("MemAvailable") or values.get("MemFree")
    if total and total > 0:
        return MemorySnapshot(total=total, available=available, usable=total)
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return MemorySnapshot(
            total=pages * page_size,
            available=available_pages * page_size,
            usable=pages * page_size,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def memory_snapshot() -> MemorySnapshot:
    snapshot = (
        _windows_memory_snapshot()
        if sys.platform == "win32"
        else _posix_memory_snapshot()
    )
    return snapshot or MemorySnapshot()


def _total_memory_bytes() -> int | None:
    """Compatibility seam retained for deterministic diagnostics tests."""
    return memory_snapshot().total


def memory_class(total_bytes: int | None = None) -> str | None:
    total = _total_memory_bytes() if total_bytes is None else total_bytes
    if not total or total <= 0:
        return None
    gib = total / (1024**3)
    for ceiling in (1, 2, 4, 8, 16, 32, 64):
        if gib <= ceiling * 1.15:
            return f"up-to-{ceiling}gb"
    return "over-64gb"


def _windows_cpu_details() -> tuple[str | None, int | None]:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            description = _safe_hardware_text(
                winreg.QueryValueEx(key, "ProcessorNameString")[0]
            )
            frequency = int(winreg.QueryValueEx(key, "~MHz")[0])
        return description, frequency if frequency > 0 else None
    except (ImportError, OSError, TypeError, ValueError):
        return None, None


def _posix_cpu_details() -> tuple[str | None, int | None]:
    description = None
    frequency = None
    try:
        lines = Path("/proc/cpuinfo").read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines()[:512]
        for line in lines:
            key, separator, value = line.partition(":")
            normalized = key.strip().lower()
            if separator and description is None and normalized in {
                "model name",
                "hardware",
                "processor",
            }:
                description = _safe_hardware_text(value)
            if separator and frequency is None and normalized == "cpu mhz":
                frequency = max(1, int(float(value.strip())))
    except (OSError, TypeError, ValueError):
        pass
    if description is None:
        description = _safe_hardware_text(platform.processor())
    return description, frequency


def cpu_details() -> tuple[str | None, int | None]:
    return _windows_cpu_details() if sys.platform == "win32" else _posix_cpu_details()


def process_memory_bytes() -> int | None:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("page_fault_count", wintypes.DWORD),
                    ("peak_working_set_size", ctypes.c_size_t),
                    ("working_set_size", ctypes.c_size_t),
                    ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                    ("quota_paged_pool_usage", ctypes.c_size_t),
                    ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                    ("quota_non_paged_pool_usage", ctypes.c_size_t),
                    ("pagefile_usage", ctypes.c_size_t),
                    ("peak_pagefile_usage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.K32GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(ProcessMemoryCounters),
                wintypes.DWORD,
            ]
            kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            process = kernel32.GetCurrentProcess()
            if kernel32.K32GetProcessMemoryInfo(
                process,
                ctypes.byref(counters),
                counters.cb,
            ):
                value = int(counters.working_set_size)
                return value if value > 0 else None
        except (AttributeError, OSError, TypeError, ValueError):
            return None
    try:
        resident_pages = int(Path("/proc/self/statm").read_text("ascii").split()[1])
        value = resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
        return value if value > 0 else None
    except (AttributeError, OSError, TypeError, ValueError, IndexError):
        return None


def display_snapshot() -> DisplaySnapshot:
    if sys.platform != "win32":
        return DisplaySnapshot()
    try:
        import ctypes

        user32 = ctypes.windll.user32
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
        dpi_getter = getattr(user32, "GetDpiForSystem", None)
        dpi = int(dpi_getter()) if dpi_getter is not None else None
        return DisplaySnapshot(
            width=width if width > 0 else None,
            height=height if height > 0 else None,
            dpi=dpi if dpi and dpi > 0 else None,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return DisplaySnapshot()


def display_class(width: int | None = None) -> str | None:
    selected = display_snapshot().width if width is None else width
    if not selected or selected <= 0:
        return None
    if selected <= 1366:
        return "compact"
    if selected <= 1920:
        return "standard"
    return "large"


def _gpu_vendor(name: str) -> str | None:
    lowered = name.lower()
    for needle, vendor in (
        ("nvidia", "NVIDIA"),
        ("radeon", "AMD"),
        (" amd ", "AMD"),
        ("intel", "Intel"),
        ("adreno", "Qualcomm"),
        ("mali", "Arm"),
        ("apple", "Apple"),
    ):
        if needle in f" {lowered} ":
            return vendor
    return None


def _windows_gpu_snapshot() -> GpuSnapshot | None:
    try:
        import ctypes
        from ctypes import wintypes

        class DisplayDevice(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("device_name", wintypes.WCHAR * 32),
                ("device_string", wintypes.WCHAR * 128),
                ("state_flags", wintypes.DWORD),
                ("device_id", wintypes.WCHAR * 128),
                ("device_key", wintypes.WCHAR * 128),
            ]

        device = DisplayDevice()
        device.cb = ctypes.sizeof(device)
        if not ctypes.windll.user32.EnumDisplayDevicesW(None, 0, ctypes.byref(device), 0):
            return None
        name = _safe_hardware_text(device.device_string)
        if name is None:
            return None
        return GpuSnapshot(
            name=name,
            vendor_name=_gpu_vendor(name),
            api_type="Direct3D",
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _posix_gpu_snapshot() -> GpuSnapshot | None:
    for candidate in (
        Path("/sys/class/kgsl/kgsl-3d0/gpu_model"),
        Path("/proc/gpuinfo"),
    ):
        try:
            name = _safe_hardware_text(candidate.read_text("utf-8", errors="ignore")[:512])
        except OSError:
            continue
        if name:
            return GpuSnapshot(
                name=name,
                vendor_name=_gpu_vendor(name),
                api_type="OpenGL ES" if hasattr(sys, "getandroidapilevel") else None,
            )
    return None


def gpu_snapshot() -> GpuSnapshot | None:
    return _windows_gpu_snapshot() if sys.platform == "win32" else _posix_gpu_snapshot()


def collect_device_profile() -> DeviceProfile:
    memory = memory_snapshot()
    display = display_snapshot()
    description, frequency = cpu_details()
    count = os.cpu_count()
    return DeviceProfile(
        processor_count=count if count and count > 0 else None,
        cpu_description=description,
        processor_frequency_mhz=frequency,
        memory=memory,
        process_memory_bytes=process_memory_bytes(),
        display=display,
        gpu=gpu_snapshot(),
    )


def os_build() -> str | None:
    value = platform.version().strip()
    return value[:64] or None
