import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional
from config import settings
import logging
import sys as _sys

logger = logging.getLogger(__name__)

# ── Platform detection ──
_is_android = hasattr(_sys, '_ANDROID_') or 'android' in getattr(_sys, 'platform', '').lower()
_is_ios = _sys.platform == 'ios' or (
    _sys.platform == 'darwin' and os.environ.get('FLET_APP_STORAGE_DATA', '') != ''
)

if _is_android:
    _DEFAULT_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
elif _is_ios:
    _DEFAULT_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
else:
    _DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

# ── SSL: dùng urllib default trên MỌI platform ──
# Custom ssl.create_default_context() bị Cloudflare chặn (403).
# urllib default SSL không bị chặn. Nếu iOS gặp cert error → thêm certifi sau.


def _urlopen(req, timeout=15):
    """urlopen wrapper — dùng urllib default SSL."""
    return urllib.request.urlopen(req, timeout=timeout)


class MoodleClient:
    """HTTP client dùng urllib.request (stdlib) — zero dependency, mọi platform.
    
    Cloudflare KHÔNG chặn urllib (đã test).
    Chạy trên Desktop, iOS, Android mà không cần cài thêm gì.
    """
    
    # ── Client-side rate limiting (Moodle has NO server-side throttle) ──
    _MIN_INTERVAL = 0.05  # 50ms = max 20 req/s (was 200ms — caused 1.6s waste per cycle)

    def __init__(self):
        self._last_login_error = ""
        self._portal_token: str = ""   # JWT from portal API — valid ~30 days
        self._last_call_time: float = 0.0  # monotonic timestamp

    def _throttle(self):
        """Ensure minimum interval between API calls."""
        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self._MIN_INTERVAL:
            time.sleep(self._MIN_INTERVAL - elapsed)
        self._last_call_time = time.monotonic()

    def close(self):
        """Không cần cleanup — urllib không dùng connection pool."""
        pass

    # ─── Low-level HTTP helpers ──────────────────────────────

    def _post(self, url: str, data: dict, timeout: float = 15) -> tuple:
        """POST form-encoded data. Returns (status, parsed_json_or_None)."""
        encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded, method='POST')
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Accept', 'application/json, */*')
        req.add_header('Accept-Language', 'en-US,en;q=0.9,vi;q=0.8')
        
        self._throttle()
        try:
            resp = _urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            logger.warning("HTTP %d from POST %s", e.code, url.split('?')[0])
            return e.code, None
        body = resp.read()
        try:
            return resp.status, json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return resp.status, None

    def _post_json(self, url: str, data: dict, timeout: float = 15) -> tuple:
        """POST JSON body. Returns (status, parsed_json_or_None)."""
        body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Content-Type', 'application/json')
        req.add_header('Accept', 'application/json')
        
        self._throttle()
        try:
            resp = _urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            logger.warning("HTTP %d from POST-JSON %s", e.code, url.split('?')[0])
            return e.code, None
        resp_body = resp.read()
        try:
            return resp.status, json.loads(resp_body)
        except (json.JSONDecodeError, ValueError):
            return resp.status, None

    def _post_multipart(self, url: str, fields: dict, files: dict, timeout: float = 60) -> tuple:
        """POST multipart/form-data (cho upload file). Returns (status, parsed_json_or_None)."""
        import uuid
        boundary = uuid.uuid4().hex
        
        body_parts = []
        # Form fields
        for key, val in fields.items():
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f'{val}\r\n'
            )
        # File fields
        for field_name, (filename, file_bytes) in files.items():
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n'
            )
            # File bytes added separately (not string)
        body_parts.append(f'--{boundary}--\r\n')
        
        # Build binary body
        body = b''
        file_items = list(files.items())
        file_idx = 0
        for i, part in enumerate(body_parts):
            body += part.encode('utf-8')
            # Insert file bytes after the header of each file part
            if i < len(body_parts) - 1 and 'filename=' in part:
                _, (_, file_bytes) = file_items[file_idx]
                body += file_bytes + b'\r\n'
                file_idx += 1
        
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
        
        resp = _urlopen(req, timeout=timeout)
        resp_body = resp.read()
        try:
            return resp.status, json.loads(resp_body)
        except (json.JSONDecodeError, ValueError):
            return resp.status, None

    def _get(self, url: str, timeout: float = 15) -> tuple:
        """GET request. Returns (status, raw_bytes)."""
        req = urllib.request.Request(url)
        req.add_header('User-Agent', _DEFAULT_UA)
        
        try:
            resp = _urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            logger.warning("HTTP %d from GET %s", e.code, url.split('?')[0])
            return e.code, None
        return resp.status, resp.read()


    # ─── Web Services API (Token-based, stateless) ───────────────────

    def _get_ws_token(self, username: str = None, password: str = None, force: bool = False) -> str:
        """Lấy Web Services token từ Moodle.
        
        Token này stateless, không ảnh hưởng browser session.
        Valid rất lâu (~30 ngày), cache trong settings.
        """
        # Return cached token nếu có
        if not force and settings.MOODLE_WS_TOKEN:
            return settings.MOODLE_WS_TOKEN
        
        user = username or settings.UTH_USERNAME
        pwd = password or settings.UTH_PASSWORD
        
        if not user or not pwd:
            logger.warning("Chưa có thông tin đăng nhập để lấy WS token.")
            self._last_login_error = "missing_credentials"
            return ""
        
        try:
            status, data = self._post(
                f"{settings.MOODLE_BASE_URL}/login/token.php",
                {'username': user, 'password': pwd, 'service': 'moodle_mobile_app'},
                timeout=15
            )
            
            if data and 'token' in data:
                settings.MOODLE_WS_TOKEN = data['token']
                from config import save_settings
                save_settings()
                logger.info("Lấy WS API token thành công.")
                self._last_login_error = ""
                return data['token']
            elif data:
                error = data.get('error', 'Unknown error')
                logger.warning(f"Không lấy được WS token: {error}")
                if 'invalidlogin' in data.get('errorcode', ''):
                    self._last_login_error = "invalid_credentials"
                else:
                    self._last_login_error = "server_error"
                return ""
            else:
                logger.error("Response không phải JSON khi lấy WS token.")
                self._last_login_error = "server_error"
                return ""
        except Exception as e:
            logger.error(f"Lỗi khi lấy WS token: {e}")
            self._last_login_error = "network_error"
            return ""
    
    def login(self, username: str = None, password: str = None, force: bool = False) -> bool:
        """Đăng nhập bằng WS token — KHÔNG tạo session, KHÔNG kick browser.
        
        Đây là phương thức login duy nhất. Sử dụng Moodle WS API,
        hoạt động trên mọi nền tảng (iOS, Android, Desktop).
        """
        if force:
            settings.MOODLE_WS_TOKEN = ""
        token = self._get_ws_token(username, password, force=force)
        return bool(token)
    
    def call_ws_api(self, function: str, **params) -> Optional[dict]:
        """Gọi Moodle Web Services REST API.
        
        Args:
            function: Tên WS function (vd: core_calendar_get_action_events_by_timesort)
            **params: Tham số cho function
            
        Returns:
            JSON response dict, hoặc None nếu lỗi.
        """
        token = self._get_ws_token()
        if not token:
            return None
        
        request_params = {
            'wstoken': token,
            'wsfunction': function,
            'moodlewsrestformat': 'json',
        }
        request_params.update(params)
        
        try:
            status, result = self._post(
                f"{settings.MOODLE_BASE_URL}/webservice/rest/server.php",
                request_params,
                timeout=20
            )
            
            if result is None:
                logger.error(f"WS API [{function}] trả về response không phải JSON (status={status}).")
                return None
            
            # Check for token expiry or invalid token
            if isinstance(result, dict) and result.get('errorcode') in ('invalidtoken', 'accessexception'):
                logger.warning(f"WS token hết hạn hoặc không hợp lệ: {result.get('error', '')}")
                # Token expired → force refresh
                settings.MOODLE_WS_TOKEN = ""
                token = self._get_ws_token(force=True)
                if token:
                    request_params['wstoken'] = token
                    status, result = self._post(
                        f"{settings.MOODLE_BASE_URL}/webservice/rest/server.php",
                        request_params,
                        timeout=20
                    )
                    if result is None:
                        logger.error(f"WS API [{function}] retry trả về response không phải JSON (status={status}).")
                        return None
                else:
                    return None
            
            # Check for other errors
            if isinstance(result, dict) and 'exception' in result:
                errorcode = result.get('errorcode', '')
                message = result.get('message', result.get('error', 'Unknown'))
                # Server-side data validation errors are non-retryable
                if errorcode == 'invalidresponse':
                    logger.warning(f"WS API [{function}]: Server data validation (non-retryable): {message}")
                else:
                    logger.error(f"WS API error [{function}] ({errorcode}): {message}")
                return None
            
            return result
        except Exception as e:
            logger.error(f"Lỗi khi gọi WS API [{function}]: {e}")
            return None



    # ─── Async Web Services API (non-blocking) ───────────

    async def call_ws_api_async(self, function: str, **params) -> Optional[dict]:
        """Gọi Moodle WS API bất đồng bộ.
        
        Dùng asyncio.to_thread() để chạy sync call_ws_api trong thread pool.
        Vẫn non-blocking từ event loop perspective.
        """
        import asyncio
        return await asyncio.to_thread(self.call_ws_api, function, **params)


    def get_user_id(self) -> Optional[int]:
        """Lấy Moodle user ID từ core_webservice_get_site_info (cached).
        
        Cần cho core_files_upload (instanceid) và các API khác cần userid.
        """
        if hasattr(self, '_cached_user_id') and self._cached_user_id:
            return self._cached_user_id
        try:
            result = self.call_ws_api('core_webservice_get_site_info')
            if result and 'userid' in result:
                self._cached_user_id = int(result['userid'])
                return self._cached_user_id
        except Exception as e:
            logger.error(f"Lỗi khi lấy user ID: {e}")
        return None

    def upload_draft_file(self, filename: str, file_bytes: bytes,
                          itemid: int = 0,
                          author: str = None,
                          license_key: str = None) -> Optional[int]:
        """Upload file lên Moodle draft area qua /webservice/upload.php.
        
        Đây là endpoint chính thức Moodle dùng cho mobile app upload.
        Dùng multipart/form-data (không base64) → hiệu quả bộ nhớ hơn.
        """
        token = self._get_ws_token()
        if not token:
            logger.error("Không có WS token để upload file.")
            return None
        
        form_data = {
            'token': token,
            'itemid': str(itemid),
            'filearea': 'draft',
            'filepath': '/',
        }
        if author:
            form_data['author'] = author
        if license_key:
            form_data['license'] = license_key

        try:
            status, result = self._post_multipart(
                f"{settings.MOODLE_BASE_URL}/webservice/upload.php",
                fields=form_data,
                files={'file': (filename, file_bytes)},
                timeout=60.0,
            )
        except Exception as e:
            logger.error("Lỗi upload file '%s': %s", filename, e)
            return None
        
        if isinstance(result, list) and len(result) > 0:
            item = result[0]
            if 'itemid' in item:
                logger.info("Upload thành công '%s' → draft itemid=%s", filename, item['itemid'])
                return item['itemid']
        
        if isinstance(result, dict):
            logger.error("Upload lỗi: %s (code=%s)", 
                        result.get('error', ''), result.get('errorcode', ''))
        
        return None

    def download_file(self, url: str) -> Optional[bytes]:
        """Tải file từ Moodle server. Tự động append wstoken."""
        token = self._get_ws_token()
        if not token:
            return None
        
        sep = '&' if '?' in url else '?'
        authed_url = f"{url}{sep}token={token}"
        
        try:
            status, content = self._get(authed_url, timeout=60)
            if status == 200:
                return content
            logger.error("Download failed (HTTP %d): %s", status, url)
        except Exception as e:
            logger.error("Download error: %s", e)
        return None

    def delete_draft_file(self, draftitemid: int, filepath: str, filename: str) -> bool:
        """Xóa một file cụ thể từ draft area."""
        try:
            result = self.call_ws_api(
                'core_files_delete_draft_files',
                draftitemid=draftitemid,
                **{
                    'files[0][filepath]': filepath,
                    'files[0][filename]': filename,
                }
            )
        except Exception as e:
            logger.error("Lỗi xóa file '%s' từ draft %d: %s", filename, draftitemid, e)
            return False

        if isinstance(result, dict) and 'parentpaths' in result:
            logger.info("Đã xóa file '%s' từ draft %d", filename, draftitemid)
            return True
        
        if isinstance(result, dict) and 'exception' in result:
            logger.error("Xóa file lỗi: %s", result.get('message', ''))
        return False

    def get_portal_token(self, username: str = None, password: str = None) -> str:
        """Lấy JWT token từ Portal UTH (dùng cho autologin deep-link)."""
        if self._portal_token:
            return self._portal_token
        user = username or settings.UTH_USERNAME
        pwd  = password or settings.UTH_PASSWORD
        try:
            status, data = self._post_json(
                f"{settings.PORTAL_API_BASE}/user/login",
                {"username": user, "password": pwd},
                timeout=10,
            )
            if data and data.get("success") and data.get("token"):
                self._portal_token = data["token"]
                logger.info("Lấy Portal JWT token thành công.")
            elif data:
                logger.warning(f"Portal login không thành công: {data.get('message')}")
        except Exception as e:
            logger.error(f"Lỗi khi lấy Portal token: {e}")
        return self._portal_token

    def build_autologin_url(self, activity_url: str = "", username: str = None, password: str = None) -> str:
        """Tạo URL autologin dẫn thẳng tới activity/course."""
        token = self.get_portal_token(username, password)
        if not token:
            return activity_url
        if activity_url:
            autologin = (
                f"{settings.MOODLE_BASE_URL}/login/index.php"
                f"?token={token}"
                f"&wantsurl={urllib.parse.quote(activity_url, safe='')}"
            )
            return autologin
        return f"{settings.MOODLE_BASE_URL}/login/index.php?token={token}"

    @property
    def last_login_error(self) -> str:
        return getattr(self, '_last_login_error', '')
