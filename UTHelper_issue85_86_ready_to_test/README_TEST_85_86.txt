UTHelper - Source ket hop Issue #85 + Issue #86

Goi nay la mot ban source day du, da ket hop:
- Issue #85: Nhac deadline lap lai trong 24 gio cuoi (1/2/3/4/6 gio).
- Issue #86: Nhac lich hoc truc tiep truoc 60 phut.

Cach dung de cap nhat thu muc repo dang co:
1. Dong UTHelper va Visual Studio.
2. Giai nen ZIP nay vao mot thu muc tam.
3. Copy TOAN BO noi dung trong thu muc giai nen vao thu muc repo UTHelper hien tai.
4. Chon Replace/Overwrite khi Windows hoi.
5. KHONG xoa thu muc .git cua repo hien tai; ZIP nay khong chua .git.
6. Mo lai Visual Studio, chay `git status --short` de xem cac file thay doi.
7. Chay test Python theo lenh o phan duoi.

Test da chay trong moi truong tao goi:
PYTHONPATH=src;extensions/flet_uth_background_sync/src;.
python -m pytest tests/test_class_schedule_notifications.py tests/test_settings_form_snapshot.py tests/test_config.py tests/test_windows_notifier.py tests/test_notification_policy.py tests/test_repeat_reminder_bridge_payload.py -q --tb=short
Ket qua: 120 passed.

Da kiem tra:
- python -m compileall -q src extensions/flet_uth_background_sync/src
- git diff --check

Ghi chu:
- Chua build Android Gradle hoac iOS Xcode trong moi truong nay.
- De test Android/iOS tren thiet bi, can chay GitHub Actions build voi source nay sau khi push branch.
- Windows scheduler chi gui khi UTHelper con chay (co the thu nho xuong system tray).
- Android can cap quyen notifications va exact alarms (neu OS yeu cau).
- iOS su dung local notifications theo lich Portal da dong bo khi app dang mo; app khong tu fetch Portal khi bi suspend.
