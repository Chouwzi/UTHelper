import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\core\client.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

import1 = "import requests"
import2 = "import requests\nimport json\nimport os\nimport requests.utils"

text = text.replace(import1, import2)

old_init = '''    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8"
        })
        self._portal_token: str = ""   # JWT from portal API — valid ~30 days'''

new_init = '''    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8"
        })
        self._portal_token: str = ""   # JWT from portal API — valid ~30 days
        self._cookie_file = "moodle_session.json"
        self._load_cookies()

    def _load_cookies(self):
        if os.path.exists(self._cookie_file):
            try:
                with open(self._cookie_file, 'r', encoding='utf-8') as f:
                    cookies_dict = json.load(f)
                    self.session.cookies.update(cookies_dict)
                logger.debug("Đã tải lại session cookie từ file.")
            except Exception as e:
                logger.error(f"Lỗi tải cookie: {e}")

    def _save_cookies(self):
        try:
            cookies_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
            with open(self._cookie_file, 'w', encoding='utf-8') as f:
                json.dump(cookies_dict, f)
            logger.debug("Đã lưu session cookie vào file.")
        except Exception as e:
            logger.error(f"Lỗi lưu cookie: {e}")'''

text = text.replace(old_init, new_init)

old_login_check = '''            if "login/index.php" not in login_res.url and "MoodleSession" in self.session.cookies.get_dict():
                logger.info("Đăng nhập UTH Moodle thành công.")
                return True'''

new_login_check = '''            if "login/index.php" not in login_res.url and "MoodleSession" in self.session.cookies.get_dict():
                logger.info("Đăng nhập UTH Moodle thành công.")
                self._save_cookies()
                return True'''

text = text.replace(old_login_check, new_login_check)

old_check = '''if not user or not pwd:'''
new_check = '''        # Optional fast-path if session is already valid?
        # A quick check can be avoided here because we do the check on fetch failing.
        # But we can try to do a fast return if valid.

        if not user or not pwd:'''

# We also want to fast check if logged in.
old_login_method = '''    @retry_with_backoff(retries=3, backoff_in_seconds=2)
    def login(self, username: str = None, password: str = None) -> bool:
        """
        Thực hiện đăng nhập vào hệ thống UTH Moodle.
        """
        user = username or settings.UTH_USERNAME
        pwd = password or settings.UTH_PASSWORD

        if not user or not pwd:'''

new_login_method = '''    @retry_with_backoff(retries=3, backoff_in_seconds=2)
    def login(self, username: str = None, password: str = None) -> bool:
        """
        Thực hiện đăng nhập vào hệ thống UTH Moodle.
        """
        user = username or settings.UTH_USERNAME
        pwd = password or settings.UTH_PASSWORD

        if "MoodleSession" in self.session.cookies.get_dict():
            try:
                # Test validity
                r = self.session.get(settings.MOODLE_BASE_URL, allow_redirects=False, timeout=5)
                if r.status_code == 200 and "login/index.php" not in r.url:
                    logger.info("Session vẫn còn hiệu lực, dùng lại session cũ.")
                    return True
            except: pass

        if not user or not pwd:'''

text = text.replace(old_login_method, new_login_method)


with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done client")
