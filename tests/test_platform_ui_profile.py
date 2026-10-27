from __future__ import annotations

import flet as ft
import pytest

import platform_utils


class _Page:
    def __init__(self, platform):
        self.platform = platform


@pytest.fixture(autouse=True)
def restore_platform_flags():
    names = (
        "IS_WINDOWS",
        "IS_ANDROID",
        "IS_IOS",
        "IS_MOBILE",
        "IS_DESKTOP",
        "UI_PLATFORM_OVERRIDE",
        "UI_PLATFORM",
        "UI_IS_ANDROID",
        "UI_IS_IOS",
        "UI_IS_MOBILE",
        "UI_IS_DESKTOP",
    )
    previous = {name: getattr(platform_utils, name) for name in names}
    yield
    for name, value in previous.items():
        setattr(platform_utils, name, value)


@pytest.fixture(params=[True, False], ids=["windows-runtime", "linux-runtime"])
def desktop_runtime(request, monkeypatch):
    monkeypatch.setattr(platform_utils, "IS_WINDOWS", request.param)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)
    monkeypatch.setattr(platform_utils, "IS_MOBILE", False)
    monkeypatch.setattr(platform_utils, "IS_DESKTOP", True)
    return request.param


def test_android_ui_override_does_not_enable_android_runtime(monkeypatch, desktop_runtime):
    monkeypatch.setenv("UTHELPER_UI_PLATFORM", "android")

    platform_utils.detect_platform(_Page(ft.PagePlatform.WINDOWS))

    assert platform_utils.IS_WINDOWS is desktop_runtime
    assert platform_utils.IS_ANDROID is False
    assert platform_utils.IS_MOBILE is False
    assert platform_utils.UI_PLATFORM_OVERRIDE == "android"
    assert platform_utils.UI_PLATFORM == "android"
    assert platform_utils.UI_IS_ANDROID is True
    assert platform_utils.UI_IS_MOBILE is True


def test_ios_ui_override_does_not_enable_ios_runtime(monkeypatch, desktop_runtime):
    monkeypatch.setenv("UTHELPER_UI_PLATFORM", "ios")

    platform_utils.detect_platform(_Page(ft.PagePlatform.WINDOWS))

    assert platform_utils.IS_WINDOWS is desktop_runtime
    assert platform_utils.IS_IOS is False
    assert platform_utils.IS_MOBILE is False
    assert platform_utils.UI_IS_IOS is True
    assert platform_utils.UI_IS_MOBILE is True


def test_desktop_override_keeps_android_runtime_capabilities(monkeypatch):
    monkeypatch.setenv("UTHELPER_UI_PLATFORM", "desktop")
    monkeypatch.setattr(platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", True)
    monkeypatch.setattr(platform_utils, "IS_MOBILE", True)
    monkeypatch.setattr(platform_utils, "IS_DESKTOP", False)

    platform_utils.detect_platform(_Page(ft.PagePlatform.ANDROID))

    assert platform_utils.IS_ANDROID is True
    assert platform_utils.IS_MOBILE is True
    assert platform_utils.UI_PLATFORM == "desktop"
    assert platform_utils.UI_IS_DESKTOP is True
    assert platform_utils.UI_IS_MOBILE is False


def test_missing_override_follows_flet_presentation_platform(monkeypatch, desktop_runtime):
    monkeypatch.delenv("UTHELPER_UI_PLATFORM", raising=False)

    platform_utils.detect_platform(_Page(ft.PagePlatform.IOS))

    assert platform_utils.IS_WINDOWS is desktop_runtime
    assert platform_utils.IS_IOS is False
    assert platform_utils.IS_MOBILE is False
    assert platform_utils.UI_PLATFORM_OVERRIDE is None
    assert platform_utils.UI_PLATFORM == "ios"
    assert platform_utils.UI_IS_IOS is True


def test_empty_explicit_environment_does_not_read_process_environment(monkeypatch):
    monkeypatch.setenv("UTHELPER_UI_PLATFORM", "android")

    assert platform_utils._read_ui_override({}) is None


def test_invalid_override_falls_back_without_logging_value(monkeypatch, caplog):
    monkeypatch.setenv("UTHELPER_UI_PLATFORM", "invalid-private-value")

    platform_utils.detect_platform(_Page(ft.PagePlatform.WINDOWS))

    assert platform_utils.UI_PLATFORM_OVERRIDE is None
    assert platform_utils.UI_PLATFORM == "desktop"
    assert "invalid-private-value" not in caplog.text
