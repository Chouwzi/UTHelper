"""Pure-Python in-memory PDF, ZIP, and Markdown generators for Demo Mode."""

from __future__ import annotations

from functools import lru_cache
import io
from urllib.parse import urlsplit
import zipfile

COURSES_ORIGIN = "https://courses.ut.edu.vn"
THNN_ORIGIN = "https://thnn.ut.edu.vn"

LECTURE_PDF_PATH = (
    "/webservice/pluginfile.php/990101/mod_resource/content/1/"
    "Bai_giang_Chuong_01_Socket_TCP_UDP.pdf"
)
SUBMITTED_REPORT_PDF_PATH = (
    "/webservice/pluginfile.php/990104/assignsubmission_file/submission_files/0/"
    "Bao_cao_nhom_01.pdf"
)
MARKDOWN_SUMMARY_PATH = (
    "/webservice/pluginfile.php/990102/mod_resource/content/1/"
    "Tom_tat_on_tap_CSDL_phan_tan.md"
)
SAMPLE_LAB_ZIP_PATH = (
    "/webservice/pluginfile.php/990101/mod_resource/content/2/"
    "Lab02_TCP_MultiClient_Sample.zip"
)
THNN_READING_PDF_PATH = (
    "/pluginfile.php/992001/mod_resource/content/1/"
    "IT_English_Unit_03_Cloud_Architecture.pdf"
)


def _pdf_escape(text: str) -> bytes:
    """Encode ASCII-clean text safely as a PDF literal string."""
    encoded = text.encode("cp1252", errors="replace")
    escaped = (
        encoded.replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
        .replace(b"\r", b" ")
        .replace(b"\n", b" ")
    )
    return escaped


def build_minimal_pdf(
    pages: list[tuple[str, list[str]]],
) -> bytes:
    """Build a valid multi-page PDF 1.4 document with outlines in pure Python."""
    if not pages:
        pages = [("UTH Demo Document", ["Sample content."])]

    # Object ID allocation:
    # 1: Catalog, 2: Pages, 3: Outlines, 4: Helvetica, 5: Helvetica-Bold
    next_id = 6
    page_obj_ids: list[int] = []
    content_obj_ids: list[int] = []
    outline_obj_ids: list[int] = []

    for _ in pages:
        content_obj_ids.append(next_id)
        page_obj_ids.append(next_id + 1)
        outline_obj_ids.append(next_id + 2)
        next_id += 3

    objects: dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R /Outlines 3 0 R >>"
    kids_refs = " ".join(f"{pid} 0 R" for pid in page_obj_ids).encode("ascii")
    objects[2] = (
        b"<< /Type /Pages /Kids [ "
        + kids_refs
        + f" ] /Count {len(pages)} >>".encode("ascii")
    )
    objects[3] = (
        f"<< /Type /Outlines /First {outline_obj_ids[0]} 0 R "
        f"/Last {outline_obj_ids[-1]} 0 R /Count {len(outline_obj_ids)} >>"
    ).encode("ascii")
    objects[4] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    objects[5] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
        b"/Encoding /WinAnsiEncoding >>"
    )

    for idx, (heading, lines) in enumerate(pages):
        cid = content_obj_ids[idx]
        pid = page_obj_ids[idx]
        oid = outline_obj_ids[idx]

        ops: list[bytes] = [
            b"BT",
            b"/F2 16 Tf",
            b"52 785 Td",
            b"(" + _pdf_escape(heading) + b") Tj",
            b"/F1 11 Tf",
            b"0 -28 Td",
        ]
        for line in lines:
            ops.append(b"(" + _pdf_escape(line) + b") Tj")
            ops.append(b"0 -18 Td")
        ops.append(f"(Page {idx + 1} / {len(pages)} - UTH Demo Sandbox) Tj".encode("ascii"))
        ops.append(b"ET")
        stream_body = b"\n".join(ops)
        objects[cid] = (
            f"<< /Length {len(stream_body)} >>\nstream\n".encode("ascii")
            + stream_body
            + b"\nendstream"
        )
        objects[pid] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> "
            + f"/Contents {cid} 0 R >>".encode("ascii")
        )

        outline_parts = [
            b"<< /Title (" + _pdf_escape(heading) + b") /Parent 3 0 R",
            f"/Dest [{pid} 0 R /Fit]".encode("ascii"),
        ]
        if idx > 0:
            outline_parts.append(f"/Prev {outline_obj_ids[idx - 1]} 0 R".encode("ascii"))
        if idx + 1 < len(outline_obj_ids):
            outline_parts.append(f"/Next {outline_obj_ids[idx + 1]} 0 R".encode("ascii"))
        outline_parts.append(b">>")
        objects[oid] = b" ".join(outline_parts)

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    max_id = next_id - 1
    for obj_id in range(1, max_id + 1):
        offsets[obj_id] = len(buf)
        buf.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        buf.extend(objects[obj_id])
        buf.extend(b"\nendobj\n")

    xref_offset = len(buf)
    buf.extend(f"xref\n0 {max_id + 1}\n".encode("ascii"))
    buf.extend(b"0000000000 65535 f \n")
    for obj_id in range(1, max_id + 1):
        buf.extend(f"{offsets[obj_id]:010d} 00000 n \n".encode("ascii"))

    buf.extend(
        (
            f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(buf)


@lru_cache(maxsize=1)
def build_lecture_pdf_bytes() -> bytes:
    """Lecture PDF for Course 101 (Network Programming)."""
    return build_minimal_pdf(
        [
            (
                "UTH - Lap trinh mang - Chuong 1: Socket TCP & UDP",
                [
                    "Khoa Cong nghe thong tin - Dai hoc Giao thong van tai TP.HCM (UTH)",
                    "Hoc phan: Lap trinh mang (0101123001) - So tin chi: 3",
                    "1. Tong quan kien truc Client - Server tren mo hinh TCP/IP.",
                    "2. Vong doi Socket TCP: socket() -> bind() -> listen() -> accept().",
                    "3. Quan ly dong thoi voi ThreadPool va I/O bat dong bo (Selector).",
                    "4. So sanh do tin cay TCP Stream va do tre thap cua UDP Datagram.",
                ],
            ),
            (
                "Chuong 2: Thiet ke giao thuc ung dung va Framing",
                [
                    "1. Van de TCP Sticky Packet va cach phan tach khung ban tin.",
                    "2. Length-prefixed framing (4-byte big-endian header) an toan.",
                    "3. Xu ly timeout, ket noi ban mo (half-open) va graceful shutdown.",
                    "4. Bai tap thuc hanh tuan 6: Xay dung TCP Echo Server da luong.",
                ],
            ),
        ]
    )


@lru_cache(maxsize=1)
def build_submitted_report_pdf_bytes() -> bytes:
    """Submitted group report PDF (`Bao_cao_nhom_01.pdf`) for Course 103."""
    return build_minimal_pdf(
        [
            (
                "UTH - Bao cao nhom 01: Phan tich be mat tan cong Web",
                [
                    "Sinh vien thuc hien: Nguyen Minh Khoi - MSSV mau: 00002026",
                    "Hoc phan: An toan thong tin (0101123003) - Nhom 04",
                    "1. Kiem ke dau vao HTTP va xac thuc tham so phia may chu.",
                    "2. Phong chong SQL Injection bang Parameterized Queries.",
                    "3. Chinh sach Content-Security-Policy (CSP) chong XSS.",
                ],
            ),
            (
                "Ket qua kiem thu va khuyen nghi cau hinh an toan",
                [
                    "1. Bat HttpOnly, Secure va SameSite=Lax cho cookie phien.",
                    "2. Gioi han kich thuoc tai len va xac thuc MIME type.",
                    "3. Tai lieu mau phuc vu che do xem thu (Demo Sandbox).",
                ],
            ),
        ]
    )


@lru_cache(maxsize=1)
def build_thnn_reading_pdf_bytes() -> bytes:
    """Reading comprehension PDF for THNN Course 201 (IT English)."""
    return build_minimal_pdf(
        [
            (
                "UTH THNN - Unit 3: Cloud Computing & Distributed Systems",
                [
                    "Course: Technical English for Information Technology (0101005004)",
                    "Key Vocabulary: Scalability, Fault Tolerance, Load Balancing, Latency.",
                    "Reading Passage: Modern transport telemetry relies on resilient edge",
                    "gateways synchronized with cloud storage clusters.",
                ],
            ),
            (
                "Writing Task Guidelines - Technical Summary",
                [
                    "1. Summarize architectural trade-offs in 180-220 words.",
                    "2. Use formal academic connectors and passive constructions.",
                    "3. Offline sample PDF generated inside UTHelper Demo Sandbox.",
                ],
            ),
        ]
    )


@lru_cache(maxsize=1)
def build_markdown_summary_bytes() -> bytes:
    """Markdown study guide for Course 102 (Distributed Databases)."""
    md = (
        "# Tóm tắt ôn tập: Hệ cơ sở dữ liệu phân tán\n\n"
        "Học phần **Hệ cơ sở dữ liệu phân tán** (`0101123002`) — Khoa CNTT UTH.\n\n"
        "## 1. Các chiến lược phân mảnh dữ liệu\n\n"
        "- **Phân mảnh ngang (Horizontal Fragmentation)**: chia các bộ (rows) theo vị từ địa lý hoặc chi nhánh.\n"
        "- **Phân mảnh dọc (Vertical Fragmentation)**: tách thuộc tính theo tần suất truy vấn, giữ khóa chính.\n"
        "- **Phân mảnh hỗn hợp**: kết hợp lọc ngang và chiếu dọc cho bảng giao dịch lớn.\n\n"
        "## 2. Giao thức cam kết hai pha (2PC)\n\n"
        "1. **Pha chuẩn bị (Prepare)**: điều phối viên hỏi tất cả các nút tham gia.\n"
        "2. **Pha quyết định (Commit / Abort)**: chỉ ghi nhận khi 100% nút trả lời `VOTE_COMMIT`.\n\n"
        "## 3. Ví dụ truy vấn phân mảnh\n\n"
        "```sql\n"
        "SELECT ma_chuyen, tram_di, tram_den\n"
        "FROM lich_trinh_q12\n"
        "WHERE trang_thai = 'DANG_CHAY';\n"
        "```\n"
    )
    return md.encode("utf-8")


@lru_cache(maxsize=1)
def build_sample_lab_zip_bytes() -> bytes:
    """Sample multi-client TCP lab `.zip` archive for Course 101."""
    guide_pdf = build_minimal_pdf(
        [
            (
                "UTH Lab 02 - Huong dan chay thu TCP Sample",
                [
                    "Buoc 1: Chay python server.py tren cong cuc bo 9000.",
                    "Buoc 2: Chay python client.py de gui thong diep kiem thu.",
                ],
            )
        ]
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "lab02_tcp_sample/README.md",
            (
                "# Lab 02: TCP Multi-Client Echo Sample\n\n"
                "Bộ mã nguồn mẫu môn **Lập trình mạng** tại UTH.\n\n"
                "- `server.py`: TCP Echo Server dùng `ThreadPoolExecutor`.\n"
                "- `client.py`: TCP Client gửi nhận khung dữ liệu UTF-8.\n"
                "- `docs/Huong_dan_chay_thu.pdf`: Tài liệu hướng dẫn chạy thử.\n"
            ),
        )
        zf.writestr(
            "lab02_tcp_sample/server.py",
            (
                '"""Sample multi-threaded TCP Echo Server for UTH Lab 02."""\n\n'
                "import socket\n"
                "from concurrent.futures import ThreadPoolExecutor\n\n\n"
                "def handle_client(conn: socket.socket) -> None:\n"
                "    with conn:\n"
                "        data = conn.recv(1024)\n"
                "        if data:\n"
                "            conn.sendall(b'ECHO: ' + data)\n"
            ),
        )
        zf.writestr(
            "lab02_tcp_sample/client.py",
            (
                '"""Sample TCP Echo Client for UTH Lab 02."""\n\n'
                "import socket\n\n\n"
                "def ping_echo(host: str = '127.0.0.1', port: int = 9000) -> bytes:\n"
                "    with socket.create_connection((host, port), timeout=3.0) as sock:\n"
                "        sock.sendall(b'Xin chao UTH')\n"
                "        return sock.recv(1024)\n"
            ),
        )
        zf.writestr("lab02_tcp_sample/docs/Huong_dan_chay_thu.pdf", guide_pdf)
    return buf.getvalue()


def _normalized_origin_and_path(url: str) -> tuple[str, str]:
    raw = str(url or "").strip()
    if not raw:
        return ("", "")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ("", "")
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    path = parsed.path or ""
    if path.startswith("/webservice/pluginfile.php/"):
        path = path.removeprefix("/webservice")
    return (origin, path)


def lookup_demo_file_bytes(url: str) -> bytes | None:
    """Return deterministic in-memory bytes for known Demo Mode file URLs."""
    origin, norm_path = _normalized_origin_and_path(url)
    if not origin or not norm_path:
        return None

    courses_norm_map = {
        LECTURE_PDF_PATH.removeprefix("/webservice"): build_lecture_pdf_bytes,
        SUBMITTED_REPORT_PDF_PATH.removeprefix("/webservice"): build_submitted_report_pdf_bytes,
        MARKDOWN_SUMMARY_PATH.removeprefix("/webservice"): build_markdown_summary_bytes,
        SAMPLE_LAB_ZIP_PATH.removeprefix("/webservice"): build_sample_lab_zip_bytes,
    }
    thnn_norm_map = {
        THNN_READING_PDF_PATH: build_thnn_reading_pdf_bytes,
    }

    if origin == COURSES_ORIGIN:
        builder = courses_norm_map.get(norm_path)
        return builder() if builder is not None else None
    if origin == THNN_ORIGIN:
        builder = thnn_norm_map.get(norm_path)
        return builder() if builder is not None else None
    return None
