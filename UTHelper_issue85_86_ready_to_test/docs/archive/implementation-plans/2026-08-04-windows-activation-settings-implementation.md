# Windows Activation and Deterministic Settings Implementation Plan

> **Archived:** Planning snapshot retained for provenance. It is not the current
> task tracker; use tests and current operator documentation as the source of truth.

**Goal:** Guarantee one packaged UTHelper instance per signed-in Windows user, make every explicit second launch reveal and focus the existing window while autostart launches stay silent, and make Settings loading, change detection, saving, and discard deterministic for every editable field.

**Architecture:** Add a Windows-only kernel-object broker below the GUI and a single `WindowActivator` above it. Bootstrap ownership before `ft.run()`, deliver activation from a bounded receiver thread through `page.run_task()`, and route tray Open through the same activator. Replace Settings' comparisons against mutable global state with an immutable, normalized `SettingsFormSnapshot`; load it transactionally after the real autostart state is known, then use the snapshot as the sole baseline for dirty checks, persistence, and discard.

**Tech Stack:** Python 3.11+, Flet 0.86.5, Pydantic, pywin32 306+, pytest, pytest-asyncio, Ruff, PowerShell, Windows named mutex/events and explicit DACLs.

## Global Constraints

- Preserve Clean Architecture boundaries: `src/platform_utils/` must not import Flet or GUI modules; `src/gui/controllers/` may coordinate Flet but must not own Win32 primitives; `AppController` remains the composition root.
- Run the Windows single-instance bootstrap before importing/starting the desktop Flet runner. Web mode and non-Windows platforms bypass it without opening Win32 handles.
- There is one production instance per tuple `(application identity, release channel, current Windows user)`. Development and packaged namespaces must be distinct.
- Named kernel objects must contain only a fixed prefix plus a SHA-256-derived digest. Never place raw SID, username, executable path, credentials, tokens, or settings values in an object name or log.
- Every named mutex/event gets an explicit DACL granting only the current user and `SYSTEM` the synchronization rights needed by this design.
- Use a named mutex for ownership, a named auto-reset activation event for SHOW requests, a named manual-reset acknowledgement event for primary readiness, and an unnamed shutdown event for receiver teardown.
- The acknowledgement event means “the primary receiver is bound and can consume SHOW,” not one acknowledgement per secondary. It stays signaled for that broker lifetime, allowing concurrent secondaries to coalesce safely, and is reset before ownership teardown.
- A manual secondary launch signals SHOW, waits at most 1.5 seconds for acknowledgement, and exits `0`. A `--autostart`/StartupApproved alias secondary exits `0` without signaling SHOW.
- If the apparent primary is stale or dies during handoff, retry mutex ownership exactly once. Become the visible primary if ownership succeeds; otherwise exit with a sanitized diagnostic and non-zero status. Do not loop indefinitely.
- The receiver waits on activation and shutdown simultaneously with a finite 250 ms maximum wait. Broker close waits at most 1.0 second for the receiver thread.
- A failure to initialize Windows primitives is fail-open: log a sanitized error and continue as a visible primary. Packaged smoke verification must treat that diagnostic as a failure.
- All user-driven reveal paths use `WindowActivator`: set `visible=True`, `minimized=False`, `focused=True`, call `page.update()`, and then await `page.window.to_front()`. The receiver thread must never access Flet directly.
- Once an activation request is received, startup-minimized logic must not hide the window again. Tray Open uses the identical activation path.
- `AUTO_UPDATE_ENABLED` defaults to `True`. `CRASH_REPORTING_CONSENT` is exactly `Literal["not_asked", "enabled", "disabled"]` and defaults to `"not_asked"`. Both are members of `SettingsFormSnapshot`.
- `AUTO_UPDATE_ENABLED` controls automatic checking only. Download/install/restart remain separate user-confirmed actions; neither this switch nor its copy may imply unattended installation.
- Settings autostart reconciliation waits at most 2.0 seconds. A stale load generation may not mutate controls, baseline, global settings, or status text.
- Secret fields use `field(repr=False)` in the snapshot. Logs and assertion messages must not include passwords, bot tokens, webhooks, or app passwords.
- All subprocess and PowerShell waits have explicit deadlines. Tests may terminate only PIDs that they launched and must not broadly kill `UTHelper.exe`, Python, or Flutter processes.
- Do not change the existing source-build autostart alias contract: it remains argument-free; packaged builds may recognize the existing alias and explicit `--autostart`.
- Before implementation, record a clean baseline with `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests -q --tb=short` and `ruff check src tests`. After each task, run its focused tests; before integration, run the full suite and lint.

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `src/platform_utils/single_instance.py` | Create | Pure bootstrap policy, Win32 kernel-object adapter, ACL creation, ownership/handoff, bounded receiver lifecycle. |
| `src/gui/controllers/window_activator.py` | Create | The one async Flet operation that reveals, restores, focuses, updates, and raises the window. |
| `src/main.py` | Modify | Perform desktop single-instance bootstrap before `ft.run()`, pass the broker into the GUI, and close it deterministically. |
| `main.py` | Modify | Propagate `src.main.main()`'s integer exit status. |
| `src/gui/compact_desktop.py` | Modify | Accept activation dependencies and return the constructed `AppController`. |
| `src/gui/app_controller.py` | Modify | Compose `WindowActivator`, tray, startup visibility, and broker callback; close broker on disconnect. |
| `src/gui/tray.py` | Modify | Inject a SHOW callback and remove the duplicate window-reveal implementation. |
| `src/gui/view_models/__init__.py` | Create | Export Settings form state types. |
| `src/gui/view_models/settings_form.py` | Create | Immutable snapshot plus canonical parsing/normalization for every editable control. |
| `src/config.py` | Modify | Add typed update/consent fields and make settings persistence report success accurately. |
| `src/gui/components/settings/system_section.py` | Modify | Add the default-on automatic-update control. |
| `src/gui/components/settings/privacy_section.py` | Create | Add the explicit three-state crash-reporting consent control. |
| `src/gui/components/crash_consent_dialog.py` | Create | Present the one-time first-run Enable/Decline consent decision without initiating diagnostics delivery. |
| `src/gui/components/settings_view.py` | Modify | Transactional generation-aware load, snapshot mapping, deterministic dirty/save/discard behavior. |
| `src/gui/view_manager.py` | Modify | Await Settings initialization before showing the view. |
| `tests/test_windows_single_instance.py` | Create | Deterministic unit coverage for naming, ACL request, roles, retry, coalescing, shutdown, and failures. |
| `tests/test_window_activator.py` | Create | Verify the exact reveal/focus/to-front operation and thread-to-UI scheduling boundary. |
| `tests/test_startup_visibility.py` | Modify | Verify forced activation cannot be hidden by startup-minimized policy. |
| `tests/test_autostart_and_tray.py` | Modify | Verify tray Open delegates to `WindowActivator`. |
| `tests/test_settings_form_snapshot.py` | Create | Cover all snapshot fields, normalization, equality, and secret repr behavior. |
| `tests/test_settings_view_state.py` | Create | Cover transactional load, generations, close, dirty checks, save/rebaseline, discard, and partial autostart failure. |
| `tests/test_crash_consent_dialog.py` | Create | Cover first-run opt-in/decline/defer semantics, persistence failures, and zero pre-consent transport activity. |
| `tests/test_settings_autostart_ui.py` | Modify | Cover bounded reconciliation and verified OS state integration. |
| `tests/test_config_extended.py` | Modify | Cover defaults, validation, serialization, and persistence result. |
| `tests/test_windows_single_instance_integration.py` | Create | Real Win32, randomized-namespace integration without invoking Flet. |
| `scripts/test_windows_single_instance_e2e.ps1` | Create | Bounded packaged app scenarios using isolated profile data and owned PIDs. |
| `scripts/test_windows_bundle_e2e.ps1` | Modify | Invoke the focused single-instance E2E after existing bundle checks. |
| `tests/test_release_hardening.py` | Modify | Pin the verifier expectations for activation coverage. |
| `REFAC_KNOWLEDGE.md` | Modify | Record kernel-object boundary, activation flow, Settings snapshot invariant, and test commands. |

---

### Task 1: Define and test Windows instance identity and kernel-object ownership

**Files:**

- Create: `src/platform_utils/single_instance.py`
- Create: `tests/test_windows_single_instance.py`

**Interfaces:**

- Consumes: application identity, release channel, packaged/development marker, current Windows SID, launch mode, and a `KernelObjectApi` adapter.
- Produces: `InstanceBootstrapResult(role, broker, exit_code, force_visible)` and a live `WindowsActivationBroker` only for a primary.
- `KernelObjectApi` exposes `current_user_sid()`, `create_user_system_security_attributes(user_sid)`, `create_mutex(name, initial_owner, security_attributes)`, `create_event(name, manual_reset, initial_state, security_attributes)`, `open_mutex(name)`, `open_event(name)`, `wait_one(handle, timeout_ms)`, `wait_many(handles, timeout_ms)`, `set_event(handle)`, `reset_event(handle)`, `release_mutex(handle)`, and `close_handle(handle)`.

- [ ] Before adding tests, capture the repository baseline with a hard outer bound and retain the exact pass/fail counts in the implementation notes:

```powershell
$baseline = Start-Job { Set-Location 'E:\Projects\UTH-Elearning-Alert-worktrees\reliability-auto-update'; $env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests -q --tb=short }
if (-not (Wait-Job $baseline -Timeout 600)) { Stop-Job $baseline; throw 'baseline pytest exceeded 600 seconds' }
Receive-Job $baseline
Remove-Job $baseline
ruff check src tests
```

- [ ] Add failing tests for deterministic and private object naming. The same inputs must yield the same three names; changing SID, release channel, or packaged marker must change all names; none may contain any raw input.

```python
def test_object_names_are_stable_private_and_environment_scoped():
    prod = build_instance_object_names(
        app_identity="com.uthelper.UTHelper",
        release_channel="stable",
        user_sid="S-1-5-21-111-222-333-1001",
        development=False,
    )
    repeated = build_instance_object_names(
        app_identity="com.uthelper.UTHelper",
        release_channel="stable",
        user_sid="S-1-5-21-111-222-333-1001",
        development=False,
    )
    dev = build_instance_object_names(
        app_identity="com.uthelper.UTHelper",
        release_channel="stable",
        user_sid="S-1-5-21-111-222-333-1001",
        development=True,
    )
    assert prod == repeated
    assert prod != dev
    rendered = " ".join((prod.mutex, prod.activation, prod.acknowledgement))
    assert "com.uthelper" not in rendered
    assert "stable" not in rendered
    assert "S-1-5-21" not in rendered
```

- [ ] Run `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_windows_single_instance.py -q`; confirm collection fails because the module does not exist.
- [ ] Add the public value objects and pure naming function. Use length-prefixed UTF-8 components before SHA-256 so concatenation cannot collide.

```python
class InstanceRole(str, Enum):
    PRIMARY = "primary"
    SECONDARY_ACTIVATED = "secondary_activated"
    SECONDARY_SILENT = "secondary_silent"
    FALLBACK_VISIBLE_PRIMARY = "fallback_visible_primary"
    HANDOFF_FAILED = "handoff_failed"


@dataclass(frozen=True, slots=True)
class InstanceObjectNames:
    mutex: str
    activation: str
    acknowledgement: str


@dataclass(slots=True)
class InstanceBootstrapResult:
    role: InstanceRole
    broker: WindowsActivationBroker | None
    exit_code: int | None
    force_visible: bool


def build_instance_object_names(
    *, app_identity: str, release_channel: str, user_sid: str, development: bool
) -> InstanceObjectNames:
    components = (app_identity, release_channel, user_sid, "dev" if development else "prod")
    payload = b"".join(len(value.encode("utf-8")).to_bytes(4, "big") + value.encode("utf-8") for value in components)
    digest = hashlib.sha256(payload).hexdigest()
    prefix = f"Local\\UTHelper-{digest}"
    return InstanceObjectNames(
        mutex=f"{prefix}-mutex",
        activation=f"{prefix}-activate",
        acknowledgement=f"{prefix}-ready",
    )
```

- [ ] Add a `FakeKernelObjectApi` in the test module. It must record security-attribute calls, created/opened handles, wait timeouts, event signals, resets, releases, and closes; model mutex ownership without sleeping.
- [ ] Add failing tests for the full decision table: first launch becomes `PRIMARY`; manual secondary signals activation and returns `SECONDARY_ACTIVATED/exit_code=0`; autostart secondary does not signal and returns `SECONDARY_SILENT/exit_code=0`; acknowledgement timeout retries ownership exactly once; successful retry returns visible primary; failed retry returns `HANDOFF_FAILED` with a non-zero code.
- [ ] Add tests proving every named object receives the same explicit current-user-plus-SYSTEM security attributes and that the bootstrap uses the SID only through naming/ACL creation.
- [ ] Implement `bootstrap_windows_instance()` with this exact signature and decision table:

```python
def bootstrap_windows_instance(
    *,
    autostart_launch: bool,
    development: bool,
    platform_name: str = sys.platform,
    app_identity: str = "com.uthelper.UTHelper",
    release_channel: str = "stable",
    kernel: KernelObjectApi | None = None,
    acknowledgement_timeout_seconds: float = 1.5,
) -> InstanceBootstrapResult:
```

- [ ] Implement `PyWin32KernelObjectApi` using lazy imports of `win32api`, `win32con`, `win32event`, `win32security`, and `pywintypes`. Obtain the token user SID from the current process; build a protected DACL with current-user and `SYSTEM` ACEs; keep every native handle owned by exactly one Python object.
- [ ] Convert only expected Win32 “already exists/not found/access denied” conditions into branch results. Let unexpected adapter exceptions reach the bootstrap fail-open handler, which emits `single_instance_fail_open` without native error text or object names and returns `FALLBACK_VISIBLE_PRIMARY`.
- [ ] Run `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_windows_single_instance.py -q`; expect all identity, ACL, primary/secondary, retry, and fail-open tests to pass without wall-clock sleeps.
- [ ] Run `ruff check src/platform_utils/single_instance.py tests/test_windows_single_instance.py`.
- [ ] Commit the implementation task:

```powershell
git add src/platform_utils/single_instance.py tests/test_windows_single_instance.py
git commit -m "feat: add secure windows instance ownership"
```

### Task 2: Add the bounded activation receiver and the single GUI activation path

**Files:**

- Modify: `src/platform_utils/single_instance.py`
- Create: `src/gui/controllers/window_activator.py`
- Create: `tests/test_window_activator.py`
- Modify: `tests/test_windows_single_instance.py`

**Interfaces:**

- `WindowsActivationBroker.bind_show_handler(handler: Callable[[], None]) -> None` starts no second receiver and makes the named ready event observable only after the handler is bound.
- `WindowsActivationBroker.close(timeout_seconds: float = 1.0) -> bool` signals shutdown and waits only for the supplied bound. `True` reports that the receiver stopped, all owned handles closed, and no callback can begin later. `False` reports shutdown pending: a callback admitted before the shutdown request may still begin or finish before receiver-exit cleanup. Activations observed after the request are never newly admitted.
- `WindowActivator.request_show() -> None` is thread-safe at the boundary: it calls only `page.run_task(self.show)`.
- `WindowActivator.show() -> None` is async and is the only code that manipulates the Flet window for a SHOW request.

- [ ] Add failing broker tests for: activation invokes the callback; ten activation signals before the receiver consumes them coalesce into at least one callback without deadlock; shutdown wins promptly; `close()` passes `1000` ms or less to all waits; repeated `close()` is idempotent; no callback occurs after a successful close; a false return permits a callback admitted before shutdown to finish while later activations are rejected.
- [ ] Implement the broker receiver with an unnamed manual-reset shutdown event and `wait_many((shutdown, activation), timeout_ms=250)`. On activation, call the bound plain callback and continue. Do not import or call Flet in this module.
- [ ] Set the acknowledgement event only after `bind_show_handler()` has installed the callback and receiver. Reset it during close before releasing ownership so a new secondary cannot mistake teardown for readiness.
- [ ] Add failing `WindowActivator` tests using a page spy that records property writes, `update()`, `run_task()`, and awaited `to_front()`.

```python
@pytest.mark.asyncio
async def test_show_restores_focuses_updates_and_raises_window():
    page = PageSpy()
    activator = WindowActivator(page)

    await activator.show()

    assert page.window.visible is True
    assert page.window.minimized is False
    assert page.window.focused is True
    assert page.update_calls == 1
    assert page.window.to_front_calls == 1


def test_request_show_schedules_async_work_instead_of_touching_window():
    page = PageSpy()
    WindowActivator(page).request_show()
    assert page.scheduled == [WindowActivator.show]
    assert page.window.direct_thread_accesses == 0
```

- [ ] Implement the controller exactly once:

```python
class WindowActivator:
    def __init__(self, page: ft.Page) -> None:
        self._page = page

    def request_show(self) -> None:
        self._page.run_task(self.show)

    async def show(self) -> None:
        self._page.window.visible = True
        self._page.window.minimized = False
        self._page.window.focused = True
        self._page.update()
        await self._page.window.to_front()
```

- [ ] Run `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_windows_single_instance.py tests/test_window_activator.py -q` and `ruff check src/platform_utils/single_instance.py src/gui/controllers/window_activator.py tests/test_windows_single_instance.py tests/test_window_activator.py`.
- [ ] Commit the implementation task:

```powershell
git add src/platform_utils/single_instance.py src/gui/controllers/window_activator.py tests/test_windows_single_instance.py tests/test_window_activator.py
git commit -m "feat: route activation through bounded broker"
```

### Task 3: Bootstrap before Flet and compose activation with tray/startup visibility

**Files:**

- Modify: `src/main.py`
- Modify: `main.py`
- Modify: `src/gui/compact_desktop.py`
- Modify: `src/gui/app_controller.py`
- Modify: `src/gui/tray.py`
- Modify: `tests/test_startup_visibility.py`
- Modify: `tests/test_autostart_and_tray.py`
- Create: `tests/test_main_single_instance.py`

**Interfaces:**

- `src.main.main() -> int` returns the secondary/failure exit status or `0` after the Flet runner exits.
- `_is_source_checkout(module_path: Path) -> bool` returns true only when `module_path.resolve().parents[1] / "pyproject.toml"` is a file; this distinguishes the repository from Flet's extracted `serious_python` runtime without inspecting a parent process.
- `gui.compact_desktop.main(page, *, activation_broker=None, force_visible=False) -> AppController` passes dependencies to the composition root.
- `AppController(page, *, activation_broker=None, force_visible=False)` owns one `WindowActivator`, binds it after page/window initialization, and closes the broker on disconnect.
- `TrayApp(page=None, *, on_show: Callable[[], None] | None = None)` delegates Open to `on_show`.

- [ ] Add failing main tests with patched platform/bootstrap/Flet runner. Assert non-Windows and `--web` bypass; Windows desktop calls bootstrap before `ft.run`; secondaries never call `ft.run`; a primary passes the exact broker and `force_visible` value into the app target; `ft.run` receives exactly one `main` keyword and no `target` keyword; broker close occurs in `finally` when `ft.run` raises.
- [ ] Add failing tests for `_is_source_checkout()`: the real worktree containing `pyproject.toml` is development; an extracted `serious_python_*` layout without the project file is packaged. Refactor `src/main.py` so web-mode detection precedes desktop bootstrap but importing the desktop GUI target remains inside `_app_target`. Use existing `is_autostart_launch()` to classify aliases/`--autostart` before bootstrap. Do not infer autostart from parent process, `sys.frozen`, or the temporary Python executable.

```python
def main() -> int:
    web_mode = _is_web_mode(sys.argv, os.environ)
    result = None
    if sys.platform == "win32" and not web_mode:
        result = bootstrap_windows_instance(
            autostart_launch=is_autostart_launch(),
            release_channel="stable",
            development=_is_source_checkout(Path(__file__)),
        )
        if result.exit_code is not None:
            return result.exit_code

    def _app_target(page: ft.Page) -> None:
        app_main(
            page,
            activation_broker=result.broker if result else None,
            force_visible=result.force_visible if result else False,
        )

    try:
        run_kwargs["main"] = _app_target
        run_kwargs.pop("target", None)
        ft.run(**run_kwargs)
        return 0
    finally:
        if result and result.broker:
            result.broker.close(timeout_seconds=1.0)
```

- [ ] Change both executable guards to `raise SystemExit(main())`. Preserve the root wrapper as a thin import; do not duplicate bootstrap policy there.
- [ ] Add failing startup tests proving `force_visible=True` makes startup visible even when `autostart_launch=True`, `START_MINIMIZED=True`, and tray setup succeeds.
- [ ] Construct `WindowActivator` at the start of `AppController._init_window()`. Pass `self.window_activator.request_show` into `TrayApp`; bind the broker callback only after the page, tray state, and initial visibility are established. If `force_visible` is true, skip `should_hide_startup_window()` and schedule SHOW once.
- [ ] Add failing tray tests proving `TrayApp.show_app()` invokes the injected callback exactly once and performs no direct page/window mutation. Preserve a safe no-op when preview code supplies no callback.
- [ ] Delete `_show_app_async()` and all duplicate reveal logic from `TrayApp`. Keep tray thread shutdown finite and independent from broker shutdown.
- [ ] In `AppController._on_disconnect`, close the broker with a 1.0-second bound before releasing page-owned resources. The outer `finally` remains an idempotent safety net.
- [ ] Run:

```powershell
$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'
python -m pytest tests/test_main_single_instance.py tests/test_startup_visibility.py tests/test_autostart_and_tray.py tests/test_window_activator.py -q
ruff check src/main.py main.py src/gui/compact_desktop.py src/gui/app_controller.py src/gui/tray.py tests/test_main_single_instance.py tests/test_startup_visibility.py tests/test_autostart_and_tray.py
```

- [ ] Commit the implementation task:

```powershell
git add src/main.py main.py src/gui/compact_desktop.py src/gui/app_controller.py src/gui/tray.py tests/test_main_single_instance.py tests/test_startup_visibility.py tests/test_autostart_and_tray.py
git commit -m "feat: reveal existing app on explicit windows launch"
```

### Task 4: Introduce the complete normalized Settings snapshot and typed defaults

**Files:**

- Create: `src/gui/view_models/__init__.py`
- Create: `src/gui/view_models/settings_form.py`
- Modify: `src/config.py`
- Create: `tests/test_settings_form_snapshot.py`
- Modify: `tests/test_config_extended.py`

**Interfaces:**

- `SettingsFormSnapshot.from_settings(value: SettingsLike) -> SettingsFormSnapshot` reads persisted/global state without importing `config.settings`.
- `SettingsFormSnapshot.from_form_values(values: Mapping[str, object]) -> SettingsFormSnapshot` parses controls and returns canonical immutable state or raises `SettingsFormValidationError` with field-safe messages.
- `SettingsFormSnapshot.to_settings_values() -> dict[str, object]` returns an explicit uppercase-field mapping suitable for assignment to `config.settings`.
- Equality between snapshots is the sole dirty-check primitive.

- [ ] Add failing config tests for `AUTO_UPDATE_ENABLED is True`, `CRASH_REPORTING_CONSENT == "not_asked"`, JSON round-trip, acceptance of all three consent values, and rejection of any other consent value.
- [ ] In `src/config.py`, import `Literal` and add exactly:

```python
AUTO_UPDATE_ENABLED: bool = Field(
    default=True,
    description="Tự động kiểm tra cập nhật",
)
CRASH_REPORTING_CONSENT: Literal["not_asked", "enabled", "disabled"] = Field(
    default="not_asked",
    description="Quyết định gửi chẩn đoán sự cố của người dùng",
)
```

- [ ] Add a failing field-coverage test whose expected set is explicit. `SettingsFormSnapshot` must contain every editable control, including fields that current `_save()` omits: Gmail address/app password, Discord webhook, Telegram enabled/token/chat ID, and debug mode.

```python
EXPECTED_FORM_FIELDS = {
    "theme", "color_critical", "color_warning", "color_safe", "color_quiz",
    "color_assignment", "color_attendance", "color_open", "color_other",
    "uth_username", "uth_password", "always_on_top", "include_submitted",
    "include_graded", "start_with_windows", "start_minimized", "minimize_to_tray",
    "auto_update_enabled", "crash_reporting_consent", "background_check_android",
    "enable_gmail", "gmail_address", "gmail_app_password", "enable_discord",
    "discord_webhook_url", "enable_telegram", "telegram_bot_token",
    "telegram_chat_id", "debug_mode", "check_interval_minutes", "fetch_months",
    "urgency_critical_hours", "urgency_warning_hours", "opening_soon_hours",
    "prefetch_workers", "notify_dnd_enable", "notify_dnd_start", "notify_dnd_end",
    "notify_ignore_submitted", "notification_profile", "notify_types",
    "notify_milestones_minutes", "notify_muted_courses",
}
assert {field.name for field in dataclasses.fields(SettingsFormSnapshot)} == EXPECTED_FORM_FIELDS
```

- [ ] Implement the frozen slotted dataclass. Mark `uth_password`, `gmail_app_password`, `discord_webhook_url`, and `telegram_bot_token` with `field(repr=False)`. Use the consent `Literal` in both the field and parser return types.
- [ ] Centralize normalization in named pure helpers: `_parse_int(name, value, default, minimum, maximum)`, `_normalize_color(value, default)`, `_normalize_csv_strings(value)`, `_normalize_notify_types(value)`, and `_normalize_milestones(value)`. Use current UI bounds: fetch months 1–3, workers 1–10, DND hours 0–23, urgency hours minimum 1, check interval minimum 0, positive unique milestones sorted descending, and trimmed unique muted courses sorted case-insensitively.
- [ ] Add table-driven tests for equivalent representations comparing equal: numeric strings versus integers, lowercase versus uppercase hex colors, duplicate/order-varied set-like controls, blank values versus documented defaults. Add invalid numeric and invalid consent tests with assertions that no secret appears in exception text.
- [ ] Implement explicit `from_settings()`, `from_form_values()`, and `to_settings_values()` mappings. Do not use `vars()`, `__dict__`, or name-case conversion because UI names and config names are intentionally different.
- [ ] Run `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_settings_form_snapshot.py tests/test_config_extended.py -q` and `ruff check src/gui/view_models src/config.py tests/test_settings_form_snapshot.py tests/test_config_extended.py`.
- [ ] Commit the implementation task:

```powershell
git add src/gui/view_models/__init__.py src/gui/view_models/settings_form.py src/config.py tests/test_settings_form_snapshot.py tests/test_config_extended.py
git commit -m "feat: add deterministic settings form snapshot"
```

### Task 5: Render automatic-update and crash-consent settings and map every control

**Files:**

- Modify: `src/gui/components/settings/system_section.py`
- Create: `src/gui/components/settings/privacy_section.py`
- Create: `src/gui/components/crash_consent_dialog.py`
- Modify: `src/gui/components/settings_view.py`
- Modify: `src/gui/app_controller.py`
- Modify: `tests/test_settings_view_state.py`
- Create: `tests/test_crash_consent_dialog.py`

**Interfaces:**

- `init_system_controls(view) -> None` creates `_sw_auto_update` with default `True`; `build_system_section(view) -> ft.Container` renders it on desktop and mobile.
- `init_privacy_controls(view) -> None` creates `_dd_crash_reporting_consent`; `build_privacy_section(view) -> ft.Container` renders exact values `not_asked`, `enabled`, and `disabled` and never collapses `not_asked` into `False`.
- `SettingsView._capture_form_snapshot() -> SettingsFormSnapshot` is the only control-to-model mapping.
- `SettingsView._apply_snapshot_to_controls(snapshot: SettingsFormSnapshot) -> None` is the only model-to-control mapping.
- `SettingsView._persist_snapshot_to_settings(snapshot: SettingsFormSnapshot) -> bool` assigns every `to_settings_values()` key and delegates to `save_settings()`.
- `CrashConsentDialog(page, on_decision: Callable[[Literal["enabled", "disabled"]], bool])` exposes `present_if_needed(current_consent) -> bool`; it presents at most once per app process and returns whether it opened.
- `AppController._persist_crash_consent(decision: Literal["enabled", "disabled"]) -> bool` persists either explicit decision transactionally and restores the previous in-memory value on failure.

- [ ] Add a failing Settings view test that installs sentinel values for every field, applies the snapshot, captures it back, and asserts exact snapshot equality. Include non-default values for auto update, all three consent states (parameterized), debug mode, and every integration credential.
- [ ] Add failing dialog tests: `not_asked` opens a modal with distinct Enable and Decline actions; `enabled` and `disabled` do not open it; dismissing with the window close control calls no decision callback and leaves `not_asked`; each action calls its exact literal once; the same dialog instance never prompts twice in one process.
- [ ] Add failing AppController persistence tests for both decisions. Assert `save_settings()` succeeds before the setting is treated as decided; a persistence failure restores `not_asked` and reports a local UI error. Patch diagnostics transport construction/network calls to raise and assert neither showing nor dismissing the prompt touches them.
- [ ] Add `_sw_auto_update` to the desktop/mobile-safe system settings group with label “Tự động kiểm tra cập nhật” and default `True`.
- [ ] Create `privacy_section.py` with a Flet dropdown labelled “Gửi chẩn đoán sự cố” and exact option keys `not_asked`, `enabled`, `disabled`. User-visible labels may be Vietnamese, but the stored values may not be translated.
- [ ] Wire the privacy section into `_init_controls()`, `_init_layout()`, theme refresh, and test fixtures. Keep the control visible on every platform because consent is cross-platform state even if reporting is unavailable on that platform.
- [ ] Implement `CrashConsentDialog` as an explicit first-run prompt. Enable maps only to `"enabled"`; Decline maps only to `"disabled"`; closing the modal without choosing maps to no value and permits deferral. The component must not import diagnostics transport, initialize an SDK, inspect a spool, or perform network I/O.
- [ ] Compose and call `present_if_needed(settings.CRASH_REPORTING_CONSENT)` once after `AppController` has attached the page and completed initial UI construction. On a persisted choice, update the Settings dropdown/baseline on its next transactional load; do not silently change an already-open form.
- [ ] Treat automatic update as check-only state in this plan. The switch label/description must not promise unattended installation; the update coordinator plan must require explicit confirmation before install/restart.
- [ ] Implement the three explicit mapping methods. The capture method passes raw control values to `SettingsFormSnapshot.from_form_values()`; the apply method sets every control plus derived summaries/chips/dependent-enabled states; the persist method assigns every uppercase key, then returns `save_settings()`.

```python
def _persist_snapshot_to_settings(self, snapshot: SettingsFormSnapshot) -> bool:
    previous = SettingsFormSnapshot.from_settings(settings)
    for name, value in snapshot.to_settings_values().items():
        setattr(settings, name, value)
    if save_settings():
        return True
    for name, value in previous.to_settings_values().items():
        setattr(settings, name, value)
    return False


def has_changes(self) -> bool:
    if self._loading or self._baseline_snapshot is None:
        return False
    return self._capture_form_snapshot() != self._baseline_snapshot
```

- [ ] Change `_write_secret(key: str, value: str) -> bool`: write non-empty values, delete the existing key for an empty value, treat keyring's “credential not found” delete result as success, and return `False` on every other backend exception. This ensures clearing an integration credential is a real persisted operation.
- [ ] Make `save_settings() -> bool` accurately combine JSON, secure-store, and legacy-cleanup writes: a required JSON, secret write/delete, or legacy cleanup failure returns `False`; successful persistence returns `True`. Update existing call sites that ignore the return without changing their behavior.
- [ ] Add tests that force JSON failure and one secure-secret write failure, asserting `False`, and a complete success asserting `True`. Patch backends; never write actual credentials.
- [ ] Run `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_settings_form_snapshot.py tests/test_settings_view_state.py tests/test_crash_consent_dialog.py tests/test_config_extended.py -q` and focused Ruff.
- [ ] Commit the implementation task:

```powershell
git add src/gui/components/settings/system_section.py src/gui/components/settings/privacy_section.py src/gui/components/crash_consent_dialog.py src/gui/components/settings_view.py src/gui/app_controller.py src/config.py tests/test_settings_view_state.py tests/test_crash_consent_dialog.py tests/test_config_extended.py
git commit -m "feat: expose update and privacy settings deterministically"
```

### Task 6: Make Settings initialization transactional and generation-safe

**Files:**

- Modify: `src/gui/components/settings_view.py`
- Modify: `src/gui/view_manager.py`
- Modify: `src/gui/app_controller.py`
- Modify: `tests/test_settings_view_state.py`
- Modify: `tests/test_settings_autostart_ui.py`

**Interfaces:**

- `SettingsView.load_current_settings() -> Awaitable[None]` owns one generation transaction.
- `SettingsView.cancel_pending_load() -> None` invalidates the active generation without waiting forever.
- `ViewManager.show_settings() -> Awaitable[None]` awaits initialization before swapping view visibility.
- `SettingsView._load_autostart_state(generation: int) -> AutostartUiState | None` is bounded and returns `None` when stale.

- [ ] Add failing async tests for the required order: increment generation; set loading; snapshot persisted settings; await real autostart state; apply all controls; capture baseline; clear loading. Assert `has_changes()` is false throughout initialization and after it.
- [ ] Add a two-generation test with controllable futures. Complete generation 2 first, then generation 1; assert the late generation 1 result changes neither control state, baseline, global settings, nor status.
- [ ] Add tests for confirmed and unconfirmed autostart reads. Confirmed OS state replaces the snapshot's `start_with_windows`; an unconfirmed read preserves the persisted value, disables editing, and reports a warning without making the form dirty.
- [ ] Replace the sync loader and scheduled `_reconcile_autostart()` with one awaited transaction:

```python
async def load_current_settings(self) -> None:
    self._load_generation += 1
    generation = self._load_generation
    self._loading = True
    persisted = SettingsFormSnapshot.from_settings(settings)
    try:
        autostart = await asyncio.wait_for(
            self._autostart_coordinator.load(), timeout=2.0
        ) if self._autostart_coordinator is not None else None
        if generation != self._load_generation:
            return
        resolved = persisted
        if autostart is not None and autostart.confirmed:
            resolved = replace(persisted, start_with_windows=autostart.enabled)
        self._apply_snapshot_to_controls(resolved)
        self._baseline_snapshot = self._capture_form_snapshot()
        self._apply_autostart_ui(autostart)
    finally:
        if generation == self._load_generation:
            self._loading = False
```

- [ ] Catch `asyncio.TimeoutError` as an unconfirmed autostart state with a user-visible retry message; do not mutate `settings.START_WITH_WINDOWS` during load. Catch no broader exception without logging a sanitized diagnostic.
- [ ] Change `ViewManager.show_settings()` to async and await `settings_view.load_current_settings()` before making Settings visible. Change `AppController._show_settings()` to await it. Remove any `page.run_task(self._reconcile_autostart)` path.
- [ ] Define close-during-load behavior: `cancel_pending_load()` increments generation, clears loading, and close proceeds immediately without a dirty prompt. The abandoned coroutine may finish later but fails its generation check.
- [ ] Run:

```powershell
$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'
python -m pytest tests/test_settings_view_state.py tests/test_settings_autostart_ui.py -q
ruff check src/gui/components/settings_view.py src/gui/view_manager.py src/gui/app_controller.py tests/test_settings_view_state.py tests/test_settings_autostart_ui.py
```

- [ ] Commit the implementation task:

```powershell
git add src/gui/components/settings_view.py src/gui/view_manager.py src/gui/app_controller.py tests/test_settings_view_state.py tests/test_settings_autostart_ui.py
git commit -m "fix: make settings initialization transactional"
```

### Task 7: Make save, partial autostart failure, rebaseline, and discard deterministic

**Files:**

- Modify: `src/gui/components/settings_view.py`
- Modify: `tests/test_settings_view_state.py`
- Modify: `tests/test_settings_autostart_ui.py`

**Interfaces:**

- `SettingsView._save(event) -> Awaitable[bool]` returns `True` only when all requested settings, including autostart, reached their requested state and independent persistence succeeded.
- `SettingsView._discard_and_close() -> None` restores the entire baseline, reapplies theme preview, then invokes the close callback.
- A failed autostart mutation does not prevent unrelated valid settings from being persisted and rebaselined.

- [ ] Add failing tests for a successful save: edit every field, save, assert every config value, assert `save_settings()` once, assert the new baseline equals the normalized form, and assert `has_changes()` is false without reopening Settings.
- [ ] Add a failing partial-failure test: change theme and autostart; make the OS reject autostart; assert theme persists, `start_with_windows` returns to verified actual state, the baseline matches the now-visible form, a warning remains, `_save()` returns `False`, and a subsequent Back does not show a dirty prompt.
- [ ] Add failing persistence-failure tests: `save_settings() == False` leaves the previous baseline intact, reports failure, returns `False`, and keeps the view open.
- [ ] Add a failing discard test that mutates every control and theme preview, invokes discard, and asserts all controls equal the baseline before close. Assert passwords/tokens are restored but never present in log capture.
- [ ] Refactor `_save()` into this fixed sequence:

  1. Capture and validate a normalized draft without mutating globals.
  2. If desktop autostart differs from baseline, await `asyncio.wait_for(self._autostart_coordinator.change(requested.start_with_windows), timeout=2.0)`.
  3. Replace only `draft.start_with_windows` with the confirmed actual state when mutation fails or times out.
  4. Persist the resulting draft, including every unrelated valid field.
  5. On persistence success, apply the persisted snapshot to controls, set it as `_baseline_snapshot`, set `_original_theme`, update always-on-top, and invoke `_on_saved` once.
  6. Return `False` with the autostart warning if the requested OS mutation failed; otherwise return `True`.

```python
requested = self._capture_form_snapshot()
persisted = requested
autostart_ok = True
if not _pu.IS_MOBILE and requested.start_with_windows != self._baseline_snapshot.start_with_windows:
    result = await asyncio.wait_for(
        self._autostart_coordinator.change(requested.start_with_windows),
        timeout=2.0,
    )
    autostart_ok = result.confirmed and result.enabled == requested.start_with_windows
    if not autostart_ok:
        actual = result.enabled if result.confirmed else self._baseline_snapshot.start_with_windows
        persisted = replace(requested, start_with_windows=actual)

if not self._persist_snapshot_to_settings(persisted):
    return False
self._apply_snapshot_to_controls(persisted)
self._baseline_snapshot = persisted
return autostart_ok
```

- [ ] Replace the inline discard closure with `_discard_and_close()`. It must call `_apply_snapshot_to_controls(self._baseline_snapshot)`, apply the baseline theme to the page, refresh summaries/dependencies, clear the dirty indicator, then close.
- [ ] Remove all direct dirty comparisons with `config.settings`, all direct control-to-global assignments from `_save()`, and the old `_apply_autostart_change()` path.
- [ ] Run `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_settings_view_state.py tests/test_settings_autostart_ui.py tests/test_settings_notification_ui.py tests/test_config_extended.py -q` and focused Ruff.
- [ ] Commit the implementation task:

```powershell
git add src/gui/components/settings_view.py tests/test_settings_view_state.py tests/test_settings_autostart_ui.py
git commit -m "fix: rebaseline settings after verified persistence"
```

### Task 8: Verify real Win32 behavior and packaged multi-launch scenarios with bounded tests

**Files:**

- Create: `tests/test_windows_single_instance_integration.py`
- Create: `scripts/test_windows_single_instance_e2e.ps1`
- Modify: `scripts/test_windows_bundle_e2e.ps1`
- Modify: `tests/test_release_hardening.py`

**Interfaces:**

- The pytest integration test creates real named objects under a randomized application identity/channel and closes every handle in `finally`.
- The PowerShell script accepts `-ExePath`, `-StartupAliasPath`, `-WorkingDirectory`, `-ProcessExitTimeoutSeconds` (default 5), and `-WindowTimeoutSeconds` (default 10); it returns non-zero on any missed deadline.
- The existing bundle E2E invokes the focused script and rejects `single_instance_fail_open` in captured packaged logs.

- [ ] Add a Windows-only integration test marked `windows_integration`. Start a primary broker and secondary bootstrap in the same test process using separate adapters; assert callback delivery, autostart silence, acknowledgement, close, and immediate namespace reuse. Use `uuid.uuid4().hex` in the app identity to avoid interacting with an installed UTHelper.
- [ ] Add a real ACL assertion that opens the security descriptor and verifies allowed SIDs are exactly the current user and `S-1-5-18` (`SYSTEM`), with no Everyone or Authenticated Users ACE.
- [ ] Run `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_windows_single_instance_integration.py -m windows_integration -q` on Windows. Skip with an explicit reason only when pywin32 is unavailable.
- [ ] Create the PowerShell E2E with `$ErrorActionPreference = 'Stop'`, a unique isolated `%APPDATA%`/`%LOCALAPPDATA%` test root, and a `Wait-Until` helper based on `Stopwatch` and `Start-Sleep -Milliseconds 100`. Every process wait uses `WaitForExit(timeoutMilliseconds)`.
- [ ] Implement these exact packaged scenarios, always recording created PIDs before cleanup:

  1. Start primary through the argument-free Startup alias with start-minimized enabled; wait at most 10 seconds for ready/hidden evidence.
  2. Launch `UTHelper.exe` manually; require secondary exit `0` within 5 seconds, same primary PID, and primary window visible/non-minimized within 10 seconds.
  3. Hide the primary again, launch the Startup alias; require exit `0` within 5 seconds and primary remains hidden for a 2-second observation window.
  4. Hide the primary, launch four manual secondaries concurrently; require all exit `0` within 5 seconds and exactly the original primary becomes visible.
  5. Terminate only the recorded primary PID, wait at most 5 seconds for exit, then launch manually; require a new primary PID and visible window within 10 seconds.

- [ ] Put cleanup in `finally`; terminate only process objects created by the script, wait 3 seconds, and use `Stop-Process -Id $ownedProcess.Id -Force` only for an owned process that missed graceful exit. Delete only the resolved unique test profile directory after verifying it is under `[IO.Path]::GetTempPath()`.
- [ ] Before changing the bundle script, add a failing `test_bundle_e2e_requires_activation_handoff_and_fail_open_scan()` to `tests/test_release_hardening.py`. It must assert that `scripts/test_windows_bundle_e2e.ps1` invokes `test_windows_single_instance_e2e.ps1` and fails when captured logs contain `single_instance_fail_open`.
- [ ] Invoke the new script from `scripts/test_windows_bundle_e2e.ps1` after existing packaging checks, passing its already-resolved executable and alias. Do not start an unbounded child or use bare `Wait-Process`.
- [ ] Update `tests/test_release_hardening.py` assertions only after observing the new test fail. Keep static bundle-integrity verification separate from runtime activation smoke behavior.
- [ ] Run:

```powershell
$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'
python -m pytest tests/test_windows_single_instance.py tests/test_windows_single_instance_integration.py tests/test_release_hardening.py -q
ruff check src tests
```

- [ ] On a packaged candidate, run with explicit bounds:

```powershell
$bundleDirectory = (Resolve-Path 'build/windows/x64/runner/Release').Path
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/test_windows_bundle_e2e.ps1 -BundleDir $bundleDirectory -ObservationSeconds 8
```

- [ ] Commit the implementation task:

```powershell
git add tests/test_windows_single_instance_integration.py scripts/test_windows_single_instance_e2e.ps1 scripts/test_windows_bundle_e2e.ps1 tests/test_release_hardening.py
git commit -m "test: verify packaged windows activation handoff"
```

### Task 9: Run regression gates and document the invariants

**Files:**

- Modify: `REFAC_KNOWLEDGE.md`

**Interfaces:**

- Documentation records ownership, callback direction, timeout values, Settings baseline rules, and commands maintainers can reproduce.
- No source interface changes are introduced in this task.

- [ ] Run the complete unit suite with an outer timeout so a regression cannot hang the terminal:

```powershell
$job = Start-Job { Set-Location 'E:\Projects\UTH-Elearning-Alert-worktrees\reliability-auto-update'; $env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests -q --tb=short }
if (-not (Wait-Job $job -Timeout 600)) { Stop-Job $job; throw 'pytest exceeded 600 seconds' }
Receive-Job $job
Remove-Job $job
```

- [ ] Run `ruff check src tests` and the Windows integration marker separately. Record exact pass/skip counts in the implementation handoff; do not describe unrun packaged tests as passed.
- [ ] Manually verify one development launch and one installed-build launch. Confirm manual shortcut/Start menu/EXE activation reveals the existing window; autostart secondary remains silent; Settings opens cleanly twice; changing then discarding every section restores all fields.
- [ ] Update `REFAC_KNOWLEDGE.md` with a concise “Windows activation and Settings state” section: bootstrap before Flet, private named-object identity/DACL, receiver-to-`WindowActivator` direction, all timeout constants, fail-open behavior, immutable snapshot membership, load generation rule, save/rebaseline rule, and partial autostart failure behavior.
- [ ] Review the diff for architecture leaks and accidental secret logging:

```powershell
git diff --check
rg -n "password|bot_token|webhook|user_sid" src/platform_utils/single_instance.py src/gui/view_models/settings_form.py tests/test_windows_single_instance.py
rg -n "while True|\.wait\(\)" src/platform_utils/single_instance.py scripts/test_windows_single_instance_e2e.ps1
rg -n "Wait-Process" scripts/test_windows_single_instance_e2e.ps1
```

- [ ] Run the full regression command again after documentation and any review fixes.
- [ ] Commit the documentation task:

```powershell
git add REFAC_KNOWLEDGE.md
git commit -m "docs: record activation and settings invariants"
```

## Final Review Checklist

- [ ] Spec coverage: manual second launch, shortcut, Start menu, explicit EXE, autostart silence, simultaneous secondaries, stale-owner recovery, tray Open, and start-minimized override all have focused tests.
- [ ] Security coverage: object names reveal no source input; ACL grants current user and SYSTEM only; no raw Win32/object/security data reaches logs.
- [ ] Lifetime coverage: bootstrap precedes Flet; acknowledgement follows handler binding; receiver has finite waits; broker/tray/process teardown is bounded and idempotent.
- [ ] Settings coverage: the explicit field-set test includes every editable control, especially `AUTO_UPDATE_ENABLED`, tri-state `CRASH_REPORTING_CONSENT`, debug mode, and all integration credentials.
- [ ] Consent coverage: first-run `not_asked` presents distinct Enable/Decline choices; dismiss defers without changing state; both decisions persist; no diagnostics SDK, spool flush, or network path runs before explicit Enable.
- [ ] State coverage: every successful load/save captures a normalized baseline; stale generations cannot commit; discard restores the whole baseline; autostart failure does not discard unrelated settings.
- [ ] Type consistency: consent uses the same three literal values in config, snapshot, dropdown, persistence, and tests; tuple/list conversions occur only at explicit snapshot/config boundaries.
- [ ] Command safety: every job, process, receiver wait, acknowledgement, Settings reconcile, and teardown has a numeric timeout; cleanup targets only owned handles/PIDs/directories.
- [ ] Quality gates: focused tests, full `pytest`, Ruff, `git diff --check`, real Win32 integration, packaged E2E, and post-integration rerun are reported separately with exact outcomes.
