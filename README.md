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
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-2.1.0-blue?style=flat-square" alt="Version" />
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/flet-0.85+-7C4DFF?style=flat-square" alt="Flet" />
  <img src="https://img.shields.io/badge/tests-314%20passed-22C55E?style=flat-square" alt="Tests" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Android%20%7C%20Web-E8710A?style=flat-square" alt="Platform" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20NC-red?style=flat-square" alt="License: PolyForm Noncommercial" /></a>
</p>

---

## ✨ Tính năng

| Tính năng | Mô tả |
|-----------|-------|
| 📋 **Theo dõi deadline** | Tự động lấy bài tập, quiz, điểm danh từ Moodle WS API |
| 📊 **Theo dõi điểm** | Giám sát thay đổi điểm theo thời gian thực, thông báo khi có điểm mới |
| 🔔 **Cảnh báo thông minh** | Phân loại `Khẩn cấp` · `Sắp hạn` · `An toàn` · `Quá hạn` |
| 📅 **Lịch học** | Xem lịch học theo tuần với deadline trực quan |
| ⚡ **Hiệu suất cao** | Startup ~4s, parallel API, grade N+1 optimization |
| 📱 **Đa nền tảng** | Windows desktop · Android APK · Web browser |
| 🎨 **6 Theme** | Midnight Blue · Ocean Teal · Sakura Pink · Nord Frost · Monokai Pro · Solarized Dark |
| 📣 **Đa kênh thông báo** | Windows Toast · Discord · Telegram · Email |
| 🔐 **Bảo mật** | Mật khẩu lưu trong Credential Manager / Keychain |
| 🖥️ **System Tray** | Chạy nền, tự động cập nhật theo lịch |
| 🔍 **Bộ lọc nâng cao** | Lọc theo môn, loại, mức cấp bách, tìm kiếm full-text |
| 🔄 **Smart Polling** | Tự động làm mới với interval tùy chỉnh |

## 📸 Screenshots

> _Coming soon_

## 🚀 Bắt đầu nhanh

### Yêu cầu hệ thống

- **Python** 3.11+ (hỗ trợ đến 3.14)
- **Windows** 10/11 (desktop) hoặc **Android** 8+

### Cài đặt từ source

```bash
# Clone repository
git clone https://github.com/Chouwzi/UTHelper.git
cd UTHelper

# Tạo virtual environment (khuyến nghị)
python -m venv .venv
.venv\Scripts\activate  # Windows

# Cài dependencies
pip install -e ".[windows]"  # Windows (đầy đủ)
pip install -e .             # Cross-platform (core only)

# Chạy ứng dụng
python src/main.py
```

### Chạy chế độ web (cho test/debug)

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
flet build windows
```

### Yêu cầu build

| Tool | Mục đích | Ghi chú |
|------|----------|---------|
| Flutter SDK | Core engine | Tự động cài bởi Flet CLI |
| VS Build Tools 2022+ | Windows build | Cần C++ Desktop workload |
| Android SDK | Android build | Tự động qua Flutter |

## 🏛️ Kiến trúc

```
src/
├── main.py                      # Entry point + crash handler
├── config.py                    # Settings (Pydantic) + keyring
├── models.py                    # Data models (Activity, Course)
│
├── core/                        # Business logic
│   ├── client.py                # MoodleClient (urllib + Cloudflare bypass)
│   ├── data_orchestrator.py     # Pipeline: WS API → activities (parallel fetch)
│   ├── ws_functions.py          # 30+ Moodle WS API wrappers
│   ├── grade_monitor.py         # Grade change detection (N+1 optimized)
│   ├── data_cache.py            # Thread-safe data cache
│   ├── filter_service.py        # Smart filtering engine
│   ├── time_utils.py            # Timezone-aware time helpers
│   ├── display_utils.py         # Display formatters
│   ├── security.py              # HTML sanitizer
│   ├── network_utils.py         # Network connectivity check
│   ├── notification_history.py  # Notification dedup + history
│   ├── update_checker.py        # GitHub release auto-update
│   └── background_scheduler.py  # Periodic background tasks
│
├── gui/                         # UI layer (Flet 0.85+)
│   ├── app_controller.py        # Main controller + navigation
│   ├── compact_desktop.py       # Desktop layout orchestrator
│   ├── tray.py                  # System tray (Windows)
│   ├── components/
│   │   ├── activity_card.py     # Activity card widget
│   │   ├── detail_view.py       # Detail view + file manager
│   │   ├── calendar_view.py     # Calendar view (weekly)
│   │   ├── grade_overview_view.py # Grade overview panel
│   │   ├── login_dialog.py      # Login dialog
│   │   └── settings_view.py     # Settings (6 themes + integrations)
│   └── core/
│       ├── theme.py             # 6 theme presets + color system
│       └── utils.py             # UI utilities
│
├── notifiers/                   # Notification channels
│   ├── manager.py               # Notification orchestrator
│   ├── windows.py               # Windows Toast notifications
│   ├── discord.py               # Discord webhook
│   ├── email.py                 # Email (SMTP/Gmail)
│   ├── telegram.py              # Telegram bot
│   └── mobile.py                # Android/iOS push notifications
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
- ✅ Không lưu credentials trong source code

## 🧪 Testing

```bash
# Chạy unit tests
cd src && python -m pytest ../tests/ -q

# Với coverage
python -m pytest ../tests/ --cov=. --cov-report=html

# 314 tests covering:
#   ├── Core modules (client, orchestrator, ws_functions)
#   ├── Grade monitoring & change detection
#   ├── Filter service & data cache
#   ├── Display utils & time utils
#   ├── Notification manager & history
#   ├── HTML parsing & sanitization
#   └── Credential security
```

### CI/CD

| Workflow | Trigger | Jobs |
|----------|---------|------|
| **CI** (`ci.yml`) | Push/PR to `develop`, `main` | 🔍 Lint (Ruff) · 🧪 Test (3.12/3.13/3.14) · 🔒 Security (pip-audit) |
| **Build Android** (`build-android.yml`) | Push to `main`, tags `v*` | 📱 Build APK/AAB · 📤 Upload artifact · 🏷️ Release |

## 🌿 Git Workflow

Gitflow workflow:

```
main          ← Production releases (tagged)
  └─ develop  ← Integration branch
       ├─ feature/*   ← New features
       ├─ bugfix/*    ← Bug fixes
       └─ hotfix/*    ← Critical production fixes
```

### Quy ước commit

[Conventional Commits](https://www.conventionalcommits.org/):

```
feat(gui): add theme switching with live preview
fix(client): handle timeout on slow connections
perf(grade): optimize N+1 API calls (37 → 0-2 per cycle)
docs: update README with build instructions
```

## 🛣️ Roadmap

- [x] Moodle WS API integration (30+ endpoints)
- [x] 6 theme presets + custom colors
- [x] Multi-channel notifications (Toast/Discord/Telegram/Email)
- [x] Android APK build
- [x] Grade monitoring & change alerts
- [x] Calendar view (weekly schedule)
- [x] Smart polling with configurable interval
- [x] Notification badge with unread count
- [x] Performance optimization (startup 15s → 4s)
- [x] CI/CD pipeline (lint + test + security)
- [ ] iOS TestFlight build
- [ ] File download & re-upload workflow
- [ ] Welcome screen for first-time users
- [ ] Offline mode with local cache

## 📄 License

[PolyForm Noncommercial 1.0.0](LICENSE)

> ⚠️ **Mã nguồn mở nhưng CẤM sử dụng thương mại.**
> Bạn được phép xem, sử dụng, sửa đổi cho mục đích cá nhân, học tập, nghiên cứu.
> **Không được** sao chép, bán, hoặc dùng cho mục đích thương mại mà không có sự đồng ý bằng văn bản của tác giả.

---

Made with ❤️ for UTH students by [@Chouwzi](https://github.com/Chouwzi).
