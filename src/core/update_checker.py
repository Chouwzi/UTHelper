import logging
import threading
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# GitHub repository info
_GITHUB_OWNER = "thainam83vn"
_GITHUB_REPO = "UTH-Elearning-Alert"
_RELEASES_URL = f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}/releases/latest"


def check_for_update(current_version: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Kiểm tra phiên bản mới trên GitHub Releases.
    
    Returns:
        (has_update, latest_version, download_url)
    """
    try:
        import requests
        resp = requests.get(
            _RELEASES_URL,
            timeout=10,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp.status_code == 404:
            # No releases yet
            return False, None, None
        resp.raise_for_status()
        data = resp.json()
        
        latest_tag = data.get("tag_name", "").lstrip("v")
        current = current_version.lstrip("v")
        
        if not latest_tag:
            return False, None, None
        
        # Simple version comparison
        try:
            from packaging.version import Version, InvalidVersion
            has_update = Version(latest_tag) > Version(current)
        except (ImportError, Exception):
            # packaging not available, fallback: string comparison
            has_update = latest_tag != current and latest_tag > current
        
        html_url = data.get("html_url", "")
        return has_update, latest_tag, html_url
    except Exception as e:
        logger.debug("Update check failed: %s", e)
        return False, None, None


def check_for_update_async(current_version: str, callback):
    """Kiểm tra update trong background thread, gọi callback(has_update, version, url)."""
    def _worker():
        result = check_for_update(current_version)
        callback(*result)
    
    t = threading.Thread(target=_worker, daemon=True, name="update-checker")
    t.start()
