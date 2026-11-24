"""Reusable opaque-box test harnesses and synthetic artifact builders for E2E tests.

All M1-M4 target imports are deferred into helper calls via ``load_module`` so
pytest collection never fails while implementation milestones develop in parallel.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

# ---------------------------------------------------------------------------
# Constants & Lazy Module Loading
# ---------------------------------------------------------------------------

WORKTREE_ROOT: Path = Path(__file__).resolve().parents[2]
PYTHON_EXE: str = sys.executable

DEFAULT_GITHUB_OWNER = "Chouwzi"
DEFAULT_GITHUB_REPO = "UTHelper"
DEFAULT_ANDROID_PACKAGE_ID = "com.uthelper.uthelper"
DEFAULT_TRUSTED_SIGNER_SHA256 = (
    "7E3547EE6A31325A47BE22049E238BA83CA1D90AFB8A30D053060D02678A0B3C"
)
DEFAULT_SIGNER_SUBJECT = "CN=UTHelper"
DEFAULT_MSI_UPGRADE_CODE = "{B1EB1032-5ACD-497D-8FD2-AB760218CBE3}"
_MSI_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")

_APPROVED_GITHUB_HOSTS = frozenset(
    {"api.github.com", "github.com", "objects.githubusercontent.com"}
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_CSC_CANDIDATES = (
    Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
)


def _ensure_worktree_sys_path() -> None:
    desired = [
        str(WORKTREE_ROOT / "src"),
        str(WORKTREE_ROOT / "extensions" / "flet_uth_background_sync" / "src"),
        str(WORKTREE_ROOT),
    ]
    for entry in reversed(desired):
        if entry not in sys.path:
            sys.path.insert(0, entry)


def load_module(dotted_name: str) -> Any:
    """Import ``dotted_name`` lazily, raising ``AssertionError`` if unavailable.

    Never skips missing milestone modules: E2E tests must fail clearly rather than
    silently pass/skip when a required contract module has not been implemented.
    """
    _ensure_worktree_sys_path()
    try:
        return importlib.import_module(dotted_name)
    except (ImportError, ModuleNotFoundError) as exc:
        raise AssertionError(
            f"Required module '{dotted_name}' is not importable from worktree "
            f"'{WORKTREE_ROOT}': {exc}"
        ) from exc


def sha256_hex(data: bytes) -> str:
    """Return lowercase hexadecimal SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def run_isolated_python(
    code: str,
    *,
    env_overrides: Mapping[str, str | None] | None = None,
    tmp_path: Path | None = None,
    timeout: float = 30.0,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute Python code in an isolated subprocess inside the worktree.

    Configures test mode, worktree imports, isolated runtime paths, and a
    no-op keyring backend. Keys mapped to ``None`` in ``env_overrides`` are
    explicitly removed from the child environment.
    """
    pythonpath = os.pathsep.join(
        [
            str(WORKTREE_ROOT / "src"),
            str(WORKTREE_ROOT / "extensions" / "flet_uth_background_sync" / "src"),
            str(WORKTREE_ROOT),
        ]
    )

    def _run_with_base(base_dir: Path) -> subprocess.CompletedProcess[str]:
        appdata = base_dir / "appdata"
        flet_data = base_dir / "flet_data"
        flet_temp = base_dir / "flet_temp"
        assets = base_dir / "assets"
        runtime_temp = base_dir / "tmp"
        for folder in (appdata, flet_data, flet_temp, assets, runtime_temp):
            folder.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["UTH_TEST_MODE"] = "1"
        env["PYTHONPATH"] = pythonpath
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
        env["APPDATA"] = str(appdata)
        env["TMPDIR"] = str(runtime_temp)
        env["FLET_APP_STORAGE_DATA"] = str(flet_data)
        env["FLET_APP_STORAGE_TEMP"] = str(flet_temp)
        env["FLET_ASSETS_DIR"] = str(assets)
        env.pop("FLET_APP_DATA", None)

        if env_overrides:
            for key, value in env_overrides.items():
                if value is None:
                    env.pop(key, None)
                else:
                    env[key] = value

        return subprocess.run(
            [PYTHON_EXE, "-c", code],
            cwd=str(cwd or WORKTREE_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )

    if tmp_path is not None:
        return _run_with_base(tmp_path)
    with tempfile.TemporaryDirectory(prefix="uth_e2e_py_") as temp_dir:
        return _run_with_base(Path(temp_dir))


def stage_distribution_channel_asset(
    assets_dir: Path,
    channel: str,
    *,
    store_product_id: str | None = None,
    android_package_id: str = DEFAULT_ANDROID_PACKAGE_ID,
) -> Path:
    """Write ``distribution-channel.json`` matching ``PROJECT.md`` interface contract."""
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "channel": channel,
        "android_package_id": android_package_id,
    }
    if store_product_id is not None:
        payload["store_product_id"] = store_product_id
    target = assets_dir / "distribution-channel.json"
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


# ---------------------------------------------------------------------------
# Local Release HTTP Server & urllib GitHub Router
# ---------------------------------------------------------------------------


@dataclass
class _FaultRule:
    status_sequence: list[int] = field(default_factory=list)
    truncate_sequence: list[int | None] = field(default_factory=list)
    delay_seconds: float = 0.0
    bad_content_range: str | None = None
    extra_bytes: bytes = b""


class _ReleaseHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, owner: LocalReleaseServer) -> None:
        self.owner = owner
        super().__init__(("127.0.0.1", 0), _ReleaseRequestHandler)


class _ReleaseRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:  # noqa: N802
        server_owner: LocalReleaseServer = self.server.owner  # type: ignore[attr-defined]
        split = urllib.parse.urlsplit(self.path)
        clean_path = split.path
        headers_lower = {k.lower(): v for k, v in self.headers.items()}
        server_owner._record_request(
            {
                "method": "GET",
                "path": clean_path,
                "raw_path": self.path,
                "host": headers_lower.get("x-original-host") or headers_lower.get("host", ""),
                "range": headers_lower.get("range"),
                "headers": dict(self.headers.items()),
            }
        )

        fault = server_owner._pop_fault_step(clean_path)
        if fault["delay_seconds"] > 0:
            time.sleep(fault["delay_seconds"])

        forced_status = fault["status"]
        if forced_status is not None:
            body = json.dumps({"error": f"simulated HTTP {forced_status}"}).encode("utf-8")
            self.send_response(forced_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True
            return

        payload_entry = server_owner._resolve_route(clean_path)
        if payload_entry is None:
            body = b'{"message":"Not Found"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True
            return

        content_type, data = payload_entry
        if fault["extra_bytes"]:
            data = data + fault["extra_bytes"]

        total = len(data)
        range_header = headers_lower.get("range")
        start = 0
        end = total - 1 if total > 0 else 0
        status_code = 200
        send_content_range: str | None = None

        if range_header and range_header.startswith("bytes="):
            spec = range_header[len("bytes=") :].strip()
            start_str, _, end_str = spec.partition("-")
            if start_str.isdigit():
                start = int(start_str)
                end = int(end_str) if end_str.isdigit() else (total - 1)
                if start >= total or end < start or end >= total:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    return
                status_code = 206
                send_content_range = fault["bad_content_range"] or f"bytes {start}-{end}/{total}"

        slice_data = data[start : end + 1] if total > 0 else b""
        declared_length = len(slice_data)
        truncate_at = fault["truncate_at"]
        wire_data = slice_data[:truncate_at] if truncate_at is not None else slice_data

        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(declared_length))
        if send_content_range is not None:
            self.send_header("Content-Range", send_content_range)
        self.send_header("Connection", "close")
        self.end_headers()
        if wire_data:
            self.wfile.write(wire_data)
        self.wfile.flush()
        if truncate_at is not None and len(wire_data) < declared_length:
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        self.close_connection = True


class LocalReleaseServer:
    """Context-managed local HTTP server simulating GitHub Releases API & assets."""

    def __init__(
        self,
        *,
        owner: str = DEFAULT_GITHUB_OWNER,
        repo: str = DEFAULT_GITHUB_REPO,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self._lock = threading.Lock()
        self._requests: list[dict[str, Any]] = []
        self._routes: dict[str, tuple[str, bytes]] = {}
        self._faults: dict[str, _FaultRule] = {}
        self._server: _ReleaseHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._tag: str | None = None
        self._manifest_packages: list[dict[str, Any]] = []

    def start(self) -> LocalReleaseServer:
        if self._server is not None:
            return self
        self._server = _ReleaseHTTPServer(self)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="e2e-local-release-server",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)

    def __enter__(self) -> LocalReleaseServer:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("LocalReleaseServer is not running")
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._requests)

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self._requests)

    def clear_requests(self) -> None:
        with self._lock:
            self._requests.clear()

    def _record_request(self, item: dict[str, Any]) -> None:
        with self._lock:
            self._requests.append(item)

    def register_bytes(
        self,
        path: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        with self._lock:
            self._routes[normalized] = (content_type, data)
        return normalized

    def register_json(self, path: str, document: dict[str, Any] | list[Any]) -> str:
        encoded = json.dumps(document, ensure_ascii=False).encode("utf-8")
        return self.register_bytes(path, encoded, content_type="application/json")

    def configure_latest_release(
        self,
        *,
        tag_name: str,
        manifest: dict[str, Any],
        draft: bool = False,
        prerelease: bool = False,
        manifest_asset_url: str | None = None,
        extra_assets: Sequence[dict[str, Any]] = (),
        release_override: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Configure ``/repos/{owner}/{repo}/releases/latest`` and manifest download."""
        manifest_path = f"/{self.owner}/{self.repo}/releases/download/{tag_name}/release-manifest.json"
        self.register_json(manifest_path, manifest)
        effective_manifest_url = (
            manifest_asset_url
            or f"https://github.com{manifest_path}"
        )
        release_doc = release_override if release_override is not None else {
            "tag_name": tag_name,
            "draft": draft,
            "prerelease": prerelease,
            "assets": [
                {
                    "name": "release-manifest.json",
                    "browser_download_url": effective_manifest_url,
                },
                *extra_assets,
            ],
        }
        latest_path = f"/repos/{self.owner}/{self.repo}/releases/latest"
        self.register_json(latest_path, release_doc)
        return latest_path, manifest_path

    def register_package(
        self,
        *,
        tag_name: str,
        filename: str,
        data: bytes,
        platform: str,
        architecture: str,
        package_type: str,
        install_channel: str,
        signer_identity: str = DEFAULT_SIGNER_SUBJECT,
        certificate_fingerprint: str = DEFAULT_TRUSTED_SIGNER_SHA256,
        install_strategy: Mapping[str, str] | None = None,
        signature_kind: str | None = None,
        manifest_sha256: str | None = None,
        manifest_size: int | None = None,
        host: str = "github.com",
    ) -> dict[str, Any]:
        """Register a binary asset on the server and return its manifest package entry."""
        if host == "objects.githubusercontent.com":
            rel_path = f"/github-production-release-asset/{self.repo}/{tag_name}/{filename}"
            public_url = f"https://objects.githubusercontent.com{rel_path}"
        else:
            rel_path = f"/{self.owner}/{self.repo}/releases/download/{tag_name}/{filename}"
            public_url = f"https://github.com/{self.owner}/{self.repo}/releases/download/{tag_name}/{filename}"

        self.register_bytes(rel_path, data)
        if install_strategy is None:
            if package_type == "msi":
                install_strategy = {"kind": "launch_msi"}
            elif package_type == "exe":
                install_strategy = {"kind": "launch_bootstrapper"}
            elif package_type == "apk":
                install_strategy = {"kind": "android_package_installer"}
            else:
                install_strategy = {"kind": "manual_sideload"}

        entry: dict[str, Any] = {
            "platform": platform,
            "architecture": architecture,
            "package_type": package_type,
            "install_channel": install_channel,
            "url": public_url,
            "sha256": manifest_sha256 or sha256_hex(data),
            "size": manifest_size if manifest_size is not None else len(data),
            "signer_identity": signer_identity,
            "certificate_fingerprint": certificate_fingerprint,
            "install_strategy": dict(install_strategy),
        }
        if signature_kind is not None:
            entry["signature_kind"] = signature_kind
        self._manifest_packages.append(entry)
        return entry

    def build_manifest(
        self,
        *,
        release_version: str,
        minimum_supported_version: str | None = None,
        schema_version: int = 3,
        packages: Sequence[dict[str, Any]] | None = None,
        published_at: str = "2026-11-18T12:00:00Z",
    ) -> dict[str, Any]:
        pkg_list = list(packages if packages is not None else self._manifest_packages)
        if schema_version == 3:
            normalized: list[dict[str, Any]] = []
            for item in pkg_list:
                copy = dict(item)
                if "signature_kind" not in copy:
                    pt = copy.get("package_type")
                    if pt == "apk":
                        copy["signature_kind"] = "apk-pinned"
                    elif pt in {"msi", "exe"}:
                        copy["signature_kind"] = "self-signed-pinned"
                    else:
                        copy["signature_kind"] = "unsigned-resign-required"
                normalized.append(copy)
            pkg_list = normalized
        return {
            "schema_version": schema_version,
            "release_version": release_version,
            "minimum_supported_version": minimum_supported_version or release_version,
            "published_at": published_at,
            "release_notes_url": (
                f"https://github.com/{self.owner}/{self.repo}/releases/tag/v{release_version}"
            ),
            "packages": pkg_list,
        }

    def set_fault(
        self,
        path: str,
        *,
        status_sequence: Sequence[int] = (),
        truncate_sequence: Sequence[int | None] = (),
        delay_seconds: float = 0.0,
        bad_content_range: str | None = None,
        extra_bytes: bytes = b"",
    ) -> None:
        normalized = path if path.startswith("/") else f"/{path}"
        with self._lock:
            self._faults[normalized] = _FaultRule(
                status_sequence=list(status_sequence),
                truncate_sequence=list(truncate_sequence),
                delay_seconds=float(delay_seconds),
                bad_content_range=bad_content_range,
                extra_bytes=extra_bytes,
            )

    def clear_faults(self) -> None:
        with self._lock:
            self._faults.clear()

    def _pop_fault_step(self, path: str) -> dict[str, Any]:
        with self._lock:
            rule = self._faults.get(path)
            if rule is None:
                return {
                    "status": None,
                    "truncate_at": None,
                    "delay_seconds": 0.0,
                    "bad_content_range": None,
                    "extra_bytes": b"",
                }
            status = rule.status_sequence.pop(0) if rule.status_sequence else None
            truncate_at = rule.truncate_sequence.pop(0) if rule.truncate_sequence else None
            return {
                "status": status,
                "truncate_at": truncate_at,
                "delay_seconds": rule.delay_seconds,
                "bad_content_range": rule.bad_content_range,
                "extra_bytes": rule.extra_bytes,
            }

    def _resolve_route(self, path: str) -> tuple[str, bytes] | None:
        with self._lock:
            return self._routes.get(path)

    def _make_routed_urlopen(self, original_urlopen: Any, *, block_external: bool = True):
        def _routed_urlopen(url_or_req: Any, data: Any = None, timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs: Any):
            if isinstance(url_or_req, urllib.request.Request):
                full_url = url_or_req.full_url
                headers = dict(url_or_req.header_items())
                method = url_or_req.get_method()
                req_data = url_or_req.data if data is None else data
            else:
                full_url = str(url_or_req)
                headers = {}
                method = "POST" if data is not None else "GET"
                req_data = data

            parsed = urllib.parse.urlsplit(full_url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme == "https" and host in _APPROVED_GITHUB_HOSTS:
                target_url = f"http://127.0.0.1:{self.port}{parsed.path}"
                if parsed.query:
                    target_url = f"{target_url}?{parsed.query}"
                headers["X-Original-Host"] = host
                rewritten = urllib.request.Request(
                    target_url,
                    data=req_data,
                    headers=headers,
                    method=method,
                )
                return original_urlopen(rewritten, timeout=timeout, **kwargs)

            if block_external and host not in _LOOPBACK_HOSTS:
                raise urllib.error.URLError(
                    f"Blocked non-loopback network request in E2E test: {full_url}"
                )
            return original_urlopen(url_or_req, data=data, timeout=timeout, **kwargs)

        return _routed_urlopen

    def route_github_https(self, monkeypatch: Any, *, block_external: bool = True) -> None:
        """Redirect ``https://{api.github.com,github.com,objects.githubusercontent.com}`` via monkeypatch."""
        original = urllib.request.urlopen
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            self._make_routed_urlopen(original, block_external=block_external),
        )

    @contextmanager
    def routed_github_https(self, *, block_external: bool = True) -> Iterator[LocalReleaseServer]:
        """Context manager variant of ``route_github_https`` for non-monkeypatch code."""
        original = urllib.request.urlopen
        urllib.request.urlopen = self._make_routed_urlopen(original, block_external=block_external)
        try:
            yield self
        finally:
            urllib.request.urlopen = original


# ---------------------------------------------------------------------------
# Simulated Android MethodChannel + DownloadManager + PackageManager
# ---------------------------------------------------------------------------


def make_minimal_apk_bytes(
    payload: bytes = b"dummy-apk",
    *,
    package_id: str = DEFAULT_ANDROID_PACKAGE_ID,
    version_code: int | None = None,
    signer_sha256: str | None = None,
    extra_entries: Mapping[str, bytes] | None = None,
) -> bytes:
    """Create minimal valid ZIP bytes starting with ``PK\\x03\\x04`` representing an APK."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("classes.dex", payload)
        meta: dict[str, Any] = {"package_id": package_id}
        if version_code is not None:
            meta["version_code"] = int(version_code)
        if signer_sha256 is not None:
            meta["signer_sha256"] = signer_sha256.upper()
        zf.writestr("META-INF/uth-test-meta.json", json.dumps(meta))
        if extra_entries:
            for name, content in extra_entries.items():
                zf.writestr(name, content)
    return buf.getvalue()


class SimulatedAndroidBridge:
    """Simulated ``com.uthelper/background_sync`` bridge, ``DownloadManager``, and ``PackageManager``.

    Implements both the ``AndroidBackgroundBridge`` surface consumed by
    ``AndroidPackageDownloader`` / ``AndroidPackageLauncher`` and the underlying
    ``AndroidBackgroundSync`` service surface, backed by real files under
    ``root_dir / "external_downloads"`` and ``root_dir / "cache" / "verified_updates"``.
    """

    def __init__(
        self,
        root_dir: Path,
        *,
        installed_package_id: str = DEFAULT_ANDROID_PACKAGE_ID,
        installed_version_code: int = 2_005_001,
        installed_signer_sha256: str = DEFAULT_TRUSTED_SIGNER_SHA256,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.external_downloads = self.root_dir / "external_downloads"
        self.verified_cache = self.root_dir / "cache" / "verified_updates"
        self.external_downloads.mkdir(parents=True, exist_ok=True)
        self.verified_cache.mkdir(parents=True, exist_ok=True)

        self.available: bool = True
        self.installed_package_id = installed_package_id
        self.installed_version_code = installed_version_code
        self.installed_signer_sha256 = installed_signer_sha256.upper()

        self.permission_granted: bool = True
        self.installer_busy: bool = False
        self.insufficient_space: bool = False
        self.storage_unavailable: bool = False
        self.fail_download_reason: str | None = None
        self.inject_exception: Exception | None = None
        self.malformed_response: Any = None
        self.enforce_apk_suffix_on_archive_parse: bool = True

        self._lock = threading.Lock()
        self._next_download_id = 100
        self._jobs: dict[str, dict[str, Any]] = {}
        self._package_bytes: dict[str, bytes] = {}
        self._default_statuses: list[str] = ["pending", "running", "completed"]
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def register_apk_payload(self, url: str, data: bytes) -> None:
        with self._lock:
            self._package_bytes[url] = data

    def set_download_status_plan(self, statuses: Sequence[str]) -> None:
        """Configure the status progression emitted by consecutive polls."""
        with self._lock:
            self._default_statuses = list(statuses)

    @staticmethod
    def _identity_key(
        url: str,
        sha256: str,
        expected_size: int,
        expected_package_id: str,
        expected_version_code: int,
        expected_certificate_sha256: str,
    ) -> str:
        raw = (
            f"{url}|{sha256.lower()}|{int(expected_size)}|"
            f"{expected_package_id}|{int(expected_version_code)}|{expected_certificate_sha256.upper()}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _valid_github_apk_url(url: str) -> bool:
        parsed = urllib.parse.urlsplit(url)
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == "github.com"
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path.startswith(f"/{DEFAULT_GITHUB_OWNER}/{DEFAULT_GITHUB_REPO}/releases/download/")
            and parsed.path.lower().endswith(".apk")
        )

    def _record_call(self, name: str, *args: Any) -> None:
        with self._lock:
            self.calls.append((name, args))

    def get_package_archive_info(self, path: Path) -> dict[str, Any] | None:
        """Simulate Android ``PackageManager.getPackageArchiveInfo()`` strictly.

        Real AOSP ``PackageParser`` rejects paths that do not end with ``.apk``
        by returning ``null``.
        """
        if self.enforce_apk_suffix_on_archive_parse and not path.name.endswith(".apk"):
            return None
        if not path.is_file():
            return None
        try:
            with zipfile.ZipFile(path, "r") as zf:
                if "META-INF/uth-test-meta.json" in zf.namelist():
                    meta = json.loads(zf.read("META-INF/uth-test-meta.json").decode("utf-8"))
                    return dict(meta)
                return {"package_id": self.installed_package_id}
        except (zipfile.BadZipFile, OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _materialize_download(self, job: dict[str, Any]) -> dict[str, Any]:
        url = job["url"]
        expected_sha256 = job["sha256"].lower()
        expected_size = int(job["expected_size"])
        key = job["key"]
        dest = self.external_downloads / f"update-{key}.apk"

        data = self._package_bytes.get(url)
        if data is None:
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = resp.read()
            except Exception:
                return {
                    "status": "failed",
                    "download_id": job["download_id"],
                    "downloaded": 0,
                    "total": expected_size,
                    "local_path": None,
                    "reason": "http_error",
                }

        dest.write_bytes(data)
        if len(data) != expected_size:
            dest.unlink(missing_ok=True)
            return {
                "status": "failed",
                "download_id": job["download_id"],
                "downloaded": len(data),
                "total": expected_size,
                "local_path": None,
                "reason": "download_size_mismatch",
            }
        if sha256_hex(data) != expected_sha256:
            dest.unlink(missing_ok=True)
            return {
                "status": "failed",
                "download_id": job["download_id"],
                "downloaded": len(data),
                "total": expected_size,
                "local_path": None,
                "reason": "download_sha256_mismatch",
            }
        return {
            "status": "completed",
            "download_id": job["download_id"],
            "downloaded": expected_size,
            "total": expected_size,
            "local_path": str(dest),
            "reason": None,
        }

    def _advance_job(self, job: dict[str, Any]) -> dict[str, Any]:
        plan: list[str] = job["plan"]
        idx = job["step"]
        status = plan[min(idx, len(plan) - 1)]
        job["step"] = idx + 1
        total = int(job["expected_size"])

        if status == "pending":
            return {
                "status": "pending",
                "download_id": job["download_id"],
                "downloaded": 0,
                "total": total,
                "local_path": None,
                "reason": None,
            }
        if status == "running":
            return {
                "status": "running",
                "download_id": job["download_id"],
                "downloaded": max(1, total // 2),
                "total": total,
                "local_path": None,
                "reason": None,
            }
        if status == "paused":
            return {
                "status": "paused",
                "download_id": job["download_id"],
                "downloaded": max(1, total // 3),
                "total": total,
                "local_path": None,
                "reason": None,
            }
        if status == "completed":
            result = self._materialize_download(job)
            job["final_result"] = result
            return result
        return {
            "status": "failed",
            "download_id": job["download_id"],
            "downloaded": 0,
            "total": total,
            "local_path": None,
            "reason": status if status not in {"failed", "missing"} else "download_failed",
        }

    async def start_update_download(
        self,
        url: str,
        sha256: str,
        expected_size: int,
        expected_package_id: str,
        expected_version_code: int,
        expected_certificate_sha256: str,
    ) -> dict[str, Any]:
        self._record_call(
            "start_update_download",
            url,
            sha256,
            expected_size,
            expected_package_id,
            expected_version_code,
            expected_certificate_sha256,
        )
        if self.inject_exception is not None:
            raise self.inject_exception
        if self.malformed_response is not None:
            return self.malformed_response
        if not self._valid_github_apk_url(url) or len(sha256) != 64 or int(expected_size) <= 0:
            return {
                "status": "failed",
                "download_id": None,
                "downloaded": 0,
                "total": int(expected_size),
                "local_path": None,
                "reason": "identity_mismatch",
            }
        if self.storage_unavailable:
            return {
                "status": "failed",
                "download_id": None,
                "downloaded": 0,
                "total": int(expected_size),
                "local_path": None,
                "reason": "storage_unavailable",
            }
        if self.insufficient_space:
            return {
                "status": "failed",
                "download_id": None,
                "downloaded": 0,
                "total": int(expected_size),
                "local_path": None,
                "reason": "insufficient_space",
            }
        if self.fail_download_reason is not None:
            return {
                "status": "failed",
                "download_id": None,
                "downloaded": 0,
                "total": int(expected_size),
                "local_path": None,
                "reason": self.fail_download_reason,
            }

        key = self._identity_key(
            url,
            sha256,
            expected_size,
            expected_package_id,
            expected_version_code,
            expected_certificate_sha256,
        )
        with self._lock:
            existing = self._jobs.get(key)
            if existing is not None:
                if "final_result" in existing:
                    return dict(existing["final_result"])
                return self._advance_job(existing)

            self._next_download_id += 1
            job = {
                "key": key,
                "download_id": self._next_download_id,
                "url": url,
                "sha256": sha256.lower(),
                "expected_size": int(expected_size),
                "expected_package_id": expected_package_id,
                "expected_version_code": int(expected_version_code),
                "expected_certificate_sha256": expected_certificate_sha256.upper(),
                "plan": list(self._default_statuses),
                "step": 0,
            }
            self._jobs[key] = job
            return self._advance_job(job)

    async def get_update_download_status(
        self,
        url: str,
        sha256: str,
        expected_size: int,
        expected_package_id: str,
        expected_version_code: int,
        expected_certificate_sha256: str,
    ) -> dict[str, Any]:
        self._record_call(
            "get_update_download_status",
            url,
            sha256,
            expected_size,
            expected_package_id,
            expected_version_code,
            expected_certificate_sha256,
        )
        if self.inject_exception is not None:
            raise self.inject_exception
        if self.malformed_response is not None:
            return self.malformed_response
        key = self._identity_key(
            url,
            sha256,
            expected_size,
            expected_package_id,
            expected_version_code,
            expected_certificate_sha256,
        )
        with self._lock:
            job = self._jobs.get(key)
            if job is None:
                return {
                    "status": "missing",
                    "download_id": None,
                    "downloaded": 0,
                    "total": int(expected_size),
                    "local_path": None,
                    "reason": None,
                }
            if "final_result" in job:
                return dict(job["final_result"])
            return self._advance_job(job)

    async def cancel_update_download(
        self,
        url: str,
        sha256: str,
        expected_size: int,
        expected_package_id: str,
        expected_version_code: int,
        expected_certificate_sha256: str,
    ) -> dict[str, Any]:
        self._record_call(
            "cancel_update_download",
            url,
            sha256,
            expected_size,
            expected_package_id,
            expected_version_code,
            expected_certificate_sha256,
        )
        key = self._identity_key(
            url,
            sha256,
            expected_size,
            expected_package_id,
            expected_version_code,
            expected_certificate_sha256,
        )
        with self._lock:
            self._jobs.pop(key, None)
        (self.external_downloads / f"update-{key}.apk").unlink(missing_ok=True)
        return {"status": "cancelled"}

    async def install_update(
        self,
        local_path: str,
        sha256: str,
        expected_size: int,
        expected_package_id: str,
        expected_version_code: int,
        expected_certificate_sha256: str,
    ) -> dict[str, Any]:
        self._record_call(
            "install_update",
            local_path,
            sha256,
            expected_size,
            expected_package_id,
            expected_version_code,
            expected_certificate_sha256,
        )
        if self.inject_exception is not None:
            raise self.inject_exception
        if self.malformed_response is not None:
            return self.malformed_response
        if self.installer_busy:
            return {
                "status": "failed",
                "stage": "release_metadata_validation",
                "bytes": 0,
                "sha256": sha256,
                "reason": "installer_busy",
            }
        expected_sha = sha256.lower()
        expected_cert = expected_certificate_sha256.upper()
        if (
            len(expected_sha) != 64
            or int(expected_size) <= 0
            or not expected_package_id
            or int(expected_version_code) <= 0
            or len(expected_cert) != 64
        ):
            return {
                "status": "failed",
                "stage": "release_metadata_validation",
                "bytes": 0,
                "sha256": expected_sha,
                "reason": "release_metadata_invalid",
            }

        release_key = expected_sha[:24]
        staged_apk = self.verified_cache / f"update-{release_key}.apk"
        staged_partial = self.verified_cache / f"update-{release_key}.apk.part"

        keep_cached = False
        try:
            if staged_apk.is_file() and staged_apk.stat().st_size == int(expected_size):
                cached_bytes = staged_apk.read_bytes()
                if sha256_hex(cached_bytes) == expected_sha:
                    archive_path = staged_apk
                else:
                    staged_apk.unlink(missing_ok=True)
                    archive_path = None
            else:
                archive_path = None

            if archive_path is None:
                source = Path(local_path)
                if not source.is_file():
                    return {
                        "status": "failed",
                        "stage": "downloaded_package_verification",
                        "bytes": 0,
                        "sha256": expected_sha,
                        "reason": "source_unavailable",
                    }
                raw = source.read_bytes()
                if len(raw) != int(expected_size):
                    return {
                        "status": "failed",
                        "stage": "downloaded_package_verification",
                        "bytes": len(raw),
                        "sha256": sha256_hex(raw),
                        "reason": "size_mismatch",
                    }
                actual_sha = sha256_hex(raw)
                if actual_sha != expected_sha:
                    return {
                        "status": "failed",
                        "stage": "downloaded_package_verification",
                        "bytes": len(raw),
                        "sha256": actual_sha,
                        "reason": "sha256_mismatch",
                    }
                staged_partial.write_bytes(raw)
                # Rename .apk.part -> .apk BEFORE calling getPackageArchiveInfo()
                os.replace(staged_partial, staged_apk)
                archive_path = staged_apk

            info = self.get_package_archive_info(archive_path)
            if info is None:
                return {
                    "status": "failed",
                    "stage": "archive_metadata_validation",
                    "bytes": int(expected_size),
                    "sha256": expected_sha,
                    "reason": "invalid_apk",
                }
            if info.get("package_id") != expected_package_id:
                return {
                    "status": "failed",
                    "stage": "archive_metadata_validation",
                    "bytes": int(expected_size),
                    "sha256": expected_sha,
                    "reason": "package_mismatch",
                }
            if int(expected_version_code) <= self.installed_version_code:
                return {
                    "status": "failed",
                    "stage": "archive_metadata_validation",
                    "bytes": int(expected_size),
                    "sha256": expected_sha,
                    "reason": "already_installed",
                }
            if "version_code" in info and int(info["version_code"]) != int(expected_version_code):
                return {
                    "status": "failed",
                    "stage": "archive_metadata_validation",
                    "bytes": int(expected_size),
                    "sha256": expected_sha,
                    "reason": "release_version_mismatch",
                }
            if "signer_sha256" in info and str(info["signer_sha256"]).upper() != expected_cert:
                return {
                    "status": "failed",
                    "stage": "archive_metadata_validation",
                    "bytes": int(expected_size),
                    "sha256": expected_sha,
                    "reason": "manifest_signer_mismatch",
                }
            if self.installed_signer_sha256 != expected_cert:
                return {
                    "status": "failed",
                    "stage": "archive_metadata_validation",
                    "bytes": int(expected_size),
                    "sha256": expected_sha,
                    "reason": "installed_signer_mismatch",
                }

            keep_cached = True
            if not self.permission_granted:
                return {
                    "status": "permission_required",
                    "stage": "install_permission",
                    "bytes": int(expected_size),
                    "sha256": expected_sha,
                    "reason": "unknown_sources_permission_required",
                }
            return {
                "status": "installer_opened",
                "stage": "installer_handoff",
                "bytes": int(expected_size),
                "sha256": expected_sha,
                "reason": None,
            }
        finally:
            staged_partial.unlink(missing_ok=True)
            if not keep_cached:
                staged_apk.unlink(missing_ok=True)

    async def cancel_update(self) -> None:
        self._record_call("cancel_update")

    # Background sync stub compatibility when passed as AndroidBackgroundSync
    async def set_credentials(self, origin: str, token: str) -> None:
        self._record_call("set_credentials", origin, "***")

    async def logout(self) -> None:
        self._record_call("logout")

    async def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._record_call("configure", payload)
        return {"ok": True}

    async def get_diagnostics(self) -> dict[str, Any]:
        return {}


# ---------------------------------------------------------------------------
# Windows Subprocess Shims (powershell.exe, msiexec.exe, Setup.exe)
# ---------------------------------------------------------------------------

_SHIM_CS_SOURCE = r"""
using System;
using System.IO;
using System.Text;
using System.Threading;

class UthShim {
    static string EscapeJson(string value) {
        if (value == null) return "";
        var sb = new StringBuilder(value.Length + 8);
        foreach (char c in value) {
            if (c == '\\') sb.Append("\\\\");
            else if (c == '"') sb.Append("\\\"");
            else if (c == '\r') sb.Append("\\r");
            else if (c == '\n') sb.Append("\\n");
            else if (c == '\t') sb.Append("\\t");
            else sb.Append(c);
        }
        return sb.ToString();
    }

    static void LogCall(string toolName, string[] args, string pkgPath) {
        string logPath = Environment.GetEnvironmentVariable("FAKE_WIN_CALL_LOG");
        if (string.IsNullOrEmpty(logPath)) return;
        var sb = new StringBuilder();
        sb.Append("{\"tool\":\"").Append(EscapeJson(toolName)).Append("\",\"package_path\":\"")
          .Append(EscapeJson(pkgPath ?? "")).Append("\",\"argv\":[");
        for (int i = 0; i < args.Length; i++) {
            if (i > 0) sb.Append(",");
            sb.Append("\"").Append(EscapeJson(args[i])).Append("\"");
        }
        sb.Append("]}");
        for (int attempt = 0; attempt < 5; attempt++) {
            try {
                File.AppendAllText(logPath, sb.ToString() + "\n", new UTF8Encoding(false));
                break;
            } catch (IOException) {
                Thread.Sleep(10);
            }
        }
    }

    static string NextPsMode() {
        string seqFile = Environment.GetEnvironmentVariable("FAKE_PS_SEQUENCE_FILE");
        if (!string.IsNullOrWhiteSpace(seqFile) && File.Exists(seqFile)) {
            string[] lines = File.ReadAllLines(seqFile, Encoding.UTF8);
            if (lines.Length > 0) {
                string first = lines[0].Trim();
                string[] rest = new string[lines.Length - 1];
                Array.Copy(lines, 1, rest, 0, rest.Length);
                File.WriteAllLines(seqFile, rest, Encoding.UTF8);
                if (!string.IsNullOrEmpty(first)) return first;
            }
        }
        return Environment.GetEnvironmentVariable("FAKE_PS_MODE") ?? "valid";
    }

    static int HandlePowerShell(string[] args) {
        string pkgPath = Environment.GetEnvironmentVariable("UTHELPER_UPDATE_PACKAGE_PATH") ?? "";
        LogCall("powershell.exe", args, pkgPath);

        string delayRaw = Environment.GetEnvironmentVariable("FAKE_PS_DELAY_SEC") ?? "0";
        double delaySec = 0;
        double.TryParse(delayRaw, System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out delaySec);
        if (delaySec > 0) {
            Thread.Sleep((int)Math.Ceiling(delaySec * 1000.0));
        }

        string mode = NextPsMode();
        if (mode == "timeout") {
            Thread.Sleep(30000);
            return 1;
        }
        if (mode == "error" || mode == "nonzero") {
            Console.Error.WriteLine("Simulated PowerShell probe failure");
            return 1;
        }
        if (mode == "malformed_json") {
            Console.WriteLine("not-valid-json");
            return 0;
        }
        if (mode == "oversized") {
            Console.WriteLine(new string('A', 70000));
            return 0;
        }

        string cmd = string.Join(" ", args);
        string version = Environment.GetEnvironmentVariable("FAKE_PS_VERSION") ?? "2.6.0";
        string subject = Environment.GetEnvironmentVariable("FAKE_PS_SIGNER_SUBJECT") ?? "CN=UTHelper";
        string sha256 = Environment.GetEnvironmentVariable("FAKE_PS_SIGNER_SHA256")
            ?? "7E3547EE6A31325A47BE22049E238BA83CA1D90AFB8A30D053060D02678A0B3C";
        string upgradeCode = Environment.GetEnvironmentVariable("FAKE_PS_UPGRADE_CODE")
            ?? "{B1EB1032-5ACD-497D-8FD2-AB760218CBE3}";
        string template = Environment.GetEnvironmentVariable("FAKE_PS_TEMPLATE") ?? "x64;1033";

        if (cmd.Contains("Get-AuthenticodeSignature")) {
            string status = (mode == "self_signed") ? "UnknownError" :
                            (mode == "invalid_status") ? "HashMismatch" : "Valid";
            bool timestamped = (mode != "not_timestamped");
            if (mode == "bad_signer") sha256 = "0000000000000000000000000000000000000000000000000000000000000000";
            if (mode == "bad_subject") subject = "CN=Untrusted";
            Console.WriteLine(
                "{\"status\":\"" + EscapeJson(status) +
                "\",\"subject\":\"" + EscapeJson(subject) +
                "\",\"fingerprint\":\"" + EscapeJson(sha256) +
                "\",\"timestamped\":" + (timestamped ? "true" : "false") + "}"
            );
            return 0;
        }
        if (cmd.Contains("WindowsInstaller.Installer")) {
            if (mode == "bad_version") version = "0.0.1";
            if (mode == "bad_upgrade_code") upgradeCode = "{00000000-0000-0000-0000-000000000000}";
            if (mode == "bad_template") template = "Intel;1033";
            Console.WriteLine(
                "{\"product_name\":\"UTHelper\",\"product_version\":\"" + EscapeJson(version) +
                "\",\"upgrade_code\":\"" + EscapeJson(upgradeCode) +
                "\",\"template\":\"" + EscapeJson(template) + "\"}"
            );
            return 0;
        }
        if (cmd.Contains("VersionInfo")) {
            if (mode == "bad_version") version = "0.0.1";
            Console.WriteLine(
                "{\"product_name\":\"UTHelper\",\"product_version\":\"" + EscapeJson(version) +
                "\",\"bundle_upgrade_code\":\"\"}"
            );
            return 0;
        }
        Console.Error.WriteLine("Unexpected PowerShell command");
        return 2;
    }

    static int HandleInstaller(string toolName, string[] args) {
        LogCall(toolName, args, args.Length > 0 ? args[0] : "");
        string delayRaw = Environment.GetEnvironmentVariable("FAKE_MSI_DELAY_SEC") ?? "0";
        double delaySec = 0;
        double.TryParse(delayRaw, System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out delaySec);
        if (delaySec > 0) {
            Thread.Sleep((int)Math.Ceiling(delaySec * 1000.0));
        }
        string exitRaw = Environment.GetEnvironmentVariable("FAKE_MSI_EXIT") ?? "0";
        int code = 0;
        int.TryParse(exitRaw, out code);
        return code;
    }

    static int Main(string[] args) {
        string exeName = Path.GetFileName(Environment.GetCommandLineArgs()[0]).ToLowerInvariant();
        if (exeName == "powershell.exe") {
            return HandlePowerShell(args);
        }
        return HandleInstaller(exeName, args);
    }
}
"""

_COMPILED_SHIM_LOCK = threading.Lock()
_COMPILED_SHIM_DIR: Path | None = None


def _get_compiled_shim_exe() -> Path:
    global _COMPILED_SHIM_DIR
    with _COMPILED_SHIM_LOCK:
        if _COMPILED_SHIM_DIR is not None:
            candidate = _COMPILED_SHIM_DIR / "uth_shim.exe"
            if candidate.is_file():
                return candidate

        csc = next((item for item in _CSC_CANDIDATES if item.is_file()), None)
        if csc is None:
            raise RuntimeError(
                "Windows C# compiler (csc.exe) was not found in .NET Framework; "
                "cannot build executable Windows shims"
            )
        cache_dir = Path(tempfile.gettempdir()) / "uth_e2e_win_shim_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cs_path = cache_dir / "uth_shim.cs"
        exe_path = cache_dir / "uth_shim.exe"
        src_hash = hashlib.sha256(_SHIM_CS_SOURCE.encode("utf-8")).hexdigest()[:16]
        marker_path = cache_dir / "uth_shim.hash"

        needs_compile = (
            not exe_path.is_file()
            or not marker_path.is_file()
            or marker_path.read_text(encoding="utf-8").strip() != src_hash
        )
        if needs_compile:
            cs_path.write_text(_SHIM_CS_SOURCE, encoding="utf-8")
            subprocess.run(
                [str(csc), "/nologo", "/optimize+", f"/out:{exe_path}", str(cs_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
            marker_path.write_text(src_hash, encoding="utf-8")
        _COMPILED_SHIM_DIR = cache_dir
        return exe_path


class WindowsShimEnv:
    """Isolated Windows subprocess shims for ``powershell.exe``, ``msiexec.exe``, and Burn ``Setup.exe``.

    Note on Windows ``CreateProcessW`` resolution:
    - ``powershell.exe`` lives in ``System32\\WindowsPowerShell\\v1.0``, so prepending
      ``bin_dir`` to ``os.environ["PATH"]`` causes ``subprocess.run(["powershell.exe", ...])``
      in ``WindowsPackageVerifier`` to execute our compiled ``bin_dir/powershell.exe`` shim.
    - ``msiexec.exe`` lives in ``C:\\Windows\\System32``, which Windows ``CreateProcessW``
      searches before ``PATH``. Therefore, ``WindowsShimEnv.process_factory`` rewrites
      ``argv[0] == "msiexec.exe"`` to ``bin_dir/msiexec.exe`` so ``WindowsPackageLauncher``
      still spawns a real subprocess without hitting the OS ``System32\\msiexec.exe``.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.bin_dir = self.root_dir / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.call_log_path = self.root_dir / "win_calls.jsonl"
        self.sequence_file = self.root_dir / "ps_sequence.txt"

        template_exe = _get_compiled_shim_exe()
        self.powershell_exe = self.bin_dir / "powershell.exe"
        self.msiexec_exe = self.bin_dir / "msiexec.exe"
        shutil.copy2(template_exe, self.powershell_exe)
        shutil.copy2(template_exe, self.msiexec_exe)

        self._saved_env: dict[str, str | None] | None = None
        self._monkeypatch: Any | None = None
        self._spawned_processes: list[subprocess.Popen[Any]] = []
        self._env_values: dict[str, str] = {
            "FAKE_PS_MODE": "valid",
            "FAKE_PS_DELAY_SEC": "0",
            "FAKE_PS_SEQUENCE_FILE": "",
            "FAKE_PS_VERSION": "2.6.0",
            "FAKE_PS_SIGNER_SUBJECT": DEFAULT_SIGNER_SUBJECT,
            "FAKE_PS_SIGNER_SHA256": DEFAULT_TRUSTED_SIGNER_SHA256,
            "FAKE_PS_UPGRADE_CODE": DEFAULT_MSI_UPGRADE_CODE,
            "FAKE_PS_TEMPLATE": "x64;1033",
            "FAKE_MSI_EXIT": "0",
            "FAKE_MSI_DELAY_SEC": "0",
            "FAKE_WIN_CALL_LOG": str(self.call_log_path),
        }

    def activate(self, monkeypatch: Any | None = None) -> WindowsShimEnv:
        """Prepend ``bin_dir`` to ``PATH`` and export ``FAKE_*`` env vars."""
        self._monkeypatch = monkeypatch
        current_path = os.environ.get("PATH", "")
        new_path = str(self.bin_dir) + os.pathsep + current_path
        if monkeypatch is not None:
            monkeypatch.setenv("PATH", new_path)
            for key, value in self._env_values.items():
                monkeypatch.setenv(key, value)
        else:
            self._saved_env = {"PATH": os.environ.get("PATH")}
            for key in self._env_values:
                self._saved_env[key] = os.environ.get(key)
            os.environ["PATH"] = new_path
            os.environ.update(self._env_values)
        return self

    def deactivate(self) -> None:
        for proc in self._spawned_processes:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2.0)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass
        self._spawned_processes.clear()
        self.sequence_file.unlink(missing_ok=True)
        if self._saved_env is not None:
            for key, prev in self._saved_env.items():
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev
            self._saved_env = None
        os.environ.pop("FAKE_PS_SEQUENCE_FILE", None)
        self._monkeypatch = None

    def _apply_env_updates(self, updates: Mapping[str, str]) -> None:
        self._env_values.update(updates)
        if self._monkeypatch is not None:
            for key, value in updates.items():
                self._monkeypatch.setenv(key, value)
        else:
            if self._saved_env is not None:
                for key in updates:
                    if key not in self._saved_env:
                        self._saved_env[key] = os.environ.get(key)
            os.environ.update(updates)

    def __enter__(self) -> WindowsShimEnv:
        return self.activate()

    def __exit__(self, *_exc: object) -> None:
        self.deactivate()

    def set_ps_mode(
        self,
        mode: str,
        *,
        delay_sec: float = 0.0,
        version: str | None = None,
        signer_subject: str | None = None,
        signer_sha256: str | None = None,
        upgrade_code: str | None = None,
        template: str | None = None,
    ) -> None:
        updates: dict[str, str] = {
            "FAKE_PS_MODE": mode,
            "FAKE_PS_DELAY_SEC": str(delay_sec),
        }
        if version is not None:
            updates["FAKE_PS_VERSION"] = version
        if signer_subject is not None:
            updates["FAKE_PS_SIGNER_SUBJECT"] = signer_subject
        if signer_sha256 is not None:
            updates["FAKE_PS_SIGNER_SHA256"] = signer_sha256
        if upgrade_code is not None:
            updates["FAKE_PS_UPGRADE_CODE"] = upgrade_code
        if template is not None:
            updates["FAKE_PS_TEMPLATE"] = template
        self._apply_env_updates(updates)

    def set_ps_sequence(self, modes: Sequence[str]) -> None:
        self.sequence_file.write_text("\n".join(modes) + "\n", encoding="utf-8")
        self._apply_env_updates({"FAKE_PS_SEQUENCE_FILE": str(self.sequence_file)})

    def set_msi_behavior(self, *, exit_code: int = 0, delay_sec: float = 0.0) -> None:
        updates = {
            "FAKE_MSI_EXIT": str(int(exit_code)),
            "FAKE_MSI_DELAY_SEC": str(delay_sec),
        }
        self._apply_env_updates(updates)

    def make_setup_exe(self, dest_path: Path) -> Path:
        """Create a real executable Burn shim (with valid ``MZ`` header) at ``dest_path``."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_get_compiled_shim_exe(), dest)
        return dest

    def make_msi_file(self, dest_path: Path, payload: bytes = b"synthetic-msi") -> Path:
        """Create a synthetic MSI file starting with the OLE compound magic header."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_MSI_OLE_MAGIC + payload)
        return dest

    def process_factory(self, argv: list[str]) -> subprocess.Popen[Any]:
        """``WindowsPackageLauncher`` process factory routing ``msiexec.exe`` to ``bin_dir``."""
        effective = list(argv)
        if effective and effective[0].lower() == "msiexec.exe":
            effective[0] = str(self.msiexec_exe)
        proc = subprocess.Popen(effective, stdin=subprocess.DEVNULL)
        self._spawned_processes.append(proc)
        return proc

    def read_calls(self) -> list[dict[str, Any]]:
        if not self.call_log_path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.call_log_path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if stripped:
                entries.append(json.loads(stripped))
        return entries

    def clear_calls(self) -> None:
        self.call_log_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Fake Flet Page Stub
# ---------------------------------------------------------------------------


class FakeWindow:
    def __init__(self) -> None:
        self.destroyed: bool = False
        self.destroy_calls: int = 0

    async def destroy(self) -> None:
        self.destroyed = True
        self.destroy_calls += 1


class FakePage:
    """Lightweight Flet ``Page`` stub matching ``tests/test_gui_app_controller.py`` contracts."""

    def __init__(self, *, width: int = 1024, height: int = 768) -> None:
        self.width = width
        self.height = height
        self.overlay: list[Any] = []
        self.views: list[Any] = []
        self.services: list[Any] = []
        self.dialogs: list[Any] = []
        self.launched_urls: list[str] = []
        self.fail_url_schemes: set[str] = set()
        self.update_count: int = 0
        self.session: Any = SimpleNamespace(connection=SimpleNamespace())
        self.window = FakeWindow()
        self.title: str = ""
        self.theme_mode: Any = None
        self.theme: Any = None
        self.on_route_change: Any = None
        self.on_view_pop: Any = None

        self._loop_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            ready = threading.Event()
            holder: list[asyncio.AbstractEventLoop] = []

            def _runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                holder.append(loop)
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    loop.close()

            thread = threading.Thread(
                target=_runner,
                name="e2e-fake-page-loop",
                daemon=True,
            )
            thread.start()
            ready.wait(timeout=5.0)
            self._loop = holder[0]
            self._loop_thread = thread
            return self._loop

    def run_task(self, handler: Any, *args: Any, **kwargs: Any) -> Future[Any]:
        loop = self._ensure_loop()
        coro = handler(*args, **kwargs)
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def update(self) -> None:
        self.update_count += 1

    def show_dialog(self, dialog: Any) -> None:
        try:
            dialog.open = True
        except Exception:
            pass
        self.dialogs.append(dialog)

    def pop_dialog(self) -> Any:
        if not self.dialogs:
            return None
        dialog = self.dialogs.pop()
        try:
            dialog.open = False
        except Exception:
            pass
        return dialog

    async def launch_url(self, url: str, **_kwargs: Any) -> bool:
        target = str(url)
        scheme = urllib.parse.urlsplit(target).scheme.lower()
        if scheme in self.fail_url_schemes:
            raise RuntimeError(f"Simulated launch_url failure for scheme: {scheme}")
        self.launched_urls.append(target)
        return True

    def close(self) -> None:
        with self._loop_lock:
            loop = self._loop
            thread = self._loop_thread
            self._loop = None
            self._loop_thread = None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5.0)


# ---------------------------------------------------------------------------
# Synthetic Binary & Packaging Artifact Builders (ELF64, DEX, APK, AAB, LTS)
# ---------------------------------------------------------------------------


def make_elf64_so(
    p_align: int = 0x4000,
    *,
    segment_aligns: Sequence[int] | None = None,
) -> bytes:
    """Build a minimal valid 64-bit AArch64 ELF shared object with ``PT_LOAD`` segment(s)."""
    aligns = (
        tuple(int(a) for a in segment_aligns)
        if segment_aligns is not None
        else (int(p_align),)
    )
    if not aligns:
        raise ValueError("segment_aligns must contain at least one alignment")

    e_ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + (b"\x00" * 8)
    ehdr_size = 64
    phdr_size = 56
    phnum = len(aligns)
    total_size = ehdr_size + phdr_size * phnum
    ehdr = struct.pack(
        "<16sHHIQQQIHHHHHH",
        e_ident,
        3,          # ET_DYN
        183,        # EM_AARCH64
        1,          # EV_CURRENT
        0,          # e_entry
        ehdr_size,  # e_phoff
        0,          # e_shoff
        0,          # e_flags
        ehdr_size,  # e_ehsize
        phdr_size,  # e_phentsize
        phnum,      # e_phnum
        64,         # e_shentsize
        0,          # e_shnum
        0,          # e_shstrndx
    )
    phdrs = bytearray()
    for idx, align in enumerate(aligns):
        vaddr = 0 if idx == 0 else idx * 0x100000
        flags = 5 if idx == 0 else 6  # PF_R|PF_X then PF_R|PF_W
        phdrs.extend(
            struct.pack(
                "<IIQQQQQQ",
                1,           # PT_LOAD
                flags,
                0,           # p_offset
                vaddr,       # p_vaddr
                vaddr,       # p_paddr
                total_size,  # p_filesz
                total_size,  # p_memsz
                align,
            )
        )
    return ehdr + bytes(phdrs)


def _encode_uleb128(value: int) -> bytes:
    out = bytearray()
    v = int(value)
    while True:
        byte = v & 0x7F
        v >>= 7
        if v != 0:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def make_minimal_dex(strings: Sequence[str]) -> bytes:
    """Build a minimal valid DEX 035 binary containing ``strings`` in its string pool."""
    header_size = 0x70
    string_list = list(strings)
    string_ids_size = len(string_list)
    string_ids_off = header_size if string_ids_size > 0 else 0
    data_off = header_size + 4 * string_ids_size

    data_buf = bytearray()
    string_offsets: list[int] = []
    for text in string_list:
        string_offsets.append(data_off + len(data_buf))
        utf16_len = len(text.encode("utf-16-le")) // 2
        data_buf.extend(_encode_uleb128(utf16_len))
        data_buf.extend(text.encode("utf-8"))
        data_buf.append(0)

    while (data_off + len(data_buf)) % 4 != 0:
        data_buf.append(0)

    data_size = len(data_buf)
    file_size = data_off + data_size

    buf = bytearray(file_size)
    buf[0:8] = b"dex\n035\x00"
    struct.pack_into(
        "<IIIIIIIIIIIIIIIIIIII",
        buf,
        32,
        file_size,
        header_size,
        0x12345678,      # ENDIAN_CONSTANT
        0, 0,            # link_size, link_off
        0,               # map_off
        string_ids_size,
        string_ids_off,
        0, 0,            # type_ids
        0, 0,            # proto_ids
        0, 0,            # field_ids
        0, 0,            # method_ids
        0, 0,            # class_defs
        data_size,
        data_off,
    )
    for idx, off in enumerate(string_offsets):
        struct.pack_into("<I", buf, string_ids_off + 4 * idx, off)
    buf[data_off : data_off + data_size] = data_buf

    buf[12:32] = hashlib.sha1(bytes(buf[32:])).digest()
    checksum = zlib.adler32(bytes(buf[12:])) & 0xFFFFFFFF
    struct.pack_into("<I", buf, 8, checksum)
    return bytes(buf)


def _default_manifest_xml(package_id: str, *, chplay_compliant: bool = True) -> str:
    extra = "" if chplay_compliant else (
        '  <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />\n'
        '  <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />\n'
    )
    allow_backup = "false" if chplay_compliant else "true"
    cleartext = "" if chplay_compliant else ' android:usesCleartextTraffic="true"'
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{package_id}">\n'
        '  <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35" />\n'
        '  <uses-permission android:name="android.permission.INTERNET" />\n'
        '  <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />\n'
        f"{extra}"
        f'  <application android:allowBackup="{allow_backup}"{cleartext}>\n'
        '    <receiver android:name="com.dexterous.flutterlocalnotifications.ScheduledNotificationReceiver" />\n'
        '    <receiver android:name="com.dexterous.flutterlocalnotifications.ScheduledNotificationBootReceiver" />\n'
        '    <receiver android:name="com.dexterous.flutterlocalnotifications.ActionBroadcastReceiver" />\n'
        '    <receiver android:name="com.uthelper.backgroundsync.notification.DeadlineAlarmReceiver" />\n'
        '    <receiver android:name="com.uthelper.backgroundsync.notification.RescheduleReceiver" />\n'
        '    <receiver android:name="com.uthelper.backgroundsync.notification.ClassScheduleAlarmReceiver" />\n'
        "  </application>\n"
        "</manifest>\n"
    )


def make_synthetic_apk(
    out_path: Path,
    *,
    package_id: str = DEFAULT_ANDROID_PACKAGE_ID,
    abis: Sequence[str] = ("arm64-v8a", "armeabi-v7a", "x86_64"),
    p_align: int = 0x4000,
    dex_strings: Sequence[str] = ("Lcom/uthelper/MainActivity;",),
    manifest_xml: str | None = None,
    distribution_channel: dict[str, Any] | None = None,
    extra_files: Mapping[str, bytes] | None = None,
) -> Path:
    """Create a synthetic APK ZIP archive with ELF64 `.so`, DEX 035, manifest, and optional channel asset."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    elf_bytes = make_elf64_so(p_align)
    dex_bytes = make_minimal_dex(dex_strings)
    xml_text = manifest_xml if manifest_xml is not None else _default_manifest_xml(package_id, chplay_compliant=False)

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("AndroidManifest.xml", xml_text.encode("utf-8"))
        zf.writestr("classes.dex", dex_bytes)
        for abi in abis:
            zf.writestr(f"lib/{abi}/libflutter.so", elf_bytes)
        if distribution_channel is not None:
            zf.writestr(
                "assets/flutter_assets/app/assets/distribution-channel.json",
                json.dumps(distribution_channel, indent=2, sort_keys=True) + "\n",
            )
        if extra_files:
            for name, content in extra_files.items():
                zf.writestr(name, content)
    return out


def make_synthetic_aab(
    out_path: Path,
    *,
    package_id: str = DEFAULT_ANDROID_PACKAGE_ID,
    abis: Sequence[str] = ("arm64-v8a", "armeabi-v7a", "x86_64"),
    p_align: int = 0x4000,
    dex_strings: Sequence[str] = ("Lcom/uthelper/MainActivity;",),
    manifest_xml: str | None = None,
    distribution_channel: dict[str, Any] | None = None,
    extra_files: Mapping[str, bytes] | None = None,
    extra_dex_files: Mapping[str, bytes] | None = None,
) -> Path:
    """Create a synthetic Android App Bundle (``.aab``) ZIP archive."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    elf_bytes = make_elf64_so(p_align)
    dex_bytes = make_minimal_dex(dex_strings)
    xml_text = manifest_xml if manifest_xml is not None else _default_manifest_xml(package_id, chplay_compliant=True)

    bundle_config_pb = (
        b"\x0a\x08\x12\x061.18.1"
        b"\x12\x06\x12\x04\x08\x01\x10\x02"
    )
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("BundleConfig.pb", bundle_config_pb)
        zf.writestr("base/manifest/AndroidManifest.xml", xml_text.encode("utf-8"))
        if not extra_dex_files or "base/dex/classes.dex" not in extra_dex_files:
            zf.writestr("base/dex/classes.dex", dex_bytes)
        if extra_dex_files:
            for name, content in extra_dex_files.items():
                zf.writestr(name, content)
        for abi in abis:
            zf.writestr(f"base/lib/{abi}/libflutter.so", elf_bytes)
        if distribution_channel is not None:
            zf.writestr(
                "base/assets/flutter_assets/app/assets/distribution-channel.json",
                json.dumps(distribution_channel, indent=2, sort_keys=True) + "\n",
            )
        if extra_files:
            for name, content in extra_files.items():
                zf.writestr(name, content)
    return out


def make_synthetic_lts_release_dir(
    dir_path: Path,
    version: str = "2.5.1",
    *,
    evidence_dir: Path | None = None,
    repository: str = f"{DEFAULT_GITHUB_OWNER}/{DEFAULT_GITHUB_REPO}",
    minimum_supported_version: str | None = None,
    signer_sha256: str = DEFAULT_TRUSTED_SIGNER_SHA256,
    commit_sha: str = "a" * 40,
    workflow_run_id: str = "100001",
) -> Path:
    """Populate ``dir_path`` with all 7 canonical GitHub LTS release assets.

    Creates: ``UTHelper-{v}.ipa``, ``UTHelper-{v}.apk``,
    ``UTHelper-{v}-arm64-v8a.apk``, ``UTHelper-Setup-{v}.exe``,
    ``UTHelper-{v}.msi``, ``release-manifest.json``, and ``SHA256SUMS``.
    When ``evidence_dir`` is provided, also writes the 5 matching
    ``*.verification.json`` files expected by ``scripts/release_inventory.py``.
    """
    release_dir = Path(dir_path)
    release_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = signer_sha256.upper()

    ipa_name = f"UTHelper-{version}.ipa"
    univ_apk_name = f"UTHelper-{version}.apk"
    arm64_apk_name = f"UTHelper-{version}-arm64-v8a.apk"
    exe_name = f"UTHelper-Setup-{version}.exe"
    msi_name = f"UTHelper-{version}.msi"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("Payload/UTHelper.app/Info.plist", f"<version>{version}</version>")
    ipa_bytes = buf.getvalue()

    univ_apk_path = make_synthetic_apk(release_dir / univ_apk_name)
    arm64_apk_path = make_synthetic_apk(release_dir / arm64_apk_name, abis=("arm64-v8a",))
    (release_dir / ipa_name).write_bytes(ipa_bytes)
    (release_dir / exe_name).write_bytes(b"MZ" + b"\x00" * 62 + b"synthetic-burn-exe")
    (release_dir / msi_name).write_bytes(_MSI_OLE_MAGIC + b"synthetic-msi")

    base_url = f"https://github.com/{repository}/releases/download/v{version}"
    specs = [
        (
            ipa_name,
            "ios",
            "com.uthelper.UTHelper",
            "arm64",
            "ipa",
            "sideload",
            "unsigned-resign-required",
            "",
            "",
            False,
            None,
            {"kind": "manual_sideload"},
            sorted(
                [
                    "arm64",
                    "build_number",
                    "bundle_id",
                    "iphoneos",
                    "ipa_container",
                    "no_embedded_profile",
                    "sha256",
                    "version",
                ]
            ),
        ),
        (
            univ_apk_name,
            "android",
            DEFAULT_ANDROID_PACKAGE_ID,
            "universal",
            "apk",
            "sideload",
            "apk-pinned",
            DEFAULT_ANDROID_PACKAGE_ID,
            fingerprint,
            True,
            None,
            {"kind": "android_package_installer"},
            sorted(
                [
                    "apk_signature",
                    "native_abis",
                    "notification_receivers",
                    "office_viewer_assets",
                    "package_id",
                    "sha256",
                    "version_code",
                    "version_name",
                ]
            ),
        ),
        (
            arm64_apk_name,
            "android",
            DEFAULT_ANDROID_PACKAGE_ID,
            "arm64",
            "apk",
            "sideload",
            "apk-pinned",
            DEFAULT_ANDROID_PACKAGE_ID,
            fingerprint,
            True,
            None,
            {"kind": "android_package_installer"},
            sorted(
                [
                    "apk_signature",
                    "native_abis",
                    "notification_receivers",
                    "office_viewer_assets",
                    "package_id",
                    "sha256",
                    "version_code",
                    "version_name",
                ]
            ),
        ),
        (
            exe_name,
            "windows",
            "UTHelper",
            "x64",
            "exe",
            "bootstrapper",
            "self-signed-pinned",
            DEFAULT_SIGNER_SUBJECT,
            fingerprint,
            True,
            True,
            {"kind": "launch_bootstrapper"},
            sorted(["authenticode", "burn_payload", "pe_header", "product_version", "timestamp"]),
        ),
        (
            msi_name,
            "windows",
            "UTHelper",
            "x64",
            "msi",
            "msi",
            "self-signed-pinned",
            DEFAULT_SIGNER_SUBJECT,
            fingerprint,
            True,
            True,
            {"kind": "launch_msi"},
            sorted(["authenticode", "msi_ole", "product_version", "template", "timestamp", "upgrade_code"]),
        ),
    ]

    if evidence_dir is not None:
        Path(evidence_dir).mkdir(parents=True, exist_ok=True)

    manifest_packages: list[dict[str, Any]] = []
    checksum_lines: list[str] = []

    for (
        name,
        platform_name,
        product_id,
        arch,
        pkg_type,
        install_channel,
        sig_kind,
        signer_id,
        cert_fp,
        sig_valid,
        ts_valid,
        strategy,
        checks,
    ) in specs:
        asset_path = release_dir / name
        raw = asset_path.read_bytes()
        digest = sha256_hex(raw)
        size = len(raw)
        checksum_lines.append(f"{digest}  {name}")
        manifest_packages.append(
            {
                "platform": platform_name,
                "architecture": arch,
                "package_type": pkg_type,
                "install_channel": install_channel,
                "url": f"{base_url}/{urllib.parse.quote(name)}",
                "sha256": digest,
                "size": size,
                "signature_kind": sig_kind,
                "signer_identity": signer_id,
                "certificate_fingerprint": cert_fp,
                "install_strategy": strategy,
            }
        )
        if evidence_dir is not None:
            evidence_doc = {
                "schema_version": 2,
                "platform": platform_name,
                "asset_name": name,
                "sha256": digest,
                "version": version,
                "product_id": product_id,
                "architecture": arch,
                "signature_kind": sig_kind,
                "signer_identity": signer_id,
                "certificate_fingerprint": cert_fp,
                "signature_valid": sig_valid,
                "timestamp_valid": ts_valid,
                "checks": checks,
                "commit_sha": commit_sha.lower(),
                "workflow_run_id": workflow_run_id,
            }
            (Path(evidence_dir) / f"{name}.verification.json").write_text(
                json.dumps(evidence_doc, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    # Touch unused locals so linters/readers see clear path usage
    assert univ_apk_path.is_file() and arm64_apk_path.is_file()

    manifest_doc = {
        "schema_version": 3,
        "release_version": version,
        "minimum_supported_version": minimum_supported_version or version,
        "published_at": "1970-01-01T00:00:00Z",
        "release_notes_url": f"https://github.com/{repository}/releases/tag/v{version}",
        "packages": manifest_packages,
    }
    manifest_path = release_dir / "release-manifest.json"
    manifest_bytes = (json.dumps(manifest_doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    checksum_lines.append(f"{sha256_hex(manifest_bytes)}  release-manifest.json")

    (release_dir / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="ascii",
        newline="\n",
    )
    return release_dir


__all__ = [
    "DEFAULT_ANDROID_PACKAGE_ID",
    "DEFAULT_GITHUB_OWNER",
    "DEFAULT_GITHUB_REPO",
    "DEFAULT_MSI_UPGRADE_CODE",
    "DEFAULT_SIGNER_SUBJECT",
    "DEFAULT_TRUSTED_SIGNER_SHA256",
    "FakePage",
    "FakeWindow",
    "LocalReleaseServer",
    "PYTHON_EXE",
    "SimulatedAndroidBridge",
    "WORKTREE_ROOT",
    "WindowsShimEnv",
    "load_module",
    "make_elf64_so",
    "make_minimal_apk_bytes",
    "make_minimal_dex",
    "make_synthetic_aab",
    "make_synthetic_apk",
    "make_synthetic_lts_release_dir",
    "run_isolated_python",
    "sha256_hex",
    "stage_distribution_channel_asset",
]
