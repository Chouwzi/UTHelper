"""Consent-gated, fail-closed delivery of allow-listed diagnostic reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import socket
import ssl
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import certifi

from diagnostics.models import CrashConsent, DiagnosticReport
from diagnostics.release_config import PublicConfigError, validate_public_sentry_dsn
from diagnostics.spool import DiagnosticSpool

_MAX_DELIVERY_SECONDS = 5.0
_MIN_DELIVERY_SECONDS = 0.1
_MAX_RETRY_AFTER_SECONDS = 300.0


class _RejectDiagnosticRedirects(HTTPRedirectHandler):
    """Keep the authenticated diagnostic envelope on its original origin."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _bounded_delivery_seconds(value: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("diagnostic delivery deadline must be finite") from exc
    if not math.isfinite(seconds):
        raise ValueError("diagnostic delivery deadline must be finite")
    return min(max(seconds, _MIN_DELIVERY_SECONDS), _MAX_DELIVERY_SECONDS)


def _open_diagnostic_request(
    request: Request,
    *,
    timeout_seconds: float,
    context: ssl.SSLContext,
) -> Any:
    """Open one request without following redirects or spawning background I/O."""
    opener = build_opener(
        HTTPSHandler(context=context),
        _RejectDiagnosticRedirects(),
    )
    return opener.open(request, timeout=_bounded_delivery_seconds(timeout_seconds))


@dataclass(frozen=True)
class DeliveryOutcome:
    """Confirmed HTTP result for one attempted report delivery."""

    confirmed: bool
    retryable: bool
    reason: str
    status_code: int | None = None
    retry_after_seconds: float | None = None

    @classmethod
    def confirmed_success(cls, *, status_code: int) -> "DeliveryOutcome":
        return cls(
            confirmed=True,
            retryable=False,
            reason="confirmed",
            status_code=status_code,
        )

    @classmethod
    def retryable_failure(
        cls,
        reason: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> "DeliveryOutcome":
        delay = None
        if retry_after is not None:
            candidate = float(retry_after)
            if math.isfinite(candidate):
                delay = min(max(candidate, 0.0), _MAX_RETRY_AFTER_SECONDS)
        return cls(
            confirmed=False,
            retryable=True,
            reason=reason,
            status_code=status_code,
            retry_after_seconds=delay,
        )

    @classmethod
    def retained_failure(
        cls,
        reason: str,
        *,
        status_code: int | None = None,
    ) -> "DeliveryOutcome":
        return cls(
            confirmed=False,
            retryable=False,
            reason=reason,
            status_code=status_code,
        )


@dataclass(frozen=True)
class FlushSummary:
    """Bounded delivery pass result; only ``sent`` reports were acknowledged."""

    sent: int = 0
    retained: int = 0
    attempted: int = 0
    skipped_consent: bool = False
    skipped_unconfigured: bool = False
    skipped_backoff: bool = False
    deadline_exhausted: bool = False
    retry_after_seconds: float = 0.0


class DiagnosticConsentGate:
    """Serialize live consent changes with capture and request boundaries."""

    def __init__(
        self,
        initial: CrashConsent = CrashConsent.NOT_ASKED,
    ) -> None:
        self._lock = threading.RLock()
        self._consent = CrashConsent(initial)

    @property
    def current(self) -> CrashConsent:
        with self._lock:
            return self._consent

    def set(
        self,
        consent: CrashConsent | str,
        *,
        while_locked: Callable[[], None] | None = None,
    ) -> None:
        selected = CrashConsent(consent)
        with self._lock:
            self._consent = selected
            if while_locked is not None:
                while_locked()

    def run_if_enabled(
        self,
        operation: Callable[[], Any],
    ) -> tuple[bool, Any]:
        """Run one operation atomically with respect to consent revocation."""
        with self._lock:
            if self._consent is not CrashConsent.ENABLED:
                return False, None
            return True, operation()


def _validated_report(report: object) -> DiagnosticReport:
    if not isinstance(report, DiagnosticReport):
        raise TypeError("report must be a validated DiagnosticReport")
    return DiagnosticReport.model_validate_json(report.model_dump_json())


def _event_from_report(report: DiagnosticReport) -> dict[str, Any]:
    frames = [
        {
            "filename": frame.relative_path,
            "module": frame.module,
            "function": frame.function,
            "lineno": frame.line,
            "in_app": True,
        }
        for frame in report.frames
    ]
    tags: dict[str, str] = {
        "schema_version": str(report.schema_version),
        "install_type": report.install_type,
        "os_family": report.os_family,
        "os_version": report.os_version,
        "architecture": report.architecture,
        "python_version": report.python_version,
        "flet_version": report.flet_version,
        "phase": report.phase.value,
        "window_state": report.window_state,
        "unclean_previous_exit": str(report.unclean_previous_exit).lower(),
    }
    if report.flutter_version is not None:
        tags["flutter_version"] = report.flutter_version
    if report.native_exception_code is not None:
        tags["native_exception_code"] = report.native_exception_code
    if report.faulting_module is not None:
        tags["faulting_module"] = report.faulting_module
    return {
        "event_id": report.event_id.hex,
        "timestamp": report.occurred_at.astimezone(timezone.utc).isoformat(),
        "release": f"uthelper@{report.app_version}",
        "environment": report.release_channel,
        "platform": "python",
        "level": "error",
        "logger": "uthelper.diagnostics",
        "fingerprint": [report.fingerprint],
        "exception": {
            "values": [
                {
                    "type": report.exception_type,
                    "stacktrace": {"frames": frames},
                }
            ]
        },
        "tags": tags,
    }


def _strict_before_send(
    _event: dict[str, Any],
    hint: dict[str, Any],
) -> dict[str, Any] | None:
    """Discard the candidate event and rebuild it from a validated report."""
    try:
        report = _validated_report(hint.get("report"))
    except (TypeError, ValueError):
        return None
    return _event_from_report(report)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        candidate = float(value.strip())
        if not math.isfinite(candidate):
            return None
        return min(max(candidate, 0.0), _MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = (parsed - datetime.now(timezone.utc)).total_seconds()
            return min(max(seconds, 0.0), _MAX_RETRY_AFTER_SECONDS)
        except (TypeError, ValueError, OverflowError):
            return None


def _http_outcome_from_error(error: HTTPError) -> DeliveryOutcome:
    status = int(error.code)
    if status == 429:
        return DeliveryOutcome.retryable_failure(
            "rate_limited",
            status_code=status,
            retry_after=_retry_after_seconds(error.headers.get("Retry-After")),
        )
    if 500 <= status <= 599:
        return DeliveryOutcome.retryable_failure(
            "server_error",
            status_code=status,
        )
    return DeliveryOutcome.retained_failure("http_rejected", status_code=status)


class _SdkClientAdapter:
    def __init__(self, client: Any, transport: Any) -> None:
        self._client = client
        self._transport = transport

    def send_report(
        self,
        report: DiagnosticReport,
        *,
        timeout_seconds: float,
    ) -> DeliveryOutcome:
        self._transport.prepare(timeout_seconds)
        self._client.capture_event({}, hint={"report": report})
        return self._transport.outcome or DeliveryOutcome.retained_failure(
            "privacy_filter_rejected"
        )


def _new_sentry_client(
    *,
    dsn: str,
    before_send: Callable[..., Any],
    default_integrations: bool,
    send_default_pii: bool,
    max_breadcrumbs: int,
    traces_sample_rate: float,
) -> _SdkClientAdapter:
    """Create a private Client with a synchronous, outcome-aware transport."""
    import sentry_sdk
    from sentry_sdk.consts import EndpointType, VERSION
    from sentry_sdk.transport import Transport

    class _ConfirmedEnvelopeTransport(Transport):
        def __init__(self, options: dict[str, Any] | None = None) -> None:
            super().__init__(options)
            self.timeout_seconds = _MAX_DELIVERY_SECONDS
            self.outcome: DeliveryOutcome | None = None

        def prepare(self, timeout_seconds: float) -> None:
            self.timeout_seconds = _bounded_delivery_seconds(timeout_seconds)
            self.outcome = None

        def capture_envelope(self, envelope: Any) -> None:
            if self.parsed_dsn is None:
                self.outcome = DeliveryOutcome.retained_failure("invalid_dsn")
                return
            if len(envelope.items) != 1 or envelope.items[0].type != "event":
                self.outcome = DeliveryOutcome.retained_failure("unexpected_envelope")
                return
            auth = self.parsed_dsn.to_auth(f"sentry.python/{VERSION}")
            request = Request(
                auth.get_api_url(EndpointType.ENVELOPE),
                data=envelope.serialize(),
                headers={
                    "Content-Type": "application/x-sentry-envelope",
                    "User-Agent": "UTHelper diagnostics",
                    "X-Sentry-Auth": auth.to_header(),
                },
                method="POST",
            )
            try:
                context = ssl.create_default_context(cafile=certifi.where())
                with _open_diagnostic_request(
                    request,
                    timeout_seconds=self.timeout_seconds,
                    context=context,
                ) as response:
                    status = int(response.getcode())
                if 200 <= status <= 299:
                    self.outcome = DeliveryOutcome.confirmed_success(
                        status_code=status
                    )
                elif status == 429:
                    self.outcome = DeliveryOutcome.retryable_failure(
                        "rate_limited",
                        status_code=status,
                    )
                elif 500 <= status <= 599:
                    self.outcome = DeliveryOutcome.retryable_failure(
                        "server_error",
                        status_code=status,
                    )
                else:
                    self.outcome = DeliveryOutcome.retained_failure(
                        "http_rejected",
                        status_code=status,
                    )
            except HTTPError as exc:
                try:
                    self.outcome = _http_outcome_from_error(exc)
                finally:
                    exc.close()
            except (TimeoutError, socket.timeout):
                self.outcome = DeliveryOutcome.retryable_failure("timeout")
            except (URLError, OSError):
                self.outcome = DeliveryOutcome.retryable_failure("offline")

    client = sentry_sdk.Client(
        dsn=dsn,
        transport=_ConfirmedEnvelopeTransport,
        before_send=before_send,
        default_integrations=default_integrations,
        send_default_pii=send_default_pii,
        max_breadcrumbs=max_breadcrumbs,
        traces_sample_rate=traces_sample_rate,
        send_client_reports=False,
        auto_session_tracking=False,
    )
    transport = client.transport
    if not isinstance(transport, _ConfirmedEnvelopeTransport):
        raise RuntimeError("Sentry did not install the confirmed transport")
    return _SdkClientAdapter(client, transport)


class SentryDiagnosticTransport:
    """Lazy Sentry adapter that accepts only a validated report object."""

    def __init__(self, dsn: str) -> None:
        self.dsn = validate_public_sentry_dsn(dsn)
        self._client: _SdkClientAdapter | None = None

    def send(
        self,
        report: DiagnosticReport,
        *,
        timeout_seconds: float = _MAX_DELIVERY_SECONDS,
    ) -> DeliveryOutcome:
        validated = _validated_report(report)
        bounded_timeout = _bounded_delivery_seconds(timeout_seconds)
        if self._client is None:
            try:
                self._client = _new_sentry_client(
                    dsn=self.dsn,
                    before_send=_strict_before_send,
                    default_integrations=False,
                    send_default_pii=False,
                    max_breadcrumbs=0,
                    traces_sample_rate=0.0,
                )
            except (ImportError, RuntimeError):
                return DeliveryOutcome.retryable_failure("sdk_unavailable")
        try:
            return self._client.send_report(
                validated,
                timeout_seconds=bounded_timeout,
            )
        except (TimeoutError, socket.timeout):
            return DeliveryOutcome.retryable_failure("timeout")
        except (URLError, OSError):
            return DeliveryOutcome.retryable_failure("offline")
        except Exception:
            return DeliveryOutcome.retained_failure("sdk_rejected")


class DiagnosticDeliveryWorker:
    """Flush a spool only after explicit opt-in and within a finite deadline."""

    def __init__(
        self,
        spool: DiagnosticSpool,
        *,
        dsn: str | None,
        monotonic: Callable[[], float] = time.monotonic,
        transport_factory: Callable[[str], SentryDiagnosticTransport] = (
            SentryDiagnosticTransport
        ),
        consent_gate: DiagnosticConsentGate | None = None,
    ) -> None:
        self.spool = spool
        self.dsn = dsn
        self.monotonic = monotonic
        self.transport_factory = transport_factory
        self.consent_gate = consent_gate
        self._failure_count = 0
        self._next_attempt_at = 0.0

    def flush_once(
        self,
        consent: CrashConsent,
        deadline_seconds: float = _MAX_DELIVERY_SECONDS,
    ) -> FlushSummary:
        if consent is not CrashConsent.ENABLED:
            if (
                self.consent_gate is None
                and consent is CrashConsent.DISABLED
            ):
                self.spool.clear()
            return FlushSummary(skipped_consent=True)
        if (
            self.consent_gate is not None
            and self.consent_gate.current is not CrashConsent.ENABLED
        ):
            return FlushSummary(
                retained=len(self.spool.pending()),
                skipped_consent=True,
            )
        if not self.dsn:
            return FlushSummary(
                retained=len(self.spool.pending()),
                skipped_unconfigured=True,
            )

        start = self.monotonic()
        budget = _bounded_delivery_seconds(deadline_seconds)
        if start < self._next_attempt_at:
            return FlushSummary(
                retained=len(self.spool.pending()),
                skipped_backoff=True,
                retry_after_seconds=self._next_attempt_at - start,
            )
        return self._send_pending_until(start + budget)

    def _send_pending_until(self, deadline: float) -> FlushSummary:
        pending = self.spool.pending()
        retained = len(pending)
        if not pending:
            return FlushSummary()
        if self.monotonic() >= deadline:
            return FlushSummary(retained=retained, deadline_exhausted=True)
        try:
            if self.consent_gate is None:
                transport = self.transport_factory(self.dsn or "")
            else:
                allowed, transport = self.consent_gate.run_if_enabled(
                    lambda: self.transport_factory(self.dsn or "")
                )
                if not allowed:
                    return FlushSummary(
                        retained=retained,
                        skipped_consent=True,
                    )
        except (PublicConfigError, ValueError):
            return FlushSummary(retained=retained, skipped_unconfigured=True)

        sent = 0
        attempted = 0
        for queued in pending:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return FlushSummary(
                    sent=sent,
                    retained=retained - sent,
                    attempted=attempted,
                    deadline_exhausted=True,
                )
            if self.consent_gate is None:
                outcome = transport.send(
                    queued.report,
                    timeout_seconds=min(remaining, _MAX_DELIVERY_SECONDS),
                )
            else:
                allowed, outcome = self.consent_gate.run_if_enabled(
                    lambda: transport.send(
                        queued.report,
                        timeout_seconds=min(remaining, _MAX_DELIVERY_SECONDS),
                    )
                )
                if not allowed:
                    return FlushSummary(
                        sent=sent,
                        retained=max(retained - sent, 0),
                        attempted=attempted,
                        skipped_consent=True,
                    )
            attempted += 1
            if outcome.confirmed:
                self.spool.acknowledge(queued.report.event_id)
                sent += 1
                self._failure_count = 0
                self._next_attempt_at = 0.0
                continue

            self._failure_count += 1
            delay = outcome.retry_after_seconds
            if delay is None or delay <= 0:
                delay = min(float(2 ** (self._failure_count - 1)), 60.0)
            self._next_attempt_at = self.monotonic() + delay
            return FlushSummary(
                sent=sent,
                retained=retained - sent,
                attempted=attempted,
                retry_after_seconds=delay,
            )
        return FlushSummary(sent=sent, retained=retained - sent, attempted=attempted)


__all__ = [
    "DiagnosticConsentGate",
    "DeliveryOutcome",
    "DiagnosticDeliveryWorker",
    "FlushSummary",
    "SentryDiagnosticTransport",
]
