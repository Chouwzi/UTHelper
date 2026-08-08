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
  <img src="https://img.shields.io/badge/flet-0.86.5-7C4DFF?style=flat-square" alt="Flet" />
  <img src="https://img.shields.io/badge/tests-448%20passed%20%7C%2022%20skipped-22C55E?style=flat-square" alt="Tests" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Android%20%7C%20iOS%20%7C%20Web-E8710A?style=flat-square" alt="Platform" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20NC-red?style=flat-square" alt="License: PolyForm Noncommercial" /></a>
</p>

---

## ✨ Tính năng

| Tính năng | Mô tả |
|-----------|-------|
| 📋 **Theo dõi deadline** | Lấy bài tập, quiz, điểm danh từ `courses.ut.edu.vn` và `thnn.ut.edu.vn` qua Moodle WS hoặc phiên web an toàn khi site không cấp WS |
| 📊 **Theo dõi điểm** | Giám sát thay đổi điểm theo thời gian thực, thông báo khi có điểm mới |
| 🔔 **Cảnh báo thông minh** | Phân loại `Khẩn cấp` · `Sắp hạn` · `An toàn` · `Quá hạn` |
| 📅 **Lịch học** | Xem lịch học theo tuần với deadline trực quan |
| ⚡ **Hiệu suất cao** | Startup ~4s, parallel API, grade N+1 optimization |
| 📱 **Đa nền tảng** | Windows MSI/EXE · Android APK · iOS IPA · Web browser |
| 🎨 **6 Theme** | Midnight Blue · Ocean Teal · Sakura Pink · Nord Frost · Monokai Pro · Solarized Dark |
| 📣 **Đa kênh thông báo** | Windows Toast · Discord · Telegram · Email |
| 🔐 **Bảo mật** | Mật khẩu lưu trong Credential Manager / Keychain |
| 🖥️ **System Tray** | Chạy nền; mở lại shortcut/Start Menu sẽ hiện cửa sổ đang ẩn |
| 🔄 **Cập nhật tin cậy** | Kiểm tra cập nhật mặc định bật; chỉ tải package đúng nền tảng sau xác minh và luôn hỏi trước khi cài |
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

# Build APK (tạo shell, vá receiver/desugaring, rồi build lại)
pip install -e ".[android-build]"
.\scripts\build_android.ps1 -Target apk

# Output: build/apk/*.apk
```

### Windows Desktop

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
.\scripts\build_installer.ps1
```

Lệnh trên build bundle Flet, tạo runner autostart không tham số, chạy verifier,
kiểm thử cửa sổ/tray và đóng gói cặp MSI + Burn EXE bằng WiX 7. Xem
[`docs/WINDOWS_EXE_PACKAGING.md`](docs/WINDOWS_EXE_PACKAGING.md) để chạy riêng từng
cổng bundle, verifier và installer.

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
- ✅ Chẩn đoán sự cố chỉ gửi sau khi đồng ý rõ ràng; xem [chính sách quyền riêng tư](docs/PRIVACY.md)

## 🧪 Testing

```bash
# Chạy unit tests
cd src && python -m pytest ../tests/ -q

# Với coverage
python -m pytest ../tests/ --cov=. --cov-report=html

# Current local baseline: 322 passed, 22 skipped.
#
# Test coverage includes:
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
| **CI** (`ci.yml`) | Push/PR to `develop`, `main` | 🔍 Lint (Ruff) · 🧪 Test (3.12/3.13/3.14) · 🔐 Private diagnostics · 🔒 Security (pip-audit) |
| **Build Android** (`build-android.yml`) | Push/PR to `main` | 📱 Diagnostic APK cố ý không cài được · 📤 Upload artifact |
| **Build iOS** (`build-ios.yml`) | Push/PR to `main` | 🍎 Simulator diagnostic ZIP, không giả dạng IPA |
| **Trusted Release** (`release.yml`) | Protected tag `v*` | ✅ Test đầy đủ · ký/xác minh IPA/APK/MSI/EXE · attestation · phát hành đúng 6 asset |

### Phát hành và tự động cập nhật

`Tự động kiểm tra cập nhật` mặc định bật, kể cả khi nâng cấp từ settings schema
cũ không có khóa này. Ứng dụng có thể kiểm tra và tải package đã xác minh, nhưng
không tự cài, tự thoát, tự khởi động lại hoặc tự mở App Store/TestFlight nếu chưa
có xác nhận rõ ràng của người dùng.

Release production chỉ được tạo từ protected `release` environment khi đủ chứng
thư Android, Apple và Windows. Inventory công khai bắt buộc đúng một IPA, APK,
MSI, Burn EXE, `release-manifest.json` và `SHA256SUMS`; thiếu một file hoặc chữ ký
sai sẽ không có release công khai. Xem
[`docs/WINDOWS_EXE_PACKAGING.md`](docs/WINDOWS_EXE_PACKAGING.md#protected-release-environment)
và [`ADR 0003`](docs/adr/0003-signed-release-update-channel.md).

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
- [x] iOS App Store Connect/TestFlight release pipeline (cần credential của owner)
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
