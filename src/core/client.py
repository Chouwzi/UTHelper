import requests
import json
import urllib.parse
import requests.utils
from requests.adapters import HTTPAdapter
from requests.exceptions import TooManyRedirects
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from typing import Optional
from config import settings
import logging
from core.network_utils import retry_with_backoff

logger = logging.getLogger(__name__)


class MoodleClient:
    def __init__(self):
        self.session = requests.Session()
        
        # Performance optimization: Connection pooling and auto-retries
        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        pool_size = max(10, settings.PREFETCH_WORKERS + 5)
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=pool_size, pool_maxsize=pool_size)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8"
        })
        self._portal_token: str = ""   # JWT from portal API — valid ~30 days
        self._load_cookies()

    def _load_cookies(self):
        if settings.MOODLE_SESSION:
            try:
                cookies_dict = json.loads(settings.MOODLE_SESSION)
                self.session.cookies.update(cookies_dict)
                logger.debug("Đã tải lại session cookie từ config.")
            except Exception as e:
                logger.error(f"Lỗi tải cookie từ config: {e}")

    def _save_cookies(self):
        try:
            cookies_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
            settings.MOODLE_SESSION = json.dumps(cookies_dict)
            from config import save_settings
            save_settings()
            logger.debug("Đã lưu session cookie vào config.")
        except Exception as e:
            logger.error(f"Lỗi lưu cookie: {e}")


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
                return data['token']
            else:
                error = data.get('error', 'Unknown error')
                logger.warning(f"Không lấy được WS token: {error}")
                return ""
        except Exception as e:
            logger.error(f"Lỗi khi lấy WS token: {e}")
            return ""
    
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
            result = resp.json()
            
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
                    result = resp.json()
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


    def get_portal_token(self, username: str = None, password: str = None) -> str:
        """Lấy JWT token từ Portal UTH (dùng cho autologin deep-link)."""
        if self._portal_token:
            return self._portal_token
        user = username or settings.UTH_USERNAME
        pwd  = password or settings.UTH_PASSWORD
        try:
            r = requests.post(
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
        """
        Tạo URL autologin dẫn thẳng tới activity/course mà không cần session trình duyệt.
        - Nếu activity_url là Moodle URL bình thường → wrap thành autologin + wantsurl
        - Trả về URL gốc nếu không lấy được token
        """
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

    @retry_with_backoff(retries=3, backoff_in_seconds=2)
    def login(self, username: str = None, password: str = None, force: bool = False) -> bool:
        """
        Thực hiện đăng nhập vào hệ thống UTH Moodle.
        """
        if force:
            self.session.cookies.clear()

        user = username or settings.UTH_USERNAME
        pwd = password or settings.UTH_PASSWORD

        if not force and "MoodleSession" in self.session.cookies.get_dict():
            try:
                # Không chuyển hướng để tránh loop 303. Nếu 303 là nó redirect ra trang Login
                r = self.session.get(f"{settings.MOODLE_BASE_URL}/my/", allow_redirects=False, timeout=5)
                if r.status_code == 200 or (r.status_code in (301, 302, 303) and "login/index.php" not in r.headers.get("Location", "")):
                    logger.info("Session lấy từ bộ nhớ vẫn còn hiệu lực, tăng tốc khởi động.")
                    return True
                else:
                    self.session.cookies.clear()
            except Exception as e:
                logger.debug(f"Test session bypass thất bại: {e}")
                self.session.cookies.clear()

        if not user or not pwd:
            logger.error("Chưa cung cấp thông tin tài khoản")
            return False

        try:
            # Bước 1: Lấy 'logintoken' từ trang đăng nhập
            res = self.session.get(settings.MOODLE_LOGIN_URL, timeout=15)
            res.raise_for_status()

            soup = BeautifulSoup(res.text, "lxml")
            token_input = soup.find("input", {"name": "logintoken"})
            
            if not token_input:
                logger.error("Không tìm thấy login token trên trang Moodle.")
                return False
                
            token = token_input.get("value")

            # Bước 2: Gửi yêu cầu đăng nhập (POST)
            payload = {
                "username": user,
                "password": pwd,
                "logintoken": token
            }
            
            # Tắt redirect để kiểm soát lỗi chuyển hướng vô tận (nếu có sự cố mismatch/loop)
            login_res = self.session.post(settings.MOODLE_LOGIN_URL, data=payload, timeout=15, allow_redirects=False)
            
            # Nếu Moodle xác thực thành công, nó sẽ trả về location tới trang testsession hoặc trang index
            if login_res.status_code in (301, 302, 303):
                loc = login_res.headers.get("Location", "")
                if loc and ("testsession" in loc or "my/" in loc or "courses.ut.edu.vn/?redirect=0" in loc):
                    logger.info("Đăng nhập UTH Moodle thành công.")
                    self._save_cookies()
                    return True
                elif loc and "login/index.php" not in loc:
                    # Trong vài trường hợp redirect ra chỗ khác
                    logger.info(f"Đăng nhập UTH Moodle có thể thành công (Location: {loc}).")
                    self._save_cookies()
                    return True
            
            # Nếu ở lại trang login hoặc bị redirect lại chính nó (tức là đăng nhập thất bại)
            if login_res.status_code == 200:
                # Nếu request về 200, thử tìm thông báo lỗi trên trang login
                login_soup = BeautifulSoup(login_res.text, "lxml")
                err_node = login_soup.find("div", {"class": "alert-danger"}) or login_soup.find("span", {"class": "error"})
                if err_node:
                    err_msg = err_node.text.strip()
                    logger.warning(f"Đăng nhập thất bại: {err_msg}")
                else:
                    logger.warning("Đăng nhập thất bại, vui lòng kiểm tra lại tài khoản/mật khẩu.")
            else:
                # Đối với trường hợp redirect (30x) quay lại trang login
                logger.warning("Đăng nhập thất bại (sai tài khoản hoặc mật khẩu).")
            
            self.session.cookies.clear()
            self._save_cookies()
            return False
            
        except Exception as e:
            logger.error(f"Lỗi khi đăng nhập: {str(e)}")
            return False

    @retry_with_backoff(retries=3)
    def fetch_calendar(self, month: int = None, year: int = None) -> Optional[str]:
        """
        Lấy nội dung trang lịch theo tháng/năm cụ thể.
        """
        url = f"{settings.MOODLE_BASE_URL}/calendar/view.php?view=month"
        if month and year:
            import datetime
            # Convert month and year to a timestamp representing the middle of the month
            # so timezone differences don't accidentally knock us into the previous month
            dt = datetime.datetime(year, month, 15)
            timestamp = int(dt.timestamp())
            url += f"&time={timestamp}"

        try:
            # allow_redirects=True is default, but we should be aware that it can follow many redirects
            res = self.session.get(url, timeout=15)
            res.raise_for_status()
            
            # Kiểm tra nếu bị chuyển hướng về trang đăng nhập -> Session hết hạn
            if "login/index.php" in res.url:
                logger.warning("Session hết hạn khi gọi fetch_calendar, tự động đăng nhập lại...")
                if self.login(force=True):
                    res = self.session.get(url, timeout=15)
                    res.raise_for_status()
                    if "login/index.php" in res.url:
                        return None
                else:
                    return None
                    
            return res.text
        except TooManyRedirects:
            logger.warning("Session bị vô hiệu hoá (TooManyRedirects). Đang tự động đăng nhập lại...")
            if self.login(force=True):
                try:
                    res = self.session.get(url, timeout=15)
                    res.raise_for_status()
                    return res.text
                except Exception as ex:
                    logger.error(f"Lỗi sau khi re-login lấy calendar: {str(ex)}")
            return None
        except Exception as e:
            logger.error(f"Lỗi khi lấy dữ liệu lịch: {str(e)}")
            return None

    @retry_with_backoff(retries=1)
    def fetch_url(self, url: str, timeout: int = 10) -> Optional[str]:
        """
        Lấy nội dung HTML của một đường dẫn bất kỳ bằng session hiện tại.
        """
        try:
            res = self.session.get(url, timeout=timeout)
            res.raise_for_status()
            
            # Kiểm tra nếu bị chuyển hướng về trang đăng nhập -> Session hết hạn
            if "login/index.php" in res.url:
                logger.warning(f"Session hết hạn khi gọi {url}, tự động đăng nhập lại...")
                if self.login(force=True):
                    res = self.session.get(url, timeout=timeout)
                    res.raise_for_status()
                    if "login/index.php" in res.url:
                        return None
                else:
                    return None
                    
            return res.text
        except TooManyRedirects:
            logger.warning(f"Session bị vô hiệu hoá (TooManyRedirects) khi gọi {url}. Đang tự động đăng nhập lại...")
            if self.login(force=True):
                try:
                    res = self.session.get(url, timeout=timeout)
                    res.raise_for_status()
                    return res.text
                except Exception as ex:
                    logger.error(f"Lỗi sau khi re-login lấy URL: {str(ex)}")
            return None
        except Exception as e:
            logger.error(f"Lỗi khi truy cập URL {url}: {str(e)}")
            return None
