<p align="center">
  <img src="src/assets/icon.png" alt="UTHelper Logo" width="140" />
</p>

<h1 align="center">UTHelper</h1>

<p align="center">
  <em>🎓 Theo dõi deadline & bài tập UTH Elearning — Không bỏ lỡ bất kỳ deadline nào.</em>
</p>

<p align="center">
  <a href="https://github.com/Chouwzi/UTHelper/actions/workflows/ci.yml">
    <img src="https://github.com/Chouwzi/UTHelper/actions/workflows/ci.yml/badge.svg?branch=develop" alt="CI" />
  </a>
  <a href="https://github.com/Chouwzi/UTHelper/actions/workflows/build-android.yml">
    <img src="https://github.com/Chouwzi/UTHelper/actions/workflows/build-android.yml/badge.svg" alt="Android Build" />
  </a>
  <a href="https://github.com/Chouwzi/UTHelper/actions/workflows/build-ios.yml">
    <img src="https://github.com/Chouwzi/UTHelper/actions/workflows/build-ios.yml/badge.svg" alt="iOS Build" />
  </a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-2.1.0-blue?style=flat-square" alt="Version" />
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/flet-0.82+-7C4DFF?style=flat-square" alt="Flet" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Android%20%7C%20iOS-E8710A?style=flat-square" alt="Platform" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20NC-red?style=flat-square" alt="License: PolyForm Noncommercial" /></a>
</p>

---

## ✨ Tính năng chính

| Tính năng | Mô tả |
|-----------|-------|
| 📋 **Theo dõi deadline** | Tự động lấy tất cả bài tập, quiz, điểm danh từ Moodle |
| 🔔 **Cảnh báo thông minh** | Phân loại theo mức khẩn cấp: `Critical` · `Warning` · `Safe` |
| ⚡ **Moodle WS API** | Truy vấn trực tiếp qua Web Services API (~0.2s/lần) |
| 📱 **Đa nền tảng** | Windows desktop · Android · iOS (Flet) |
| 🎨 **6 Theme** | Midnight Blue · Ocean Teal · Sakura Pink · Nord Frost · Monokai Pro · Solarized Dark |
| 📣 **Đa kênh thông báo** | Windows Toast · Discord · Telegram · Email |
| 🔐 **Bảo mật** | Mật khẩu lưu trong Credential Manager / Keychain |
| 🖥️ **System Tray** | Chạy nền, tự động cập nhật theo lịch |
| 🔍 **Bộ lọc nâng cao** | Lọc theo môn, loại hoạt động, mức cấp bách, tag |

## 📸 Screenshots

> _Coming soon_

## 🚀 Bắt đầu nhanh

### Yêu cầu hệ thống

- **Python** 3.11+ 
- **Windows** 10/11 (desktop) hoặc **Android** 8+ / **iOS** 15+

### Cài đặt từ source

```bash
# Clone repository
git clone https://github.com/Chouwzi/UTHelper.git
cd UTHelper

# Tạo virtual environment (khuyến nghị)
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

# Cài dependencies
pip install -e ".[windows]"  # Windows (đầy đủ)
pip install -e .             # Cross-platform (core only)

# Chạy ứng dụng
python src/main.py
```

### Chạy chế độ web (cho test)

```bash
python src/main.py --web
# Mở http://localhost:8561
```

## 🏗️ Build

### Android APK

```bash
# Set encoding (bắt buộc trên Windows)
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# Build APK
flet build apk

# Output: build/apk/UTHelper.apk
```

### Windows Desktop

```bash
$env:PYTHONIOENCODING = 'utf-8'

# Build executable
flet build windows

# Cleanup bundle (giảm kích thước)
python scripts/post_build_cleanup.py

# Package portable folder
powershell scripts/package_flet_windows_bundle.ps1
```

### Yêu cầu build

| Tool | Mục đích | Ghi chú |
|------|----------|---------|
| Flutter SDK | Core engine | Tự động cài bởi Flet CLI |
| VS Build Tools 2022+ | Windows build | Cần C++ Desktop workload |
| Android SDK | Android build | Tự động qua Flutter |
| CMake | Native compilation | Đi kèm VS Build Tools |

### Troubleshooting

| Lỗi | Giải pháp |
|------|-----------|
| `UnicodeEncodeError: cp1252` | Set `$env:PYTHONIOENCODING = 'utf-8'` |
| `MSBuild not found` | Cài VS Build Tools với C++ Desktop workload |
| Bundle quá lớn | Chạy `python scripts/post_build_cleanup.py` |
| Port bị chiếm | Kill process: `Get-NetTCPConnection -LocalPort 8561` |

## 🏛️ Kiến trúc

```
src/
├── main.py                      # Entry point + crash handler
├── config.py                    # Settings (Pydantic) + keyring
├── models.py                    # Data models (Activity, Course)
│
├── core/                        # Business logic
│   ├── client.py                # MoodleClient (urllib + WS API)
│   ├── data_orchestrator.py     # Pipeline: WS API → activities
│   ├── ws_functions.py          # Moodle WS API wrappers
│   ├── parser.py                # HTML parser (fallback)
│   ├── security.py              # HTML sanitizer
│   ├── filter_service.py        # Smart filtering engine
│   └── display_utils.py         # Display helpers
│
├── gui/                         # UI layer (Flet)
│   ├── app_controller.py        # Main controller + navigation
│   ├── compact_desktop.py       # Desktop layout orchestrator
│   ├── tray.py                  # System tray (Windows)
│   ├── components/              # UI components
│   │   ├── activity_card.py     # Activity card widget
│   │   ├── detail_view.py       # Detail view + file manager
│   │   ├── login_dialog.py      # Login dialog
│   │   └── settings_view.py     # Settings (6 themes + colors)
│   └── core/                    # UI foundations
│       ├── theme.py             # 6 theme presets + color system
│       └── utils.py             # UI utilities
│
├── notifiers/                   # Notification channels
│   ├── manager.py               # Notification orchestrator
│   ├── windows.py               # Windows Toast notifications
│   ├── discord.py               # Discord webhook
│   ├── email.py                 # Email (SMTP/Gmail)
│   └── telegram.py              # Telegram bot
│
└── platform_utils/              # Platform abstraction
    ├── android.py               # Android-specific (storage, etc.)
    └── credentials.py           # Cross-platform credential store
```

## 🔐 Bảo mật

- ✅ Mật khẩu lưu trong **Windows Credential Manager** / **macOS Keychain** (không plaintext)
- ✅ HTML content từ Moodle được **sanitize** trước khi hiển thị
- ✅ SSL verification luôn bật, timeout trên mọi request
- ✅ Zero dependency HTTP client (stdlib `urllib.request`)

### Credentials

| Key | Mô tả |
|-----|-------|
| `UTH_PASSWORD` | Mật khẩu đăng nhập Moodle |
| `MOODLE_WS_TOKEN` | WS API token (tự động lấy) |
| `GMAIL_APP_PASSWORD` | App password Gmail |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL |

## 🧪 Testing

```bash
# Chạy unit tests
python -m pytest tests/ -q

# Với coverage
python -m pytest tests/ --cov=src --cov-report=html

# 44 tests covering:
#   ├── HTML parsing & sanitization
#   ├── Data orchestration
#   ├── WS API integration
#   ├── Filter service
#   ├── Notification manager
#   └── Credential security
```

## 🌿 Git Workflow

Project sử dụng **Gitflow** workflow:

```
main          ← Production releases (tagged)
  └─ develop  ← Integration branch
       ├─ feature/*   ← New features
       ├─ bugfix/*    ← Bug fixes
       └─ hotfix/*    ← Critical production fixes (branch from main)
```

### Quy ước commit

Sử dụng [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(gui): add theme switching with live preview
fix(client): handle timeout on slow connections  
docs: update README with build instructions
refactor(core): simplify WS API response parsing
perf(parser): reduce memory usage in HTML parsing
```

## 🛣️ Roadmap

- [x] Moodle WS API integration
- [x] 6 theme presets + custom colors
- [x] Multi-channel notifications
- [x] Android APK build
- [ ] iOS TestFlight build
- [ ] Tappable footer urgency counters
- [ ] Calendar view
- [ ] File download & re-upload workflow
- [ ] Welcome screen for first-time users

## 📄 License

[PolyForm Noncommercial 1.0.0](LICENSE)

> ⚠️ **Mã nguồn mở nhưng CẤM sử dụng thương mại.**
> Bạn được phép xem, sử dụng, sửa đổi cho mục đích cá nhân, học tập, nghiên cứu.
> **Không được** sao chép ý tưởng, bán, hoặc dùng cho mục đích thương mại mà không có sự đồng ý bằng văn bản của tác giả.

Made with ❤️ for UTH students by [@Chouwzi](https://github.com/Chouwzi).
