"""Prepare a Flet-generated Flutter Android project for channel-specific builds."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
from typing import Sequence


_PROHIBITED_CHPLAY_PERMISSIONS = (
    "android.permission.REQUEST_INSTALL_PACKAGES",
    "android.permission.FOREGROUND_SERVICE",
    "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
    "android.permission.USE_EXACT_ALARM",
)
_PROHIBITED_CHPLAY_SERVICES = (
    "com.dexterous.flutterlocalnotifications.ForegroundService",
    "androidx.work.impl.foreground.SystemForegroundService",
)


def _patch_app_gradle(gradle_file: Path, *, channel: str, target_sdk: int) -> bool:
    if not gradle_file.is_file():
        return False
    content = gradle_file.read_text(encoding="utf-8")
    is_kts = gradle_file.suffix == ".kts"
    if is_kts:
        content = re.sub(r"targetSdk\s*=\s*[^\r\n]+", f"targetSdk = {target_sdk}", content)
        content = re.sub(r"compileSdk\s*=\s*[^\r\n]+", f"compileSdk = {target_sdk}", content)
    else:
        content = re.sub(
            r"targetSdk(?:Version)?\s*=?\s*[^\r\n]+",
            f"targetSdk = {target_sdk}",
            content,
        )
        content = re.sub(
            r"compileSdk(?:Version)?\s*=?\s*[^\r\n]+",
            f"compileSdk = {target_sdk}",
            content,
        )

    if channel == "chplay":
        content = re.sub(
            r"useLegacyPackaging\s*=?\s*true",
            "useLegacyPackaging = false",
            content,
        )
        if "useLegacyPackaging = false" not in content and "// flet: legacy_packaging" in content:
            content = content.replace(
                "// flet: legacy_packaging",
                "useLegacyPackaging = false\n// flet: legacy_packaging",
                1,
            )

    gradle_file.write_text(content, encoding="utf-8")
    return True


def _patch_gradle_properties(properties_file: Path, *, channel: str) -> None:
    desired = {
        "org.gradle.jvmargs": (
            "-Xmx3G -XX:MaxMetaspaceSize=1G -XX:ReservedCodeCacheSize=256m "
            "-XX:+HeapDumpOnOutOfMemoryError"
        ),
        "org.gradle.workers.max": "2",
        "kotlin.compiler.execution.strategy": "in-process",
        "kotlin.incremental": "false",
        "uthDistributionChannel": channel,
    }
    existing_lines = (
        properties_file.read_text(encoding="utf-8").splitlines()
        if properties_file.is_file()
        else ["android.useAndroidX=true"]
    )
    filtered = [
        line
        for line in existing_lines
        if line.split("=", 1)[0].strip() not in desired
    ]
    for key, value in desired.items():
        filtered.append(f"{key}={value}")
    properties_file.write_text("\n".join(filtered) + "\n", encoding="utf-8")


def _ensure_application_attr(xml: str, attr: str, value: str) -> str:
    xml = re.sub(rf'android:{attr}="[^"]*"', f'android:{attr}="{value}"', xml)
    if f"android:{attr}=" not in xml:
        xml = re.sub(
            r"<application\b",
            f'<application android:{attr}="{value}"',
            xml,
            count=1,
        )
    return xml


def _patch_chplay_manifest(manifest_file: Path) -> bool:
    if not manifest_file.is_file():
        return False
    xml = manifest_file.read_text(encoding="utf-8")
    if "xmlns:tools=" not in xml:
        xml = xml.replace(
            "<manifest",
            '<manifest xmlns:tools="http://schemas.android.com/tools"',
            1,
        )

    for perm in _PROHIBITED_CHPLAY_PERMISSIONS:
        xml = re.sub(
            rf"(?m)^[ \t]*<uses-permission\b[^>]*{re.escape(perm)}[^>]*/>\r?\n?",
            "",
            xml,
        )

    remove_perms = "".join(
        f'    <uses-permission android:name="{perm}" tools:node="remove" />\n'
        for perm in _PROHIBITED_CHPLAY_PERMISSIONS
    )
    xml = re.sub(
        r"(?m)^([ \t]*)<application\b",
        lambda m: remove_perms + m.group(1) + "<application",
        xml,
        count=1,
    )

    xml = re.sub(
        r"(?s)[ \t]*<provider\b[^>]*UthUpdateFileProvider.*?</provider>\r?\n?",
        "",
        xml,
    )
    xml = re.sub(
        r"(?m)^[ \t]*<provider\b[^>]*UthUpdateFileProvider[^>]*/>\r?\n?",
        "",
        xml,
    )

    remove_services: list[str] = []
    for service_name in _PROHIBITED_CHPLAY_SERVICES:
        xml = re.sub(
            rf"(?s)[ \t]*<service\b[^>]*{re.escape(service_name)}.*?</service>\r?\n?",
            "",
            xml,
        )
        xml = re.sub(
            rf"(?m)^[ \t]*<service\b[^>]*{re.escape(service_name)}[^>]*/>\r?\n?",
            "",
            xml,
        )
        if service_name not in xml:
            remove_services.append(
                f'        <service android:name="{service_name}" tools:node="remove" />\n'
            )
    if remove_services:
        remove_services_block = "".join(remove_services)
        xml = re.sub(
            r"([ \t]*)</application>",
            lambda m: remove_services_block + m.group(1) + "</application>",
            xml,
            count=1,
        )

    xml = _ensure_application_attr(xml, "allowBackup", "false")
    xml = _ensure_application_attr(xml, "usesCleartextTraffic", "false")
    xml = _ensure_application_attr(xml, "extractNativeLibs", "false")

    manifest_file.write_text(xml, encoding="utf-8")
    return True


def prepare_android_flutter_project(
    project_root: Path,
    *,
    channel: str,
    target_sdk: int = 36,
) -> None:
    resolved_channel = channel.strip().lower()
    if resolved_channel not in {"apk_lts", "chplay"}:
        raise ValueError(f"Unsupported Android channel: {channel!r}")

    android_dir = project_root / "android"
    if not android_dir.is_dir():
        raise FileNotFoundError(f"Flutter Android project directory not found: {android_dir}")

    _patch_gradle_properties(android_dir / "gradle.properties", channel=resolved_channel)
    _patch_app_gradle(
        android_dir / "app" / "build.gradle.kts",
        channel=resolved_channel,
        target_sdk=target_sdk,
    )
    _patch_app_gradle(
        android_dir / "app" / "build.gradle",
        channel=resolved_channel,
        target_sdk=target_sdk,
    )
    if resolved_channel == "chplay":
        _patch_chplay_manifest(
            android_dir / "app" / "src" / "main" / "AndroidManifest.xml"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("build/flutter"),
        help="Path to Flet-generated Flutter project root",
    )
    parser.add_argument(
        "--channel",
        required=True,
        choices=("apk_lts", "chplay"),
        help="Target Android distribution channel",
    )
    parser.add_argument(
        "--target-sdk",
        type=int,
        default=36,
        help="Android compileSdk and targetSdk level (default: 36)",
    )
    args = parser.parse_args(argv)
    prepare_android_flutter_project(
        args.project_root,
        channel=args.channel,
        target_sdk=args.target_sdk,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
