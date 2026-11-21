"""Add and validate the app-owned privacy manifest in a built iOS app."""

from __future__ import annotations

import argparse
import plistlib
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
PRIVACY_MANIFEST = ROOT / "packaging" / "ios" / "PrivacyInfo.xcprivacy"
_ATS_BYPASSES = (
    "NSAllowsArbitraryLoads",
    "NSAllowsArbitraryLoadsForMedia",
    "NSAllowsArbitraryLoadsInWebContent",
    "NSAllowsLocalNetworking",
)


class IOSAppStorePreparationError(ValueError):
    """Raised when an app bundle does not meet the iOS Store contract."""


def read_privacy_manifest(path: Path = PRIVACY_MANIFEST) -> dict[str, object]:
    try:
        with Path(path).open("rb") as stream:
            manifest = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        raise IOSAppStorePreparationError("privacy manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise IOSAppStorePreparationError("privacy manifest root must be a dictionary")
    if manifest.get("NSPrivacyTracking") is not False:
        raise IOSAppStorePreparationError("iOS Store app must not use tracking")
    if manifest.get("NSPrivacyTrackingDomains") != []:
        raise IOSAppStorePreparationError("tracking domains must be empty")
    if not isinstance(manifest.get("NSPrivacyCollectedDataTypes"), list):
        raise IOSAppStorePreparationError("collected data types must be declared")
    api_types = manifest.get("NSPrivacyAccessedAPITypes")
    if not isinstance(api_types, list) or not api_types:
        raise IOSAppStorePreparationError("required reason APIs must be declared")
    reasons = {
        item.get("NSPrivacyAccessedAPIType"): item.get(
            "NSPrivacyAccessedAPITypeReasons"
        )
        for item in api_types
        if isinstance(item, dict)
    }
    if reasons.get("NSPrivacyAccessedAPICategoryFileTimestamp") != ["C617.1"]:
        raise IOSAppStorePreparationError("file timestamp reason is missing")
    if reasons.get("NSPrivacyAccessedAPICategoryUserDefaults") != ["CA92.1"]:
        raise IOSAppStorePreparationError("user defaults reason is missing")
    if reasons.get("NSPrivacyAccessedAPICategorySystemBootTime") != ["35F9.1"]:
        raise IOSAppStorePreparationError("system boot time reason is missing")
    return manifest


def prepare_app_bundle(app_bundle: Path) -> Path:
    bundle = Path(app_bundle)
    if bundle.suffix != ".app" or bundle.is_symlink() or not bundle.is_dir():
        raise IOSAppStorePreparationError("app bundle must be a regular .app directory")
    info_path = bundle / "Info.plist"
    try:
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        raise IOSAppStorePreparationError("app Info.plist is invalid") from exc
    if not isinstance(info, dict):
        raise IOSAppStorePreparationError("app Info.plist root must be a dictionary")
    if info.get("ITSAppUsesNonExemptEncryption") is not False:
        raise IOSAppStorePreparationError(
            "App Store builds must declare exempt encryption in Info.plist"
        )
    transport_security = info.get("NSAppTransportSecurity", {})
    if not isinstance(transport_security, dict):
        raise IOSAppStorePreparationError("App Transport Security configuration is invalid")
    if any(transport_security.get(key) is True for key in _ATS_BYPASSES):
        raise IOSAppStorePreparationError("App Transport Security must remain enabled")
    if transport_security.get("NSExceptionDomains"):
        raise IOSAppStorePreparationError("App Store builds cannot allow insecure domain exceptions")

    read_privacy_manifest()
    target = bundle / "PrivacyInfo.xcprivacy"
    if target.is_symlink():
        raise IOSAppStorePreparationError("app privacy manifest cannot be a symlink")
    shutil.copyfile(PRIVACY_MANIFEST, target)
    read_privacy_manifest(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-bundle", required=True, type=Path)
    args = parser.parse_args()
    result = prepare_app_bundle(args.app_bundle)
    print(f"Prepared iOS App Store privacy manifest: {result}")


if __name__ == "__main__":
    main()
