from bs4 import BeautifulSoup
from typing import List, Optional
from models import Assignment, ActivityDetail
from core.security import HTMLSanitizer
from datetime import datetime
import logging
import re
import json

logger = logging.getLogger(__name__)

class MoodleParser:
    @staticmethod
    def parse_assignments(html: str) -> List[Assignment]:
        """
        Phân tích danh sách bài tập từ HTML Lịch/Dòng thời gian của Moodle.
        Tập trung vào cấu trúc xem theo tháng của lịch (calendar.html).
        """
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        assignments = {}

        # 1. Phân tích danh sách khóa học từ menu thả xuống (dropdown filter)
        course_map = {}
        course_select = soup.find("select", {"name": "course"})
        if course_select:
            for option in course_select.find_all("option"):
                val = option.get("value")
                name = " ".join(option.text.split())
                if val:
                    course_map[val] = name

        # 2. Phân tích tất cả các mục sự kiện
        events = soup.find_all("li", {"data-region": "event-item"})
        
        for event in events:
            try:
                # Cần thẻ anchor để lấy URL và tiêu đề
                a_tag = event.find("a", {"data-action": "view-event"})
                if not a_tag:
                    continue
                    
                url = a_tag.get("href", "")
                
                # Tiêu đề có thể chứa nhiều khoảng trắng hoặc xuống dòng
                raw_title = a_tag.find("span", class_="eventname").text if a_tag.find("span", class_="eventname") else a_tag.get("title", "")
                title = " ".join(raw_title.split())
                
                # Xử lý loại sự kiện
                raw_eventtype = event.get("data-event-eventtype", "")
                raw_component = event.get("data-event-component", "")   # mod_quiz, mod_assign, mod_attendance …

                _COMPONENT_MAP = {
                    "mod_quiz":       "quiz",
                    "mod_assign":     "assignment",
                    "mod_attendance": "attendance",
                    "mod_scorm":      "quiz",
                    "mod_lesson":     "quiz",
                }

                # Làm sạch tiêu đề
                clean_title = title
                title_lower = title.lower()
                if "tới hạn" in title_lower:
                    clean_title = title.replace(" tới hạn", "").replace(" Tới hạn", "")
                elif "kết thúc" in title_lower:
                    clean_title = title.replace(" kết thúc", "").replace(" Kết thúc", "")
                elif "bắt đầu" in title_lower:
                    clean_title = title.replace(" bắt đầu", "").replace(" Bắt đầu", "")

                clean_title = clean_title.replace(" (Due date)", "").strip()
                if not clean_title:
                    clean_title = title

                # eventtype quyết định ngữ nghĩa, component quyết định module type
                is_open      = raw_eventtype == "open" or "bắt đầu" in title_lower
                module_type  = _COMPONENT_MAP.get(raw_component, "")

                if raw_eventtype == "attendance" or "điểm danh" in title_lower:
                    mapped_type = "attendance"
                elif raw_eventtype in ("due", "close") or "tới hạn" in title_lower or "kết thúc" in title_lower:
                    mapped_type = module_type or "deadline"
                elif is_open:
                    # Preserve module type but mark as open via composite "quiz_open" etc.
                    mapped_type = (module_type + "_open") if module_type else "open"
                else:
                    mapped_type = module_type or "other"
                
                # Lấy ID khóa học và phân giải tên khóa học
                course_id = event.get("data-courseid") or event.get("data-eventtype-course", "")
                course_name = course_map.get(course_id, f"Khóa học {course_id}" if course_id else "Không rõ khóa học")
                if course_name == "Tất cả các khóa học":
                    course_name = "Sự kiện chung"
                
                # Tìm thẻ td cha để lấy timestamp ngày tháng
                td = event.find_parent("td", attrs={"data-day-timestamp": True})
                if td:
                    timestamp_str = td.get("data-day-timestamp")
                    dt = datetime.fromtimestamp(int(timestamp_str))
                    # Mặc định thời gian là cuối ngày nếu không có giờ cụ thể
                    deadline = dt.replace(hour=23, minute=59, second=59)
                else:
                    deadline = datetime(2099, 12, 31, 23, 59, 59)

                # CMID từ URL là mã định danh thực tế nhất của hoạt động
                cmid_match = re.search(r"id=(\d+)", url)
                cmid = cmid_match.group(1) if cmid_match else ""
                
                # ID ổn định: course_id + cmid
                stable_id = f"{course_id}_{cmid}" if course_id and cmid else (cmid or event.get("data-event-id") or "unknown")

                assign = Assignment(
                    id=str(stable_id),
                    course_id=str(course_id),
                    course_name=course_name,
                    title=clean_title,
                    event_type=mapped_type,
                    deadline=deadline,
                    url=url
                )

                # Lọc trùng lặp sự kiện mở/đóng: ưu tiên sự kiện có thời hạn (đóng) thay vì mở để giữ giao diện chuẩn
                if str(stable_id) not in assignments:
                    assignments[str(stable_id)] = assign
                else:
                    existing = assignments[str(stable_id)]
                    is_existing_open = existing.event_type == "open" or existing.event_type.endswith("_open")
                    is_new_open = mapped_type == "open" or mapped_type.endswith("_open")
                    
                    if is_existing_open and not is_new_open:
                        assignments[str(stable_id)] = assign
                    elif not is_existing_open and not is_new_open:
                        # Nếu cả 2 đều là đóng, lấy cái có thời hạn trễ hơn làm hạn chót
                        if deadline > existing.deadline:
                            assignments[str(stable_id)] = assign

            except Exception as e:
                logger.warning(f"Lỗi khi phân tích một khối sự kiện: {str(e)}")

        return list(assignments.values())

    @staticmethod
    def _extract_course_id(soup: BeautifulSoup) -> str:
        """Trích xuất Course ID từ JavaScript config hoặc breadcrumbs."""
        # 1. Thử từ M.cfg (đáng tin cậy nhất)
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string and "M.cfg" in script.string:
                match = re.search(r'"courseId":\s*(\d+)', script.string)
                if match:
                    return match.group(1)
        
        # 2. Thử từ breadcrumbs
        breadcrumb = soup.find("ol", class_="breadcrumb")
        if breadcrumb:
            for a in breadcrumb.find_all("a"):
                href = a.get("href", "")
                match = re.search(r"id=(\d+)", href)
                if match and "course/view.php" in href:
                    return match.group(1)
                    
        # 3. Thử từ body class
        body = soup.find("body")
        if body and body.get("class"):
            for cls in body.get("class"):
                if cls.startswith("course-"):
                    return cls.replace("course-", "")
                    
        return ""

    @staticmethod
    def _extract_cmid(soup: BeautifulSoup) -> str:
        """Trích xuất Course Module ID (cmid) từ body class hoặc URL."""
        body = soup.find("body")
        if body and body.get("class"):
            for cls in body.get("class"):
                if cls.startswith("cmid-"):
                    return cls.replace("cmid-", "")
        return ""

    @staticmethod
    def _parse_attendance_table(soup: BeautifulSoup) -> list[dict[str, str]]:
        """Phân tích bảng điểm danh thành danh sách các bản ghi."""
        records = []
        table = soup.find("table", class_="generaltable")
        if not table:
            return records
            
        headers = [th.text.strip() for th in table.find_all("th")]
        if not headers:
            return records
            
        rows = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= len(headers):
                record = {}
                for i, header in enumerate(headers):
                    if i < len(cells):
                        record[header] = cells[i].text.strip()
                if record:
                    records.append(record)
        return records

    @staticmethod
    def parse_activity_page(html: str, url: str = "") -> Optional[Assignment]:
        """
        Phân tích chi tiết từ trang hoạt động cụ thể (Bài kiểm tra, Bài tập hoặc Sự kiện lịch).
        Trích xuất hạn chót chính xác, trạng thái nộp bài và các thông tin chi tiết khác.
        """
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        
        # 1. Tiêu đề
        header = soup.find("h3", class_="h2") or soup.find("h2") or soup.find("h1")
        title = " ".join(header.text.split()) if header else "Hoạt động không tên"
        
        # 2. Tên khóa học (Lấy chi tiết từ Breadcrumb và Banner)
        course_name = "Không rõ khóa học"
        course_full_name = ""
        
        # Thử lấy tên đầy đủ từ banner
        banner_h2 = soup.find("div", class_="page-banner-content")
        if banner_h2 and banner_h2.find("h2"):
            course_full_name = " ".join(banner_h2.find("h2").text.split())

        breadcrumb = soup.find("ol", class_="breadcrumb")
        if breadcrumb:
            items = breadcrumb.find_all("li")
            for item in items:
                a = item.find("a")
                if a and ("course/view.php?id=" in a.get("href", "") or "course/view.php?name=" in a.get("href", "")):
                    name_raw = a.text.strip() or a.get("title", "").strip()
                    course_name = " ".join(name_raw.split())
                    break
        
        if course_name == "Không rõ khóa học" or not course_name:
            # Tìm kiếm fallback ở tiêu đề trang nếu breadcrumb thất bại
            course_node = soup.find("div", class_="page-header-headings")
            if course_node:
                course_name = " ".join(course_node.text.split())
        
        if not course_full_name:
            course_full_name = course_name


        # 3. Chi tiết nội dung (Mô tả)
        description_html = ""
        # Thử các ID/class phổ biến của phần mô tả
        intro = soup.find("div", id="intro") or soup.find("div", class_="description") or soup.find("div", id="event-description")
        if not intro:
            # Tìm kiếm fallback trong khu vực nội dung chính nếu là trang sự kiện đơn giản
            content_v = soup.find("div", {"role": "main"}) or soup.find("section", id="region-main")
            if content_v:
                # Thử tìm wrapper cho văn bản thực tế
                event_content = content_v.find("div", class_="event-content") or content_v.find("div", class_="no-overflow")
                if event_content:
                    intro = event_content

        if intro:
            description_html = HTMLSanitizer.sanitize(str(intro))

        # 4. Dữ liệu Meta (Trạng thái/Thông tin bài kiểm tra)
        status_data = {}
        status_table = soup.find("table", class_="generaltable")
        if status_table:
            for row in status_table.find_all("tr"):
                header_cell = row.find("th")
                value_cell = row.find("td")
                if header_cell and value_cell:
                    h_text = " ".join(header_cell.text.split())
                    v_text = " ".join(value_cell.text.split())
                    if h_text and v_text:
                        status_data[h_text] = v_text

        quiz_info_list = []
        attempts_allowed = None
        time_limit = None
        
        quiz_info_div = soup.find("div", class_="quizinfo")
        quiz_attempt_div = soup.find("div", class_="quizattempt")
        if quiz_info_div or quiz_attempt_div:
            info_text = (quiz_info_div.get_text(separator=" ").lower() if quiz_info_div else "")
            attempt_text = (quiz_attempt_div.get_text(separator=" ").lower() if quiz_attempt_div else "")
            quiz_text = (info_text + " " + attempt_text).replace('\xa0', ' ')

            if any(x in quiz_text for x in [
                "this quiz is currently not available",
                "bài kiểm tra này hiện không khả dụng",
                "xin lỗi, trắc nghiệm không thể vào được",
                "trắc nghiệm không thể vào được",
                "trắc nghiệm này hiện không vào được"
            ]):
                status = "not_opened"

            if quiz_info_div:
                for p in quiz_info_div.find_all("p"):
                    text = " ".join(p.text.split())
                    if text:
                        quiz_info_list.append(text)
                    
                    # Trích xuất chi tiết
                    text_low = text.lower()
                    if "attempts allowed" in text_low or "số lần làm bài cho phép" in text_low:
                        parts = text.split(":", 1)
                        if len(parts) > 1:
                            attempts_allowed = parts[1].strip()
                    
                    if "time limit" in text_low or "thời gian làm bài" in text_low:
                        parts = text.split(":", 1)
                        if len(parts) > 1:
                            time_limit = parts[1].strip()
        
        # Thử tìm time limit trong modal/confirm nếu trang quiz đang mở
        if not time_limit:
            confirm_box = soup.find("div", id="id_honestycheckheadercontainer")
            if confirm_box:
                static_text = confirm_box.find("div", class_="form-control-static")
                if static_text:
                    match = re.search(r"limit of\s+(.*?)\.", static_text.text)
                    if match:
                        time_limit = match.group(1).strip()


        # 5. Xác định Course ID và CMID
        course_id = MoodleParser._extract_course_id(soup)
        cmid = MoodleParser._extract_cmid(soup) or (url.split("id=")[-1] if "id=" in url else "detail")
        
        # 6. Xử lý bảng điểm danh (nếu có)
        attendance_records = []
        body_class = soup.find("body").get("class", []) if soup.find("body") else []
        if "path-mod-attendance" in body_class:
            attendance_records = MoodleParser._parse_attendance_table(soup)

        # 7. Hạn chót chính xác & Trạng thái nộp bài
        deadline = None
        open_time = None
        # Chỉ gán unknown nếu chưa có trạng thái trước đó (ví dụ từ quiz)
        if "status" not in locals() or status is None:
            status = "unknown"
        
        # Kiểm tra vùng chứa ngày tháng bài tập (Moodle 4.x)
        date_region = soup.find("div", {"data-region": "activity-dates"})
        if date_region:
            for div in date_region.find_all("div"):
                text = div.text.strip()
                if any(x in text for x in ["Đóng lúc:", "Due:", "Deadline:", "Kết thúc lúc:", "Closed:", "Closes:", "Close:", "Đã đóng:"]):
                    date_parts = text.split(":", 1)
                    if len(date_parts) > 1:
                        deadline = MoodleParser._parse_moodle_date(date_parts[1].strip())
                # Capture open/available-from date into status_data for display
                elif any(x in text for x in ["Opened:", "Available from:", "Mở từ:", "Bắt đầu từ:", "Opens:", "Đã mở:"]):
                    label_vi = "Mở từ"
                    date_parts = text.split(":", 1)
                    if len(date_parts) > 1 and label_vi not in status_data:
                        val_str = date_parts[1].strip()
                        status_data[label_vi] = val_str
                        try:
                            # Phân tích "Mở từ" để xác định xem đã tới giờ chưa
                            open_time = MoodleParser._parse_moodle_date(val_str)
                        except Exception:
                            pass
        
        # Fallback lấy hạn chót trong trang chi tiết sự kiện lịch
        if not deadline:
            event_detail = soup.find("div", class_="event-details")
            if event_detail:
                # Thường chứa thông tin thời gian
                time_node = event_detail.find("div", class_="row") 
                if time_node:
                    deadline = MoodleParser._parse_moodle_date(time_node.text.strip())

        # Xác định trạng thái nộp bài — dùng key cụ thể thay vì pattern rộng
        for k, v in status_data.items():
            k_low = k.lower()
            v_low = v.lower()
            # Chỉ xét "Submission status" / "Trạng thái nộp bài"
            if "submission status" in k_low or "trạng thái nộp" in k_low or "trạng thái bài nộp" in k_low:
                if any(x in v_low for x in ["submitted for grading", "đã nộp"]):
                    status = "submitted"
                elif any(x in v_low for x in ["not submitted", "chưa nộp"]):
                    status = "not_submitted"

        # Cập nhật thành graded nếu có thông tin đã chấm (ngay cả khi chưa nộp bài đối với offline/attendance task)
        for k, v in status_data.items():
            if "grading status" in k.lower() or "trạng thái chấm" in k.lower():
                v_low = v.lower()
                if ("graded" in v_low or "đã chấm" in v_low) and "not graded" not in v_low:
                    status = "graded"
                    break

        # Kiểm tra trạng thái nộp bài của quiz
        if quiz_attempt_div and any(x in quiz_attempt_div.text.lower() for x in ["no more attempts", "không cho phép nhiều lần thử"]):
            status = "submitted"
            
        # Nếu có bảng quizattemptsummary tức là đã từng làm bài
        if soup.find("table", class_=lambda c: c and "quizattemptsummary" in c):
            if status in ["unknown", "not_opened", "not_submitted"]:
                status = "submitted"
                
        # Lấy điểm tổng kết của quiz nếu có
        feedback_div = soup.find("div", id="feedback")
        if feedback_div:
            h3 = feedback_div.find("h3")
            if h3 and "điểm" in h3.text.lower():
                status_data["KẾT QUẢ"] = h3.text.strip()
                status = "graded"  # Đã có điểm

        # Unique ID ổn định dựa trên course_id + cmid
        activity_id = f"{course_id}_{cmid}" if course_id and cmid else str(cmid)
        
        details = ActivityDetail(
            description_html=description_html,
            status_data=status_data,
            quiz_info=quiz_info_list,
            attempts_allowed=attempts_allowed,
            time_limit=time_limit,
            course_full_name=course_full_name,
            attendance_records=attendance_records,
            open_time=open_time
        )


        
        # Xác định loại hoạt động từ URL hoặc body class
        _URL_TYPE_MAP = {
            "/mod/quiz/":       "quiz",
            "/mod/assign/":     "assignment",
            "/mod/attendance/": "attendance",
            "/mod/scorm/":      "quiz",
            "/mod/lesson/":     "quiz",
        }
        detected_type = "deadline"
        url_lower = (url or "").lower()
        for pattern, t in _URL_TYPE_MAP.items():
            if pattern in url_lower:
                detected_type = t
                break
        if detected_type == "deadline":
            # Fallback: body class
            for cls in body_class:
                if "path-mod-quiz" in cls:
                    detected_type = "quiz"
                    break
                if "path-mod-assign" in cls:
                    detected_type = "assignment"
                    break

        return Assignment(
            id=activity_id,
            course_id=str(course_id),
            course_name=course_name,
            title=title,
            event_type=detected_type,
            deadline=deadline or datetime(2099, 12, 31, 23, 59, 59),
            url=url,
            submission_status=status,
            details=details
        )

    @staticmethod
    def _parse_moodle_date(date_str: str) -> Optional[datetime]:
        """
        Phân tích định dạng ngày của Moodle, ví dụ: 'Thứ Bảy, 14 tháng 3 2026, 10:32 PM'
        Hỗ trợ cả các cụm từ tiếng Anh và tiếng Việt phổ biến.
        """
        # Từ điển chuyển đổi tên tháng tiếng Việt
        vi_months = {
            "tháng 1,": "January", "tháng 2,": "February", "tháng 3,": "March",
            "tháng 4,": "April", "tháng 5,": "May", "tháng 6,": "June",
            "tháng 7,": "July", "tháng 8,": "August", "tháng 9,": "September",
            "tháng 10,": "October", "tháng 11,": "November", "tháng 12,": "December",
            "tháng 1": "January", "tháng 2": "February", "tháng 3": "March",
            "tháng 4": "April", "tháng 5": "May", "tháng 6": "June",
            "tháng 7": "July", "tháng 8": "August", "tháng 9": "September",
            "tháng 10": "October", "tháng 11": "November", "tháng 12": "December"
        }
        
        # Từ điển chuyển đổi tên thứ tiếng Việt
        vi_days = {
            "Thứ Hai": "Monday", "Thứ Ba": "Tuesday", "Thứ Tư": "Wednesday",
            "Thứ Năm": "Thursday", "Thứ Sáu": "Friday", "Thứ Bảy": "Saturday",
            "Chủ Nhật": "Sunday"
        }

        # Chuẩn hóa các chuỗi địa phương hóa sang tiếng Anh để dùng strptime
        normalized = date_str
        for vi, en in vi_months.items():
            normalized = normalized.replace(vi, en)
        for vi, en in vi_days.items():
            normalized = normalized.replace(vi, en)
        
        # Loại bỏ các giới từ/từ nối tiếng Việt phổ biến
        normalized = normalized.replace(" vào lúc", ",").replace(" lúc", ",")

        formats = [
            "%A, %d %B %Y, %I:%M %p",
            "%A, %d %B %Y, %H:%M",
            "%d %B %Y, %I:%M %p",
            "%d %B %Y, %H:%M",
            "%A, %d %B %Y, %I:%M%p",
            "%d %B %Y"
        ]

        for fmt in formats:
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue
        
        # Thử phương án fallback cuối cùng: tách chuỗi và lấy các phần
        try:
             # Nếu định dạng là "14 March 2026, 10:32"
             return datetime.strptime(normalized.split(", ", 1)[1], "%d %B %Y, %I:%M %p")
        except Exception:
             return None
