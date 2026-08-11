# 📡 Portal UTH — API Documentation (Reverse-Engineered)

> **Status**: Maintained reference. Captures and credentials must remain outside
> Git; only redacted endpoint contracts belong in this document.

> **Source**: `https://portal.ut.edu.vn`
> **Method**: Chrome DevTools network traffic capture (live session)
> **Date**: 2026-06-22
> **Auth**: JWT Bearer Token (HS256, 30-day expiry)
> **SPA Framework**: React Router
> **Backend**: Spring Boot (Java) — inferred from response headers

---

## Table of Contents

1. [Authentication](#-1-authentication)
2. [User / Profile](#-2-user--profile)
3. [Học tập (Academic)](#-3-học-tập-academic)
4. [Lịch học (Schedule)](#-4-lịch-học-schedule)
5. [Học phí (Tuition)](#-5-học-phí-tuition)
6. [Xét tốt nghiệp (Graduation)](#-6-xét-tốt-nghiệp-graduation)
7. [ĐKHP Điều kiện (Conditional Registration)](#-7-đkhp-điều-kiện)
8. [Dịch vụ / Giấy tờ (Student Papers)](#-8-dịch-vụ--giấy-tờ)
9. [Thông báo (Notifications)](#-9-thông-báo-notifications)
10. [Khảo sát & Sự kiện (Survey & Events)](#-10-khảo-sát--sự-kiện)
11. [External APIs](#-11-external-apis)
12. [Technical Details](#-technical-details)
13. [SPA Routes](#-spa-routes)
14. [Semester IDs](#-semester-ids)
15. [Sample Data](#-sample-data)

---

## 🔐 1. Authentication

### `POST /api/v1/user/login`

**Query Params:**
- `g-recaptcha-response` (string, required) — reCAPTCHA v2/v3 token

**Request Headers:**
```
Content-Type: application/json
Origin: https://portal.ut.edu.vn
Referer: https://portal.ut.edu.vn/
```

**Request Body:**
```json
{
  "username": "STUDENT_ID",
  "password": "YOUR_PASSWORD"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "status": 200,
  "message": "Đăng nhập thành công",
  "body": "sv",
  "token": "eyJ...REDACTED",
  "timestamp": "2026-06-22T08:45:09.027+00:00"
}
```

**JWT Token Payload (decoded):**
```json
{
  "data": {
    "id": 100625,
    "idGV": null,
    "role": "sv",
    "isFemale": false,
    "idKhoa": 33,
    "hoDem": "Nguyễn Văn",
    "ten": "A",
    "email": "student@example.com",
    "soDienThoai": "0900000000",
    "idKhoaHoc": 21,
    "idHeDaoTao": 1,
    "idLoaiDaoTao": 20,
    "idNganh": 1042,
    "idChuyenNganh": 1269,
    "idLopHoc": 6540
  },
  "sub": "STUDENT_ID",
  "iat": 1782117909,
  "exp": 1784709909
}
```

**Notes:**
- Token hết hạn sau **30 ngày** (`exp - iat = 2592000s`)
- `body` field chứa role: `"sv"` = sinh viên, có thể có `"gv"` = giảng viên
- reCAPTCHA token có thể lấy từ browser session hoặc reCAPTCHA Enterprise API
- Algorithm: HS256 (HMAC-SHA256)

---

## 👤 2. User / Profile

> Tất cả endpoint dưới đây cần header: `Authorization: Bearer {jwt_token}`

### `GET /api/v1/user/getSummaryProfile`

**Description:** Thông tin tóm tắt sinh viên (hiển thị trên dashboard)

**Response:**
```json
{
  "success": true,
  "status": 200,
  "message": null,
  "body": {
    "maSinhVien": "STUDENT_ID",
    "khoaHoc": "2024",
    "hoDem": "Nguyễn Văn",
    "ten": "A",
    "gioiTinh": false,
    "ngaySinh2": "01/01/2000",
    "noiSinhTinh": "TP.HCM",
    "soDienThoai": "0900000000",
    "heDaoTao": "Đại học - chính quy",
    "loaiHinhDT": "Tiên tiến",
    "nganh": "Công nghệ thông tin",
    "khoa": "Ban Công nghệ số",
    "chuyenNganh": "Công nghệ thông tin",
    "email": "student@example.com",
    "svNganh2": false,
    "isAllowChangeAvatar": true
  },
  "token": null,
  "timestamp": "2026-06-22T08:45:09.491+00:00"
}
```

### `GET /api/v1/user/profile`

**Description:** Thông tin đầy đủ kèm ảnh base64 (response rất lớn ~470KB do chứa ảnh)

**Response:** Cùng format nhưng `body` chứa thêm field `image` dạng `data:image/png;base64,...`

### `GET /api/v1/user/image`

**Description:** Chỉ lấy ảnh đại diện

### `GET /api/v1/user/check`

**Description:** Kiểm tra trạng thái tài khoản (active, locked, etc.)

### `GET /api/v1/user/getCredit`

**Description:** Số dư tài khoản UTH

**Response:**
```json
{
  "success": true,
  "status": 200,
  "body": "0.00Đ (UTH)",
  "token": null,
  "timestamp": "2026-06-22T08:45:10.414+00:00"
}
```

---

## 📚 3. Học tập (Academic)

### `GET /api/v1/hoctap/tiendo`

**Description:** Tiến độ tích lũy tín chỉ

**Response:**
```json
{
  "success": true,
  "status": 200,
  "body": {
    "canDat": 120,
    "hienTai": 61
  }
}
```
- `canDat`: Tổng tín chỉ cần đạt để tốt nghiệp
- `hienTai`: Tín chỉ đã tích lũy

### `GET /api/v1/hoctap/hocky`

**Description:** Danh sách tất cả học kỳ

**Response:**
```json
{
  "success": true,
  "status": 200,
  "body": [
    {"id": 76, "tenDot": "Học kỳ hè năm học 2025-2026"},
    {"id": 75, "tenDot": "Học kỳ 2 năm học 2025-2026"},
    {"id": 74, "tenDot": "Học kỳ 1 năm học 2025-2026"},
    {"id": 73, "tenDot": "Học kỳ hè năm học 2024-2025"},
    {"id": 72, "tenDot": "Học kỳ 2 năm học 2024-2025"},
    {"id": 71, "tenDot": "Học kỳ 1 năm học 2024-2025"}
  ]
}
```

### `GET /api/v1/hoctap/kqtheoky/{idDot}`

**Description:** Kết quả học tập theo học kỳ (điểm các môn)

**Params:** `idDot` — ID của học kỳ (lấy từ API `hocky`)

**Response:** Mảng điểm các môn trong kỳ (rỗng nếu chưa có kết quả)

### `GET /api/v1/hoctap/montheoky/{idDot}`

**Description:** Danh sách môn học đăng ký trong kỳ

**Response:**
```json
{
  "success": true,
  "status": 200,
  "body": [
    {"id": 172754, "maHocPhan": "0120005105", "tenMonHoc": "Triết học Mác - Lênin", "soTinChi": 3},
    {"id": 172723, "maHocPhan": "0120122010", "tenMonHoc": "XD phần mềm hướng đối tượng", "soTinChi": 3},
    {"id": 176506, "maHocPhan": "TT04", "tenMonHoc": "General English 4", "soTinChi": 0},
    {"id": 177258, "maHocPhan": "006124", "tenMonHoc": "Tin học cơ bản", "soTinChi": 0},
    {"id": 180522, "maHocPhan": "0120121003", "tenMonHoc": "Hệ quản trị cơ sở dữ liệu", "soTinChi": 3},
    {"id": 180519, "maHocPhan": "0120121137", "tenMonHoc": "Quản trị doanh nghiệp CNTT", "soTinChi": 3}
  ]
}
```

### `GET /api/v1/hoctap/bangdiem2`

**Description:** Bảng điểm toàn khóa (full transcript tất cả các kỳ)

### `GET /api/v1/hoctap/getChungChi`

**Description:** Danh sách chứng chỉ (tiếng Anh, tin học, ...)

### `GET /api/v1/hoctap/getKetQuaDauVao`

**Description:** Kết quả đầu vào (điểm thi/xét tuyển)

### `GET /api/v1/hoctap/diemMien`

**Description:** Điểm được miễn (chuyển đổi từ trường khác, chứng chỉ quốc tế, ...)

### `GET /api/v1/hoctap/chuongtrinhkhung`

**Description:** Chương trình khung đào tạo (curriculum framework — tất cả môn trong chương trình)

---

## 📅 4. Lịch học (Schedule)

### `GET /api/v1/lichhoc/thang?date={YYYY-MM-DD}`

**Description:** Lịch học theo tháng (dùng ngày đầu tháng)

**Params:** `date` — Ngày bất kỳ trong tháng, format `YYYY-MM-DD` (VD: `2026-06-01`)

**Response:**
```json
{
  "success": true,
  "status": 200,
  "body": [
    {
      "date": "29/06/2026",
      "total": 2,
      "subjects": [
        {"name": "Lập trình Web", "nameToDisplay": "Lập trình Web", "color": "#50E3C2"},
        {"name": "Lập trình mạng", "nameToDisplay": "Lập trình mạng", "color": "#9013FE"}
      ]
    },
    {
      "date": "27/06/2026",
      "total": 1,
      "subjects": [
        {"name": "Thiết kế mạng", "nameToDisplay": "Thiết kế mạng", "color": "#4A90E2"}
      ]
    }
  ]
}
```

### `GET /api/v1/lichhoc/lichTuan?date={YYYY-MM-DD}`

**Description:** Lịch học theo tuần (chi tiết hơn — có tiết, phòng, giảng viên)

**Params:** `date` — Ngày bất kỳ trong tuần, format `YYYY-MM-DD`

### `GET /api/v1/lichhoc/songayhoc`

**Description:** Tổng số ngày đã học (attendance tracking)

---

## 💰 5. Học phí (Tuition)

### `GET /api/v1/hocphi?idDot={idDot}`

**Description:** Chi tiết học phí theo học kỳ

**Params:** `idDot` — ID học kỳ (lấy từ `hoctap/hocky`)

### `GET /api/v1/hocphi/khoanthukhac`

**Description:** Các khoản thu khác ngoài học phí (bảo hiểm, ký túc xá, ...)

---

## 🎓 6. Xét tốt nghiệp (Graduation)

### `GET /api/v1/xetTotNghiep/getChungChi`

**Description:** Chứng chỉ liên quan đến điều kiện tốt nghiệp

### `GET /api/v1/xetTotNghiep/xetTotNghiepInfo`

**Description:** Thông tin xét tốt nghiệp tổng quan

### `GET /api/v1/xetTotNghiep/getDotXetTotNghiep`

**Description:** Danh sách đợt xét tốt nghiệp

### `GET /api/v1/xetTotNghiep/xetTotNghiep?idDot={idDot}`

**Description:** Kết quả xét tốt nghiệp theo đợt

**Params:** `idDot` — ID đợt xét (lấy từ `getDotXetTotNghiep`). Dùng `idDot=0` cho đợt mặc định.

---

## 📝 7. ĐKHP Điều kiện

### `GET /api/v1/dkhpdk/getDot`

**Description:** Đợt ĐKHP điều kiện hiện tại (conditional course registration period)

---

## 📄 8. Dịch vụ / Giấy tờ (Student Papers)

### `GET /api/v1/dichVu/getAll`

**Description:** Danh sách dịch vụ có thể đặt (bảng điểm, giấy xác nhận, ...)

### `GET /api/v1/order/getAll`

**Description:** Đơn đặt dịch vụ của sinh viên (lịch sử đặt)

### `GET /api/v1/address`

**Description:** Địa chỉ nhận giấy tờ

---

## 🔔 9. Thông báo (Notifications)

### `GET /api/v1/notification/category`

**Description:** Danh mục thông báo (VD: thông báo chung, đào tạo, ...)

**Note:** Không cần auth (public API)

### `GET /api/v1/notification?categoryId={id}&page={n}&size={n}`

**Description:** Danh sách thông báo theo danh mục (phân trang)

**Params:**
- `categoryId` — ID danh mục (VD: `368` = Thông báo chung)
- `page` — Trang (bắt đầu từ 1)
- `size` — Số item mỗi trang (VD: 10)

**Note:** Không cần auth (public API)

### `GET /api/v1/notification/getPopup`

**Description:** Thông báo popup (hiển thị khi vào dashboard)

### `GET /api/v1/notification/getNote`

**Description:** Ghi chú cá nhân của sinh viên

---

## 📊 10. Khảo sát & Sự kiện

### `GET /api/v1/survey/checkSurvey`

**Description:** Kiểm tra có khảo sát nào đang mở không

### `GET /api/v1/eventWeb/getAll`

**Description:** Danh sách sự kiện trên portal

**Response:**
```json
{
  "success": true,
  "status": 200,
  "body": [
    {"id": 68, "name": "KHÁM PHÁ CHUYÊN NGÀNH KỸ THUẬT NĂNG LƯỢNG GIÓ..."},
    {"id": 67, "name": "KHÁM PHÁ CHUYÊN NGÀNH HỆ THỐNG ĐIỀU KHIỂN..."},
    {"id": 57, "name": "Tuyển sinh UTH 2026"},
    {"id": 56, "name": "UTH xét tuyển kết hợp"}
  ]
}
```

---

## 🌐 11. External APIs

Phát hiện từ JS bundles:

| Endpoint | Description |
|----------|-------------|
| `https://{host}/api/GetGradeByCourse.php` | Tra cứu điểm theo môn (PHP backend, external host) |
| `https://uth-api-online-nh.ut.edu.vn/api/getdatanhaphoc.php?token={token}` | Dữ liệu nhập học online (separate server) |

---

## 🔧 Technical Details

### Common Request Headers (Authenticated)
```http
Authorization: Bearer {jwt_token}
Accept: application/json
Content-Type: application/json
Origin: https://portal.ut.edu.vn
Referer: https://portal.ut.edu.vn/{current_page}
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```

### Standard Response Format
Tất cả API đều trả về cùng format:
```json
{
  "success": true,
  "status": 200,
  "message": null,
  "body": "...",
  "token": null,
  "timestamp": "2026-06-22T08:45:09.027+00:00"
}
```

- `success` — boolean, true nếu thành công
- `status` — HTTP status code
- `message` — Thông báo lỗi hoặc null
- `body` — Dữ liệu chính (object, array, hoặc string)
- `token` — JWT mới (chỉ có khi login)
- `timestamp` — ISO 8601 timestamp (UTC)

### Security Headers (Response)
```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 0
Cache-Control: no-cache, no-store, max-age=0, must-revalidate
```

### CORS Configuration
```
Access-Control-Allow-Origin: https://portal.ut.edu.vn
Access-Control-Allow-Credentials: false
Access-Control-Expose-Headers: Content-Disposition, Link
```

### Google Analytics
- Tracking ID: `G-SJ8NQZKEC7`
- GTM container: `45je66h0h1v9238017498za200zd9238017498`

---

## 🗺️ SPA Routes

| Route | Page | APIs Triggered |
|-------|------|----------------|
| `/` | Login page | `notification/category`, `notification?categoryId=` |
| `/dashboard` | Trang chủ | `getSummaryProfile`, `image`, `check`, `getCredit`, `tiendo`, `hocky`, `kqtheoky/{id}`, `montheoky/{id}`, `lichhoc/thang`, `lichhoc/songayhoc`, `notification/getPopup`, `notification/getNote`, `survey/checkSurvey`, `eventWeb/getAll` |
| `/inforDetail` | Thông tin cá nhân | `user/profile`, `image`, `eventWeb/getAll` |
| `/calendar` | Lịch học | `lichhoc/lichTuan?date=`, `image`, `eventWeb/getAll` |
| `/educationprogram` | Chương trình đào tạo | `hoctap/chuongtrinhkhung`, `image`, `eventWeb/getAll` |
| `/transcript` | Bảng điểm | `hoctap/bangdiem2`, `hoctap/getChungChi`, `hoctap/getKetQuaDauVao`, `hoctap/diemMien`, `image`, `eventWeb/getAll` |
| `/studentpaper` | Giấy tờ sinh viên | `dichVu/getAll`, `order/getAll`, `address`, `image`, `eventWeb/getAll` |
| `/coursesregistration` | ĐKHP | `image`, `eventWeb/getAll` |
| `/conditional` | ĐKHP điều kiện | `dkhpdk/getDot`, `image`, `eventWeb/getAll` |
| `/tuition` | Học phí | `hocphi?idDot=`, `hocphi/khoanthukhac`, `hoctap/hocky`, `image`, `eventWeb/getAll` |
| `/graduation` | Tốt nghiệp | `xetTotNghiep/*` (4 APIs), `image`, `eventWeb/getAll` |
| `/notedetail` | Chi tiết ghi chú | TBD |
| `/newfeeds/{categoryId}/{postId}` | Chi tiết thông báo | TBD |

---

## 📋 Semester IDs

| ID | Tên đợt | Ghi chú |
|----|---------|---------|
| 76 | Học kỳ hè năm học 2025-2026 | Kỳ hiện tại |
| 75 | Học kỳ 2 năm học 2025-2026 | |
| 74 | Học kỳ 1 năm học 2025-2026 | |
| 73 | Học kỳ hè năm học 2024-2025 | |
| 72 | Học kỳ 2 năm học 2024-2025 | |
| 71 | Học kỳ 1 năm học 2024-2025 | Kỳ đầu tiên |

---

## 📊 Sample Data

### Môn học kỳ hiện tại (HK Hè 2025-2026, id=76)
| Mã HP | Tên môn | Tín chỉ |
|--------|---------|---------|
| 0120005105 | Triết học Mác - Lênin | 3 |
| 0120122010 | XD phần mềm hướng đối tượng | 3 |
| TT04 | General English 4 | 0 |
| 006124 | Tin học cơ bản | 0 |
| 0120121003 | Hệ quản trị cơ sở dữ liệu | 3 |
| 0120121137 | Quản trị doanh nghiệp CNTT | 3 |

### Lịch học mẫu (tháng 6/2026)
| Ngày | Số môn | Môn học |
|------|--------|---------|
| 29/06 | 2 | Lập trình Web, Lập trình mạng |
| 27/06 | 1 | Thiết kế mạng |
| 26/06 | 1 | Lập trình Web |
| 24/06 | 2 | Lập trình mạng, Thiết kế mạng |
| 22/06 | 2 | Lập trình Web, Lập trình mạng |

### Tiến độ học tập
- **Cần đạt**: 120 tín chỉ
- **Đã tích lũy**: 61 tín chỉ (50.8%)

---

## 🔮 Ứng dụng cho UTHelper

| Tính năng | API sử dụng | Ưu tiên |
|-----------|-------------|---------|
| Thông báo điểm mới | `hoctap/kqtheoky/{id}` | ⭐⭐⭐ |
| Cảnh báo lịch học | `lichhoc/lichTuan`, `lichhoc/thang` | ⭐⭐⭐ |
| Theo dõi học phí | `hocphi?idDot=` | ⭐⭐ |
| Tiến độ tốt nghiệp | `hoctap/tiendo`, `xetTotNghiep/*` | ⭐⭐ |
| Thông báo portal mới | `notification?categoryId=&page=&size=` | ⭐⭐⭐ |
| Khảo sát mới | `survey/checkSurvey` | ⭐ |
| Sự kiện mới | `eventWeb/getAll` | ⭐ |

---

> **Tổng cộng: 30+ API endpoints** đã được phát hiện và document.
> **Discovery method**: Chrome DevTools MCP — live network traffic analysis
> **Last updated**: 2026-06-22
