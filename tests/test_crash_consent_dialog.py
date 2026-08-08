from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui.app_controller import AppController
from gui.components.crash_consent_dialog import CrashConsentDialog


class FakePage:
    def __init__(self):
        self.dialogs = []
        self.pop_count = 0

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        self.pop_count += 1


@pytest.fixture(autouse=True)
def reset_process_prompt_state(monkeypatch):
    monkeypatch.setattr(CrashConsentDialog, "_presented_in_process", False)


def _button(dialog, text):
    return next(action for action in dialog.actions if action.content == text)


def test_not_asked_opens_modal_with_enable_decline_and_later_actions():
    page = FakePage()
    component = CrashConsentDialog(page, Mock(return_value=True))

    assert component.present_if_needed("not_asked") is True
    assert len(page.dialogs) == 1
    dialog = page.dialogs[0]
    assert dialog.modal is True
    assert {action.content for action in dialog.actions} == {
        "Bật",
        "Từ chối",
        "Để sau",
    }


@pytest.mark.parametrize("consent", ["enabled", "disabled"])
def test_explicit_consent_never_opens_prompt(consent):
    page = FakePage()

    assert CrashConsentDialog(page, Mock()).present_if_needed(consent) is False
    assert page.dialogs == []


def test_window_dismiss_defers_without_calling_decision():
    page = FakePage()
    decision = Mock()
    component = CrashConsentDialog(page, decision)
    component.present_if_needed("not_asked")

    page.dialogs[0].on_dismiss(SimpleNamespace())

    decision.assert_not_called()
    assert component.current_consent == "not_asked"


def test_later_button_is_a_user_accessible_deferral_without_a_decision():
    page = FakePage()
    decision = Mock()
    component = CrashConsentDialog(page, decision)
    component.present_if_needed("not_asked")

    _button(page.dialogs[0], "Để sau").on_click(SimpleNamespace())

    decision.assert_not_called()
    assert component.current_consent == "not_asked"
    assert page.pop_count == 1


@pytest.mark.parametrize(
    ("button_text", "literal"),
    [("Bật", "enabled"), ("Từ chối", "disabled")],
)
def test_each_action_calls_its_exact_literal_once(button_text, literal):
    page = FakePage()
    decision = Mock(return_value=True)
    component = CrashConsentDialog(page, decision)
    component.present_if_needed("not_asked")

    _button(page.dialogs[0], button_text).on_click(SimpleNamespace())

    decision.assert_called_once_with(literal)
    assert component.current_consent == literal
    assert page.pop_count == 1


def test_same_dialog_instance_never_prompts_twice():
    page = FakePage()
    component = CrashConsentDialog(page, Mock(return_value=True))

    assert component.present_if_needed("not_asked") is True
    assert component.present_if_needed("not_asked") is False
    assert len(page.dialogs) == 1


def test_showing_and_dismissing_prompt_never_touches_transport(monkeypatch):
    import httpx
    import urllib.request

    monkeypatch.setattr(httpx, "post", Mock(side_effect=AssertionError("network touched")))
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        Mock(side_effect=AssertionError("network touched")),
    )
    page = FakePage()
    component = CrashConsentDialog(page, Mock())

    assert component.present_if_needed("not_asked") is True
    page.dialogs[0].on_dismiss(SimpleNamespace())


@pytest.mark.parametrize("decision", ["enabled", "disabled"])
def test_controller_persists_each_explicit_decision_before_treating_it_as_decided(
    monkeypatch, decision
):
    import gui.app_controller as controller_module

    controller = AppController.__new__(AppController)
    controller._show_snackbar = Mock()
    controller.settings_view = SimpleNamespace(
        visible=True,
        _dd_crash_reporting_consent=SimpleNamespace(value="not_asked"),
        _baseline_snapshot=object(),
    )
    monkeypatch.setattr(controller_module.settings, "CRASH_REPORTING_CONSENT", "not_asked")

    def save():
        assert controller_module.settings.CRASH_REPORTING_CONSENT == decision
        return True

    monkeypatch.setattr(controller_module, "save_settings", save)
    original_baseline = controller.settings_view._baseline_snapshot

    assert controller._persist_crash_consent(decision) is True
    assert controller_module.settings.CRASH_REPORTING_CONSENT == decision
    assert controller.settings_view._dd_crash_reporting_consent.value == "not_asked"
    assert controller.settings_view._baseline_snapshot is original_baseline
    controller._show_snackbar.assert_not_called()


@pytest.mark.parametrize("decision", ["enabled", "disabled"])
def test_controller_rolls_back_failed_consent_and_reports_local_error(
    monkeypatch, decision
):
    import gui.app_controller as controller_module

    controller = AppController.__new__(AppController)
    controller._show_snackbar = Mock()
    monkeypatch.setattr(controller_module.settings, "CRASH_REPORTING_CONSENT", "not_asked")
    monkeypatch.setattr(controller_module, "save_settings", lambda: False)

    assert controller._persist_crash_consent(decision) is False
    assert controller_module.settings.CRASH_REPORTING_CONSENT == "not_asked"
    controller._show_snackbar.assert_called_once()
    assert "không thể lưu" in controller._show_snackbar.call_args.args[0].casefold()
