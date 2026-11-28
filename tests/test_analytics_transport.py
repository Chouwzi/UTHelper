"""Unit tests for PostHog /batch/ wire payload, captive portals, retries, and epoch gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.message import Message
from email.utils import format_datetime
import http.client
import io
import json
from pathlib import Path
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest

from analytics.models import (
    AnalyticsConfig,
    AnalyticsConsent,
    AnalyticsEvent,
    DeviceClass,
    DistributionChannelName,
    EventName,
    OsFamily,
    PackageFormatName,
    ReleaseChannel,
)
from analytics.spool import AnalyticsSpool
from analytics.transport import (
    AnalyticsConsentEpochGate,
    AnalyticsDeliveryWorker,
    BatchDeliveryOutcome,
    PostHogHttpTransport,
)
from analytics.uuid7 import datetime_to_unix_ms, format_utc_ms, uuid7_from_ms

VALID_KEY = "phc_0123456789abcdefghijKLMNOPQRST"
VALID_HOST = "https://us.i.posthog.com"
NOW = datetime(2026, 11, 28, 9, 0, 0, tzinfo=UTC)
SESSION_ID = uuid7_from_ms(datetime_to_unix_ms(NOW))
DISTINCT_ID = str(uuid4())

CFG = AnalyticsConfig(
    api_key=VALID_KEY,
    host=VALID_HOST,
    app_version="2.7.0",
    release_channel=ReleaseChannel.RELEASE,
    distribution_channel=DistributionChannelName.MSSTORE,
    package_format=PackageFormatName.MSIX,
    os_family=OsFamily.WINDOWS,
    device_class=DeviceClass.DESKTOP,
)


class _FakeResponse:
    def __init__(self, status: int, content_type: str, body: bytes) -> None:
        self._status = status
        self.headers = {"Content-Type": content_type}
        self._stream = io.BytesIO(body)

    def getcode(self) -> int:
        return self._status

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None


def _http_error(
    status: int,
    *,
    content_type: str = "application/json",
    body: bytes = b'{"error":"bad"}',
    retry_after: str | None = None,
) -> HTTPError:
    headers = Message()
    headers["Content-Type"] = content_type
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        url=f"{VALID_HOST}/batch/",
        code=status,
        msg="HTTP Error",
        hdrs=headers,
        fp=io.BytesIO(body),
    )


def _sample_event(idx: int = 0) -> AnalyticsEvent:
    ts = format_utc_ms(datetime_to_unix_ms(NOW + timedelta(seconds=idx)))
    return AnalyticsEvent.build(
        event=EventName.SCREEN_VIEWED,
        event_properties={"screen": "courses", "nav_source": "rail"},
        distinct_id=DISTINCT_ID,
        session_id=SESSION_ID,
        timestamp=ts,
        config=CFG,
    )


def test_posthog_wire_payload_omits_sent_at_and_keeps_api_key_in_body_only():
    captured_requests = []

    def fake_opener(request, *, timeout_seconds, context):
        captured_requests.append((request, timeout_seconds, context))
        return _FakeResponse(200, "application/json; charset=utf-8", b'{"status":"Ok"}')

    transport = PostHogHttpTransport(
        api_key=VALID_KEY,
        host="https://eu.i.posthog.com/",
        app_version="2.7.0",
        opener_func=fake_opener,
    )
    ev = _sample_event()
    outcome = transport.send_batch([ev], timeout_seconds=5.0)

    assert outcome.confirmed is True
    assert len(captured_requests) == 1
    req, timeout_sec, ssl_ctx = captured_requests[0]
    assert req.full_url == "https://eu.i.posthog.com/batch/"
    assert VALID_KEY not in req.full_url
    assert timeout_sec == 5.0
    assert ssl_ctx is not None

    headers_lower = {k.lower(): v for k, v in req.header_items()}
    assert "authorization" not in headers_lower
    assert headers_lower["content-type"] == "application/json; charset=utf-8"
    assert headers_lower["accept"] == "application/json"
    assert headers_lower["user-agent"] == "UTHelper-Analytics/2.7.0"

    wire_payload = json.loads(req.data.decode("utf-8"))
    assert set(wire_payload.keys()) == {"api_key", "batch"}
    assert "sent_at" not in wire_payload
    assert wire_payload["api_key"] == VALID_KEY
    assert len(wire_payload["batch"]) == 1

    wire_ev = wire_payload["batch"][0]
    assert wire_ev["uuid"] == ev.uuid
    assert wire_ev["distinct_id"] == DISTINCT_ID
    assert wire_ev["timestamp"] == ev.timestamp
    props = wire_ev["properties"]
    assert props["$session_id"] == SESSION_ID
    assert props["$process_person_profile"] is False
    assert props["$geoip_disable"] is True
    assert "$ip" in props and props["$ip"] is None


@pytest.mark.parametrize("ok_body", [b'{"status":1}', b'{"status":"Ok"}', b'{"status":"ok"}'])
def test_transport_confirms_valid_posthog_200_json_statuses(ok_body: bytes):
    transport = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: _FakeResponse(200, "application/json", ok_body),
    )
    outcome = transport.send_batch([_sample_event()])
    assert outcome.confirmed is True


@pytest.mark.parametrize(
    "status,ctype,body,expected_reason",
    [
        (200, "text/html; charset=utf-8", b"<html><body>Campus Wi-Fi Login</body></html>", "captive_portal"),
        (200, "application/xhtml+xml", b"<html/>", "captive_portal"),
        (200, "text/plain", b"Ok", "unexpected_content_type"),
        (200, "application/json", b"x" * 5000, "response_too_large"),
        (200, "application/json", b"{not-json", "invalid_json_response"),
        (200, "application/json", b"[1]", "invalid_json_response"),
        # Guard against Python bool(True) == 1 trap!
        (200, "application/json", b'{"status":true}', "unconfirmed_status"),
        (200, "application/json", b'{"status":0}', "unconfirmed_status"),
        (204, "application/json", b"", "unexpected_status"),
    ],
)
def test_transport_rejects_captive_portal_and_unconfirmed_2xx_responses(
    status: int,
    ctype: str,
    body: bytes,
    expected_reason: str,
):
    transport = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: _FakeResponse(status, ctype, body),
    )
    outcome = transport.send_batch([_sample_event()])
    assert outcome.confirmed is False
    assert outcome.retryable is True
    assert outcome.reason == expected_reason


def test_transport_error_classifications_redirect_429_poison_401_5xx_and_network():
    # 302 redirect blocked by _RejectAnalyticsRedirects -> retained & retryable
    t_302 = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: (_ for _ in ()).throw(
            _http_error(302, content_type="text/html", body=b"redirect")
        ),
    )
    out_302 = t_302.send_batch([_sample_event()])
    assert out_302.confirmed is False and out_302.retryable is True
    assert out_302.reason == "redirect_rejected"

    # 429 Retry-After clamped into [1s, 300s]
    t_429_high = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: (_ for _ in ()).throw(
            _http_error(429, retry_after="9999")
        ),
    )
    out_429_high = t_429_high.send_batch([_sample_event()])
    assert out_429_high.retryable is True
    assert out_429_high.retry_after_seconds == 300.0

    future_http_date = format_datetime(datetime.now(UTC) + timedelta(seconds=0.1), usegmt=True)
    t_429_low = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: (_ for _ in ()).throw(
            _http_error(429, retry_after=future_http_date)
        ),
    )
    out_429_low = t_429_low.send_batch([_sample_event()])
    assert out_429_low.retry_after_seconds == 1.0

    # 400 and 413 JSON -> drop poison batch
    for code in (400, 413):
        t_poison = PostHogHttpTransport(
            api_key=VALID_KEY,
            host=VALID_HOST,
            opener_func=lambda *_a, c=code, **_kw: (_ for _ in ()).throw(_http_error(c)),
        )
        out_poison = t_poison.send_batch([_sample_event()])
        assert out_poison.drop_batch is True
        assert out_poison.confirmed is False

    # 401 JSON -> disable runtime; 401 HTML (campus proxy) -> retryable!
    t_401_json = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: (_ for _ in ()).throw(
            _http_error(401, content_type="application/json", body=b'{"detail":"Invalid token"}')
        ),
    )
    assert t_401_json.send_batch([_sample_event()]).disable_runtime is True

    t_401_html = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: (_ for _ in ()).throw(
            _http_error(401, content_type="text/html", body=b"<html>Proxy Auth</html>")
        ),
    )
    out_401_html = t_401_html.send_batch([_sample_event()])
    assert out_401_html.disable_runtime is False
    assert out_401_html.retryable is True

    # Timeout and URLError
    t_timeout = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: (_ for _ in ()).throw(socket.timeout("timed out")),
    )
    assert t_timeout.send_batch([_sample_event()]).reason == "timeout"

    t_offline = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: (_ for _ in ()).throw(URLError("offline")),
    )
    assert t_offline.send_batch([_sample_event()]).reason == "offline"


def test_delivery_worker_drops_poison_400_and_delivers_next_batch_and_disables_on_401(
    tmp_path: Path,
):
    spool = AnalyticsSpool(tmp_path / "pending", clock=lambda: NOW)
    spool.enqueue([_sample_event(1)], fsync=False)
    spool.enqueue([_sample_event(2)], fsync=False)
    assert len(spool.pending()) == 2

    outcomes = iter(
        [
            BatchDeliveryOutcome.poison_batch("bad_request", status_code=400),
            BatchDeliveryOutcome.confirmed_success(status_code=200),
        ]
    )

    class StubTransport:
        def send_batch(self, events, *, timeout_seconds):
            return next(outcomes)

    worker = AnalyticsDeliveryWorker(
        spool,
        api_key=VALID_KEY,
        host=VALID_HOST,
        rng=lambda: 0.5,
        transport_factory=lambda **_kw: StubTransport(),
    )
    summary = worker.flush_once()
    assert summary.dropped_batches == 1
    assert summary.sent_batches == 1
    assert summary.retained_batches == 0
    assert spool.pending() == ()

    # Now enqueue another batch and return 401 invalid_api_key
    spool.enqueue([_sample_event(3)], fsync=False)
    disabled_called = []
    worker_401 = AnalyticsDeliveryWorker(
        spool,
        api_key=VALID_KEY,
        host=VALID_HOST,
        on_permanent_disable=lambda: disabled_called.append(True),
        transport_factory=lambda **_kw: type(
            "T",
            (),
            {
                "send_batch": lambda self, evs, *, timeout_seconds: BatchDeliveryOutcome.permanent_disable(
                    "invalid_api_key", status_code=401
                )
            },
        )(),
    )
    sum_401 = worker_401.flush_once()
    assert sum_401.disabled_invalid_key is True
    assert worker_401.disabled_for_process is True
    assert disabled_called == [True]
    # Subsequent flush attempts immediately skip without hitting transport.
    assert worker_401.flush_once().disabled_invalid_key is True


def test_consent_epoch_gate_purges_in_0ms_without_blocking_on_inflight_socket(
    tmp_path: Path,
):
    spool = AnalyticsSpool(tmp_path / "pending", clock=lambda: NOW)
    spool.enqueue([_sample_event(1)], fsync=False)
    spool.enqueue([_sample_event(2)], fsync=False)
    gate = AnalyticsConsentEpochGate(AnalyticsConsent.ENABLED)

    socket_started = threading.Event()
    release_socket = threading.Event()
    send_count = 0

    class SlowSocketTransport:
        def send_batch(self, events, *, timeout_seconds):
            nonlocal send_count
            send_count += 1
            socket_started.set()
            assert release_socket.wait(timeout=3.0)
            return BatchDeliveryOutcome.confirmed_success(status_code=200)

    worker = AnalyticsDeliveryWorker(
        spool,
        api_key=VALID_KEY,
        host=VALID_HOST,
        consent_gate=gate,
        transport_factory=lambda **_kw: SlowSocketTransport(),
    )
    t = threading.Thread(target=worker.flush_once, daemon=True)
    t.start()
    assert socket_started.wait(timeout=2.0)

    # Revoke consent on UI thread while worker is blocked inside SlowSocketTransport!
    t0 = time.perf_counter()
    gate.set_consent(AnalyticsConsent.DISABLED, on_revoke=spool.clear)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 200.0
    assert spool.pending() == ()

    release_socket.set()
    t.join(timeout=2.0)
    # Batch #2 was NEVER sent because epoch changed mid-flight!
    assert send_count == 1


def test_consent_epoch_gate_noop_same_consent_does_not_bump_epoch(tmp_path: Path):
    gate = AnalyticsConsentEpochGate(AnalyticsConsent.ENABLED)
    assert gate.epoch == 0
    assert gate.set_consent(AnalyticsConsent.ENABLED) == 0
    assert gate.set_consent("enabled") == 0
    assert gate.epoch == 0
    assert gate.is_enabled_at(0) is True

    # Mid-flight no-op set_consent("enabled") must NOT abort acknowledge()!
    spool = AnalyticsSpool(tmp_path / "pending", clock=lambda: NOW)
    spool.enqueue([_sample_event(1)], fsync=False)

    def _opener(request, *, timeout_seconds, context):
        gate.set_consent("enabled")
        return _FakeResponse(200, "application/json", b'{"status":"Ok"}')

    worker = AnalyticsDeliveryWorker(
        spool,
        api_key=VALID_KEY,
        host=VALID_HOST,
        consent_gate=gate,
        transport_factory=lambda **kw: PostHogHttpTransport(**kw, opener_func=_opener),
    )
    summary = worker.flush_once()
    assert summary.sent_batches == 1
    assert summary.skipped_consent is False
    assert spool.pending() == ()

    # Real state transitions DO bump epoch once per transition
    assert gate.set_consent(AnalyticsConsent.DISABLED) == 1
    assert gate.set_consent(AnalyticsConsent.DISABLED) == 1
    assert gate.set_consent(AnalyticsConsent.ENABLED) == 2


def test_transport_aborts_before_opener_when_is_cancelled_and_worker_passes_epoch_cancel(
    tmp_path: Path,
):
    opener_calls = 0

    def _never_open(request, *, timeout_seconds, context):
        nonlocal opener_calls
        opener_calls += 1
        return _FakeResponse(200, "application/json", b'{"status":"Ok"}')

    transport = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=_never_open,
    )
    cancelled_out = transport.send_batch([_sample_event()], is_cancelled=lambda: True)
    assert cancelled_out.confirmed is False
    assert opener_calls == 0

    # Verify AnalyticsDeliveryWorker passes epoch cancellation into PostHogHttpTransport
    spool = AnalyticsSpool(tmp_path / "pending_cancel", clock=lambda: NOW)
    spool.enqueue([_sample_event(1)], fsync=False)
    gate = AnalyticsConsentEpochGate(AnalyticsConsent.ENABLED)

    class _RevokeDuringPayloadTransport(PostHogHttpTransport):
        def build_batch_payload(self, events):
            payload = super().build_batch_payload(events)
            gate.set_consent(AnalyticsConsent.DISABLED, on_revoke=spool.clear)
            return payload

    worker = AnalyticsDeliveryWorker(
        spool,
        api_key=VALID_KEY,
        host=VALID_HOST,
        consent_gate=gate,
        transport_factory=lambda **kw: _RevokeDuringPayloadTransport(
            **kw, opener_func=_never_open
        ),
    )
    summary = worker.flush_once()
    assert opener_calls == 0
    assert summary.skipped_consent is True
    assert summary.sent_batches == 0


@pytest.mark.parametrize(
    "exc",
    [
        http.client.BadStatusLine("garbage"),
        http.client.IncompleteRead(b"partial", 16),
        ValueError("malformed header value"),
    ],
)
def test_transport_classifies_http_exception_and_value_error_as_retryable_http_protocol_error(
    tmp_path: Path,
    exc: Exception,
):
    transport = PostHogHttpTransport(
        api_key=VALID_KEY,
        host=VALID_HOST,
        opener_func=lambda *_a, **_kw: (_ for _ in ()).throw(exc),
    )
    outcome = transport.send_batch([_sample_event()])
    assert outcome.confirmed is False
    assert outcome.retryable is True
    assert outcome.reason == "http_protocol_error"

    spool = AnalyticsSpool(tmp_path / f"pending_{type(exc).__name__}", clock=lambda: NOW)
    spool.enqueue([_sample_event(1)], fsync=False)
    worker = AnalyticsDeliveryWorker(
        spool,
        api_key=VALID_KEY,
        host=VALID_HOST,
        rng=lambda: 0.0,
        transport_factory=lambda **_kw: transport,
    )
    summary = worker.flush_once()
    assert summary.sent_batches == 0
    assert summary.retained_batches == 1
    assert summary.retry_after_seconds >= 1.0

