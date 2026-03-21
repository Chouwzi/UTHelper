class C:
    BG             = "#0B0F1A"
    SURFACE        = "#141B2D"
    SURFACE_HOVER  = "#1A2340"
    ACCENT         = "#3B82F6"
    CRITICAL       = "#EF4444"
    WARNING        = "#F59E0B"
    SAFE           = "#10B981"
    TEXT_PRIMARY   = "#F1F5F9"
    TEXT_SECONDARY = "#94A3B8"
    BORDER         = "#1E293B"

_TYPE_LABELS = {
    "quiz":       "QUIZ",
    "assignment": "BÀI TẬP",
    "attendance": "ĐIỂM DANH",
    "deadline":   "HẠN NỘP",
    "open":       "SẮP MỞ",
    "other":      "SỰ KIỆN",
}

_DEADLINE_TYPES = {"deadline", "quiz", "assignment"}

_TYPE_FILTER_MAP = {
    "quiz":       {"quiz"},
    "assignment": {"assignment", "deadline"},
    "attendance": {"attendance"},
    "open":       {"open"},
    "other":      {"other"},
}

_TYPE_COLORS = {
    "quiz":       "#7C3AED",   # tím
    "assignment": "#2563EB",   # xanh dương
    "attendance": "#D97706",   # cam
    "deadline":   "#2563EB",   # giống màu bài tập
    "open":       "#0891B2",   # xanh lơ
    "other":      "#6B7280",   # xám
}

def load_theme_from_settings():
    try:
        from config import settings
        C.CRITICAL = getattr(settings, 'COLOR_CRITICAL', '#EF4444')
        C.WARNING = getattr(settings, 'COLOR_WARNING', '#F59E0B')
        C.SAFE = getattr(settings, 'COLOR_SAFE', '#10B981')
        
        _TYPE_COLORS['quiz'] = getattr(settings, 'COLOR_QUIZ', '#7C3AED')
        _TYPE_COLORS['assignment'] = getattr(settings, 'COLOR_ASSIGNMENT', '#2563EB')
        _TYPE_COLORS['deadline'] = getattr(settings, 'COLOR_ASSIGNMENT', '#2563EB')
        _TYPE_COLORS['attendance'] = getattr(settings, 'COLOR_ATTENDANCE', '#D97706')
        _TYPE_COLORS['open'] = getattr(settings, 'COLOR_OPEN', '#0891B2')
        _TYPE_COLORS['other'] = getattr(settings, 'COLOR_OTHER', '#6B7280')
    except Exception as e:
        import traceback
        traceback.print_exc()

# Lần đầu import gọi luôn để khởi tạo màu
load_theme_from_settings()
