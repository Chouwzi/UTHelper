from typing import List, Dict, Any, Tuple
from datetime import datetime
from core.time_utils import parse_datetime
from core.display_utils import clean_course_name, urgency_str, _TYPE_FILTER_MAP

class FilterService:
    """
    Service này dùng để lọc danh sách các hoạt động và đếm số lượng 
    trong một vòng lặp O(n) để tối ưu hiệu năng.
    """

    @staticmethod
    def filter_and_count(
        activities: List[Dict[str, Any]],
        active_urgency: str = "all",
        active_type: str = "all",
        active_course: str = "all",
        search_query: str = "",
        include_overdue: bool = False
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, int]]]:
        """
        Lọc danh sách hoạt động dựa trên các tiêu chí và tiện tay đếm luôn 
        số lượng theo từng loại (khẩn cấp, phân loại, khóa học) chỉ với 1 vòng lặp.
        
        Trả về:
            - Danh sách các mục đã lọc xong.
            - Một chiếc dict chứa các bộ đếm: `urgency`, `type`, `course`.
        """
        
        filtered_results = []
        
        # Chuẩn bị sẵn các bộ đếm
        counts = {
            "urgency": {"all": 0, "critical": 0, "warning": 0, "safe": 0, "overdue": 0},
            "type": {"all": 0},
            "course": {"all": 0}
        }
        
        search_lower = search_query.lower()
        now = datetime.now()

        for a in activities:
            # Lấy trước mấy thông tin cần thiết
            dl_str = a.get("deadline", "")
            dl = parse_datetime(dl_str) if dl_str else None

            # Tính độ khẩn cấp, xem có bị quá hạn không
            is_overdue = dl and dl < now
            if isinstance(a.get("urgency"), str):
                u_str = a.get("urgency")
            else:
                u_str = urgency_str(a.get("urgency")) if not is_overdue else "overdue"
                
            if is_overdue:
                u_str = "overdue"

            # Nếu là mục quá hạn mà không yêu cầu hiển thị thì bỏ qua luôn cho nhẹ máy
            if is_overdue and not include_overdue and active_urgency != "overdue":
                continue
                
            counts["urgency"]["all"] += 1
            counts["urgency"][u_str] = counts["urgency"].get(u_str, 0) + 1
            
            c_name = clean_course_name(a.get("course", ""))
            counts["course"]["all"] += 1
            counts["course"][c_name] = counts["course"].get(c_name, 0) + 1
            
            # Xử lý trường hợp đặc biệt cho các mục đang mở
            is_open_override = a.get("is_open", False)
            open_time_str = a.get("details", {}).get("open_time", "")
            if open_time_str:
                ot = parse_datetime(open_time_str)
                if ot and datetime.now() < ot:
                    is_open_override = True
            
            # Phân loại cho nó chuẩn bài
            a_type = "open" if is_open_override else a.get("type", "other")
            counts["type"]["all"] += 1
            counts["type"][a_type] = counts["type"].get(a_type, 0) + 1

            # Giờ thì kiểm tra xem có khớp với những gì người dùng đang lọc không
            match_urgency = (active_urgency == "all" or active_urgency == u_str)
            
            if active_type == "all":
                match_type = True
            elif active_type == "open":
                match_type = is_open_override
            else:
                allowed = _TYPE_FILTER_MAP.get(active_type, {active_type})
                match_type = a.get("type") in allowed and not is_open_override
                
            match_course = (active_course == "all" or active_course == c_name)
            
            match_search = True
            if search_lower:
                title_match = search_lower in str(a.get("title", "")).lower()
                course_match = search_lower in str(a.get("course", "")).lower()
                match_search = title_match or course_match

            if match_urgency and match_type and match_course and match_search:
                filtered_results.append(a)

        return filtered_results, counts
