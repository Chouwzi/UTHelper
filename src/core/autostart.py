import os
import sys
import winreg
import logging

logger = logging.getLogger(__name__)

def add_to_startup(app_name="UTHElearningAlert") -> bool:
    """
    Adds the current script/executable to the Windows Startup Registry.
    This ensures the app starts silently in the tray when the user logs in.
    """
    if sys.platform != "win32":
        logger.warning("Autostart is only supported on Windows.")
        return False
        
    # Get the path of the current executing Python script or packaged EXE
    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
    else:
        # If running as script, we need to run python.exe script_path.py
        # But for an autostart tray app, we should ideally compile it to EXE or use pythonw.exe
        pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw_path):
            pythonw_path = sys.executable # Fallback to normal python
            
        script_path = os.path.abspath(sys.argv[0])
        exe_path = f'"{pythonw_path}" "{script_path}"'
        
    try:
        # Open the registry key
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0,
                             winreg.KEY_SET_VALUE)
        
        # Set the value
        winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
        winreg.CloseKey(key)
        logger.info(f"Successfully added {app_name} to Windows startup.")
        return True
    except Exception as e:
        logger.error(f"Failed to add to startup: {str(e)}")
        return False

def remove_from_startup(app_name="UTHElearningAlert") -> bool:
    """
    Removes the application from Windows Startup Registry.
    """
    if sys.platform != "win32":
        return False
        
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0,
                             winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, app_name)
        winreg.CloseKey(key)
        logger.info(f"Successfully removed {app_name} from Windows startup.")
        return True
    except FileNotFoundError:
        # Key didn't exist
        return True
    except Exception as e:
        logger.error(f"Failed to remove from startup: {str(e)}")
        return False
