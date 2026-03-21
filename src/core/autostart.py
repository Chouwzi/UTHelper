import os
import sys
import winreg
import logging

logger = logging.getLogger(__name__)

def add_to_startup(app_name="UTHElearningAlert") -> bool:
    if sys.platform != "win32":
        logger.warning("Chỉ hỗ trợ Windows.")
        return False

    try:
        if getattr(sys, 'frozen', False):
            exe_path = f'"{sys.executable}" --autostart'
        else:
            pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw_path):
                pythonw_path = sys.executable
            script_path = os.path.abspath(sys.argv[0])
            exe_path = f'"{pythonw_path}" "{script_path}" --autostart'

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
        winreg.CloseKey(key)
        logger.info(app_name + " added to startup.")
        return True
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

def remove_from_startup(app_name="UTHElearningAlert") -> bool:
    if sys.platform != "win32": return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, app_name)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return True
    except Exception as e:
        logger.error(f"Error: {e}")
        return False
