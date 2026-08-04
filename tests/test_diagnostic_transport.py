"""Consent, privacy, acknowledgement, and retry tests for diagnostics delivery."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import UUID

import pytest

from diagnostics.models import AppPhase, CrashConsent, DiagnosticFrame, DiagnosticReport
from diagnostics.spool import DiagnosticSpool
from diagnostics.transport import (
    DeliveryOutcome,
    DiagnosticDeliveryWorker,
    SentryDiagnosticTransport,
    _open_diagnostic_request,
    _strict_before_send,
)

VALID_DSN = "https://0123456789abcdef@o123.ingest.sentry.io/456"
NOW = datetime(2026, 8, 4, 5, 0, tzinfo=timezone.utc)


def _report() -> DiagnosticReport:
    return DiagnosticReport(
        schema_version=1,
        event_id=UUID("00000000-0000-0000-0000-000000000123"),
        fingerprint="a" * 64,
        occurred_at=NOW,
        app_version="2.2.0",
        release_channel="stable",
        install_type="msi",
        os_family="Windows",
        os_version="11",
        architecture="AMD64",
        python_version="3.13.5",
        flet_version="0.86.5",
        exception_type="ValueError",
        frames=(
            DiagnosticFrame(
                module="gui.app_controller",
                function="load",
                relative_path="gui/app_controller.py",
                line=10,
            ),
        ),
        phase=AppPhase.GUI,
        window_state="tray",
        unclean_previous_exit=False,
    )


def _worker_with_one_report(tmp_path: Path, **kwargs):
    spool = DiagnosticSpool(tmp_path / "spool", clock=lambda: NOW)
    assert spool.enqueue(_report()).stored
    return spool, DiagnosticDeliveryWorker(spool, dsn=VALID_DSN, **kwargs)


@pytest.mark.parametrize("consent", [CrashConsent.NOT_ASKED, CrashConsent.DISABLED])
def test_delivery_never_constructs_sentry_before_opt_in(
    tmp_path, consent, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        "diagnostics.transport._new_sentry_client",
        lambda **_: calls.append(True),
    )
    spool, worker = _worker_with_one_report(tmp_path)

    summary = worker.flush_once(consent)

    assert summary.skipped_consent
    assert calls == []
    assert len(spool.pending()) == (0 if consent is CrashConsent.DISABLED else 1)


def test_enabled_without_dsn_constructs_no_client_or_network(tmp_path, monkeypatch):
    calls = []
    spool = DiagnosticSpool(tmp_path / "spool", clock=lambda: NOW)
    assert spool.enqueue(_report()).stored
    worker = DiagnosticDeliveryWorker(spool, dsn=None)
    monkeypatch.setattr(
        "diagnostics.transport._new_sentry_client",
        lambda **_: calls.append(True),
    )

    summary = worker.flush_once(CrashConsent.ENABLED)

    assert summary.skipped_unconfigured
    assert calls == []
    assert len(spool.pending()) == 1


def test_strict_before_send_reconstructs_only_allowlisted_event():
    report = _report()
    unsafe = {
        "event_id": "attacker",
        "message": "raw secret",
        "user": {"email": "student@example.com"},
        "request": {"url": "https://moodle.invalid"},
        "contexts": {"device": {"name": "laptop"}},
        "breadcrumbs": [{"message": "secret"}],
        "modules": {"secret": "1"},
        "extra": {"raw": "secret"},
    }

    event = _strict_before_send(unsafe, {"report": report})

    assert set(event) == {
        "event_id",
        "timestamp",
        "release",
        "environment",
        "platform",
        "level",
        "logger",
        "fingerprint",
        "exception",
        "tags",
    }
    serialized = repr(event)
    assert "student@example.com" not in serialized
    assert "moodle.invalid" not in serialized
    assert "raw secret" not in serialized
    assert event["event_id"] == report.event_id.hex
    assert event["fingerprint"] == [report.fingerprint]


class _FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.events = []
        self.timeouts = []

    def send_report(self, report, *, timeout_seconds):
        self.events.append(_strict_before_send({}, {"report": report}))
        self.timeouts.append(timeout_seconds)
        return self.outcomes.pop(0)


def _install_fake_client(monkeypatch, outcomes):
    client = _FakeClient(outcomes)
    init_calls = []

    def build(**kwargs):
        init_calls.append(kwargs)
        return client

    monkeypatch.setattr("diagnostics.transport._new_sentry_client", build)
    return client, init_calls


def test_confirmed_success_is_the_only_outcome_acknowledged(tmp_path, monkeypatch):
    client, init_calls = _install_fake_client(
        monkeypatch,
        [DeliveryOutcome.confirmed_success(status_code=200)],
    )
    spool, worker = _worker_with_one_report(tmp_path)

    summary = worker.flush_once(CrashConsent.ENABLED)

    assert summary.sent == 1
    assert summary.retained == 0
    assert spool.pending() == ()
    assert len(client.events) == 1
    assert init_calls[0]["default_integrations"] is False
    assert init_calls[0]["send_default_pii"] is False
    assert init_calls[0]["max_breadcrumbs"] == 0
    assert init_calls[0]["traces_sample_rate"] == 0.0


@pytest.mark.parametrize(
    "outcome",
    (
        DeliveryOutcome.retryable_failure("offline"),
        DeliveryOutcome.retryable_failure("timeout"),
        DeliveryOutcome.retryable_failure("rate_limited", status_code=429, retry_after=3),
        DeliveryOutcome.retryable_failure("server_error", status_code=503),
    ),
)
def test_retryable_failures_are_retained_without_busy_retry(
    tmp_path, monkeypatch, outcome
):
    client, _ = _install_fake_client(monkeypatch, [outcome])
    spool, worker = _worker_with_one_report(tmp_path)

    summary = worker.flush_once(CrashConsent.ENABLED, deadline_seconds=0.2)

    assert summary.sent == 0
    assert summary.retained == 1
    assert summary.retry_after_seconds > 0
    assert len(spool.pending()) == 1
    assert len(client.events) == 1
    assert 0 < client.timeouts[0] <= 0.2


def test_retry_backoff_blocks_immediate_second_network_attempt(tmp_path, monkeypatch):
    client, init_calls = _install_fake_client(
        monkeypatch,
        [
            DeliveryOutcome.retryable_failure("offline"),
            DeliveryOutcome.confirmed_success(status_code=200),
        ],
    )
    now = [100.0]
    spool, worker = _worker_with_one_report(tmp_path, monotonic=lambda: now[0])

    first = worker.flush_once(CrashConsent.ENABLED)
    now[0] += 0.5
    backed_off = worker.flush_once(CrashConsent.ENABLED)
    now[0] += 0.6
    recovered = worker.flush_once(CrashConsent.ENABLED)

    assert first.retry_after_seconds == 1
    assert backed_off.skipped_backoff
    assert backed_off.retry_after_seconds == pytest.approx(0.5)
    assert recovered.sent == 1
    assert len(client.events) == 2
    assert len(init_calls) == 2
    assert spool.pending() == ()


def test_transport_rejects_unvalidated_report_without_constructing_sdk(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "diagnostics.transport._new_sentry_client",
        lambda **_: calls.append(True),
    )
    transport = SentryDiagnosticTransport(VALID_DSN)

    with pytest.raises(TypeError, match="DiagnosticReport"):
        transport.send({"exception_type": "ValueError"})

    assert calls == []


class _HttpResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status


def test_real_sdk_adapter_confirms_http_and_envelope_is_strict(monkeypatch):
    captured = {}

    def successful_post(request, *, timeout_seconds, context):
        captured.update(
            request=request,
            timeout=timeout_seconds,
            context=context,
        )
        return _HttpResponse(200)

    monkeypatch.setattr(
        "diagnostics.transport._open_diagnostic_request",
        successful_post,
    )

    outcome = SentryDiagnosticTransport(VALID_DSN).send(
        _report(),
        timeout_seconds=0.25,
    )

    assert outcome.confirmed
    assert outcome.status_code == 200
    assert captured["timeout"] == pytest.approx(0.25)
    request = captured["request"]
    assert request.full_url.endswith("/api/456/envelope/")
    assert request.get_header("X-sentry-auth").startswith("Sentry sentry_key=")
    event = json.loads(request.data.splitlines()[-1])
    assert set(event) == {
        "event_id",
        "timestamp",
        "release",
        "environment",
        "platform",
        "level",
        "logger",
        "fingerprint",
        "exception",
        "tags",
    }
    assert "user" not in event
    assert "request" not in event
    assert "contexts" not in event
    assert "breadcrumbs" not in event
    assert "modules" not in event


@pytest.mark.parametrize(
    ("failure", "reason", "status"),
    (
        (
            HTTPError(
                VALID_DSN,
                429,
                "rate limited",
                Message(),
                None,
            ),
            "rate_limited",
            429,
        ),
        (HTTPError(VALID_DSN, 503, "unavailable", Message(), None), "server_error", 503),
        (socket.timeout(), "timeout", None),
        (URLError("offline"), "offline", None),
    ),
)
def test_real_sdk_adapter_retains_http_and_network_failures(
    monkeypatch, failure, reason, status
):
    if isinstance(failure, HTTPError) and failure.code == 429:
        failure.headers["Retry-After"] = "3"

    def failed_post(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(
        "diagnostics.transport._open_diagnostic_request",
        failed_post,
    )

    outcome = SentryDiagnosticTransport(VALID_DSN).send(_report())

    assert not outcome.confirmed
    assert outcome.retryable
    assert outcome.reason == reason
    assert outcome.status_code == status
    if status == 429:
        assert outcome.retry_after_seconds == 3


def test_real_sdk_adapter_treats_redirect_as_retained_rejection(monkeypatch):
    headers = Message()
    headers["Location"] = "https://other-origin.invalid/capture"

    def redirect(*_args, **_kwargs):
        raise HTTPError(VALID_DSN, 302, "redirect", headers, None)

    monkeypatch.setattr(
        "diagnostics.transport._open_diagnostic_request",
        redirect,
    )

    outcome = SentryDiagnosticTransport(VALID_DSN).send(_report())

    assert not outcome.confirmed
    assert not outcome.retryable
    assert outcome.reason == "http_rejected"
    assert outcome.status_code == 302


def test_non_finite_retry_after_is_ignored():
    outcome = DeliveryOutcome.retryable_failure(
        "rate_limited",
        status_code=429,
        retry_after=float("nan"),
    )

    assert outcome.retry_after_seconds is None


def test_diagnostic_post_never_follows_redirect_to_second_origin():
    first_requests = []
    second_requests = []

    class SecondOrigin(BaseHTTPRequestHandler):
        def do_GET(self):
            second_requests.append(("GET", dict(self.headers), b""))
            self.send_response(204)
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            second_requests.append(
                ("POST", dict(self.headers), self.rfile.read(length))
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            return

    second = HTTPServer(("127.0.0.1", 0), SecondOrigin)
    second_url = f"http://127.0.0.1:{second.server_port}/capture"

    class RedirectingOrigin(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            first_requests.append((dict(self.headers), self.rfile.read(length)))
            self.send_response(302)
            self.send_header("Location", second_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            return

    first = HTTPServer(("127.0.0.1", 0), RedirectingOrigin)
    servers = (first, second)
    threads = [
        threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.02},
            daemon=True,
        )
        for server in servers
    ]
    for thread in threads:
        thread.start()

    envelope = b'{"event":"allowlisted"}'
    request = Request(
        f"http://127.0.0.1:{first.server_port}/envelope",
        data=envelope,
        headers={"X-Sentry-Auth": "Sentry sentry_key=public"},
        method="POST",
    )
    try:
        with pytest.raises(HTTPError) as captured:
            _open_diagnostic_request(
                request,
                timeout_seconds=0.5,
                context=ssl.create_default_context(),
            )
        assert captured.value.code == 302
        time.sleep(0.05)
        assert len(first_requests) == 1
        assert first_requests[0][1] == envelope
        assert second_requests == []
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=1.0)
            assert not thread.is_alive()


def test_diagnostic_socket_timeout_is_synchronous_and_bounded():
    class SlowOrigin(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            time.sleep(0.25)
            try:
                self.send_response(204)
                self.end_headers()
            except OSError:
                pass

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), SlowOrigin)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.02},
        daemon=True,
    )
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_port}/slow",
        data=b"allowlisted-envelope",
        method="POST",
    )
    started = time.monotonic()
    try:
        with pytest.raises((TimeoutError, socket.timeout, URLError)):
            _open_diagnostic_request(
                request,
                timeout_seconds=0.1,
                context=ssl.create_default_context(),
            )
        assert time.monotonic() - started < 0.8
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)
        assert not thread.is_alive()


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_delivery_deadline_is_rejected_before_sdk(
    tmp_path, monkeypatch, deadline
):
    calls = []
    monkeypatch.setattr(
        "diagnostics.transport._new_sentry_client",
        lambda **_: calls.append(True),
    )
    _spool, worker = _worker_with_one_report(tmp_path)

    with pytest.raises(ValueError, match="finite"):
        worker.flush_once(CrashConsent.ENABLED, deadline_seconds=deadline)

    assert calls == []


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_transport_timeout_is_rejected_before_sdk(monkeypatch, timeout):
    calls = []
    monkeypatch.setattr(
        "diagnostics.transport._new_sentry_client",
        lambda **_: calls.append(True),
    )
    transport = SentryDiagnosticTransport(VALID_DSN)

    with pytest.raises(ValueError, match="finite"):
        transport.send(_report(), timeout_seconds=timeout)

    assert calls == []


def test_deadline_is_clamped_and_expired_deadline_does_not_construct_client(
    tmp_path, monkeypatch
):
    client, init_calls = _install_fake_client(
        monkeypatch,
        [DeliveryOutcome.confirmed_success(status_code=200)],
    )
    ticks = iter((100.0, 100.2))
    spool, worker = _worker_with_one_report(
        tmp_path,
        monotonic=lambda: next(ticks),
    )

    summary = worker.flush_once(CrashConsent.ENABLED, deadline_seconds=-10)

    assert summary.deadline_exhausted
    assert len(spool.pending()) == 1
    assert client.events == []
    assert init_calls == []
