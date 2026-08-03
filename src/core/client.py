import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional
from config import settings
import logging
import sys as _sys

logger = logging.getLogger(__name__)


def _normalize_draft_filepath(filepath: str) -> str:
    """Return a Moodle draft path with exactly one leading and trailing slash."""
    parts = str(filepath or "").replace("\\", "/").strip("/")
    return f"/{parts}/" if parts else "/"


@dataclass(frozen=True)
class DraftFileRecord:
    """The exact identity Moodle assigned to an uploaded draft file."""

    itemid: int
    filepath: str
    filename: str

    @property
    def identity(self) -> tuple[str, str]:
        return self.filepath, self.filename


@dataclass(frozen=True)
class DraftUploadResult:
    """Structured draft upload outcome without response content or credentials."""

    record: Optional[DraftFileRecord] = None
    error_code: str = ""


def _draft_upload_error_code(payload: object) -> str:
    """Return only an explicit, syntactically safe Moodle ``errorcode`` field."""
    if not isinstance(payload, dict) or "errorcode" not in payload:
        return ""
    raw_code = str(payload.get("errorcode", "")).strip().lower()
    if not raw_code:
        return "moodleerror"
    return raw_code if raw_code.replace("_", "").isalnum() else "moodleerror"

# Phát hiện hệ điều hành/nền tảng
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

# SSL: dùng urllib default trên MỌI platform
# Custom ssl.create_default_context() bị Cloudflare chặn (403).
# urllib default SSL không bị chặn. Nếu iOS gặp cert error → thêm certifi sau.


def _urlopen(req, timeout=15):
    """urlopen wrapper - dùng urllib default SSL."""
    return urllib.request.urlopen(req, timeout=timeout)


class MoodleClient:
    """HTTP client dùng urllib.request (stdlib) - zero dependency, mọi platform.
    
    Cloudflare KHÔNG chặn urllib (đã test).
    Chạy trên Desktop, iOS, Android mà không cần cài thêm gì.
    """
    
    # Giới hạn tần suất gọi API ở phía Client (Moodle không tự động bóp băng thông từ server-side)
    _MIN_INTERVAL = 0.05  # 50ms = tối đa 20 req/s (trước đây là 200ms - gây lãng phí 1.6s cho mỗi chu kỳ quét)

    def __init__(self):
        self._last_login_error = ""
        self._portal_token: str = ""   # JWT lấy từ portal API - thời hạn khoảng 30 ngày
        self._last_call_time: float = 0.0  # Nhãn thời gian đơn điệu (monotonic)

    def _throttle(self):
        """Ensure minimum interval between API calls."""
        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self._MIN_INTERVAL:
            time.sleep(self._MIN_INTERVAL - elapsed)
        self._last_call_time = time.monotonic()

    def close(self):
        """Không cần cleanup - urllib không dùng connection pool."""
        pass

    # Low-level HTTP helpers

    def _post(self, url: str, data: dict, timeout: float = 15) -> tuple:
        """POST form-encoded data. Returns (status, parsed_json_or_None)."""
        import gzip
        import random
        encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded, method='POST')
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Accept', 'application/json, */*')
        req.add_header('Accept-Language', 'en-US,en;q=0.9,vi;q=0.8')
        req.add_header('Accept-Encoding', 'gzip')
        
        max_attempts = 3
        for attempt in range(max_attempts):
            self._throttle()
            try:
                resp = _urlopen(req, timeout=timeout)
                body = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
                try:
                    return resp.status, json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    return resp.status, None
            except urllib.error.HTTPError as e:
                if e.code >= 500 and attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    logger.warning(
                        "POST %s trả về HTTP %d. Đang thử lại sau %.2fs...",
                        url.split('?')[0], e.code, backoff
                    )
                    time.sleep(backoff)
                    continue
                logger.warning("HTTP %d từ POST %s", e.code, url.split('?')[0])
                return e.code, None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                is_timeout = isinstance(e, TimeoutError) or "time" in str(e).lower()
                if attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    logger.warning(
                        "POST %s thất bại ở lần thử %d (%s): %s. Đang thử lại sau %.2fs...",
                        url.split('?')[0], attempt + 1, "Timeout" if is_timeout else "NetworkError", e, backoff
                    )
                    time.sleep(backoff)
                    continue
                else:
                    logger.error("POST %s thất bại sau %d lần thử: %s", url.split('?')[0], max_attempts, e)
                    raise e

    def _post_json(self, url: str, data: dict, timeout: float = 15) -> tuple:
        """POST JSON body. Returns (status, parsed_json_or_None)."""
        import gzip
        import random
        body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Content-Type', 'application/json')
        req.add_header('Accept', 'application/json')
        req.add_header('Accept-Encoding', 'gzip')
        
        max_attempts = 3
        for attempt in range(max_attempts):
            self._throttle()
            try:
                resp = _urlopen(req, timeout=timeout)
                resp_body = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip':
                    resp_body = gzip.decompress(resp_body)
                try:
                    return resp.status, json.loads(resp_body)
                except (json.JSONDecodeError, ValueError):
                    return resp.status, None
            except urllib.error.HTTPError as e:
                if e.code >= 500 and attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                logger.warning("HTTP %d from POST-JSON %s", e.code, url.split('?')[0])
                return e.code, None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                else:
                    logger.error("POST-JSON %s failed: %s", url.split('?')[0], e)
                    raise e

    def _post_multipart(self, url: str, fields: dict, files: dict, timeout: float = 60) -> tuple:
        """POST multipart/form-data (cho upload file). Returns (status, parsed_json_or_None)."""
        import uuid
        import gzip
        import random
        boundary = uuid.uuid4().hex
        
        body_parts = []
        # Các trường biểu mẫu (form fields)
        for key, val in fields.items():
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f'{val}\r\n'
            )
        # Các trường tệp tin (file fields)
        for field_name, (filename, file_bytes) in files.items():
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n'
            )
        body_parts.append(f'--{boundary}--\r\n')
        
        # Dựng thân yêu cầu dạng nhị phân (binary body)
        body = b''
        file_items = list(files.items())
        file_idx = 0
        for i, part in enumerate(body_parts):
            body += part.encode('utf-8')
            # Chèn dữ liệu byte của tệp ngay sau phần đầu (header) của mỗi phần tệp
            if i < len(body_parts) - 1 and 'filename=' in part:
                _, (_, file_bytes) = file_items[file_idx]
                body += file_bytes + b'\r\n'
                file_idx += 1
        
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
        req.add_header('Accept-Encoding', 'gzip')
        
        max_attempts = 2  # Kích thước tệp lớn, giảm số lần thử lại (retry)
        for attempt in range(max_attempts):
            self._throttle()
            try:
                resp = _urlopen(req, timeout=timeout)
                resp_body = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip':
                    resp_body = gzip.decompress(resp_body)
                try:
                    return resp.status, json.loads(resp_body)
                except (json.JSONDecodeError, ValueError):
                    return resp.status, None
            except urllib.error.HTTPError as e:
                if e.code >= 500 and attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(10.0, 1.0 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                logger.warning("HTTP %d from POST-MP %s", e.code, url.split('?')[0])
                return e.code, None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(10.0, 1.0 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                else:
                    logger.error("POST-MP %s failed: %s", url.split('?')[0], e)
                    raise e

    def _get(self, url: str, timeout: float = 15) -> tuple:
        """GET request. Returns (status, raw_bytes)."""
        import gzip
        req = urllib.request.Request(url)
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Accept-Encoding', 'gzip')
        
        try:
            resp = _urlopen(req, timeout=timeout)
            body = resp.read()
            if resp.info().get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            return resp.status, body
        except urllib.error.HTTPError as e:
            logger.warning("HTTP %d from GET %s", e.code, url.split('?')[0])
            return e.code, None


    # Web Services API (Dựa trên Token, không lưu trạng thái/stateless)

    def _get_ws_token(self, username: str = None, password: str = None, force: bool = False) -> str:
        """Lấy Web Services token từ Moodle.
        
        Token này stateless, không ảnh hưởng browser session.
        Valid rất lâu (~30 ngày), cache trong settings.
        """
        # Trả về cached token nếu có
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
        """Đăng nhập bằng WS token - KHÔNG tạo session, KHÔNG kick browser.
        
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
            
            # Kiểm tra xem token hết hạn hoặc không hợp lệ
            if isinstance(result, dict) and result.get('errorcode') in ('invalidtoken', 'accessexception'):
                logger.warning(f"WS token hết hạn hoặc không hợp lệ: {result.get('error', '')}")
                # Token đã hết hạn → bắt buộc làm mới (force refresh)
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
            
            # Kiểm tra các lỗi khác từ phía hệ thống
            if isinstance(result, dict) and 'exception' in result:
                errorcode = result.get('errorcode', '')
                message = result.get('message', result.get('error', 'Unknown'))
                # Các lỗi xác thực dữ liệu phía server (data validation) không thể thực hiện lại (non-retryable)
                if errorcode == 'invalidresponse':
                    logger.warning(f"WS API [{function}]: Server data validation (non-retryable): {message}")
                else:
                    logger.error(f"WS API error [{function}] ({errorcode}): {message}")
                return None
            
            return result
        except Exception as e:
            logger.error(f"Lỗi khi gọi WS API [{function}]: {e}")
            return None



    # Async Web Services API (Bất đồng bộ/non-blocking)

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

    def upload_draft_file_result(
        self,
        filename: str,
        file_bytes: bytes,
        itemid: int = 0,
        filepath: str = "/",
    ) -> DraftUploadResult:
        """Upload a file and return its identity or a sanitized Moodle error code."""
        token = self._get_ws_token()
        if not token:
            logger.error("Không có WS token để upload file.")
            return DraftUploadResult(error_code="missingtoken")

        form_data = {
            'token': token,
            'itemid': str(itemid),
            'filearea': 'draft',
            'filepath': _normalize_draft_filepath(filepath),
        }

        try:
            status, result = self._post_multipart(
                f"{settings.MOODLE_BASE_URL}/webservice/upload.php",
                fields=form_data,
                files={'file': (filename, file_bytes)},
                timeout=60.0,
            )
        except Exception as e:
            logger.error("Lỗi upload file '%s': %s", filename, e)
            return DraftUploadResult(error_code="transporterror")

        if not 200 <= status < 300:
            logger.warning("Draft upload failed with HTTP status %d", status)
            return DraftUploadResult(error_code="httpstatus")
        if isinstance(result, dict):
            return DraftUploadResult(
                error_code=_draft_upload_error_code(result) or "invalidresponse"
            )
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            logger.warning("Draft upload returned an unexpected response shape")
            return DraftUploadResult(error_code="invalidresponse")

        uploaded = result[0]
        error_code = _draft_upload_error_code(uploaded)
        if error_code:
            return DraftUploadResult(error_code=error_code)
        raw_itemid = uploaded.get("itemid")
        server_filename = uploaded.get("filename")
        server_filepath = uploaded.get("filepath")
        if isinstance(raw_itemid, bool) or not isinstance(server_filename, str) or not server_filename or not isinstance(server_filepath, str):
            logger.warning("Draft upload response did not include a valid file identity")
            return DraftUploadResult(error_code="invalidresponse")
        try:
            server_itemid = int(raw_itemid)
        except (TypeError, ValueError):
            logger.warning("Draft upload response did not include a valid item ID")
            return DraftUploadResult(error_code="invalidresponse")
        if server_itemid <= 0:
            logger.warning("Draft upload response contained a non-positive item ID")
            return DraftUploadResult(error_code="invalidresponse")
        return DraftUploadResult(
            record=DraftFileRecord(
                itemid=server_itemid,
                filepath=_normalize_draft_filepath(server_filepath),
                filename=server_filename,
            )
        )

    def upload_draft_file_record(
        self,
        filename: str,
        file_bytes: bytes,
        itemid: int = 0,
        filepath: str = "/",
    ) -> Optional[DraftFileRecord]:
        """Compatibility wrapper returning only the server-confirmed identity."""
        return self.upload_draft_file_result(
            filename, file_bytes, itemid, filepath
        ).record

    def upload_draft_file(self, filename: str, file_bytes: bytes,
                          itemid: int = 0,
                          author: str = None,
                          license_key: str = None) -> Optional[int]:
        """Compatibility wrapper returning only the Moodle draft item ID."""
        record = self.upload_draft_file_record(filename, file_bytes, itemid, "/")
        return record.itemid if record else None

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
