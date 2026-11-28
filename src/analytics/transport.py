"""Consent-gated, captive-portal-resistant PostHog ``/batch/`` HTTP transport."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import http.client
import inspect
import json
import math
import random
import socket
import ssl
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import certifi

from analytics.models import (
    AnalyticsConfigError,
    AnalyticsConsent,
    AnalyticsEvent,
    validate_posthog_api_key,
    validate_posthog_host,
)
from analytics.spool import (
    AnalyticsSpool,
    MAX_BATCH_BYTES,
    MAX_EVENTS_PER_BATCH,
)
from analytics.uuid7 import extract_uuid7_ms

MAX_DELIVERY_TIMEOUT_SECONDS = 5.0
MIN_DELIVERY_TIMEOUT_SECONDS = 0.1
MIN_RETRY_AFTER_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 300.0
MAX_RESPONSE_BODY_BYTES = 4 * 1024


class _RejectAnalyticsRedirects(HTTPRedirectHandler):
    """Never follow HTTP redirects so ``api_key`` and payloads stay on origin."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _bounded_timeout_seconds(value: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("analytics delivery timeout must be finite") from exc
    if not math.isfinite(seconds):
        raise ValueError("analytics delivery timeout must be finite")
    return min(max(seconds, MIN_DELIVERY_TIMEOUT_SECONDS), MAX_DELIVERY_TIMEOUT_SECONDS)


def _clamp_retry_seconds(value: float) -> float:
    if not math.isfinite(value):
        return MIN_RETRY_AFTER_SECONDS
    return min(max(value, MIN_RETRY_AFTER_SECONDS), MAX_RETRY_AFTER_SECONDS)


def _parse_retry_after_seconds(
    value: str | None,
    *,
    now_utc: datetime | None = None,
) -> float | None:
    """Parse an HTTP ``Retry-After`` header and clamp into ``[1.0s, 300.0s]``."""
    if not value or not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        numeric = float(stripped)
        if not math.isfinite(numeric):
            return None
        return _clamp_retry_seconds(numeric)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(stripped)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            reference = now_utc or datetime.now(UTC)
            delta = (parsed - reference).total_seconds()
            return _clamp_retry_seconds(delta)
        except (TypeError, ValueError, OverflowError):
            return None


def _media_type_from_headers(headers: Any) -> str:
    if headers is None:
        return ""
    raw = ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        raw = str(getter("Content-Type") or "")
    return raw.split(";", 1)[0].strip().casefold()


def _open_analytics_request(
    request: Request,
    *,
    timeout_seconds: float,
    context: ssl.SSLContext,
) -> Any:
    opener = build_opener(
        HTTPSHandler(context=context),
        _RejectAnalyticsRedirects(),
    )
    return opener.open(request, timeout=_bounded_timeout_seconds(timeout_seconds))


@dataclass(frozen=True, slots=True)
class BatchDeliveryOutcome:
    """Confirmed HTTP result for one ``POST <host>/batch/`` attempt."""

    confirmed: bool
    retryable: bool
    reason: str
    status_code: int | None = None
    retry_after_seconds: float | None = None
    drop_batch: bool = False
    disable_runtime: bool = False

    @classmethod
    def confirmed_success(cls, *, status_code: int = 200) -> "BatchDeliveryOutcome":
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
    ) -> "BatchDeliveryOutcome":
        delay = _clamp_retry_seconds(float(retry_after)) if retry_after is not None else None
        return cls(
            confirmed=False,
            retryable=True,
            reason=reason,
            status_code=status_code,
            retry_after_seconds=delay,
        )

    @classmethod
    def poison_batch(
        cls,
        reason: str,
        *,
        status_code: int | None = None,
    ) -> "BatchDeliveryOutcome":
        return cls(
            confirmed=False,
            retryable=False,
            reason=reason,
            status_code=status_code,
            drop_batch=True,
        )

    @classmethod
    def permanent_disable(
        cls,
        reason: str,
        *,
        status_code: int | None = None,
    ) -> "BatchDeliveryOutcome":
        return cls(
            confirmed=False,
            retryable=False,
            reason=reason,
            status_code=status_code,
            disable_runtime=True,
        )


@dataclass(frozen=True, slots=True)
class AnalyticsFlushSummary:
    """Bounded delivery pass summary."""

    sent_batches: int = 0
    sent_events: int = 0
    dropped_batches: int = 0
    retained_batches: int = 0
    attempted_batches: int = 0
    skipped_consent: bool = False
    skipped_unconfigured: bool = False
    skipped_backoff: bool = False
    disabled_invalid_key: bool = False
    deadline_exhausted: bool = False
    retry_after_seconds: float = 0.0


class AnalyticsConsentEpochGate:
    """Non-blocking epoch gate for instant ``0ms`` opt-out / reset / logout purges.

    Unlike locking around blocking socket calls, ``AnalyticsConsentEpochGate`` never
    holds its lock across network I/O. Revoking consent or resetting state increments
    ``_epoch`` and runs local RAM/spool cleanup in ``< 1ms``, while any in-flight
    HTTP worker notices the epoch mismatch upon socket return and aborts immediately.
    """

    def __init__(
        self,
        initial: AnalyticsConsent | str = AnalyticsConsent.ENABLED,
    ) -> None:
        self._lock = threading.RLock()
        self._consent = AnalyticsConsent(initial)
        self._epoch = 0

    @property
    def current(self) -> AnalyticsConsent:
        with self._lock:
            return self._consent

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def snapshot(self) -> tuple[AnalyticsConsent, int]:
        """Return ``(consent, epoch)`` atomically without blocking on network I/O."""
        with self._lock:
            return self._consent, self._epoch

    def is_enabled_at(self, expected_epoch: int) -> bool:
        """Return True iff consent is ``ENABLED`` and ``_epoch == expected_epoch``."""
        with self._lock:
            return (
                self._consent is AnalyticsConsent.ENABLED
                and self._epoch == expected_epoch
            )

    def set_consent(
        self,
        consent: AnalyticsConsent | str,
        *,
        on_revoke: Callable[[], None] | None = None,
    ) -> int:
        """Update live consent, increment ``_epoch`` on change, and run ``on_revoke`` if disabled."""
        selected = AnalyticsConsent(consent)
        with self._lock:
            if selected != self._consent:
                self._consent = selected
                self._epoch += 1
            current_epoch = self._epoch
            if selected is not AnalyticsConsent.ENABLED and on_revoke is not None:
                on_revoke()
            return current_epoch

    def bump_epoch(
        self,
        *,
        on_purge: Callable[[], None] | None = None,
    ) -> int:
        """Increment ``_epoch`` to invalidate in-flight work and run ``on_purge``."""
        with self._lock:
            self._epoch += 1
            current_epoch = self._epoch
            if on_purge is not None:
                on_purge()
            return current_epoch


AnalyticsConsentGate = AnalyticsConsentEpochGate


class PostHogHttpTransport:
    """Stdlib ``urllib.request`` + ``certifi`` client for PostHog ``POST <host>/batch/``."""

    def __init__(
        self,
        *,
        api_key: str,
        host: str,
        app_version: str = "unknown",
        allow_private_host: bool = False,
        opener_func: Callable[..., Any] = _open_analytics_request,
    ) -> None:
        self.api_key = validate_posthog_api_key(api_key)
        self.host = validate_posthog_host(
            host,
            allow_private_dev=allow_private_host,
        )
        self.endpoint_url = f"{self.host}/batch/"
        safe_ver = (
            app_version
            if isinstance(app_version, str)
            and app_version
            and all(0x21 <= ord(c) <= 0x7E for c in app_version)
            else "unknown"
        )
        self.user_agent = f"UTHelper-Analytics/{safe_ver[:32]}"
        self._opener_func = opener_func
        self._ssl_context: ssl.SSLContext | None = None

    def _get_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            self._ssl_context = ssl.create_default_context(cafile=certifi.where())
        return self._ssl_context

    def build_batch_payload(self, events: Sequence[AnalyticsEvent]) -> bytes:
        """Validate events and serialize ``{"api_key": ..., "batch": [...]}`` (no ``sent_at``)."""
        if not events or len(events) > MAX_EVENTS_PER_BATCH:
            raise ValueError(
                f"batch must contain 1..{MAX_EVENTS_PER_BATCH} AnalyticsEvent items"
            )
        wire_batch: list[dict[str, Any]] = []
        for candidate in events:
            if not isinstance(candidate, AnalyticsEvent):
                raise TypeError("batch items must be AnalyticsEvent instances")
            validated = AnalyticsEvent.model_validate_json(candidate.model_dump_json())
            wire = validated.to_wire_dict()
            props = wire["properties"]
            extract_uuid7_ms(props["$session_id"])
            if props.get("$process_person_profile") is not False:
                raise ValueError("$process_person_profile must be False")
            if props.get("$geoip_disable") is not True:
                raise ValueError("$geoip_disable must be True")
            if "$ip" not in props or props["$ip"] is not None:
                raise ValueError("$ip must be None")
            wire_batch.append(wire)

        payload_dict = {
            "api_key": self.api_key,
            "batch": wire_batch,
        }
        encoded = json.dumps(
            payload_dict,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_BATCH_BYTES:
            raise ValueError("encoded /batch/ payload exceeds 64 KiB")
        return encoded

    def send_batch(
        self,
        events: Sequence[AnalyticsEvent],
        *,
        timeout_seconds: float = MAX_DELIVERY_TIMEOUT_SECONDS,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> BatchDeliveryOutcome:
        """Deliver one batch and confirm ONLY on verified JSON ``200`` status."""
        try:
            payload_bytes = self.build_batch_payload(events)
        except (TypeError, ValueError):
            return BatchDeliveryOutcome.poison_batch("invalid_batch_payload")

        bounded_timeout = _bounded_timeout_seconds(timeout_seconds)
        request = Request(
            self.endpoint_url,
            data=payload_bytes,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        if is_cancelled is not None and is_cancelled():
            return BatchDeliveryOutcome.retryable_failure("cancelled")
        try:
            with self._opener_func(
                request,
                timeout_seconds=bounded_timeout,
                context=self._get_ssl_context(),
            ) as response:
                status = int(response.getcode())
                media_type = _media_type_from_headers(getattr(response, "headers", None))
                body = response.read(MAX_RESPONSE_BODY_BYTES + 1)
            return self._classify_ok_response(
                status=status,
                media_type=media_type,
                body=body,
            )
        except HTTPError as exc:
            try:
                return self._classify_http_error(exc)
            finally:
                try:
                    exc.close()
                except Exception:
                    pass
        except (TimeoutError, socket.timeout):
            return BatchDeliveryOutcome.retryable_failure("timeout")
        except (http.client.HTTPException, ValueError):
            return BatchDeliveryOutcome.retryable_failure("http_protocol_error")
        except (URLError, ssl.SSLError, OSError):
            return BatchDeliveryOutcome.retryable_failure("offline")

    @staticmethod
    def _classify_ok_response(
        *,
        status: int,
        media_type: str,
        body: bytes,
    ) -> BatchDeliveryOutcome:
        if status != 200:
            if 300 <= status <= 399:
                return BatchDeliveryOutcome.retryable_failure(
                    "redirect_rejected",
                    status_code=status,
                )
            return BatchDeliveryOutcome.retryable_failure(
                "unexpected_status",
                status_code=status,
            )

        if media_type in {"text/html", "application/xhtml+xml"}:
            return BatchDeliveryOutcome.retryable_failure(
                "captive_portal",
                status_code=200,
            )
        if media_type != "application/json":
            return BatchDeliveryOutcome.retryable_failure(
                "unexpected_content_type",
                status_code=200,
            )
        if len(body) > MAX_RESPONSE_BODY_BYTES:
            return BatchDeliveryOutcome.retryable_failure(
                "response_too_large",
                status_code=200,
            )
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return BatchDeliveryOutcome.retryable_failure(
                "invalid_json_response",
                status_code=200,
            )
        if not isinstance(decoded, dict):
            return BatchDeliveryOutcome.retryable_failure(
                "invalid_json_response",
                status_code=200,
            )

        raw_status = decoded.get("status")
        if type(raw_status) is not bool and raw_status in (1, "Ok", "ok"):
            return BatchDeliveryOutcome.confirmed_success(status_code=200)
        return BatchDeliveryOutcome.retryable_failure(
            "unconfirmed_status",
            status_code=200,
        )

    @staticmethod
    def _classify_http_error(error: HTTPError) -> BatchDeliveryOutcome:
        status = int(error.code)
        headers = getattr(error, "headers", None)
        media_type = _media_type_from_headers(headers)

        if 300 <= status <= 399:
            return BatchDeliveryOutcome.retryable_failure(
                "redirect_rejected",
                status_code=status,
            )
        if status == 429:
            retry_header = headers.get("Retry-After") if headers is not None else None
            return BatchDeliveryOutcome.retryable_failure(
                "rate_limited",
                status_code=429,
                retry_after=_parse_retry_after_seconds(retry_header),
            )
        if status in {400, 413}:
            if media_type in {"text/html", "application/xhtml+xml"}:
                return BatchDeliveryOutcome.retryable_failure(
                    "captive_portal",
                    status_code=status,
                )
            reason = "bad_request" if status == 400 else "payload_too_large"
            return BatchDeliveryOutcome.poison_batch(reason, status_code=status)
        if status == 401:
            if media_type == "application/json":
                try:
                    raw = error.read(MAX_RESPONSE_BODY_BYTES + 1)
                    if len(raw) <= MAX_RESPONSE_BODY_BYTES:
                        parsed = json.loads(raw.decode("utf-8"))
                        if isinstance(parsed, dict):
                            return BatchDeliveryOutcome.permanent_disable(
                                "invalid_api_key",
                                status_code=401,
                            )
                except (OSError, UnicodeError, json.JSONDecodeError):
                    pass
            return BatchDeliveryOutcome.retryable_failure(
                "proxy_auth_or_portal",
                status_code=401,
            )
        if 500 <= status <= 599:
            return BatchDeliveryOutcome.retryable_failure(
                "server_error",
                status_code=status,
            )
        return BatchDeliveryOutcome.retryable_failure(
            "http_error",
            status_code=status,
        )


def _accepts_is_cancelled(send_fn: Callable[..., Any]) -> bool:
    try:
        sig = inspect.signature(send_fn)
    except (TypeError, ValueError):
        return False
    params = sig.parameters
    if "is_cancelled" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class AnalyticsDeliveryWorker:
    """Flush spooled batches with epoch-checked consent, jittered backoff, and poison drops."""

    def __init__(
        self,
        spool: AnalyticsSpool,
        *,
        api_key: str | None,
        host: str | None,
        app_version: str = "unknown",
        allow_private_host: bool = False,
        consent_gate: AnalyticsConsentEpochGate | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
        transport_factory: Callable[..., PostHogHttpTransport] | None = None,
        on_permanent_disable: Callable[[], None] | None = None,
    ) -> None:
        self.spool = spool
        self.api_key = api_key
        self.host = host
        self.app_version = app_version
        self.allow_private_host = allow_private_host
        self.consent_gate = consent_gate or AnalyticsConsentEpochGate()
        self.monotonic = monotonic
        self.rng = rng
        self._transport_factory = transport_factory or PostHogHttpTransport
        self._on_permanent_disable = on_permanent_disable
        self._transport: PostHogHttpTransport | None = None
        self._state_lock = threading.Lock()
        self._failure_count = 0
        self._next_attempt_at = 0.0
        self._disabled_for_process = False

    @property
    def disabled_for_process(self) -> bool:
        with self._state_lock:
            return self._disabled_for_process

    def _compute_backoff_delay(self, explicit_retry_after: float | None) -> float:
        jitter_sample = min(max(float(self.rng()), 0.0), 1.0)
        if explicit_retry_after is not None:
            base = _clamp_retry_seconds(explicit_retry_after)
            jitter = jitter_sample * min(base * 0.1, 5.0)
            return _clamp_retry_seconds(base + jitter)
        base = _clamp_retry_seconds(float(2 ** max(0, self._failure_count - 1)))
        jitter = jitter_sample * min(base * 0.2, 10.0)
        return _clamp_retry_seconds(base + jitter)

    def _resolve_transport(self) -> PostHogHttpTransport | None:
        if not self.api_key or not self.host:
            return None
        if self._transport is None:
            self._transport = self._transport_factory(
                api_key=self.api_key,
                host=self.host,
                app_version=self.app_version,
                allow_private_host=self.allow_private_host,
            )
        return self._transport

    def flush_once(
        self,
        *,
        deadline_seconds: float = MAX_DELIVERY_TIMEOUT_SECONDS,
    ) -> AnalyticsFlushSummary:
        """Deliver pending batches within ``deadline_seconds`` if consented and configured."""
        consent, start_epoch = self.consent_gate.snapshot()
        if consent is not AnalyticsConsent.ENABLED:
            return AnalyticsFlushSummary(skipped_consent=True)

        with self._state_lock:
            if self._disabled_for_process:
                return AnalyticsFlushSummary(
                    retained_batches=len(self.spool.pending()),
                    disabled_invalid_key=True,
                )

        if not self.api_key or not self.host:
            return AnalyticsFlushSummary(
                retained_batches=len(self.spool.pending()),
                skipped_unconfigured=True,
            )

        now = self.monotonic()
        budget = _bounded_timeout_seconds(deadline_seconds)
        with self._state_lock:
            if now < self._next_attempt_at:
                return AnalyticsFlushSummary(
                    retained_batches=len(self.spool.pending()),
                    skipped_backoff=True,
                    retry_after_seconds=self._next_attempt_at - now,
                )

        try:
            transport = self._resolve_transport()
        except (AnalyticsConfigError, ValueError):
            return AnalyticsFlushSummary(
                retained_batches=len(self.spool.pending()),
                skipped_unconfigured=True,
            )
        if transport is None:
            return AnalyticsFlushSummary(
                retained_batches=len(self.spool.pending()),
                skipped_unconfigured=True,
            )

        pending = self.spool.pending()
        total_initial = len(pending)
        if not pending:
            return AnalyticsFlushSummary()

        deadline = now + budget
        sent_batches = 0
        sent_events = 0
        dropped_batches = 0
        attempted_batches = 0

        accepts_cancel = _accepts_is_cancelled(transport.send_batch)
        is_cancelled = lambda: not self.consent_gate.is_enabled_at(start_epoch)  # noqa: E731

        for queued in pending:
            if not self.consent_gate.is_enabled_at(start_epoch):
                return AnalyticsFlushSummary(
                    sent_batches=sent_batches,
                    sent_events=sent_events,
                    dropped_batches=dropped_batches,
                    retained_batches=0,
                    attempted_batches=attempted_batches,
                    skipped_consent=True,
                )

            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return AnalyticsFlushSummary(
                    sent_batches=sent_batches,
                    sent_events=sent_events,
                    dropped_batches=dropped_batches,
                    retained_batches=max(
                        total_initial - sent_batches - dropped_batches, 0
                    ),
                    attempted_batches=attempted_batches,
                    deadline_exhausted=True,
                )

            # Socket I/O executes OUTSIDE consent_gate._lock so UI opt-out never blocks.
            call_timeout = min(remaining, MAX_DELIVERY_TIMEOUT_SECONDS)
            if accepts_cancel:
                outcome = transport.send_batch(
                    queued.batch.events,
                    timeout_seconds=call_timeout,
                    is_cancelled=is_cancelled,
                )
            else:
                outcome = transport.send_batch(
                    queued.batch.events,
                    timeout_seconds=call_timeout,
                )
            if outcome.reason != "cancelled":
                attempted_batches += 1

            if not self.consent_gate.is_enabled_at(start_epoch):
                return AnalyticsFlushSummary(
                    sent_batches=sent_batches,
                    sent_events=sent_events,
                    dropped_batches=dropped_batches,
                    retained_batches=0,
                    attempted_batches=attempted_batches,
                    skipped_consent=True,
                )

            if outcome.confirmed:
                self.spool.acknowledge(queued.batch.batch_id)
                sent_batches += 1
                sent_events += len(queued.batch.events)
                with self._state_lock:
                    self._failure_count = 0
                    self._next_attempt_at = 0.0
                continue

            if outcome.drop_batch:
                self.spool.drop_poison(queued.batch.batch_id)
                dropped_batches += 1
                continue

            if outcome.disable_runtime:
                with self._state_lock:
                    self._disabled_for_process = True
                if self._on_permanent_disable is not None:
                    self._on_permanent_disable()
                return AnalyticsFlushSummary(
                    sent_batches=sent_batches,
                    sent_events=sent_events,
                    dropped_batches=dropped_batches,
                    retained_batches=max(
                        total_initial - sent_batches - dropped_batches, 0
                    ),
                    attempted_batches=attempted_batches,
                    disabled_invalid_key=True,
                )

            with self._state_lock:
                self._failure_count += 1
                delay = self._compute_backoff_delay(outcome.retry_after_seconds)
                self._next_attempt_at = self.monotonic() + delay

            return AnalyticsFlushSummary(
                sent_batches=sent_batches,
                sent_events=sent_events,
                dropped_batches=dropped_batches,
                retained_batches=max(
                    total_initial - sent_batches - dropped_batches, 0
                ),
                attempted_batches=attempted_batches,
                retry_after_seconds=delay,
            )

        return AnalyticsFlushSummary(
            sent_batches=sent_batches,
            sent_events=sent_events,
            dropped_batches=dropped_batches,
            retained_batches=max(total_initial - sent_batches - dropped_batches, 0),
            attempted_batches=attempted_batches,
        )


__all__ = [
    "AnalyticsConsentEpochGate",
    "AnalyticsConsentGate",
    "AnalyticsDeliveryWorker",
    "AnalyticsFlushSummary",
    "BatchDeliveryOutcome",
    "MAX_DELIVERY_TIMEOUT_SECONDS",
    "MAX_RESPONSE_BODY_BYTES",
    "MAX_RETRY_AFTER_SECONDS",
    "MIN_RETRY_AFTER_SECONDS",
    "PostHogHttpTransport",
]
