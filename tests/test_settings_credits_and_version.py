"""TDD tests for v2.6.0 UI version synchronization and Settings Credits view."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft
import pytest

from core.distribution import DistributionChannel, build_distribution_profile
from core.version import APP_VERSION, get_app_version
from gui.components.settings_view import SettingsView
from gui.core.theme import C, THEME_ORDER, apply_theme, load_theme_from_settings
from platform_utils import quiz_client_bootstrap
from scripts.prepare_windows_bundle import main as prepare_bundle_main
from scripts.prepare_windows_bundle import prepare_windows_bundle


ROOT = Path(__file__).resolve().parents[1]


def _make_dummy_page():
    return Mock(
        spec=ft.Page,
        window=Mock(),
        theme_mode="dark",
        on_route_change=None,
    )


def _collect_text_values(control) -> list[str]:
    found: list[str] = []
    if isinstance(control, ft.Text) and isinstance(control.value, str):
        found.append(control.value)
    content = getattr(control, "content", None)
    if isinstance(content, ft.BaseControl):
        found.extend(_collect_text_values(content))
    elif isinstance(content, str):
        found.append(content)
    for child in getattr(control, "controls", None) or ():
        found.extend(_collect_text_values(child))
    return found


def _collect_controls(control) -> list[object]:
    items: list[object] = [control]
    content = getattr(control, "content", None)
    if isinstance(content, ft.BaseControl):
        items.extend(_collect_controls(content))
    for child in getattr(control, "controls", None) or ():
        items.extend(_collect_controls(child))
    return items


@pytest.fixture(autouse=True)
def _restore_theme():
    yield
    load_theme_from_settings()


def test_app_version_matches_pyproject_2_6_0_in_source_and_packaged_modes(tmp_path):
    import gui.app_controller as app_controller
    import src.main as app_main

    assert APP_VERSION == "2.6.0"
    assert app_controller.APP_VERSION == "v2.6.0"
    assert app_main.__version__ == "2.6.0"

    runtime_version = tmp_path / "release-version"
    runtime_version.write_bytes(b"2.6.0\n")
    assert (
        get_app_version(
            pyproject_path=tmp_path / "missing-pyproject.toml",
            runtime_version_path=runtime_version,
        )
        == "2.6.0"
    )


@pytest.mark.parametrize("stale_bytes", [b"2.5.0\n", b"2.5.1\n"])
def test_prepare_windows_bundle_syncs_stale_release_version_from_pyproject(
    tmp_path: Path, stale_bytes: bytes
):
    (tmp_path / "UTHelper.exe").write_bytes(b"MZ-runner")
    runtime_file = tmp_path / "app" / "assets" / "release-version"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_bytes(stale_bytes)

    alias = prepare_windows_bundle(tmp_path)

    assert alias == tmp_path / "UTHelperAutostart.exe"
    assert runtime_file.read_bytes() == b"2.6.0\n"
    assert (
        get_app_version(
            pyproject_path=tmp_path / "missing-pyproject.toml",
            runtime_version_path=runtime_file,
        )
        == "2.6.0"
    )


def test_prepare_windows_bundle_cli_supports_explicit_pyproject_sync(tmp_path: Path):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "UTHelper.exe").write_bytes(b"MZ-runner")
    runtime_file = bundle_dir / "app" / "assets" / "release-version"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_bytes(b"2.5.0\n")

    custom_pyproject = tmp_path / "pyproject.toml"
    custom_pyproject.write_text('[project]\nversion = "2.6.0"\n', encoding="utf-8")

    rc = prepare_bundle_main([str(bundle_dir), "--pyproject", str(custom_pyproject)])

    assert rc == 0
    assert runtime_file.read_bytes() == b"2.6.0\n"


def test_quiz_client_bootstrap_syncs_stale_local_release_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    extension_root = tmp_path / "extensions" / "flet_uth_quiz"
    flutter_package = extension_root / "flutter" / "flet_uth_quiz"
    flutter_package.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "2.6.0"\n[tool.flet.windows]\n',
        encoding="utf-8",
    )
    (extension_root / "pyproject.toml").write_text(
        "[project]\nname='flet-uth-quiz'\n", encoding="utf-8"
    )
    (flutter_package / "lib.dart").write_text("class Extension {}\n", encoding="utf-8")

    stale_src_version = tmp_path / "src" / "assets" / "release-version"
    stale_src_version.parent.mkdir(parents=True)
    stale_src_version.write_bytes(b"2.5.0\n")

    observed_during_build: list[bytes] = []

    def fake_build(_command, *, cwd, env, check, timeout=None):
        observed_during_build.append(stale_src_version.read_bytes())
        (cwd / "build" / "windows" / "app" / "assets").mkdir(
            parents=True, exist_ok=True
        )
        (cwd / "build" / "windows" / "UTHelper.exe").write_bytes(b"client")
        (cwd / "build" / "windows" / "app" / "assets" / "release-version").write_bytes(
            stale_src_version.read_bytes()
        )
        generated = cwd / "build" / "flutter" / "lib"
        generated.mkdir(parents=True, exist_ok=True)
        (generated / "flet_generated.dart").write_text(
            "flet_uth_quiz.Extension()\n", encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(quiz_client_bootstrap, "_flet_cli_is_installed", lambda: True)
    monkeypatch.setattr(quiz_client_bootstrap.subprocess, "run", fake_build)
    monkeypatch.setattr(
        quiz_client_bootstrap.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**11, used=10**10, free=10**10),
    )
    monkeypatch.delenv("UTHELPER_SKIP_QUIZ_CLIENT_BUILD", raising=False)

    quiz_client_bootstrap.ensure_windows_quiz_client(tmp_path)

    assert observed_during_build == [b"2.6.0\n"]
    assert stale_src_version.read_bytes() == b"2.6.0\n"
    assert (
        tmp_path / "build" / "windows" / "app" / "assets" / "release-version"
    ).read_bytes() == b"2.6.0\n"


@pytest.mark.parametrize(
    ("channel", "expected_hub_label"),
    [
        (DistributionChannel.SOURCE, "UTHelper v2.6.0"),
        (DistributionChannel.EXE_LTS, "UTHelper v2.6.0"),
        (DistributionChannel.APK_LTS, "UTHelper v2.6.0"),
        (DistributionChannel.MSSTORE, "UTHelper v2.6.0 · Microsoft Store"),
        (DistributionChannel.CHPLAY, "UTHelper v2.6.0 · Google Play"),
    ],
)
def test_settings_version_labels_display_2_6_0_across_all_distribution_channels(
    channel: DistributionChannel, expected_hub_label: str
):
    page = _make_dummy_page()
    profile = build_distribution_profile(channel)
    view = SettingsView(
        page=page,
        orchestrator=None,
        on_close=lambda: None,
        distribution_profile=profile,
    )
    view.update = Mock()

    # 1. Settings Hub footer
    assert view._hub_version.value == expected_hub_label
    assert view._version_text is view._hub_version

    # 2. Settings -> Hệ thống
    view._navigate_to_detail("system")
    system_texts = _collect_text_values(view._detail_content_holder)
    assert any("v2.6.0" in text for text in system_texts)
    assert hasattr(view, "_system_version_text")
    assert "v2.6.0" in view._system_version_text.value

    # 3. Settings -> Debug Device Info
    view._debug_info_text.update = Mock()
    view._do_show_device_info()
    assert "App: v2.6.0" in view._debug_info_text.value


def test_no_hardcoded_stale_2_5_version_in_gui_or_packaging_sources():
    stale_pattern = re.compile(r"\b2\.5\.(?:0|1)\b")
    violations: list[str] = []

    for root in (ROOT / "src" / "gui", ROOT / "packaging" / "windows"):
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".wxs", ".wixproj"}:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if stale_pattern.search(line):
                    violations.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")

    assert violations == []


def test_settings_hub_preserves_eight_cards_and_exposes_credits_entry_pill():
    page = _make_dummy_page()
    view = SettingsView(page=page, orchestrator=None, on_close=lambda: None)

    assert len(view._category_defs) == 8
    assert len(view._category_cards) == 8
    assert hasattr(view, "_credits_entry_pill")
    assert callable(view._credits_entry_pill.on_click)

    pill_texts = _collect_text_values(view._credits_entry_pill)
    assert any("UTHelper v2.6.0" in text for text in pill_texts)
    assert "Giới thiệu & Tác giả" in pill_texts


def test_credits_entry_pill_opens_credits_detail_with_founder_and_contributors_hierarchy():
    page = _make_dummy_page()
    closed_calls: list[bool] = []
    launched_urls: list[str] = []

    view = SettingsView(
        page=page,
        orchestrator=None,
        on_close=lambda: closed_calls.append(True),
        distribution_profile=build_distribution_profile(DistributionChannel.MSSTORE),
    )
    view.update = Mock()
    view._launch_external_store_url = lambda url, fallback_url=None: (
        launched_urls.append(url) or True
    )
    view._baseline_snapshot = view._capture_form_snapshot()
    assert view.has_changes() is False

    # Clicking the Hub footer pill navigates directly to the in-window "credits" screen.
    view._credits_entry_pill.on_click(None)

    assert view._current_screen == "credits"
    assert view._active_category_id == "credits"
    assert view._detail_title.value == "Giới thiệu & Đóng góp"
    assert "UTHelper v2.6.0" in view._detail_subtitle.value
    assert view._sticky_footer.visible is False
    assert view.has_changes() is False
    assert view._unsaved_dot.visible is False

    # Verify visual hierarchy: distinct Founder primary card vs secondary Contributors card.
    assert hasattr(view, "_credits_identity_card")
    assert hasattr(view, "_credits_founder_card")
    assert hasattr(view, "_credits_contributors_card")
    assert view._credits_founder_card is not view._credits_contributors_card

    identity_texts = _collect_text_values(view._credits_identity_card)
    assert "UTHelper" in identity_texts
    assert "v2.6.0" in identity_texts
    assert "Microsoft Store" in identity_texts

    founder_texts = _collect_text_values(view._credits_founder_card)
    assert "Sáng lập & Phát triển chính" in founder_texts
    assert "Nguyễn Trần Đình Chương" in founder_texts
    assert "@Chouwzi" in founder_texts
    assert any("Khởi xướng dự án" in text for text in founder_texts)
    assert any("03/2026" in text for text in founder_texts)

    contributor_texts = _collect_text_values(view._credits_contributors_card)
    assert "Đóng góp từ cộng đồng" in contributor_texts
    assert any("@Baong123" in text and "Nguyễn Trương Bảo" in text for text in contributor_texts)
    assert "@nguyen2006-dev" in contributor_texts
    assert any("@ThienNguyen0210" in text and "ThienNguyen-Dev" in text for text in contributor_texts)
    assert "Nguyễn Trần Đình Chương" not in contributor_texts

    # Founder card uses accent-elevated border; contributor card stays visually subordinate.
    assert view._credits_founder_card.border != view._credits_contributors_card.border

    # External GitHub links route cleanly through _launch_external_store_url.
    buttons = [
        ctrl
        for ctrl in _collect_controls(view._detail_content_holder)
        if isinstance(ctrl, ft.TextButton)
    ]
    assert len(buttons) >= 2
    for btn in buttons:
        btn.on_click(None)
    assert "https://github.com/Chouwzi" in launched_urls
    assert "https://github.com/Chouwzi/UTHelper" in launched_urls

    # Back returns cleanly to Hub without closing Settings or dirtying form state.
    asyncio.run(view._handle_back(None, on_closed=None))
    assert view._current_screen == "hub"
    assert view._active_category_id is None
    assert view._view_switcher.content is view._hub_container
    assert closed_calls == []
    assert view.has_changes() is False
    assert view._unsaved_dot.visible is False


def test_credits_view_and_hub_pill_adapt_live_across_all_nine_themes():
    page = _make_dummy_page()
    view = SettingsView(page=page, orchestrator=None, on_close=lambda: None)
    view.update = Mock()
    view._navigate_to_detail("credits")

    assert len(THEME_ORDER) == 9
    for theme_name in THEME_ORDER:
        apply_theme(theme_name)
        view._refresh_section_colors()

        assert view._credits_entry_pill.bgcolor == C.SURFACE, theme_name
        assert view._credits_entry_label.color == C.ACCENT, theme_name
        assert view._hub_version.color == C.TEXT_SECONDARY, theme_name
        assert view._credits_identity_card.bgcolor == C.SURFACE, theme_name
        assert view._credits_founder_card.bgcolor == C.SURFACE, theme_name
        assert view._credits_contributors_card.bgcolor == C.SURFACE, theme_name
        assert view._credits_founder_name.color == C.TEXT_PRIMARY, theme_name
        assert view._credits_founder_handle.color == C.ACCENT, theme_name
        assert view._credits_version_badge_text.color == C.ACCENT, theme_name
        assert view._credits_disclaimer_text.color == C.TEXT_SECONDARY, theme_name
        assert view._system_version_text.color == C.TEXT_SECONDARY, theme_name
