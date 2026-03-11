# Note: requires win11toast or winotify, but for standard we will use standard print or a simpler approach if not available.
# Since we didn't add win11toast to requirements, we will use a basic Tkinter messagebox or a standard system bell for now,
# but architecturally it is separated here so it can be updated easily to use `windows-toasts` or `win11toast` library.
import logging
from typing import List
from models import Assignment, UrgencyLevel

logger = logging.getLogger(__name__)

class WindowsNotifier:
    def __init__(self):
        pass

    def notify(self, assignments: List[Assignment]):
        """
        Sends a desktop notification.
        """
        if not assignments:
            return

        critical = [a for a in assignments if a.urgency == UrgencyLevel.CRITICAL]
        warnings = [a for a in assignments if a.urgency == UrgencyLevel.WARNING]
        
        total = len(critical) + len(warnings)
        if total == 0:
            return

        title = "UTH Elearning Alert"
        msg = f"You have {len(critical)} critical assignments and {len(warnings)} upcoming deadlines."

        logger.info(f"NOTIFICATION: {title} - {msg}")
        
        try:
            # Fallback to tkinter messagebox if no native toast lib is installed
            from tkinter import messagebox
            import tkinter as tk
            
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            
            if critical:
                messagebox.showwarning(title, msg, parent=root)
            else:
                messagebox.showinfo(title, msg, parent=root)
                
            root.destroy()
        except Exception as e:
            logger.error(f"Failed to show notification: {e}")
