import json
import os
import urllib.parse
from typing import Optional
from config import settings
import logging
from core.network_utils import retry_with_backoff
import sys as _sys

# ── iOS/mobile detection ──
_is_android = hasattr(_sys, '_ANDROID_') or 'android' in getattr(_sys, 'platform', '').lower()
_is_ios = _sys.platform == 'ios' or (
    _sys.platform == 'darwin' and os.environ.get('FLET_APP_STORAGE_DATA', '') != ''
)
_is_mobile = _is_android or _is_ios

# ── HTTP client selection ──
# Desktop: curl_cffi (bypasses Cloudflare TLS fingerprint blocking)
# Mobile:  httpx (curl_cffi C-extensions don't work on iOS/Android)
_USE_CURL_CFFI = False
if not _is_mobile:
    try:
        from curl_cffi import requests as _cffi_requests
        _USE_CURL_CFFI = True
    except ImportError:
        pass

if not _USE_CURL_CFFI:
    import httpx

logger = logging.getLogger(__name__)

if _is_android:
    _DEFAULT_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
elif _is_ios:
    _DEFAULT_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
else:
    _DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


class MoodleClient:
    def __init__(self):
        self._last_login_error = ""
        self._portal_token: str = ""   # JWT from portal API — valid ~30 days
        
        if _USE_CURL_CFFI:
            # curl_cffi: impersonates Chrome TLS fingerprint → bypasses Cloudflare
            self.session = _cffi_requests.Session(
                impersonate="chrome124",
                timeout=15,
                headers={
                    "User-Agent": _DEFAULT_UA,
                    "Accept": "application/json, */*",
                    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
                },
            )
        else:
            # httpx: works on iOS/Android (pure Python friendly)
            try:
                import certifi
                verify_val = certifi.where()
            except ImportError:
                verify_val = True
            transport = httpx.HTTPTransport(retries=3)
            self.session = httpx.Client(
                transport=transport,
                verify=verify_val,
                follow_redirects=True,
                timeout=httpx.Timeout(15.0),
                headers={
                    "User-Agent": _DEFAULT_UA,
                    "Accept": "application/json, */*",
                    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
                },
            )

    def close(self):
        """Close the underlying HTTP client and release connection pool."""
        self.session.close()


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
            resp = self.session.post(
                f"{settings.MOODLE_BASE_URL}/login/token.php",
                data={
                    'username': user,
                    'password': pwd,
                    'service': 'moodle_mobile_app'
                },
                timeout=15
            )
            data = resp.json()
            
            if 'token' in data:
                settings.MOODLE_WS_TOKEN = data['token']
                from config import save_settings
                save_settings()
                logger.info("Lấy WS API token thành công.")
                self._last_login_error = ""
                return data['token']
            else:
                error = data.get('error', 'Unknown error')
                logger.warning(f"Không lấy được WS token: {error}")
                # Phân loại lỗi cho UI
                if 'invalidlogin' in data.get('errorcode', ''):
                    self._last_login_error = "invalid_credentials"
                else:
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
            resp = self.session.post(
                f"{settings.MOODLE_BASE_URL}/webservice/rest/server.php",
                data=request_params,
                timeout=20
            )
            try:
                result = resp.json()
            except (json.JSONDecodeError, ValueError):
                logger.error(f"WS API [{function}] trả về response không phải JSON (status={resp.status_code}).")
                return None
            
            # Check for token expiry or invalid token
            if isinstance(result, dict) and result.get('errorcode') in ('invalidtoken', 'accessexception'):
                logger.warning(f"WS token hết hạn hoặc không hợp lệ: {result.get('error', '')}")
                # Token expired → force refresh
                settings.MOODLE_WS_TOKEN = ""
                token = self._get_ws_token(force=True)
                if token:
                    request_params['wstoken'] = token
                    resp = self.session.post(
                        f"{settings.MOODLE_BASE_URL}/webservice/rest/server.php",
                        data=request_params,
                        timeout=20
                    )
                    try:
                        result = resp.json()
                    except (json.JSONDecodeError, ValueError):
                        logger.error(f"WS API [{function}] retry trả về response không phải JSON (status={resp.status_code}).")
                        return None
                else:
                    return None
            
            # Check for other errors
            if isinstance(result, dict) and 'exception' in result:
                logger.error(f"WS API error [{function}]: {result.get('message', result.get('error', 'Unknown'))}")
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
            'itemid': itemid,
            'filearea': 'draft',
            'filepath': '/',
        }
        if author:
            form_data['author'] = author
        if license_key:
            form_data['license'] = license_key

        try:
            resp = self.session.post(
                f"{settings.MOODLE_BASE_URL}/webservice/upload.php",
                data=form_data,
                files={
                    'file': (filename, file_bytes),
                },
                timeout=60.0,
            )
            result = resp.json()
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
            resp = self.session.get(authed_url, timeout=60)
            if resp.status_code == 200:
                return resp.content
            logger.error("Download failed (HTTP %d): %s", resp.status_code, url)
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
            r = self.session.post(
                f"{settings.PORTAL_API_BASE}/user/login",
                json={"username": user, "password": pwd},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=10,
            )
            data = r.json()
            if data.get("success") and data.get("token"):
                self._portal_token = data["token"]
                logger.info("Lấy Portal JWT token thành công.")
            else:
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
