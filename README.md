# UTHelper - UTH Elearning Alert

Ứng dụng hỗ trợ sinh viên UTH theo dõi deadline, bài tập và thông báo từ hệ thống Elearning (Moodle).

## Tính năng

- **Theo dõi deadline tự động**: Hiển thị tất cả bài tập, quiz, điểm danh sắp tới
- **Thông báo thông minh**: Cảnh báo theo mức độ khẩn cấp (critical/warning/safe)
- **WS API Integration**: Sử dụng Moodle Web Services API cho tốc độ nhanh (0.2s)
- **Đa kênh thông báo**: Windows Toast, Discord, Telegram, Email
- **Bảo mật**: Mật khẩu lưu trong Windows Credential Manager
- **System Tray**: Chạy nền, tự động cập nhật
- **Bộ lọc thông minh**: Lọc theo môn học, loại hoạt động, mức độ cấp bách

## Cài đặt & Chạy

### Yêu cầu
- Windows 10/11
- Python 3.11+

### Chạy từ source
```bash
# Clone repo
git clone <repo-url>
cd UTH-Elearning-Alert

# Cài dependencies
pip install -e .

# Chạy ứng dụng
cd src
python main.py
```

### Build Windows executable
```bash
# Set encoding (required for Flet CLI on Windows)
$env:PYTHONIOENCODING = 'utf-8'

# Build bằng Flet CLI
flet build windows

# Cleanup để giảm kích thước bundle
python scripts/post_build_cleanup.py

# Package thành portable folder
powershell scripts/package_flet_windows_bundle.ps1
```

### Yêu cầu build
- Visual Studio Build Tools 2022+ với C++ Desktop workload
- Flutter SDK (tự động cài bởi Flet CLI)
- CMake (đi kèm VS Build Tools)

### Troubleshooting build
- **MSBuild errors**: Cài Visual Studio Build Tools với "C++ Desktop development" workload
- **Unicode errors (cp1252)**: Set `$env:PYTHONIOENCODING = 'utf-8'` trước khi build
- **Bundle quá lớn**: Chạy `python scripts/post_build_cleanup.py` sau build

## Kiến trúc

```
src/
├── main.py                    # Entry point
├── config.py                  # Settings + keyring integration
├── models.py                  # Pydantic data models
├── core/
│   ├── client.py              # MoodleClient (HTTP + WS API)
│   ├── data_orchestrator.py   # Data pipeline (WS API → activities)
│   ├── ws_functions.py        # Moodle WS API wrappers
│   ├── parser.py              # HTML parser (fallback)
│   ├── security.py            # HTML sanitizer
│   ├── filter_service.py      # Smart filtering
│   └── display_utils.py       # Display helpers
├── gui/
│   ├── app_controller.py      # Main app logic
│   ├── tray.py                # System tray
│   ├── components/            # UI components
│   │   ├── activity_card.py   # Activity card widget
│   │   ├── detail_view.py     # Detail view
│   │   ├── login_dialog.py    # Login dialog
│   │   └── settings_view.py   # Settings panel
│   └── core/                  # Theme, utils
├── notifiers/                 # Notification channels
│   ├── manager.py             # Notification orchestrator
│   ├── windows.py             # Windows Toast
│   ├── discord.py             # Discord webhook
│   ├── email.py               # Email (SMTP)
│   └── telegram.py            # Telegram bot
└── scripts/
    ├── post_build_cleanup.py  # Bundle size optimizer
    └── package_flet_windows_bundle.ps1
```

## Bảo mật

- Mật khẩu và tokens lưu trong **Windows Credential Manager** (không plaintext)
- HTML content từ Moodle được sanitize trước khi hiển thị
- Dependencies được cập nhật lên bản bảo mật mới nhất
- SSL verification luôn bật, timeout trên mọi request

## Cấu hình

Ứng dụng lưu cài đặt tại `%APPDATA%\UTHElearningAlert\settings.json`.

Secrets (mật khẩu, tokens) được lưu riêng trong Windows Credential Manager:
- `UTH_PASSWORD` - Mật khẩu đăng nhập
- `MOODLE_WS_TOKEN` - WS API token
- `GMAIL_APP_PASSWORD` - App password Gmail
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `DISCORD_WEBHOOK_URL` - Discord webhook

## Test

```bash
# Chạy unit tests
cd src
python -m pytest ../tests/ -q

# 44 tests covering:
# - HTML parsing & sanitization
# - Data orchestration
# - WS API integration
# - Filter service
# - Notification manager
# - Credential security
```

## License

MIT License
