from __future__ import annotations

import asyncio
import builtins
import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.main as application


def test_main_import_does_not_bind_user_log_during_pytest_collection():
    assert application._TEST_MODE is True
    assert application._LOGGING_RUNTIME is None


class _BrokerSpy:
    def __init__(self) -> None:
        self.close_calls: list[float] = []

    def close(self, *, timeout_seconds: float) -> None:
        self.close_calls.append(timeout_seconds)


class _RuntimeSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def start(self) -> None:
        self.calls.append(("start",))

    def attach_page(self, page) -> None:
        self.calls.append(("attach_page", page))

    def attach_asyncio_loop(self, loop) -> None:
        self.calls.append(("attach_asyncio_loop", loop))

    def mark_phase(self, phase) -> None:
        self.calls.append(("mark_phase", phase))

    def record_exception(self, exc, phase) -> str:
        self.calls.append(("record_exception", exc, phase))
        return "safe-reference"

    def close(self, *, clean: bool) -> None:
        self.calls.append(("close", clean))


@pytest.fixture(autouse=True)
def fake_diagnostic_runtime(monkeypatch):
    runtimes: list[_RuntimeSpy] = []

    def factory(*_args, **_kwargs):
        runtime = _RuntimeSpy()
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(application, "create_default_runtime", factory)
    return runtimes


def _install_flet_runner(monkeypatch, *, invoke_target: bool = False, error=None):
    calls: list[dict[str, object]] = []

    def run(**kwargs) -> None:
        calls.append(kwargs)
        if invoke_target:
            result = kwargs["main"](object())
            if inspect.isawaitable(result):
                asyncio.run(result)
        if error is not None:
            raise error

    fake_flet = SimpleNamespace(
        Page=object,
        AppView=SimpleNamespace(
            WEB_BROWSER="web-browser",
            FLET_APP_HIDDEN="flet-app-hidden",
        ),
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
    assert set(calls[0]) == {"main", "assets_dir", "view"}
    assert calls[0]["view"] == "flet-app-hidden"
    assert "target" not in calls[0]


def test_windows_desktop_bootstraps_before_importing_flet(monkeypatch):
    """A secondary must exit before loading the heavyweight UI runtime."""
    events: list[str] = []
    _install_flet_runner(monkeypatch)
    result = SimpleNamespace(exit_code=0, broker=None, force_visible=False)
    real_import = builtins.__import__

    def recording_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "flet":
            events.append("flet-import")
        return real_import(name, globals, locals, fromlist, level)

    def bootstrap(**kwargs):
        events.append("bootstrap")
        return result

    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.setattr(application, "bootstrap_windows_instance", bootstrap)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda path: False)
    monkeypatch.setattr(builtins, "__import__", recording_import)

    assert application.main() == 0

    assert events == ["bootstrap"]


def test_source_windows_quiz_build_failure_marks_native_quiz_unavailable(
    monkeypatch, fake_diagnostic_runtime
):
    calls = _install_flet_runner(monkeypatch)
    result = SimpleNamespace(exit_code=None, broker=None, force_visible=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.delenv("FLET_FORCE_WEB_SERVER", raising=False)
    monkeypatch.delenv("FLET_DISPLAY_URL_PREFIX", raising=False)
    monkeypatch.setattr(application, "_TEST_MODE", False)
    monkeypatch.setattr(application.quiz_client_bootstrap, "_source_quiz_client_ready", None)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _path: True)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(
        application, "bootstrap_windows_instance", lambda **_kwargs: result
    )
    changed_directories = []
    monkeypatch.setattr(os, "chdir", lambda path: changed_directories.append(path))
    monkeypatch.setattr(
        application.quiz_client_bootstrap, "add_source_quiz_package_to_path", lambda _root: None
    )
    monkeypatch.setattr(
        application.quiz_client_bootstrap,
        "ensure_windows_quiz_client",
        lambda _root: (_ for _ in ()).throw(RuntimeError("custom client build failed")),
    )

    assert application.main() == 0
    assert len(calls) == 1
    assert application.quiz_client_bootstrap.source_quiz_client_unavailable() is True
    assert fake_diagnostic_runtime[0].calls[0] == ("start",)
    assert changed_directories == [Path(application.__file__).resolve().parents[1]]


def test_source_windows_quiz_failure_rolls_back_added_sys_path(
    monkeypatch, fake_diagnostic_runtime
):
    _install_flet_runner(monkeypatch)
    result = SimpleNamespace(exit_code=None, broker=None, force_visible=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.delenv("FLET_FORCE_WEB_SERVER", raising=False)
    monkeypatch.delenv("FLET_DISPLAY_URL_PREFIX", raising=False)
    monkeypatch.setattr(application, "_TEST_MODE", False)
    monkeypatch.setattr(application.quiz_client_bootstrap, "_source_quiz_client_ready", None)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _path: True)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(
        application, "bootstrap_windows_instance", lambda **_kwargs: result
    )
    monkeypatch.setattr(os, "chdir", lambda _path: None)

    project_root = Path(application.__file__).resolve().parents[1]
    quiz_src = str((project_root / "extensions" / "flet_uth_quiz" / "src").resolve())
    monkeypatch.setattr(
        application.sys,
        "path",
        [entry for entry in sys.path if entry != quiz_src],
    )
    monkeypatch.setattr(
        application.quiz_client_bootstrap,
        "_added_source_paths",
        set(),
    )
    monkeypatch.setattr(
        application.quiz_client_bootstrap,
        "ensure_windows_quiz_client",
        lambda _root: (_ for _ in ()).throw(RuntimeError("build failed")),
    )

    assert application.main() == 0
    assert application.quiz_client_bootstrap.source_quiz_client_unavailable() is True
    assert quiz_src not in application.sys.path


def test_flet_run_outside_project_root_marks_quiz_client_unavailable(
    monkeypatch, tmp_path, fake_diagnostic_runtime
):
    _install_flet_runner(monkeypatch)
    result = SimpleNamespace(exit_code=None, broker=None, force_visible=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.delenv("FLET_FORCE_WEB_SERVER", raising=False)
    monkeypatch.setenv("FLET_DISPLAY_URL_PREFIX", "http://127.0.0.1:8550")
    monkeypatch.setattr(application, "_TEST_MODE", False)
    monkeypatch.setattr(application.quiz_client_bootstrap, "_source_quiz_client_ready", None)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _path: True)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(
        application, "bootstrap_windows_instance", lambda **_kwargs: result
    )
    monkeypatch.chdir(tmp_path)

    assert application.main() == 0
    assert application.quiz_client_bootstrap.source_quiz_client_unavailable() is True


@pytest.mark.parametrize("exit_code", [0, 2])
def test_secondary_instance_exits_without_starting_flet(
    monkeypatch, exit_code, fake_diagnostic_runtime
):
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


def test_flet_force_web_server_is_explicit_web_mode():
    assert application._is_web_mode([], {"FLET_FORCE_WEB_SERVER": "true"}) is True
    assert application._is_web_mode([], {"FLET_FORCE_WEB_SERVER": "false"}) is False


def test_primary_passes_bootstrap_dependencies_to_desktop_composition(monkeypatch):
    app_calls: list[tuple[object, object, bool]] = []
    broker = _BrokerSpy()
    result = SimpleNamespace(exit_code=None, broker=broker, force_visible=True)
    _install_flet_runner(monkeypatch, invoke_target=True)
    startup_calls: list[tuple[object, bool]] = []
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
    monkeypatch.setattr(
        application,
        "_show_startup_screen",
        lambda page, _ft, *, publish, compact_desktop: startup_calls.append(
            (page, publish)
        ),
    )
    desktop_module = SimpleNamespace(
        main=lambda page, *, activation_broker, force_visible, error_reporter: app_calls.append(
            (page, activation_broker, force_visible, error_reporter)
        )
    )
    monkeypatch.setitem(sys.modules, "gui.compact_desktop", desktop_module)

    assert application.main() == 0

    assert len(app_calls) == 1
    assert app_calls[0][1:3] == (broker, True)
    assert callable(app_calls[0][3])
    assert startup_calls == [(app_calls[0][0], True)]
    assert broker.close_calls == [1.0]


def test_primary_broker_is_closed_when_flet_runner_raises(
    monkeypatch, fake_diagnostic_runtime
):
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

    assert application.main() == 1

    assert broker.close_calls == [1.0]
    runtime = fake_diagnostic_runtime[0]
    assert any(call[0] == "record_exception" for call in runtime.calls)
    assert ("close", True) in runtime.calls


def test_unhandled_base_exception_leaves_run_marker(monkeypatch, fake_diagnostic_runtime):
    broker = _BrokerSpy()
    result = SimpleNamespace(exit_code=None, broker=broker, force_visible=False)
    _install_flet_runner(monkeypatch, error=KeyboardInterrupt())
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.setattr(application, "bootstrap_windows_instance", lambda **_: result)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _path: True)

    with pytest.raises(KeyboardInterrupt):
        application.main()

    assert broker.close_calls == [1.0]
    runtime = fake_diagnostic_runtime[0]
    assert not any(call[0] == "record_exception" for call in runtime.calls)
    assert ("close", False) in runtime.calls


def test_broker_close_failure_cannot_skip_runtime_close(
    monkeypatch, fake_diagnostic_runtime
):
    class BrokenBroker(_BrokerSpy):
        def close(self, *, timeout_seconds: float) -> None:
            super().close(timeout_seconds=timeout_seconds)
            raise RuntimeError("broker close failed")

    broker = BrokenBroker()
    result = SimpleNamespace(exit_code=None, broker=broker, force_visible=False)
    _install_flet_runner(monkeypatch)
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.setattr(application, "bootstrap_windows_instance", lambda **_: result)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _path: True)

    with pytest.raises(RuntimeError, match="broker close failed"):
        application.main()

    assert ("close", True) in fake_diagnostic_runtime[0].calls


def test_source_checkout_detection_uses_the_project_pyproject_file(tmp_path):
    extracted_module = tmp_path / "serious_python_0.86.5" / "src" / "main.py"
    extracted_module.parent.mkdir(parents=True)
    extracted_module.touch()

    assert application._is_source_checkout(Path(application.__file__)) is True
    assert application._is_source_checkout(extracted_module) is False
