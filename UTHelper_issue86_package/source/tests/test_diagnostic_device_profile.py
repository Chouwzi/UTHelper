from diagnostics import device_profile


def test_device_profile_never_exposes_stable_identity(monkeypatch):
    monkeypatch.setattr(device_profile.platform, "system", lambda: "Windows")
    monkeypatch.setattr(device_profile.platform, "version", lambda: "10.0.26100")

    profile = {
        "os_family": device_profile.os_family(),
        "os_build": device_profile.os_build(),
        "device_class": device_profile.device_class("Windows"),
        "memory_class": device_profile.memory_class(),
        "display_class": device_profile.display_class(),
    }

    assert profile["device_class"] == "desktop"
    assert profile["os_build"] == "10.0.26100"
    assert not ({"hostname", "username", "serial", "mac", "ip"} & set(profile))


def test_memory_class_is_coarse_and_bounded(monkeypatch):
    monkeypatch.setattr(device_profile, "_total_memory_bytes", lambda: 7 * 1024**3)

    assert device_profile.memory_class() == "up-to-8gb"


def test_probe_failures_return_none(monkeypatch):
    monkeypatch.setattr(device_profile, "_total_memory_bytes", lambda: None)
    monkeypatch.setattr(device_profile.sys, "platform", "linux")

    assert device_profile.memory_class() is None
    assert device_profile.display_class() is None


def test_collect_device_profile_keeps_hardware_data_identity_free(monkeypatch):
    memory = device_profile.MemorySnapshot(
        total=16 * 1024**3,
        available=6 * 1024**3,
        usable=16 * 1024**3,
    )
    display = device_profile.DisplaySnapshot(width=1920, height=1080, dpi=96)
    gpu = device_profile.GpuSnapshot(
        name="AMD Radeon 780M Graphics",
        vendor_name="AMD",
        api_type="Direct3D",
    )
    monkeypatch.setattr(device_profile, "memory_snapshot", lambda: memory)
    monkeypatch.setattr(
        device_profile,
        "cpu_details",
        lambda: ("AMD Ryzen 7 7840HS", 3800),
    )
    monkeypatch.setattr(device_profile, "process_memory_bytes", lambda: 123_456)
    monkeypatch.setattr(device_profile, "display_snapshot", lambda: display)
    monkeypatch.setattr(device_profile, "gpu_snapshot", lambda: gpu)
    monkeypatch.setattr(device_profile.os, "cpu_count", lambda: 16)

    profile = device_profile.collect_device_profile()

    assert profile.processor_count == 16
    assert profile.cpu_description == "AMD Ryzen 7 7840HS"
    assert profile.processor_frequency_mhz == 3800
    assert profile.memory == memory
    assert profile.process_memory_bytes == 123_456
    assert profile.display == display
    assert profile.gpu == gpu
    assert not hasattr(profile, "hostname")
    assert not hasattr(profile, "device_id")


def test_hardware_text_is_bounded_and_normalizes_whitespace():
    assert device_profile._safe_hardware_text(" Intel   Core i7 ") == "Intel Core i7"
    assert device_profile._safe_hardware_text("GPU\nsecret") == "GPU secret"
    assert len(device_profile._safe_hardware_text("x" * 500) or "") == 160
