import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\core\client.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_login_fast = '''        if "MoodleSession" in self.session.cookies.get_dict():
            try:
                # Test validity
                r = self.session.get(settings.MOODLE_BASE_URL, allow_redirects=False, timeout=5)
                if r.status_code == 200 and "login/index.php" not in r.url:
                    logger.info("Session vẫn còn hiệu lực, dùng lại session cũ.")
                    return True
            except: pass'''

new_login_fast = '''        if "MoodleSession" in self.session.cookies.get_dict():
            try:
                # Dùng một trang cần auth để test xem session có bị văng ra trang login không
                r = self.session.get(f"{settings.MOODLE_BASE_URL}/my/", allow_redirects=True, timeout=5)
                if r.status_code == 200 and "login/index.php" not in r.url:
                    logger.info("Session lấy từ bộ nhớ vẫn còn hiệu lực, tăng tốc khởi động.")
                    return True
            except Exception as e:
                logger.debug(f"Test session bypass thất bại: {e}")'''

text = text.replace(old_login_fast, new_login_fast)
with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done client 2")
