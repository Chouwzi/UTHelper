import re
path = 'src/gui/app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

text = text.replace('on_test_tray=self._on_test_tray',
'''on_test_tray=self._on_test_tray,
            on_test_tele=self._on_test_tele,
            on_test_discord=self._on_test_discord,
            on_test_mail=self._on_test_mail''')

new_funcs = '''    def _test_notification_base(self):
        import random, datetime
        from models import Assignment, UrgencyLevel
        return Assignment(
            id=str(random.randint(10000, 99999)),
            title='BÀI KIỂM THỬ THÔNG BÁO UTH-ELEARNING-ALERT',
            type='Bài Tập Nộp',
            course_name='Công nghệ Phần mềm',
            deadline=datetime.datetime.now() + datetime.timedelta(hours=12),
            deadline_str='Ngay bây giờ',
            urgency=UrgencyLevel.CRITICAL,
            link='https://github.com/microsoft/vscode-copilot',
            status='Chưa nộp'
        )

    def _on_test_tray(self):
        # We pass it to the notifier manager directly to let Windows handle it
        dummy = self._test_notification_base()
        from notifiers.windows import WindowsNotifier
        WindowsNotifier().notify([dummy])

    def _on_test_tele(self):
        dummy = self._test_notification_base()
        from notifiers.telegram import TelegramNotifier
        TelegramNotifier().notify([dummy])

    def _on_test_discord(self):
        dummy = self._test_notification_base()
        from notifiers.discord import DiscordNotifier
        DiscordNotifier().notify([dummy])

    def _on_test_mail(self):
        dummy = self._test_notification_base()
        from notifiers.email import EmailNotifier
        EmailNotifier().notify([dummy])'''

text = re.sub(r'    def _on_test_tray\(self\).*?logger\.debug\(\'Da gui test notification\.\'\)', new_funcs, text, flags=re.DOTALL)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
