import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from notifiers.windows import WindowsNotifier


def test_single_activity_toast_opens_exact_activity_url():
    toaster = MagicMock()
    module = MagicMock()
    module.InteractableWindowsToaster.return_value = toaster
    toast = MagicMock()
    module.Toast.return_value = toast
    activity = {
        "title": "Quiz 1",
        "course_name": "Course",
        "event_type": "quiz",
        "deadline": None,
        "url": "https://courses.example/mod/quiz/view.php?id=42",
    }

    notifier = WindowsNotifier.__new__(WindowsNotifier)
    notifier.app_id = "UTHelper"
    notifier.aumid = "Package_family!UTHelper"
    notifier.tray_app = None
    notifier.last_error = ""
    with patch.dict(sys.modules, {"windows_toasts": module}):
        assert notifier.notify([activity]) is True

    assert toast.launch_action == activity["url"]
    toaster.show_toast.assert_called_once_with(toast)
