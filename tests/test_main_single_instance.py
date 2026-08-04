from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.main as application


class _BrokerSpy:
    def __init__(self) -> None:
        self.close_calls: list[float] = []

    def close(self, *, timeout_seconds: float) -> None:
        self.close_calls.append(timeout_seconds)


def _install_flet_runner(monkeypatch, *, invoke_target: bool = False, error=None):
    calls: list[dict[str, object]] = []

    def run(**kwargs) -> None:
        calls.append(kwargs)
        if invoke_target:
            kwargs["main"](object())
        if error is not None:
            raise error

    fake_flet = SimpleNamespace(
        Page=object,
        AppView=SimpleNamespace(WEB_BROWSER="web-browser"),
        run=run,
    )
    monkeypatch.setitem(sys.modules, "flet", fake_flet)
    return calls


@pytest.mark.parametrize(
    ("platform_name", "argv"),
    [
        ("linux", ["main.py"]),
        ("win32", ["main.py", "--web"]),
    ],
)
def test_non_desktop_windows_paths_bypass_single_instance_bootstrap(
    monkeypatch, platform_name, argv
):
    calls = _install_flet_runner(monkeypatch)
    bootstrap_calls: list[dict[str, object]] = []
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application.sys, "platform", platform_name)
    monkeypatch.setattr(application.sys, "argv", argv)
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **kwargs: bootstrap_calls.append(kwargs),
        raising=False,
    )

    assert application.main() == 0

    assert bootstrap_calls == []
    assert len(calls) == 1


def test_windows_desktop_bootstraps_before_running_flet(monkeypatch):
    events: list[str] = []
    calls = _install_flet_runner(monkeypatch)
    result = SimpleNamespace(exit_code=None, broker=None, force_visible=False)
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    def bootstrap(**kwargs):
        assert calls == []
        events.append("bootstrap")
        assert kwargs == {
            "autostart_launch": False,
            "release_channel": "stable",
            "development": True,
        }
        return result

    monkeypatch.setattr(
        application, "bootstrap_windows_instance", bootstrap, raising=False
    )
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False, raising=False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda path: True, raising=False)

    assert application.main() == 0

    assert events == ["bootstrap"]
    assert len(calls) == 1
    assert set(calls[0]) == {"main", "assets_dir"}
    assert "target" not in calls[0]


@pytest.mark.parametrize("exit_code", [0, 2])
def test_secondary_instance_exits_without_starting_flet(monkeypatch, exit_code):
    calls = _install_flet_runner(monkeypatch)
    result = SimpleNamespace(exit_code=exit_code, broker=None, force_visible=False)
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **kwargs: result,
        raising=False,
    )
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False, raising=False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda path: True, raising=False)

    assert application.main() == exit_code

    assert calls == []


def test_primary_passes_bootstrap_dependencies_to_desktop_composition(monkeypatch):
    app_calls: list[tuple[object, object, bool]] = []
    broker = _BrokerSpy()
    result = SimpleNamespace(exit_code=None, broker=broker, force_visible=True)
    _install_flet_runner(monkeypatch, invoke_target=True)
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **kwargs: result,
        raising=False,
    )
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False, raising=False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda path: True, raising=False)
    desktop_module = SimpleNamespace(
        main=lambda page, *, activation_broker, force_visible: app_calls.append(
            (page, activation_broker, force_visible)
        )
    )
    monkeypatch.setitem(sys.modules, "gui.compact_desktop", desktop_module)

    assert application.main() == 0

    assert len(app_calls) == 1
    assert app_calls[0][1:] == (broker, True)
    assert broker.close_calls == [1.0]


def test_primary_broker_is_closed_when_flet_runner_raises(monkeypatch):
    broker = _BrokerSpy()
    result = SimpleNamespace(exit_code=None, broker=broker, force_visible=False)
    _install_flet_runner(monkeypatch, error=RuntimeError("flet failed"))
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **kwargs: result,
        raising=False,
    )
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False, raising=False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda path: True, raising=False)

    with pytest.raises(RuntimeError, match="flet failed"):
        application.main()

    assert broker.close_calls == [1.0]


def test_source_checkout_detection_uses_the_project_pyproject_file(tmp_path):
    extracted_module = tmp_path / "serious_python_0.86.5" / "src" / "main.py"
    extracted_module.parent.mkdir(parents=True)
    extracted_module.touch()

    assert application._is_source_checkout(Path(application.__file__)) is True
    assert application._is_source_checkout(extracted_module) is False
