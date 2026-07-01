"""Auto-update: check, download, and apply updates via GitHub Releases.

Works with public/private repos.  Uses only stdlib (urllib, zipfile, subprocess).
Platforms:
  - Windows: download .zip → batch-script swap → restart
  - Android: download .apk → delegate to apk_installer
  - iOS/other: open browser to release page (fallback)
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Callable, Optional, Tuple

from platform_utils import IS_ANDROID, IS_WINDOWS, IS_IOS

logger = logging.getLogger(__name__)

# GitHub repo
_GITHUB_OWNER = "Chouwzi"
_GITHUB_REPO = "UTHelper"
_RELEASES_URL = f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}/releases/latest"
_UA = "UTHelper-UpdateChecker/2.0"


# Helpers

def _version_tuple(v: str) -> tuple:
    """Convert '2.1.0' → (2, 1, 0)."""
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except (ValueError, AttributeError):
        return (0,)


def _get_update_temp_dir() -> Path:
    """Temp directory for downloads."""
    d = Path(tempfile.gettempdir()) / "uthelper_update"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Check

def check_for_update(current_version: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """Check GitHub Releases for newer version.

    Returns (has_update, latest_version, release_page_url, asset_download_url).
    """
    try:
        req = urllib.request.Request(_RELEASES_URL)
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", _UA)

        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))

        latest_tag = data.get("tag_name", "").lstrip("v")
        current = current_version.lstrip("v")
        if not latest_tag:
            return False, None, None, None

        has_update = _version_tuple(latest_tag) > _version_tuple(current)
        html_url = data.get("html_url", "")
        asset_url = _find_platform_asset(data.get("assets", []))
        return has_update, latest_tag, html_url, asset_url

    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.debug("Update check HTTP error: %s", e)
        return False, None, None, None
    except Exception as e:
        logger.debug("Update check failed: %s", e)
        return False, None, None, None


def _find_platform_asset(assets: list) -> Optional[str]:
    """Find the best download asset for current platform."""
    if not assets:
        return None

    if IS_ANDROID:
        preferred = [".apk"]
    elif IS_WINDOWS:
        # Prefer .zip for auto-extract, .exe as fallback
        preferred = [".zip", ".exe"]
    elif IS_IOS:
        preferred = [".ipa"]
    else:
        preferred = [".apk", ".zip"]

    for ext in preferred:
        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith(ext):
                return asset.get("browser_download_url", "")

    # Fallback: first asset
    return assets[0].get("browser_download_url", "") if assets else None


# Download

def download_update(url: str, progress_cb: Callable[[float], None] = None) -> Optional[Path]:
    """Download update file to temp dir with progress.

    Returns path to downloaded file, or None on failure.
    """
    if not url:
        return None
    try:
        temp_dir = _get_update_temp_dir()
        filename = url.split("/")[-1] or "update"
        dest = temp_dir / filename

        if dest.exists():
            dest.unlink()

        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        resp = urllib.request.urlopen(req, timeout=120)

        total_size = int(resp.headers.get("Content-Length", 0))
        downloaded = 0

        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total_size > 0:
                    progress_cb(downloaded / total_size)

        logger.info("Downloaded update: %s (%d bytes)", dest.name, downloaded)
        return dest

    except Exception as e:
        logger.error("Download failed: %s", e)
        return None


# Apply (Windows)

def apply_update_windows(zip_path: Path) -> bool:
    """Extract .zip and launch batch updater that replaces files after app exits.

    Returns True if updater was launched (caller should sys.exit).
    """
    if not zip_path or not zip_path.exists():
        return False

    try:
        install_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
        temp_dir = _get_update_temp_dir()
        extract_dir = temp_dir / "extracted"

        # Clean & extract
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # Handle single top-level directory in zip
        contents = list(extract_dir.iterdir())
        source_dir = contents[0] if len(contents) == 1 and contents[0].is_dir() else extract_dir

        exe_name = Path(sys.executable).name if getattr(sys, "frozen", False) else "UTHelper.exe"
        pid = os.getpid()

        bat_content = f'''@echo off
chcp 65001 >nul 2>&1
title UTHelper Updater
echo.
echo   Dang cap nhat UTHelper...
echo   Doi ung dung dong hoan toan...
echo.

:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find /I "{pid}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)

echo   Sao luu phien ban cu...
if exist "{install_dir}\\__backup" rmdir /s /q "{install_dir}\\__backup"
mkdir "{install_dir}\\__backup" 2>nul

for %%f in ("{install_dir}\\*") do (
    if /I not "%%~nxf"=="__backup" (
        move /y "%%f" "{install_dir}\\__backup\\" >nul 2>&1
    )
)
for /d %%d in ("{install_dir}\\*") do (
    if /I not "%%~nxd"=="__backup" (
        move /y "%%d" "{install_dir}\\__backup\\" >nul 2>&1
    )
)

echo   Cai dat phien ban moi...
xcopy /s /e /y /q "{source_dir}\\*" "{install_dir}\\" >nul

echo   Khoi dong lai UTHelper...
start "" "{install_dir}\\{exe_name}"

timeout /t 3 /nobreak >nul
rmdir /s /q "{install_dir}\\__backup" 2>nul
rmdir /s /q "{temp_dir}" 2>nul
exit
'''
        bat_path = temp_dir / "update.bat"
        bat_path.write_text(bat_content, encoding="utf-8")

        # Launch detached
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
        logger.info("Windows updater launched - exiting for update.")
        return True

    except Exception as e:
        logger.error("Windows apply_update failed: %s", e)
        return False


# Apply (Android)

def apply_update_android(apk_path: Path) -> bool:
    """Trigger Android PackageInstaller intent for the downloaded APK.

    Uses pyjnius to call Android Java APIs.
    Returns True if install intent was launched.
    """
    if not apk_path or not apk_path.exists():
        return False

    try:
        from jnius import autoclass, cast  # type: ignore[import]

        # Get Activity context
        activity_class_name = os.environ.get("MAIN_ACTIVITY_HOST_CLASS_NAME")
        if activity_class_name:
            HostClass = autoclass(activity_class_name)
            context = cast("android.app.Activity", HostClass.mActivity)
        else:
            # Fallback for Kivy-style
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            context = cast("android.app.Activity", PythonActivity.mActivity)

        Intent = autoclass("android.content.Intent")
        File = autoclass("java.io.File")
        BuildVer = autoclass("android.os.Build$VERSION")

        apk_file = File(str(apk_path))
        intent = Intent(Intent.ACTION_VIEW)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

        if BuildVer.SDK_INT >= 24:
            # Android 7+ requires FileProvider
            FileProvider = autoclass("androidx.core.content.FileProvider")
            authority = context.getPackageName() + ".fileprovider"
            uri = FileProvider.getUriForFile(context, authority, apk_file)
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        else:
            Uri = autoclass("android.net.Uri")
            uri = Uri.fromFile(apk_file)

        intent.setDataAndType(uri, "application/vnd.android.package-archive")
        context.startActivity(intent)

        logger.info("Android install intent launched for %s", apk_path.name)
        return True

    except ImportError:
        logger.warning("pyjnius not available - cannot install APK programmatically.")
        return False
    except Exception as e:
        logger.error("Android install failed: %s", e)
        return False


# Async wrappers

def check_for_update_async(current_version: str, callback):
    """Check update in background. callback(has_update, version, release_url, asset_url)."""
    def _worker():
        result = check_for_update(current_version)
        callback(*result)
    threading.Thread(target=_worker, daemon=True, name="update-checker").start()


def download_update_async(url: str, progress_cb=None, done_cb=None):
    """Download in background. done_cb(Optional[Path])."""
    def _worker():
        result = download_update(url, progress_cb)
        if done_cb:
            done_cb(result)
    threading.Thread(target=_worker, daemon=True, name="update-downloader").start()
