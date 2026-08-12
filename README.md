<p align="center">
  <img src="src/assets/icon.png" alt="UTHelper Logo" width="140" />
</p>

<h1 align="center">UTHelper</h1>

<p align="center">
  <em>Theo dõi bài tập, thời hạn và lịch học từ hệ thống UTH E-learning.</em>
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
  <img src="https://img.shields.io/github/v/release/Chouwzi/UTHelper?style=flat-square" alt="Latest release" />
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/flet-0.86.5-7C4DFF?style=flat-square" alt="Flet" />
  <img src="https://img.shields.io/badge/tests-pytest%20%2B%20CI-22C55E?style=flat-square" alt="Tests: pytest and CI" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Android%20%7C%20iOS%20%7C%20Web-E8710A?style=flat-square" alt="Platform" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20NC-red?style=flat-square" alt="License: PolyForm Noncommercial" /></a>
</p>

---

UTHelper tổng hợp hoạt động học tập từ Moodle và lịch học từ UTH Portal trong
một giao diện gọn. Ứng dụng theo dõi bài tập, quiz, điểm danh, thời hạn, trạng
thái nộp bài và thay đổi điểm. Dữ liệu đăng nhập được lưu cục bộ bằng kho bí mật
của hệ điều hành khi nền tảng hỗ trợ.

UTHelper là dự án độc lập dành cho sinh viên. Dự án không đại diện cho Trường
Đại học Giao thông vận tải Thành phố Hồ Chí Minh và không thay thế các cổng
thông tin chính thức của trường.

## Tính năng

| Nhóm | Khả năng |
|---|---|
| Hoạt động học tập | Đồng bộ bài tập, quiz và điểm danh từ các Moodle site được hỗ trợ |
| Bài nộp | Xem trạng thái, tải lên, thêm, thay thế, đổi tên và xóa tệp khi Moodle cho phép |
| Lịch học | Hiển thị môn, khung giờ, phòng, cơ sở và trạng thái buổi học trong ngày |
| Điểm | Theo dõi thay đổi điểm và phát thông báo khi phát hiện dữ liệu mới |
| Nhắc hạn | Phân loại hoạt động theo mức khẩn cấp và mốc thời gian cấu hình được |
| Thông báo | Windows Toast, thông báo di động, Discord, Telegram và email |
| Làm việc ngoại tuyến | Đọc snapshot gần nhất trong lúc dịch vụ trường hoặc mạng không khả dụng |
| Cập nhật | Kiểm tra package đúng nền tảng, xác minh metadata và yêu cầu xác nhận trước khi cài |
| Giao diện | Sáu theme, bộ lọc theo môn và loại hoạt động, tìm kiếm, lịch và system tray trên Windows |

Khả năng sửa bài nộp phụ thuộc cấu hình, thời hạn và quyền do Moodle trả về.
Các thao tác bị khóa, bài nhóm hoặc loại bài không được hỗ trợ sẽ mở luồng web
thay vì cố ghi dữ liệu qua API.

## Nền tảng

| Nền tảng | Trạng thái | Gói phân phối |
|---|---|---|
| Windows 10/11 | Đường sử dụng chính | MSI và installer EXE |
| Android 8 trở lên | Được build và kiểm tra bằng CI | APK |
| iOS | Gói thiết bị không ký; người dùng phải tự ký lại | IPA không ký |
| Web | Dùng cho phát triển và chẩn đoán | Chạy từ source |

Tải bản phát hành tại [GitHub Releases](https://github.com/Chouwzi/UTHelper/releases).
Mỗi release production phải có manifest, checksum và bằng chứng xác minh tương
ứng với package công bố.

## Cài đặt từ source

Yêu cầu Python 3.11 trở lên. Các workflow CI hiện kiểm tra Python 3.12, 3.13 và
3.14.

```powershell
git clone https://github.com/Chouwzi/UTHelper.git
cd UTHelper

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[windows]"

python src\main.py
```

Chế độ Web dành cho phát triển:

```powershell
python src\main.py --web
```

Ứng dụng phục vụ tại `http://localhost:8561` nếu không cấu hình cổng khác.

## Phát triển

Cài project, extension native và công cụ kiểm tra:

```powershell
python -m pip install -e ".[windows]"
python -m pip install -e extensions\flet_uth_background_sync
python -m pip install pytest pytest-timeout ruff
```

Chạy các cổng kiểm tra chính:

```powershell
$env:PYTHONPATH = "src;extensions/flet_uth_background_sync/src;."
python -m pytest tests -q --tb=short
ruff check src tests scripts
python scripts\measure_ui_performance.py
```

Các test live dùng tài khoản hoặc course thật bị tắt theo mặc định. Không đưa
credential, cookie, token, HAR chưa che dữ liệu hoặc payload sinh viên vào
commit, fixture hay log CI.

Đọc [CONTRIBUTING.md](CONTRIBUTING.md) trước khi mở pull request. Repository dùng
Gitflow: tính năng và bugfix đi vào `develop`; release ổn định đi từ `develop`
sang `main`; hotfix từ `main` phải merge ngược lại `develop`. Quy tắc nhánh đầy
đủ nằm trong [hướng dẫn Gitflow](docs/guides/gitflow.md).

## Build package

### Android

```powershell
python -m pip install -e ".[android-build]"
.\scripts\build_android.ps1 -Target apk
```

APK cục bộ nằm trong `build/apk`. Build production còn yêu cầu version code
chuẩn và khóa ký do release workflow cung cấp.

### Windows

```powershell
python -m pip install -e ".[windows]"
.\scripts\build_installer.ps1
```

Entry point này build Flet bundle, kiểm tra cửa sổ và system tray, xác minh
bundle, rồi đóng gói MSI và Burn EXE bằng WiX. Xem
[Windows packaging](docs/guides/windows-packaging.md) để biết dependency, ký mã
và các cổng xác minh.

Danh mục script được duy trì tại [scripts/README.md](scripts/README.md). Không
dùng file sinh ra trong `build/` làm source hoặc commit chúng vào repository.

## Kiến trúc

```text
src/
├── core/             use case, Moodle/Portal service, cache và policy
├── gui/              Flet views, presenter, controller và composition root
├── notifiers/        các adapter thông báo
├── platform_utils/   tích hợp Windows, Android, iOS và package update
├── config.py         settings, migration và secure persistence
└── main.py           bootstrap, single-instance và Flet host

extensions/
└── flet_uth_background_sync/   bridge native cho đồng bộ và thông báo
```

Luồng phụ thuộc mong muốn là `gui -> core use cases -> service/adapters`. Core
không phụ thuộc GUI. Các quyết định kiến trúc có hiệu lực nằm trong
[docs/adr](docs/adr); kế hoạch và bằng chứng refactor nằm trong
[docs/architecture](docs/architecture).

## Bảo mật và quyền riêng tư

- Mật khẩu và token bí mật dùng Windows Credential Manager hoặc Keychain khi
  backend tương ứng khả dụng.
- HTML từ Moodle được làm sạch trước khi hiển thị.
- Request mạng có xác minh TLS và timeout hữu hạn.
- Chẩn đoán sự cố chỉ được gửi sau khi người dùng đồng ý.
- Updater xác minh package, metadata và chữ ký theo chính sách của từng nền
  tảng trước khi mở installer.

Đọc [chính sách quyền riêng tư](docs/PRIVACY.md) để biết dữ liệu chẩn đoán và
thời hạn lưu. Báo cáo lỗ hổng theo [SECURITY.md](SECURITY.md); không đăng
credential hoặc chi tiết khai thác chưa vá trong issue công khai.

## Tài liệu

[Chỉ mục tài liệu](docs/README.md) là điểm bắt đầu cho API contract, hướng dẫn
vận hành, test matrix, ADR và hồ sơ kiến trúc. Tài liệu trong `docs/archive`
chỉ lưu lịch sử thiết kế, không phải backlog hiện hành.

## Giấy phép

Mã nguồn được công bố theo
[PolyForm Noncommercial 1.0.0](LICENSE). Giấy phép cho phép sử dụng, nghiên cứu
và sửa đổi trong phạm vi phi thương mại; đây không phải giấy phép nguồn mở theo
định nghĩa OSI. Mọi hình thức sử dụng thương mại cần có thỏa thuận riêng với
tác giả.

UTHelper được duy trì bởi [@Chouwzi](https://github.com/Chouwzi).
