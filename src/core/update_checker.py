"""Auto-update checker via GitHub Releases API.

Works with both public and private repos.
Uses urllib (stdlib) — no external HTTP dependency.
"""
import json
import logging
import platform
import threading
import urllib.request
import urllib.error
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# GitHub repository info
_GITHUB_OWNER = "Chouwzi"
_GITHUB_REPO = "UTHelper"
_RELEASES_URL = f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}/releases/latest"

_UA = "UTHelper-UpdateChecker/1.0"


def _version_tuple(v: str) -> tuple:
    """Convert '2.1.0' to (2, 1, 0) for comparison."""
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except (ValueError, AttributeError):
        return (0,)


def check_for_update(current_version: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """Kiểm tra phiên bản mới trên GitHub Releases.

    Returns:
        (has_update, latest_version, release_page_url, asset_download_url)
        - asset_download_url: direct download link for platform-specific asset (APK/EXE)
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

        # Version comparison
        has_update = _version_tuple(latest_tag) > _version_tuple(current)

        html_url = data.get("html_url", "")

        # Find platform-specific asset download URL
        asset_url = _find_platform_asset(data.get("assets", []))

        return has_update, latest_tag, html_url, asset_url

    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.debug("No releases found (404).")
        else:
            logger.debug("Update check HTTP error: %s", e)
        return False, None, None, None
    except Exception as e:
        logger.debug("Update check failed: %s", e)
        return False, None, None, None


def _find_platform_asset(assets: list) -> Optional[str]:
    """Find the best download asset for current platform.

    Priority:
    - Android: .apk
    - Windows: .exe or .zip
    - iOS: .ipa
    """
    if not assets:
        return None

    system = platform.system().lower()

    # Map platform to preferred file extensions
    if system == "linux" and hasattr(platform, "android_ver"):
        # Flet Android runs on Linux kernel
        preferred = [".apk"]
    elif system == "windows":
        preferred = [".exe", ".zip", ".msi"]
    elif system == "darwin":
        preferred = [".ipa", ".dmg", ".zip"]
    else:
        # Generic mobile (Flet mobile detection)
        preferred = [".apk", ".ipa"]

    for ext in preferred:
        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith(ext):
                return asset.get("browser_download_url", "")

    # Fallback: return first asset
    if assets:
        return assets[0].get("browser_download_url", "")
    return None


def check_for_update_async(current_version: str, callback):
    """Kiểm tra update trong background thread.
    
    callback(has_update, version, release_url, asset_url)
    """
    def _worker():
        result = check_for_update(current_version)
        callback(*result)

    t = threading.Thread(target=_worker, daemon=True, name="update-checker")
    t.start()
