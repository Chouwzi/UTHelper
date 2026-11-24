"""Multi-site WebView authentication bootstrap, URL canonicalization, and recovery."""

from __future__ import annotations

import inspect
import json
import logging
import posixpath
import re
import threading
import urllib.parse
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Literal, Optional

from core.quiz_player import _is_quiz_session_leased, quiz_session_key
from core.url_validation import validate_https_url

logger = logging.getLogger(__name__)

UthWebSite = Literal["courses", "thnn", "portal", "external"]
_AuthSite = Literal["courses", "thnn", "portal"]
_MoodleSite = Literal["courses", "thnn"]

_COURSES_HOST = "courses.ut.edu.vn"
_ELEARNING_ALIAS_HOST = "elearning.ut.edu.vn"
_THNN_HOST = "thnn.ut.edu.vn"
_PORTAL_HOST = "portal.ut.edu.vn"
_PORTAL_ORIGIN = f"https://{_PORTAL_HOST}"

_AUTH_SITES: frozenset[str] = frozenset({"courses", "thnn", "portal"})
_HOST_TO_SITE: dict[str, _AuthSite] = {
    _COURSES_HOST: "courses",
    _ELEARNING_ALIAS_HOST: "courses",
    _THNN_HOST: "thnn",
    _PORTAL_HOST: "portal",
}
_CANONICAL_HOST_FOR_SITE: dict[_AuthSite, str] = {
    "courses": _COURSES_HOST,
    "thnn": _THNN_HOST,
    "portal": _PORTAL_HOST,
}
_DEFAULT_URL_FOR_SITE: dict[_AuthSite, str] = {
    "courses": f"https://{_COURSES_HOST}/my/",
    "thnn": f"https://{_THNN_HOST}/my/",
    "portal": f"{_PORTAL_ORIGIN}/dashboard",
}
_MOODLE_LOGIN_PATHS: frozenset[str] = frozenset({"/login/index.php", "/login"})
_PORTAL_LOGIN_PATHS: frozenset[str] = frozenset({"", "/", "/login"})


@dataclass(frozen=True)
class WebViewAuthBootstrap:
    """Authentication bootstrap payload applied before loading a WebView URL."""

    target_url: str
    site: UthWebSite
    cookies: tuple[dict[str, Any], ...] = ()
    portal_init_script: str = ""


def _parse_uth_url(
    url: object,
) -> tuple[Optional[_AuthSite], Optional[urllib.parse.SplitResult]]:
    """Return ``(site, split_result)`` only for HTTPS UTH URLs with no explicit port."""
    validated = validate_https_url(url)
    if validated is None:
        return None, None
    parsed = urllib.parse.urlsplit(validated)
    if parsed.port is not None:
        return None, None
    host = (parsed.hostname or "").casefold()
    site = _HOST_TO_SITE.get(host)
    if site is None:
        return None, None
    return site, parsed


def _canonicalize_uth_url(site: _AuthSite, parsed: urllib.parse.SplitResult) -> str:
    """Canonicalize scheme and host (including ``elearning`` -> ``courses``)."""
    return urllib.parse.urlunsplit(
        (
            "https",
            _CANONICAL_HOST_FOR_SITE[site],
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def _normalize_url_path(path: str) -> str:
    """Unquote, collapse repeated slashes, resolve dot segments, and lowercase a URL path."""
    unquoted = urllib.parse.unquote(path or "")
    if not unquoted:
        return ""
    collapsed = re.sub(r"/+", "/", unquoted)
    if not collapsed.startswith("/"):
        collapsed = f"/{collapsed}"
    normalized = posixpath.normpath(collapsed)
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    return normalized.lower()


def classify_uth_webview_url(url: str) -> UthWebSite:
    """Classify a WebView URL as ``courses``, ``thnn``, ``portal``, or ``external``."""
    site, _ = _parse_uth_url(url)
    return site if site is not None else "external"


def is_uth_login_url(url: str) -> Optional[Literal["courses", "thnn", "portal"]]:
    """Return the UTH site key if ``url`` is a UTH login entry URL, else ``None``."""
    site, parsed = _parse_uth_url(url)
    if site is None or parsed is None:
        return None
    norm_path = _normalize_url_path(parsed.path)
    if site in ("courses", "thnn"):
        return site if norm_path in _MOODLE_LOGIN_PATHS else None
    if site == "portal":
        return "portal" if norm_path in _PORTAL_LOGIN_PATHS else None
    return None


def _is_uth_auth_flow_url(url: str) -> bool:
    """Return True for UTH login entry URLs and auth-flow subpaths (logout, password reset)."""
    if is_uth_login_url(url) is not None:
        return True
    site, parsed = _parse_uth_url(url)
    if site is None or parsed is None:
        return False
    norm_path = _normalize_url_path(parsed.path)
    if site in ("courses", "thnn"):
        return norm_path == "/login" or norm_path.startswith("/login/")
    if site == "portal":
        return (
            norm_path in _PORTAL_LOGIN_PATHS
            or norm_path.startswith("/login/")
            or norm_path == "/logout"
            or norm_path.startswith("/logout/")
        )
    return False


def _extract_safe_wantsurl(
    parsed: urllib.parse.SplitResult,
    expected_site: _MoodleSite,
) -> tuple[bool, Optional[str]]:
    """Extract and validate a same-site non-auth-flow ``wantsurl`` query parameter.

    Returns ``(has_wantsurl_param, safe_canonical_url)``.
    """
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    wants_values: list[str] = []
    for key, values in params.items():
        if key.lower() == "wantsurl":
            wants_values.extend(values)
    if not wants_values:
        return False, None
    if len(wants_values) != 1:
        return True, None
    validated = validate_https_url(wants_values[0])
    if validated is None:
        return True, None
    target_site, target_parsed = _parse_uth_url(validated)
    if target_site != expected_site or target_parsed is None:
        return True, None
    canonical_target = _canonicalize_uth_url(target_site, target_parsed)
    if _is_uth_auth_flow_url(canonical_target):
        return True, None
    return True, canonical_target


def normalize_uth_webview_target_url(url: str, *, has_credentials: bool = True) -> str:
    """Canonicalize UTH URLs, safely unwrap ``wantsurl``, and rewrite login targets."""
    if not isinstance(url, str) or not url:
        return ""
    site, parsed = _parse_uth_url(url)
    if site is None or parsed is None:
        return url
    canonical = _canonicalize_uth_url(site, parsed)
    login_site = is_uth_login_url(canonical)
    if login_site is None:
        return canonical
    if login_site in ("courses", "thnn"):
        has_wantsurl, safe_wantsurl = _extract_safe_wantsurl(parsed, login_site)
        if has_wantsurl:
            return (
                safe_wantsurl
                if safe_wantsurl is not None
                else _DEFAULT_URL_FOR_SITE[login_site]
            )
        if has_credentials:
            return _DEFAULT_URL_FOR_SITE[login_site]
        return canonical
    if has_credentials:
        return _DEFAULT_URL_FOR_SITE["portal"]
    return canonical


def _js_string_literal(value: str) -> str:
    """Serialize ``value`` as a JS/JSON string literal safe even in HTML contexts."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def build_portal_local_storage_script(token: str, role: str = "sv") -> str:
    """Return an origin-gated JS snippet injecting Portal JWT and role into ``localStorage``."""
    if not isinstance(token, str) or not token.strip():
        return ""
    clean_token = token.strip()
    clean_role = role.strip() if isinstance(role, str) and role.strip() else "sv"
    token_json = _js_string_literal(clean_token)
    role_json = _js_string_literal(clean_role)
    return (
        "(function () {"
        "try {"
        f'if (window.location && window.location.origin === "{_PORTAL_ORIGIN}") {{'
        f'window.localStorage.setItem("account", {token_json});'
        f'window.localStorage.setItem("role", {role_json});'
        "}"
        "} catch (_e) {}"
        "})();"
    )


def _normalize_cookie_dicts(
    raw: Any,
    *,
    default_domain: str = "",
) -> list[dict[str, Any]]:
    """Normalize cookie exports from ``MoodleClient`` or legacy test doubles."""
    if not raw:
        return []
    if isinstance(raw, dict):
        if "name" in raw and "value" in raw:
            items: list[Any] = [raw]
        else:
            items = [
                {
                    "name": str(name),
                    "value": str(value),
                    "domain": default_domain,
                    "domain_specified": False,
                    "path": "/",
                    "secure": True,
                    "expires": None,
                    "httpOnly": True,
                    "sameSite": None,
                }
                for name, value in raw.items()
                if str(name or "").strip() and value is not None
            ]
    elif isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
        items = list(raw)
    else:
        return []

    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = item.get("value")
        if not name or value is None:
            continue
        cookie_copy = dict(item)
        cookie_copy["name"] = name
        cookie_copy["value"] = str(value)
        domain = str(cookie_copy.get("domain") or "").strip()
        cookie_copy["domain"] = domain or default_domain
        path = str(cookie_copy.get("path") or "").strip()
        cookie_copy["path"] = path or "/"
        normalized.append(cookie_copy)
    return normalized


def _has_moodle_session_cookie(cookies: Iterable[dict[str, Any]]) -> bool:
    """Return True when exported cookies include a non-empty ``MoodleSession`` cookie."""
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip().casefold()
        value = cookie.get("value")
        if name.startswith("moodlesession") and bool(str(value or "").strip()):
            return True
    return False


def _merge_cookies(
    primary: Iterable[dict[str, Any]],
    sibling: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Merge primary and sibling Moodle cookies, keeping primary entries on key collisions."""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for group in (primary, sibling):
        for cookie in group:
            key = (
                str(cookie.get("name") or ""),
                str(cookie.get("domain") or "").strip().lstrip(".").casefold(),
                str(cookie.get("path") or "/").strip() or "/",
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(cookie)
    return tuple(merged)


def _filter_supported_kwargs(
    fn: Callable[..., Any],
    requested: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Filter ``requested`` kwargs to those accepted by ``fn``.

    Returns ``(supported_kwargs, accepts_arbitrary_kwargs)``.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return dict(requested), True
    if any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in sig.parameters.values()
    ):
        return dict(requested), True
    supported = {
        key: val
        for key, val in requested.items()
        if key in sig.parameters
        and sig.parameters[key].kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return supported, False


def _client_last_login_error(client: Any) -> str:
    """Read public ``last_login_error`` (falling back to ``_last_login_error`` for fakes)."""
    value = getattr(client, "last_login_error", None)
    if not isinstance(value, str):
        value = getattr(client, "_last_login_error", "")
    return value if isinstance(value, str) else ""


class WebViewRecoveryTracker:
    """Thread-safe per-site navigation memory and bounded 1-shot login recovery budget."""

    def __init__(self, *, max_attempts_per_site: int = 1) -> None:
        self._lock = threading.RLock()
        self._max_attempts = max(0, int(max_attempts_per_site))
        self._attempts: dict[str, int] = {}
        self._intended_urls: dict[str, str] = {}

    def record_navigation(self, url: str) -> None:
        """Record a canonicalized non-auth-flow UTH URL as the latest intended destination."""
        site, parsed = _parse_uth_url(url)
        if site is None or parsed is None:
            return
        canonical = _canonicalize_uth_url(site, parsed)
        if _is_uth_auth_flow_url(canonical):
            return
        with self._lock:
            self._intended_urls[site] = canonical

    def note_authenticated_landing(self, url: str) -> None:
        """Reset recovery budget for ``url``'s site upon landing on an authenticated UTH page."""
        site, parsed = _parse_uth_url(url)
        if site is None or parsed is None:
            return
        canonical = _canonicalize_uth_url(site, parsed)
        if _is_uth_auth_flow_url(canonical):
            return
        with self._lock:
            self._attempts[site] = 0
            self._intended_urls[site] = canonical

    def can_attempt_recovery(self, site: str) -> bool:
        """Return True iff ``site`` is a supported UTH site with recovery budget remaining."""
        if site not in _AUTH_SITES:
            return False
        with self._lock:
            return self._attempts.get(site, 0) < self._max_attempts

    def consume_recovery_attempt(self, site: str) -> bool:
        """Atomically consume one recovery attempt for ``site``; return False if exhausted."""
        if site not in _AUTH_SITES:
            return False
        with self._lock:
            used = self._attempts.get(site, 0)
            if used >= self._max_attempts:
                return False
            self._attempts[site] = used + 1
            return True

    def _refund_recovery_attempt(self, site: str) -> None:
        """Restore one recovery attempt when recovery was deferred without attempting reload."""
        if site not in _AUTH_SITES:
            return
        with self._lock:
            used = self._attempts.get(site, 0)
            if used > 0:
                self._attempts[site] = used - 1

    def intended_url_for(self, site: str) -> str:
        """Return the last recorded non-login URL for ``site``, or its default dashboard."""
        if site not in _AUTH_SITES:
            return ""
        with self._lock:
            recorded = self._intended_urls.get(site)
            if recorded:
                return recorded
        return _DEFAULT_URL_FOR_SITE[site]


class UthWebViewAuthService:
    """Coordinate pre-navigation auth bootstrap and bounded reactive login recovery."""

    def __init__(
        self,
        *,
        courses_client: Any = None,
        thnn_client: Any = None,
        portal_client: Any = None,
        credentials_provider: Optional[Callable[[], tuple[str, str]]] = None,
    ) -> None:
        self._courses_client = courses_client
        self._thnn_client = thnn_client
        self._portal_client = portal_client
        self._credentials_provider = credentials_provider

    def client_for_site(self, site: str) -> Any:
        """Return the underlying session client registered for ``site``, or ``None``."""
        if site == "courses":
            return self._courses_client
        if site == "thnn":
            return self._thnn_client
        if site == "portal":
            return self._portal_client
        return None

    def _read_credentials(self) -> tuple[str, str]:
        if self._credentials_provider is None:
            return "", ""
        try:
            raw = self._credentials_provider()
        except Exception as exc:
            logger.warning(
                "WebView credentials provider failed (%s)",
                type(exc).__name__,
            )
            return "", ""
        if not isinstance(raw, (tuple, list)) or len(raw) != 2:
            return "", ""
        username = raw[0].strip() if isinstance(raw[0], str) else ""
        password = raw[1] if isinstance(raw[1], str) else ""
        if not username or not password.strip():
            return "", ""
        return username, password

    def has_credentials(self, site: Optional[UthWebSite] = None) -> bool:
        """Return True when usable credentials and a matching site client exist."""
        if site in _AUTH_SITES and self.client_for_site(site) is None:
            return False
        return self._has_credentials(site)

    def _has_credentials(self, site: Optional[UthWebSite] = None) -> bool:
        if self._credentials_provider is not None:
            username, password = self._read_credentials()
            return bool(username and password)
        if site in ("courses", "thnn"):
            return self.client_for_site(site) is not None
        if site == "portal":
            return False
        if site is None:
            return self._courses_client is not None or self._thnn_client is not None
        return False

    def _has_active_quiz_lease(self, site: _MoodleSite, client: Any) -> bool:
        candidates: list[str] = []
        username, _ = self._read_credentials()
        if username:
            candidates.append(username)
        owner = getattr(client, "_session_owner", "")
        if isinstance(owner, str) and owner.strip() and owner.strip() not in candidates:
            candidates.append(owner.strip())
        for account_id in candidates:
            try:
                session_key = quiz_session_key(site, account_id)
            except ValueError:
                continue
            if _is_quiz_session_leased(session_key):
                return True
        return False

    @staticmethod
    def _invoke_cookies(
        client: Any,
        *,
        ensure_login: bool = True,
        verify_live: bool = False,
        force_relogin: bool = False,
        default_domain: str = "",
    ) -> list[dict[str, Any]]:
        """Invoke ``client.get_session_cookies`` adapting to narrow or legacy signatures."""
        getter = getattr(client, "get_session_cookies", None)
        if not callable(getter):
            return []
        requested = {
            "ensure_login": ensure_login,
            "verify_live": verify_live,
            "force_relogin": force_relogin,
        }
        supported, accepts_varkw = _filter_supported_kwargs(getter, requested)
        if (
            not ensure_login
            and not force_relogin
            and not accepts_varkw
            and "ensure_login" not in supported
        ):
            return []
        try:
            raw = getter(**supported)
        except Exception as exc:
            logger.warning(
                "Failed to fetch Moodle session cookies (%s)",
                type(exc).__name__,
            )
            return []
        return _normalize_cookie_dicts(raw, default_domain=default_domain)

    def _warm_sibling_cookies(self, site: _MoodleSite) -> list[dict[str, Any]]:
        sibling_site: _MoodleSite = "thnn" if site == "courses" else "courses"
        sibling = self.client_for_site(sibling_site)
        if sibling is None:
            return []
        return self._invoke_cookies(
            sibling,
            ensure_login=False,
            verify_live=False,
            force_relogin=False,
            default_domain=_CANONICAL_HOST_FOR_SITE[sibling_site],
        )

    @staticmethod
    def _invoke_portal_session(
        portal_client: Any,
        username: str,
        password: str,
        *,
        force: bool = False,
    ) -> tuple[str, str]:
        """Invoke ``portal_client.ensure_web_session`` safely without logging secrets."""
        ensure_fn = getattr(portal_client, "ensure_web_session", None)
        if not callable(ensure_fn):
            return "", ""
        try:
            if force:
                supported, _ = _filter_supported_kwargs(ensure_fn, {"force": True})
                result = ensure_fn(username, password, **supported)
            else:
                result = ensure_fn(username, password)
        except Exception as exc:
            logger.warning(
                "Failed to establish Portal web session (%s)",
                type(exc).__name__,
            )
            return "", ""

        if isinstance(result, (tuple, list)) and len(result) >= 2:
            token = result[0].strip() if isinstance(result[0], str) else ""
            role = result[1].strip() if isinstance(result[1], str) else ""
            return token, role or "sv"
        if isinstance(result, str):
            return result.strip(), "sv"
        return "", ""

    def prepare_initial_auth(self, url: str) -> WebViewAuthBootstrap:
        """Prepare target URL, Moodle cookies, or origin-gated Portal script before load."""
        site = classify_uth_webview_url(url)
        if site == "external":
            return WebViewAuthBootstrap(
                target_url=url,
                site="external",
                cookies=(),
                portal_init_script="",
            )

        if site in ("courses", "thnn"):
            primary = self.client_for_site(site)
            has_creds = self._has_credentials(site)

            primary_cookies: list[dict[str, Any]] = []
            sibling_cookies: list[dict[str, Any]] = []
            if has_creds and primary is not None:
                primary_cookies = self._invoke_cookies(
                    primary,
                    ensure_login=True,
                    verify_live=True,
                    force_relogin=False,
                    default_domain=_CANONICAL_HOST_FOR_SITE[site],
                )
            if has_creds:
                sibling_cookies = self._warm_sibling_cookies(site)
            merged_cookies = _merge_cookies(primary_cookies, sibling_cookies)
            target_url = normalize_uth_webview_target_url(
                url,
                has_credentials=_has_moodle_session_cookie(primary_cookies),
            )
            return WebViewAuthBootstrap(
                target_url=target_url,
                site=site,
                cookies=merged_cookies,
                portal_init_script="",
            )

        # site == "portal"
        portal_init_script = ""
        username, password = self._read_credentials()
        if self._portal_client is not None and username and password:
            token, role = self._invoke_portal_session(
                self._portal_client,
                username,
                password,
                force=False,
            )
            if token:
                portal_init_script = build_portal_local_storage_script(token, role)
        target_url = normalize_uth_webview_target_url(
            url,
            has_credentials=bool(portal_init_script),
        )
        return WebViewAuthBootstrap(
            target_url=target_url,
            site="portal",
            cookies=(),
            portal_init_script=portal_init_script,
        )

    def recover_from_login_redirect(
        self,
        login_url: str,
        tracker: WebViewRecoveryTracker,
    ) -> Optional[WebViewAuthBootstrap]:
        """Attempt one bounded forced re-authentication when WebView lands on a login URL."""
        site = is_uth_login_url(login_url)
        if site is None:
            return None

        if site in ("courses", "thnn"):
            client = self.client_for_site(site)
            if client is None or not self._has_credentials(site):
                return None
            if self._has_active_quiz_lease(site, client):
                logger.info(
                    "Skipping WebView login recovery for %s while quiz session lease is active",
                    site,
                )
                return None
            if not tracker.consume_recovery_attempt(site):
                return None

            _, parsed = _parse_uth_url(login_url)
            safe_wantsurl: Optional[str] = None
            if parsed is not None:
                _, safe_wantsurl = _extract_safe_wantsurl(parsed, site)
            target_url = safe_wantsurl or tracker.intended_url_for(site)

            client_lock = getattr(client, "_session_lock", None)
            lock_ctx = (
                client_lock
                if client_lock is not None
                and hasattr(client_lock, "__enter__")
                and hasattr(client_lock, "__exit__")
                else nullcontext()
            )
            with lock_ctx:
                primary_cookies = self._invoke_cookies(
                    client,
                    ensure_login=True,
                    verify_live=False,
                    force_relogin=True,
                    default_domain=_CANONICAL_HOST_FOR_SITE[site],
                )
                login_error = _client_last_login_error(client)
            if login_error == "quiz_session_active":
                tracker._refund_recovery_attempt(site)
                logger.info(
                    "Skipping WebView login recovery for %s while quiz session is active",
                    site,
                )
                return None
            if not _has_moodle_session_cookie(primary_cookies):
                return None

            sibling_cookies = self._warm_sibling_cookies(site)
            return WebViewAuthBootstrap(
                target_url=target_url,
                site=site,
                cookies=_merge_cookies(primary_cookies, sibling_cookies),
                portal_init_script="",
            )

        # site == "portal"
        if self._portal_client is None:
            return None
        username, password = self._read_credentials()
        if not (username and password):
            return None
        if not tracker.consume_recovery_attempt("portal"):
            return None

        target_url = tracker.intended_url_for("portal")
        token, role = self._invoke_portal_session(
            self._portal_client,
            username,
            password,
            force=True,
        )
        if not token:
            return None
        portal_init_script = build_portal_local_storage_script(token, role)
        if not portal_init_script:
            return None
        return WebViewAuthBootstrap(
            target_url=target_url,
            site="portal",
            cookies=(),
            portal_init_script=portal_init_script,
        )
