# Private Crash Diagnostics Implementation Plan

> **Archived:** Planning snapshot retained for provenance. It is not the current
> task tracker; use tests and current operator documentation as the source of truth.

**Goal:** Capture silent and uncaught UTHelper failures locally and, only after explicit consent, deliver a strictly allow-listed anonymous diagnostic report without uploading raw logs or Moodle/user data.

**Architecture:** A diagnostics package owns typed reports, redaction, bounded logging, an atomic offline spool, runtime exception hooks, run-state markers, and a narrow Sentry transport. `main.py` initializes this package before GUI imports and records only scrubbed event objects; a deterministic build patch adds Flutter error breadcrumbs without pretending to catch every native crash.

**Tech Stack:** Python 3.11+, Pydantic/dataclasses, standard-library logging/faulthandler/threading/asyncio/urllib, Sentry SDK with default integrations disabled, Flet 0.86.5, pytest 9, PowerShell/GitHub Actions.

## Global Constraints

- Crash reporting stays disabled while `CRASH_REPORTING_CONSENT == "not_asked"` or `"disabled"`; no diagnostic network request occurs before explicit opt-in.
- Scrub and validate reports before disk persistence; never spool raw exceptions, logs, local variables, environment variables, HTTP data, Moodle content, stable device IDs, screenshots, replays, or minidumps.
- Keep at most 20 queued events, 1 MiB total, and seven days of reports; use atomic same-directory replacement.
- Use no stable installation/client identifier; event UUIDs and content fingerprints are per-event only.
- Network connect/read/total deadlines are finite, and startup/UI rendering never waits for delivery.
- An uncleared run marker means only `unclean_previous_exit`, not a proven crash.
- Every generated Flutter patch is anchor/version checked and fails the build when the expected runner changes.
- The official Flet 0.86.5 build template URL is `https://github.com/flet-dev/flet/releases/download/v0.86.5/flet-build-template.zip`; its reviewed SHA-256 is `8f95dc20ef6d901d9b5ee59f00e33d19f1d2bc6be8d6d3b800c4aab3d7315b73`.
- Preserve and chain pre-existing Python hooks after local capture; guard against recursive hook failure.
- Native minidump upload and an out-of-process hang watchdog are outside this implementation.

---

## File structure

- `src/diagnostics/models.py`: immutable allow-listed diagnostic schema and context enums.
- `src/diagnostics/redaction.py`: path/message/frame normalization and final allow-list validation.
- `src/diagnostics/logging_setup.py`: idempotent rotating logging and pre-persistence redaction filter.
- `src/diagnostics/spool.py`: atomic bounded offline queue with deduplication and retention.
- `src/diagnostics/transport.py`: consent-gated Sentry adapter and retry outcome model.
- `src/diagnostics/release_config.py`: validate and load the public ingestion DSN from a packaged asset.
- `src/diagnostics/runtime.py`: Python hooks, Flet error capture, faulthandler, run marker, and lifecycle.
- `src/diagnostics/windows_evidence.py`: narrow recent Windows Application Error metadata lookup.
- `src/diagnostics/__init__.py`: narrow construction exports only.
- `src/config.py`: tri-state consent field and schema migration.
- `src/main.py`: one diagnostics bootstrap/lifecycle boundary replacing duplicate logging setup.
- `scripts/prepare_flet_diagnostics_template.py`: verify and patch the immutable Flet template before compilation.
- `scripts/generate_public_runtime_config.py`: write a deterministic non-secret diagnostics asset before release builds.
- `scripts/verify_flutter_diagnostics.py`: release/build verification of the template and generated source contract.
- `tests/test_diagnostic_redaction.py`: privacy invariants and secret fuzz corpus.
- `tests/test_diagnostic_logging.py`: rotation, handler idempotence, and redaction-before-write.
- `tests/test_diagnostic_spool.py`: queue atomicity, caps, dedupe, retention, and corruption recovery.
- `tests/test_diagnostic_transport.py`: consent, Sentry event filtering, timeout/backoff outcomes.
- `tests/test_diagnostic_release_config.py`: packaged asset, DSN validation, missing-config, and generator behavior.
- `tests/test_diagnostic_runtime.py`: hook chaining, run-state, page/async capture, and recursion safety.
- `tests/test_diagnostic_subprocess.py`: real uncaught thread/process/abort evidence with deadlines.
- `tests/test_windows_crash_evidence.py`: matching, time window, basename-only output, and unavailable API behavior.
- `tests/test_flutter_diagnostics_patch.py`: known Flet template patch, deterministic ZIP, and anchor drift failure.
- `tests/fixtures/flutter_template/main.dart`: reviewed Flet 0.86.5 entry fixture.
- `tests/fixtures/flutter_template/main.patched.dart`: expected pre-compile patched fixture.
- `pyproject.toml`: pinned-compatible Sentry SDK dependency and diagnostics package inclusion.
- `.github/workflows/ci.yml`: diagnostics tests and generated-runner contract check.
- `docs/PRIVACY.md`: exact collected/forbidden fields, consent, retention, revocation, and IP caveat.
- `REFAC_KNOWLEDGE.md`: diagnostics boundary and privacy invariants.

### Task 1: Define the allow-listed report schema

**Files:**
- Create: `src/diagnostics/__init__.py`
- Create: `src/diagnostics/models.py`
- Test: `tests/test_diagnostic_redaction.py`

**Interfaces:**
- Consumes: `core.version.APP_VERSION` as a non-secret release string.
- Produces: `CrashConsent`, `AppPhase`, `DiagnosticFrame`, `DiagnosticReport`, `DiagnosticContext`, and `SCHEMA_VERSION = 1`.

- [ ] **Step 1: Write the failing schema tests**

```python
def test_report_rejects_unknown_or_forbidden_fields():
    with pytest.raises(ValidationError):
        DiagnosticReport.model_validate({
            **valid_report_dict(),
            "username": "student123",
        })


def test_consent_is_tri_state():
    assert {item.value for item in CrashConsent} == {
        "not_asked", "enabled", "disabled"
    }
```

- [ ] **Step 2: Run the tests and verify the missing package failure**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_redaction.py -v --timeout=20`

Expected: collection fails because `diagnostics.models` does not exist.

- [ ] **Step 3: Implement immutable strict models**

```python
SCHEMA_VERSION = 1


class CrashConsent(str, Enum):
    NOT_ASKED = "not_asked"
    ENABLED = "enabled"
    DISABLED = "disabled"


class AppPhase(str, Enum):
    BOOT = "boot"
    GUI = "gui"
    BACKGROUND_SYNC = "background_sync"
    UPDATE = "update"
    SHUTDOWN = "shutdown"


class DiagnosticFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=240)
    module: str
    function: str
    relative_path: str
    line: int = Field(ge=0, le=10_000_000)


class DiagnosticContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    source_root: Path = Field(exclude=True)
    app_version: str
    release_channel: str
    install_type: str
    os_family: str
    os_version: str
    architecture: str
    python_version: str
    flet_version: str
    flutter_version: str | None = None
    phase: AppPhase
    window_state: Literal["foreground", "tray", "unknown"]
    unclean_previous_exit: bool = False


class DiagnosticReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=512)
    schema_version: Literal[1]
    event_id: UUID
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    occurred_at: datetime
    app_version: str
    release_channel: str
    install_type: str
    os_family: str
    os_version: str
    architecture: str
    python_version: str
    flet_version: str
    flutter_version: str | None = None
    exception_type: str
    frames: tuple[DiagnosticFrame, ...] = Field(max_length=40)
    phase: AppPhase
    window_state: Literal["foreground", "tray", "unknown"]
    unclean_previous_exit: bool
```

- [ ] **Step 4: Run the schema tests**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_redaction.py -v --timeout=20`

Expected: schema tests pass.

- [ ] **Step 5: Commit the report contract**

```powershell
git add src/diagnostics tests/test_diagnostic_redaction.py
git commit -m "feat: define private diagnostic report schema"
```

### Task 2: Scrub exceptions before persistence

**Files:**
- Create: `src/diagnostics/redaction.py`
- Modify: `tests/test_diagnostic_redaction.py`

**Interfaces:**
- Consumes: `DiagnosticContext` and `BaseException`.
- Produces: `build_report(exc: BaseException, context: DiagnosticContext, *, occurred_at: datetime | None = None) -> DiagnosticReport` and `sanitize_log_text(value: object) -> str`.

- [ ] **Step 1: Add a parameterized secret corpus and path test**

```python
@pytest.mark.parametrize("secret", [
    "student@ut.edu.vn",
    "sesskey=0123456789abcdef",
    "MoodleSession=abc123",
    "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
    "https://courses.ut.edu.vn/mod/assign/view.php?id=123&token=secret",
    r"C:\\Users\\Alice\\Documents\\Bai tap lon.pdf",
    "Advanced Calculus Final Assignment",
])
def test_report_bytes_never_contain_secret(secret, diagnostic_context):
    report = build_report(RuntimeError(f"failed: {secret}"), diagnostic_context)
    assert secret not in report.model_dump_json()


def test_unknown_module_path_is_reduced_to_basename(diagnostic_context):
    report = build_report(RuntimeError("boom"), diagnostic_context)
    assert all("Users" not in frame.relative_path for frame in report.frames)
```

- [ ] **Step 2: Run the privacy tests and verify leaked values fail assertions**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_redaction.py -v --timeout=20`

Expected: failures show raw secret/path leakage until redaction exists.

- [ ] **Step 3: Implement allow-list construction, bounded strings, and fingerprinting**

```python
_SECRET_PATTERNS = (
    re.compile(r"(?i)(password|token|sesskey|cookie|authorization)\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]+"),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    re.compile(r"https?://\S+"),
    re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+\\\S+"),
)


def sanitize_log_text(value: object) -> str:
    text = str(value).replace("\x00", "")[:4096]
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def build_report(exc, context, *, occurred_at=None):
    frames = tuple(_safe_frames(exc.__traceback__, context.source_root))[-40:]
    exception_type = _safe_identifier(type(exc).__name__)
    fingerprint_source = "|".join(
        [exception_type, context.phase.value]
        + [f"{f.module}:{f.function}:{f.relative_path}:{f.line}" for f in frames[-8:]]
    )
    return DiagnosticReport(
        schema_version=SCHEMA_VERSION,
        event_id=uuid4(),
        fingerprint=sha256(fingerprint_source.encode("utf-8")).hexdigest(),
        occurred_at=occurred_at or datetime.now(timezone.utc),
        exception_type=exception_type,
        frames=frames,
        **context.model_dump(exclude={"source_root"}),
    )
```

The exception message is deliberately absent from `DiagnosticReport`. Regex redaction cannot reliably distinguish arbitrary course names, assignment titles, and filenames from harmless text; only the exception type and normalized application frames leave the machine. `sanitize_log_text()` exists solely for bounded local logging.

- [ ] **Step 4: Run the complete redaction corpus**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_redaction.py -v --timeout=20`

Expected: all values are absent from serialized report bytes; only allowed keys remain.

- [ ] **Step 5: Commit pre-persistence redaction**

```powershell
git add src/diagnostics/redaction.py tests/test_diagnostic_redaction.py
git commit -m "feat: redact diagnostics before persistence"
```

### Task 3: Replace duplicate unbounded logging with one rotating logger

**Files:**
- Create: `src/diagnostics/logging_setup.py`
- Create: `tests/test_diagnostic_logging.py`
- Modify: `src/main.py`

**Interfaces:**
- Consumes: `sanitize_log_text()`.
- Produces: `configure_logging(data_dir: Path, *, debug: bool) -> LoggingRuntime` and `LoggingRuntime.close() -> None`.

- [ ] **Step 1: Write failing rotation, idempotence, and pre-write redaction tests**

```python
def test_logging_is_idempotent_bounded_and_redacted(tmp_path):
    first = configure_logging(tmp_path, debug=True)
    second = configure_logging(tmp_path, debug=True)
    logging.getLogger("test").error("token=secret student@ut.edu.vn")
    for handler in logging.getLogger().handlers:
        handler.flush()
    payload = b"".join(path.read_bytes() for path in (tmp_path / "logs").glob("app.log*"))
    assert b"secret" not in payload
    assert b"student@ut.edu.vn" not in payload
    assert sum(isinstance(h, RotatingFileHandler) for h in logging.getLogger().handlers) == 1
    assert first is second


def test_legacy_oversized_logs_are_removed_before_handler_opens(tmp_path):
    log = tmp_path / "logs" / "app.log"
    log.parent.mkdir()
    log.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    configure_logging(tmp_path, debug=False)
    assert log.stat().st_size < 2 * 1024 * 1024
```

- [ ] **Step 2: Run and verify current duplicate `FileHandler` setup fails**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_logging.py -v --timeout=20`

Expected: `diagnostics.logging_setup` is missing and current `main.py` has no bounded owner.

- [ ] **Step 3: Implement one owned rotating handler and safe legacy cleanup**

```python
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
_OWNER_MARKER = "_uthelper_diagnostic_handler"


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_log_text(record.getMessage())
        record.args = ()
        return True


def configure_logging(data_dir: Path, *, debug: bool) -> LoggingRuntime:
    root = logging.getLogger()
    owned = next((h for h in root.handlers if getattr(h, _OWNER_MARKER, False)), None)
    if owned is not None:
        return getattr(owned, "_uthelper_runtime")
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _remove_oversized_legacy_logs(log_dir, MAX_LOG_BYTES)
    handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT, encoding="utf-8",
    )
    handler.addFilter(RedactingFilter())
    setattr(handler, _OWNER_MARKER, True)
    runtime = LoggingRuntime(handler)
    setattr(handler, "_uthelper_runtime", runtime)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    return runtime
```

Remove both ad-hoc `debug_app.log` and `app.log` setup blocks from `src/main.py`; initialize this owner once after the data directory exists.

- [ ] **Step 4: Run logging tests and a main import smoke test**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_logging.py tests/test_config.py -v --timeout=30`

Expected: one rotating handler, bounded files, no corpus values on disk, imports pass.

- [ ] **Step 5: Commit bounded logging**

```powershell
git add src/main.py src/diagnostics/logging_setup.py tests/test_diagnostic_logging.py
git commit -m "fix: bound and redact application logs"
```

### Task 4: Build the atomic bounded diagnostic spool

**Files:**
- Create: `src/diagnostics/spool.py`
- Create: `tests/test_diagnostic_spool.py`

**Interfaces:**
- Consumes: validated `DiagnosticReport` only.
- Produces: `DiagnosticSpool(root: Path, clock: Callable[[], datetime])`, `enqueue(report) -> EnqueueResult`, `pending() -> tuple[QueuedReport, ...]`, `acknowledge(event_id: UUID) -> None`, and `clear() -> None`.

- [ ] **Step 1: Write failing cap, atomicity, corruption, and dedupe tests**

```python
def test_spool_is_bounded_and_deduplicates_fingerprint(tmp_path, reports):
    spool = DiagnosticSpool(tmp_path)
    assert spool.enqueue(reports[0]).stored
    assert spool.enqueue(reports[0].model_copy(update={"event_id": uuid4()})).deduplicated
    for report in reports[1:30]:
        spool.enqueue(report)
    pending = spool.pending()
    assert len(pending) <= 20
    assert sum(item.path.stat().st_size for item in pending) <= 1024 * 1024


def test_spool_ignores_and_quarantines_invalid_json(tmp_path):
    (tmp_path / "bad.json").write_text('{"username":"secret"}', encoding="utf-8")
    assert DiagnosticSpool(tmp_path).pending() == ()
    assert not (tmp_path / "bad.json").exists()
```

- [ ] **Step 2: Run and verify the missing spool failure**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_spool.py -v --timeout=20`

Expected: collection fails because `DiagnosticSpool` does not exist.

- [ ] **Step 3: Implement atomic writes and deterministic pruning**

```python
MAX_EVENTS = 20
MAX_TOTAL_BYTES = 1024 * 1024
MAX_AGE = timedelta(days=7)


def enqueue(self, report: DiagnosticReport) -> EnqueueResult:
    self._prune()
    if any(item.report.fingerprint == report.fingerprint for item in self.pending()):
        return EnqueueResult(stored=False, deduplicated=True)
    payload = report.model_dump_json().encode("utf-8")
    if len(payload) > MAX_TOTAL_BYTES:
        return EnqueueResult(stored=False, too_large=True)
    temporary = self.root / f".{report.event_id}.tmp"
    final = self.root / f"{report.occurred_at.timestamp():020.6f}-{report.event_id}.json"
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, final)
    self._prune()
    return EnqueueResult(stored=final.exists())
```

Pruning sorts by validated report timestamp/event ID, deletes expired/invalid files, then removes oldest until both caps hold. `clear()` deletes only resolved regular `*.json`/`.*.tmp` children under the exact spool root.

- [ ] **Step 4: Run spool tests including simulated `os.replace` failure**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_spool.py -v --timeout=20`

Expected: caps, dedupe, invalid-file handling, and failure cleanup pass.

- [ ] **Step 5: Commit the local queue**

```powershell
git add src/diagnostics/spool.py tests/test_diagnostic_spool.py
git commit -m "feat: add bounded diagnostic spool"
```

### Task 5: Add tri-state consent migration and consent-gated delivery

**Files:**
- Modify: `src/config.py`
- Modify: `pyproject.toml`
- Create: `src/diagnostics/transport.py`
- Create: `src/diagnostics/release_config.py`
- Create: `scripts/generate_public_runtime_config.py`
- Create: `tests/test_diagnostic_transport.py`
- Create: `tests/test_diagnostic_release_config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: `CrashConsent`, `DiagnosticSpool`, packaged `assets/diagnostics-config.json`; development may explicitly override with `UTH_SENTRY_DSN`.
- Produces: `DeliveryOutcome`, `SentryDiagnosticTransport.send(report) -> DeliveryOutcome`, and `DiagnosticDeliveryWorker.flush_once(consent, deadline_seconds=5.0) -> FlushSummary`.

- [ ] **Step 1: Write failing default/migration and no-network-before-consent tests**

```python
def test_crash_consent_defaults_to_not_asked():
    assert Settings().CRASH_REPORTING_CONSENT == "not_asked"


@pytest.mark.parametrize("consent", ["not_asked", "disabled"])
def test_delivery_never_constructs_sentry_before_opt_in(tmp_path, consent, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "diagnostics.transport._new_sentry_client",
        lambda **_: calls.append(True),
    )
    summary = worker_with_one_report(tmp_path).flush_once(CrashConsent(consent))
    assert summary.skipped_consent
    assert calls == []


def test_packaged_dsn_is_loaded_from_local_asset_not_build_environment(tmp_path):
    asset = tmp_path / "diagnostics-config.json"
    asset.write_text(json.dumps({"schema_version": 1, "sentry_dsn": VALID_DSN}), "utf-8")
    assert load_public_dsn(asset, development=False, environ={}) == VALID_DSN


def test_generator_rejects_management_token_or_non_https_dsn(tmp_path):
    with pytest.raises(PublicConfigError):
        generate_public_config(tmp_path, "http://key@example.invalid/1?auth_token=secret")
```

- [ ] **Step 2: Run and verify the new field/transport are absent**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_config.py tests/test_diagnostic_transport.py -v --timeout=30`

Expected: field, transport, and packaged-config imports fail.

- [ ] **Step 3: Add the validated setting and strict Sentry adapter**

```python
CRASH_REPORTING_CONSENT: Literal["not_asked", "enabled", "disabled"] = Field(
    default="not_asked",
    description="Explicit privacy consent for anonymous crash reports",
)
```

Add `sentry-sdk>=2,<3` to project dependencies and `pytest-timeout>=2.3,<3` to the dev dependency group so every stated test deadline is enforced in a clean environment. `generate_public_runtime_config.py --sentry-dsn "$env:SENTRY_DSN" --output assets/diagnostics-config.json` validates an HTTPS Sentry ingestion DSN, rejects query/fragment/user-password forms and management/auth-token fields, and writes only `schema_version` plus `sentry_dsn` with sorted keys through atomic replacement. `assets/diagnostics-config.json` is generated and git-ignored; every platform release build invokes the generator before Flet packages assets. The GitHub environment variable is named `SENTRY_DSN`; the packaged app reads the asset. `UTH_SENTRY_DSN` is accepted only when an explicit development flag is true.

Initialize Sentry lazily only inside the enabled worker with `default_integrations=False`, `send_default_pii=False`, `max_breadcrumbs=0`, `traces_sample_rate=0.0`, and `before_send=_strict_before_send`. `_strict_before_send` reconstructs a Sentry event from the already validated report and drops `user`, `request`, `contexts.device`, `breadcrumbs`, `modules`, and every unknown top-level key.

```python
def flush_once(self, consent: CrashConsent, deadline_seconds: float = 5.0) -> FlushSummary:
    if consent is not CrashConsent.ENABLED:
        if consent is CrashConsent.DISABLED:
            self.spool.clear()
        return FlushSummary(skipped_consent=True)
    if not self.dsn:
        return FlushSummary(skipped_unconfigured=True)
    deadline = self.monotonic() + min(max(deadline_seconds, 0.1), 5.0)
    return self._send_pending_until(deadline)
```

- [ ] **Step 4: Test success, offline, timeout, 429, 5xx, acknowledgement, and backoff**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_config.py tests/test_diagnostic_transport.py tests/test_diagnostic_release_config.py -v --timeout=30`

Expected: reports are acknowledged only after confirmed send; retryable failures retain them; consent gates make zero SDK/network calls.

- [ ] **Step 5: Commit consent and transport**

```powershell
git add src/config.py pyproject.toml src/diagnostics/transport.py src/diagnostics/release_config.py scripts/generate_public_runtime_config.py tests/test_config.py tests/test_diagnostic_transport.py tests/test_diagnostic_release_config.py .gitignore
git commit -m "feat: deliver consented anonymous diagnostics"
```

### Task 6: Install chain-safe runtime hooks and run-state tracking

**Files:**
- Create: `src/diagnostics/runtime.py`
- Create: `tests/test_diagnostic_runtime.py`
- Modify: `src/main.py`

**Interfaces:**
- Consumes: `build_report()`, `DiagnosticSpool`, `DiagnosticDeliveryWorker`, and `DiagnosticContext` provider.
- Produces: `DiagnosticRuntime.start()`, `record_exception(exc, phase)`, `attach_page(page)`, `mark_phase(phase)`, and `close(clean: bool)`.

- [ ] **Step 1: Write failing hook-chain, recursion, marker, and page tests**

```python
def test_thread_hook_spools_once_and_chains_existing_hook(runtime, monkeypatch):
    chained = Mock()
    monkeypatch.setattr(threading, "excepthook", chained)
    runtime.start()
    args = fake_thread_exception(RuntimeError("boom"))
    threading.excepthook(args)
    assert len(runtime.spool.pending()) == 1
    chained.assert_called_once_with(args)


def test_unclean_marker_is_reported_but_clean_close_removes_it(tmp_path):
    first = make_runtime(tmp_path)
    first.start()
    second = make_runtime(tmp_path)
    second.start()
    assert second.context().unclean_previous_exit is True
    second.close(clean=True)
    assert not (tmp_path / "diagnostics" / "run-state.json").exists()
```

- [ ] **Step 2: Run and verify runtime is missing**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_runtime.py -v --timeout=30`

Expected: collection fails because `DiagnosticRuntime` does not exist.

- [ ] **Step 3: Implement guarded hook installation and atomic lifecycle marker**

```python
def _capture_guarded(self, exc: BaseException, phase: AppPhase) -> None:
    if getattr(self._local, "capturing", False):
        return
    self._local.capturing = True
    try:
        self.spool.enqueue(build_report(exc, self.context(phase)))
    except Exception:
        self.emergency_writer.write("diagnostic capture failed\n")
    finally:
        self._local.capturing = False


def start(self) -> None:
    self._previous = HookSet.capture()
    self._write_run_state(phase=AppPhase.BOOT)
    self._install_sys_thread_unraisable_hooks()
    self._enable_faulthandler_to_fresh_file()
    self.delivery_executor.submit(self.delivery.flush_once, self.consent_provider())


def close(self, clean: bool) -> None:
    self.shutdown_event.set()
    self._restore_hooks_if_owned()
    self._disable_and_close_faulthandler()
    if clean:
        self.run_state_path.unlink(missing_ok=True)
    self.delivery_executor.shutdown(wait=False, cancel_futures=True)
```

`attach_page(page)` wraps/chains the existing `page.on_error` and schedules no network I/O. Async loop integration chains the previous handler and captures only an actual `BaseException`. The run-state JSON contains schema, app version, phase, coarse timestamp, and clean/unclean state only.

- [ ] **Step 4: Replace `main.py` startup catch with runtime lifecycle**

Construct the runtime after data-directory and logging setup, call `runtime.start()` before GUI imports, attach the Flet page at the start of `_app_target`, call `record_exception()` before rendering a safe reference-only crash screen, wrap `ft.run(**run_kwargs)` in `try/except/finally`, and call `close(clean=True)` only on ordinary return or handled application exit. Do not display raw traceback or upload inside the catch path.

- [ ] **Step 5: Run runtime, logging, config, and main smoke tests**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_runtime.py tests/test_diagnostic_logging.py tests/test_config.py -v --timeout=45`

Expected: hook chaining/restore, one-event dedupe, run markers, page errors, and main import pass.

- [ ] **Step 6: Commit runtime integration**

```powershell
git add src/main.py src/diagnostics/runtime.py tests/test_diagnostic_runtime.py
git commit -m "feat: capture uncaught application failures"
```

### Task 7: Prove behavior in bounded subprocesses

**Files:**
- Create: `tests/helpers/diagnostic_crash_child.py`
- Create: `tests/test_diagnostic_subprocess.py`

**Interfaces:**
- Consumes: public `DiagnosticRuntime` construction and spool format.
- Produces: no runtime API; provides black-box evidence for main/thread/async/unraisable/abort exit modes.

- [ ] **Step 1: Add the child modes and failing black-box assertions**

```python
@pytest.mark.parametrize("mode", ["main", "thread", "async", "unraisable"])
def test_uncaught_child_spools_sanitized_report(tmp_path, mode):
    completed = subprocess.run(
        [sys.executable, HELPER, mode, str(tmp_path)],
        text=True, capture_output=True, timeout=10, check=False,
        env={**os.environ, "PYTHONPATH": TEST_PYTHONPATH},
    )
    assert completed.returncode != 0 or mode in {"thread", "unraisable"}
    payload = next((tmp_path / "telemetry" / "pending").glob("*.json")).read_text("utf-8")
    assert "student@ut.edu.vn" not in payload
    assert "sesskey" not in payload.lower()


def test_abort_leaves_unclean_marker_without_claiming_python_exception(tmp_path):
    subprocess.run([sys.executable, HELPER, "abort", str(tmp_path)], timeout=10, check=False)
    assert (tmp_path / "diagnostics" / "run-state.json").exists()
```

- [ ] **Step 2: Run and verify missing child harness failure**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_subprocess.py -v --timeout=60`

Expected: helper or expected spool evidence is missing.

- [ ] **Step 3: Implement deterministic child modes with secrets only in exceptions**

The helper constructs diagnostics under the supplied temporary root with consent disabled, starts it, then triggers exactly one selected failure. `abort` calls `os.abort()` after flushing the run-state file; it is not expected to create a Python exception report.

```python
if mode == "thread":
    thread = threading.Thread(target=lambda: raise_secret_error(), daemon=False)
    thread.start()
    thread.join(timeout=2)
elif mode == "async":
    asyncio.run(raise_secret_error_async())
elif mode == "unraisable":
    trigger_unraisable_del_error()
elif mode == "abort":
    os.abort()
else:
    raise_secret_error()
```

- [ ] **Step 4: Run every subprocess case with test and child deadlines**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_subprocess.py -v --timeout=60`

Expected: all subprocesses terminate within 10 seconds; Python failures spool sanitized reports; abort leaves only an unclean marker/fault evidence.

- [ ] **Step 5: Commit subprocess evidence**

```powershell
git add tests/helpers/diagnostic_crash_child.py tests/test_diagnostic_subprocess.py
git commit -m "test: exercise diagnostic crash boundaries"
```

### Task 8: Correlate narrowly scoped Windows crash evidence

**Files:**
- Create: `src/diagnostics/windows_evidence.py`
- Create: `tests/test_windows_crash_evidence.py`
- Modify: `src/diagnostics/models.py`
- Modify: `src/diagnostics/runtime.py`

**Interfaces:**
- Consumes: an uncleared `RunState` with start/heartbeat timestamps and executable basename.
- Produces: `find_recent_application_error(run_state, *, now, reader) -> WindowsCrashEvidence | None`; evidence contains only `exception_code`, `faulting_module_basename`, and coarse `event_time`.

- [ ] **Step 1: Write failing match, window, and privacy tests**

```python
def test_only_matching_recent_uthelper_event_is_returned(fake_events, run_state):
    evidence = find_recent_application_error(
        run_state,
        now=datetime(2026, 8, 4, 3, tzinfo=timezone.utc),
        reader=lambda *_args, **_kwargs: fake_events,
    )
    assert evidence.exception_code == "0xc0000409"
    assert evidence.faulting_module_basename == "flutter_windows.dll"
    assert "Users" not in evidence.model_dump_json()


def test_unavailable_event_api_and_unrelated_events_return_none(run_state):
    assert find_recent_application_error(
        run_state, now=NOW, reader=lambda *_args, **_kwargs: []
    ) is None
```

- [ ] **Step 2: Run and verify the evidence provider is absent**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_windows_crash_evidence.py -v --timeout=20`

Expected: collection fails because `diagnostics.windows_evidence` does not exist.

- [ ] **Step 3: Implement a narrow provider behind an injected reader**

```python
@dataclass(frozen=True)
class WindowsCrashEvidence:
    exception_code: str
    faulting_module_basename: str
    event_time: datetime


def find_recent_application_error(run_state, *, now, reader):
    if sys.platform != "win32" or now - run_state.last_heartbeat > timedelta(minutes=10):
        return None
    for event in reader(event_id=1000, since=run_state.started_at, limit=50):
        if event.application_basename.casefold() != run_state.executable_basename.casefold():
            continue
        if not (run_state.started_at <= event.time <= now):
            continue
        return WindowsCrashEvidence(
            exception_code=_safe_exception_code(event.exception_code),
            faulting_module_basename=Path(event.faulting_module).name[:128],
            event_time=_coarsen_to_minute(event.time),
        )
    return None
```

The production reader uses `win32evtlog` only on Windows, requests Application Error event ID 1000 in the marker time window, stops after 50 events, and never returns event message text, report ID, application path, machine name, username, process ID, or unrelated application data. Permission/API failure returns `None` after a local sanitized debug log.

- [ ] **Step 4: Attach evidence only to the next sanitized unclean-exit report**

Extend `DiagnosticReport` with optional strict fields `native_exception_code` and `faulting_module`, add them to the Sentry allow-list, and make `DiagnosticRuntime.start()` query only when an old marker exists. Absence of an event leaves the report classified as unclean rather than native crash.

- [ ] **Step 5: Run evidence, redaction, and runtime tests**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_windows_crash_evidence.py tests/test_diagnostic_redaction.py tests/test_diagnostic_runtime.py -v --timeout=40`

Expected: only a recent matching UTHelper event contributes basename/code metadata; unavailable or unrelated events contribute nothing.

- [ ] **Step 6: Commit Windows correlation**

```powershell
git add src/diagnostics/windows_evidence.py src/diagnostics/models.py src/diagnostics/runtime.py tests/test_windows_crash_evidence.py tests/test_diagnostic_redaction.py tests/test_diagnostic_runtime.py
git commit -m "feat: correlate anonymous Windows crash evidence"
```

### Task 9: Patch Flutter error handlers before compilation

**Files:**
- Create: `scripts/prepare_flet_diagnostics_template.py`
- Create: `scripts/verify_flutter_diagnostics.py`
- Create: `tests/test_flutter_diagnostics_patch.py`
- Create: `tests/fixtures/flutter_template/main.dart`
- Create: `tests/fixtures/flutter_template/main.patched.dart`
- Modify: Windows build scripts/workflows identified by `rg -l "flet build windows" .github/workflows scripts`
- Modify: `src/diagnostics/runtime.py`
- Modify: `tests/test_diagnostic_runtime.py`

**Interfaces:**
- Consumes: immutable official `v0.86.5/flet-build-template.zip` with its reviewed SHA-256.
- Produces: `prepare_template(source_zip: Path, output_zip: Path) -> PreparedTemplate`, a deterministic patched template ZIP passed to Flet's supported `--template` option, and CLI exit codes 0 prepared/verified, 2 unknown hash/anchors, 3 invalid output.

- [ ] **Step 1: Add known-input, idempotence, and anchor-drift tests**

```python
def test_patch_matches_reviewed_fixture(tmp_path):
    source_zip = make_official_template_zip(tmp_path, FIXTURE)
    output_zip = tmp_path / "flet-template-diagnostics.zip"
    prepared = prepare_template(source_zip, output_zip)
    assert prepared.official_template_sha256 == EXPECTED_OFFICIAL_SHA256
    assert read_main_dart(output_zip) == EXPECTED.read_text("utf-8")
    assert prepare_template(source_zip, output_zip).output_sha256 == prepared.output_sha256


def test_unknown_runner_fails_closed(tmp_path):
    source_zip = make_official_template_zip(tmp_path, "void main() {}")
    with pytest.raises(UnknownRunnerTemplate):
        prepare_template(source_zip, tmp_path / "patched.zip")
```

- [ ] **Step 2: Run and verify patcher is absent**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_flutter_diagnostics_patch.py -v --timeout=20`

Expected: import failure for `scripts.prepare_flet_diagnostics_template`.

- [ ] **Step 3: Implement one reviewed pre-compile insertion and deterministic ZIP**

The script verifies the official template ZIP SHA-256 before extraction. It patches only `build/{{cookiecutter.out_dir}}/lib/main.dart`, verifies exact pre/post anchor counts, and rebuilds a deterministic ZIP with normalized member order, timestamps, and permissions. The inserted Dart imports `dart:io`, installs `FlutterError.onError` and `PlatformDispatcher.instance.onError`, writes only error runtime type, sanitized stack symbols, app phase, and coarse timestamp to `%APPDATA%\UTHelper\diagnostics\flutter-errors.jsonl` on Windows, then invokes the previous handler. It bounds the bridge file at 64 KiB by replacing it with the newest record when necessary.

```python
if sha256(source_zip.read_bytes()).hexdigest() != OFFICIAL_TEMPLATE_SHA256:
    raise UnknownRunnerTemplate("official Flet template hash changed")
if source.count(PRE_ANCHOR) != 1 or source.count(POST_ANCHOR) != 1:
    raise UnknownRunnerTemplate("Flet runner anchors changed; refusing diagnostics patch")
patched = source.replace(PRE_ANCHOR, PRE_ANCHOR + INSERTION, 1)
_write_deterministic_template(output_zip, members, patched)
verify_template(output_zip)
```

- [ ] **Step 4: Build Windows with the reviewed template, then verify generated source**

Before `flet build windows`, download/cache the immutable official template at `build/support/flet-build-template-0.86.5.zip`, run `python scripts/prepare_flet_diagnostics_template.py --source build/support/flet-build-template-0.86.5.zip --output build/support/flet-build-template-0.86.5-diagnostics.zip`, and pass `--template build/support/flet-build-template-0.86.5-diagnostics.zip` to Flet. After the build, run `python scripts/verify_flutter_diagnostics.py --template build/support/flet-build-template-0.86.5-diagnostics.zip --project-root build/flutter`; the verifier checks the source hash, deterministic output hash, and generated `build/flutter/lib/main.dart` hook content. Each workflow step has finite `timeout-minutes`. Android and iOS builds do not receive this Windows-only bridge.

- [ ] **Step 5: Consume and delete bridge records at next Python startup**

Add `consume_flutter_records(path) -> tuple[FlutterErrorEvidence, ...]` to `runtime.py`. It reads at most 64 KiB, validates only runtime type, symbol, phase, and coarse time, converts each record through the existing sanitized report path, and deletes the file after successful import. Malformed or oversized content is deleted without upload and logged locally after redaction.

- [ ] **Step 6: Run fixture, bridge-consumption, and workflow contract tests**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_flutter_diagnostics_patch.py tests/test_diagnostic_runtime.py tests/test_release_hardening.py -v --timeout=40`

Expected: the reviewed template transforms deterministically before compilation, bridge records are sanitized and consumed once, and every Windows Flet build uses and verifies the patched template.

- [ ] **Step 7: Commit the Flutter capture contract**

```powershell
git add scripts/prepare_flet_diagnostics_template.py scripts/verify_flutter_diagnostics.py src/diagnostics/runtime.py tests/fixtures/flutter_template tests/test_flutter_diagnostics_patch.py tests/test_diagnostic_runtime.py .github/workflows scripts
git commit -m "feat: capture generated Flutter errors safely"
```

### Task 10: Document privacy controls and run the diagnostic release gate

**Files:**
- Create: `docs/PRIVACY.md`
- Modify: `README.md`
- Modify: `REFAC_KNOWLEDGE.md`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_diagnostic_redaction.py`
- Test: `tests/test_diagnostic_subprocess.py`

**Interfaces:**
- Consumes: all diagnostic contracts above.
- Produces: user-visible privacy disclosure and mandatory CI check `Private diagnostics`.

- [ ] **Step 1: Add a documentation contract test**

```python
def test_privacy_document_states_consent_retention_and_forbidden_data():
    text = Path("docs/PRIVACY.md").read_text("utf-8").lower()
    for phrase in (
        "explicit consent", "20 reports", "7 days", "ip address",
        "password", "moodle", "raw log", "disable", "delete",
    ):
        assert phrase in text
```

- [ ] **Step 2: Write the exact disclosure and architecture notes**

Document the allowed and forbidden field lists verbatim from the design, the first-run opt-in, the absence of stable device identifiers, local queue location/caps, immediate queue deletion on revoke, how to send a synthetic test report, the network-IP caveat, Sentry retention/contact details, and the fact that an unclean exit is not definitive proof of a crash.

- [ ] **Step 3: Add the bounded CI diagnostic gate**

The `Private diagnostics` job runs with `contents: read`, `timeout-minutes: 10`, no Sentry DSN, and:

```powershell
$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'
pytest tests/test_diagnostic_redaction.py tests/test_diagnostic_logging.py tests/test_diagnostic_spool.py tests/test_diagnostic_transport.py tests/test_diagnostic_release_config.py tests/test_diagnostic_runtime.py tests/test_diagnostic_subprocess.py tests/test_windows_crash_evidence.py tests/test_flutter_diagnostics_patch.py -v --timeout=60
```

- [ ] **Step 4: Run targeted, full, lint, and secret-output scans**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests/test_diagnostic_redaction.py tests/test_diagnostic_logging.py tests/test_diagnostic_spool.py tests/test_diagnostic_transport.py tests/test_diagnostic_release_config.py tests/test_diagnostic_runtime.py tests/test_diagnostic_subprocess.py tests/test_windows_crash_evidence.py tests/test_flutter_diagnostics_patch.py -v --timeout=120`

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; pytest tests -q --timeout=180`

Run: `ruff check src tests scripts`

Run: `rg -n -i "password|sesskey|moodlesession|authorization|raw log|send_default_pii\s*=\s*true" src/diagnostics docs/PRIVACY.md .github/workflows`

Expected: targeted/full tests and Ruff pass; review confirms matches are tests, forbidden-field documentation, or safe configuration only.

- [ ] **Step 5: Commit documentation and CI**

```powershell
git add docs/PRIVACY.md README.md REFAC_KNOWLEDGE.md .github/workflows/ci.yml tests/test_diagnostic_redaction.py tests/test_diagnostic_subprocess.py
git commit -m "docs: publish crash diagnostic privacy contract"
```

## Completion evidence

- A disabled/not-asked installation makes zero diagnostic network calls.
- A consented synthetic exception creates a schema-valid scrubbed report, survives
  offline restart, sends once, and is acknowledged only after confirmed delivery.
- The secret corpus is absent from persistent log/spool bytes and captured transport
  payloads.
- Python main/thread/async/unraisable boundaries are exercised by real bounded child
  processes; `os.abort()` produces an unclean marker without a false Python exception.
- Generated Flutter runner patch/verification succeeds for the pinned fixture and
  fails closed on drift.
- Log/spool byte, count, and age caps are measured by tests.
- Full tests, Ruff, and `Private diagnostics` CI pass without a configured Sentry DSN.
- A real Sentry test event and retention/project configuration remain a deployment
  prerequisite; their absence is reported as unconfigured, not treated as success.
