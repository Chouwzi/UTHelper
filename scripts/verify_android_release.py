"""Verify signed Android release APKs (apk_lts) and CH Play AABs and emit native evidence."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
from typing import Sequence
import xml.etree.ElementTree as ET
import zipfile

try:
    from scripts.release_metadata import ReleaseMetadataError, release_build_number
except ModuleNotFoundError:  # Direct ``python scripts/verify_android_release.py``.
    from release_metadata import ReleaseMetadataError, release_build_number


_ANDROID_NS = "http://schemas.android.com/apk/res/android"
_TOOLS_NS = "http://schemas.android.com/tools"
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_HEX64 = re.compile(r"^[0-9A-F]{64}$")
_COMMIT = re.compile(r"^[0-9A-Fa-f]{40}$")
_CERTIFICATE_LINE = re.compile(
    r"^(?:Signer #\d+|V\d+(?:\.\d+)? Signer:)\s+"
    r"certificate SHA-256 digest:\s*(.+?)\s*$",
    re.MULTILINE,
)
_KEYTOOL_SHA256_LINE = re.compile(
    r"^\s*SHA256:\s*([0-9A-Fa-f:]+)\s*$",
    re.MULTILINE,
)
_RECEIVERS = (
    "com.dexterous.flutterlocalnotifications.ScheduledNotificationReceiver",
    "com.dexterous.flutterlocalnotifications.ScheduledNotificationBootReceiver",
    "com.dexterous.flutterlocalnotifications.ActionBroadcastReceiver",
    "com.uthelper.backgroundsync.notification.DeadlineAlarmReceiver",
    "com.uthelper.backgroundsync.notification.RescheduleReceiver",
    "com.uthelper.backgroundsync.notification.ClassScheduleAlarmReceiver",
)
_CHPLAY_MIN_TARGET_SDK = 35
_ELF_16KB_ALIGN = 0x4000
_CHPLAY_PROHIBITED_PERMISSIONS = frozenset(
    {
        "android.permission.REQUEST_INSTALL_PACKAGES",
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
        "android.permission.USE_EXACT_ALARM",
    }
)
_CHPLAY_PROHIBITED_DEX_STRINGS = (
    "REQUEST_INSTALL_PACKAGES",
    "ACTION_MANAGE_UNKNOWN_APP_SOURCES",
    "application/vnd.android.package-archive",
    "UthUpdateFileProvider",
    "ApkUpdateInstaller",
    "ApkUpdateDownloadManager",
    "start_update_download",
    "get_update_download_status",
    "cancel_update_download",
    "install_update",
    "cancel_update",
)
_SIXTY_FOUR_BIT_ABIS = frozenset({"arm64-v8a", "x86_64"})
_REQUIRED_OFFICE_VIEWER_ASSETS = (
    "docx_host.html",
    "docx_host.js",
    "docx-preview.min.js",
    "jszip.min.js",
    "pptx_host.html",
    "pptx_host.js",
    "pptx_worker.js",
    "pptx_engine.js",
    "viewer.css",
    "viewer_common.js",
)
_REQUIRED_PDF_VIEWER_ASSETS = (
    "pdf_host.html",
    "pdf_host.js",
    "pdf_viewer.css",
    "pdf.min.mjs",
    "pdf.worker.min.mjs",
)


class AndroidVerificationError(ValueError):
    pass


def _normalize_fingerprint(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", value).upper()
    if not _HEX64.fullmatch(normalized):
        raise AndroidVerificationError("Android certificate fingerprint must be SHA-256")
    return normalized


def _version_key(path: Path) -> tuple[int, ...]:
    pieces = path.name.split(".")
    if not pieces or any(not piece.isdigit() for piece in pieces):
        return ()
    return tuple(int(piece) for piece in pieces)


def _resolve_tools(android_home: Path) -> tuple[Path, Path]:
    build_tools_root = android_home / "build-tools"
    versions = (
        sorted(
            (path for path in build_tools_root.iterdir() if path.is_dir() and _version_key(path)),
            key=_version_key,
        )
        if build_tools_root.is_dir()
        else []
    )
    if not versions:
        raise AndroidVerificationError("Android build-tools directory is missing")
    apksigner = versions[-1] / ("apksigner.bat" if os.name == "nt" else "apksigner")

    command_line_root = android_home / "cmdline-tools"
    analyzer_candidates = [
        command_line_root
        / "latest"
        / "bin"
        / ("apkanalyzer.bat" if os.name == "nt" else "apkanalyzer")
    ]
    if command_line_root.is_dir():
        analyzer_candidates.extend(
            path / "bin" / ("apkanalyzer.bat" if os.name == "nt" else "apkanalyzer")
            for path in sorted(
                command_line_root.iterdir(), key=lambda item: item.name, reverse=True
            )
            if path.is_dir() and path.name != "latest"
        )
    apkanalyzer = next((path for path in analyzer_candidates if path.is_file()), None)
    if not apksigner.is_file() or apkanalyzer is None:
        raise AndroidVerificationError("apksigner or apkanalyzer is missing from Android SDK")
    return apksigner, apkanalyzer


def _run(command: Sequence[str | Path]) -> str:
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            timeout=60,
            check=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AndroidVerificationError("Android native verifier command failed") from exc
    return completed.stdout.strip()


def _android_attr(element: ET.Element, name: str) -> str | None:
    ns_key = f"{{{_ANDROID_NS}}}{name}"
    if ns_key in element.attrib:
        return element.attrib[ns_key]
    if name in element.attrib:
        return element.attrib[name]
    return None


def _tools_attr(element: ET.Element, name: str) -> str | None:
    ns_key = f"{{{_TOOLS_NS}}}{name}"
    if ns_key in element.attrib:
        return element.attrib[ns_key]
    return None


# ---------------------------------------------------------------------------
# Pure-Python AOSP aapt.pb.XmlNode Protobuf Decoder (Real .aab Manifests)
# ---------------------------------------------------------------------------


def _pb_read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(buf):
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 70:
            raise AndroidVerificationError("AAB manifest protobuf varint overflow")
    raise AndroidVerificationError("AAB manifest protobuf truncated varint")


def _pb_iter_fields(buf: bytes) -> list[tuple[int, int, bytes | int]]:
    fields: list[tuple[int, int, bytes | int]] = []
    pos = 0
    length = len(buf)
    while pos < length:
        tag, pos = _pb_read_varint(buf, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            val, pos = _pb_read_varint(buf, pos)
            fields.append((field_num, wire_type, val))
        elif wire_type == 1:
            if pos + 8 > length:
                raise AndroidVerificationError("AAB manifest protobuf truncated fixed64")
            fields.append((field_num, wire_type, buf[pos : pos + 8]))
            pos += 8
        elif wire_type == 2:
            size, pos = _pb_read_varint(buf, pos)
            if pos + size > length:
                raise AndroidVerificationError("AAB manifest protobuf truncated length-delimited")
            fields.append((field_num, wire_type, buf[pos : pos + size]))
            pos += size
        elif wire_type == 5:
            if pos + 4 > length:
                raise AndroidVerificationError("AAB manifest protobuf truncated fixed32")
            fields.append((field_num, wire_type, buf[pos : pos + 4]))
            pos += 4
        else:
            raise AndroidVerificationError(f"Unsupported protobuf wire type: {wire_type}")
    return fields


def _pb_decode_primitive(buf: bytes) -> str | None:
    for field_num, wire_type, val in _pb_iter_fields(buf):
        if field_num == 6 and wire_type == 0 and isinstance(val, int):
            signed = val - (1 << 32) if val >= (1 << 31) else val
            return str(signed)
        if field_num == 7 and wire_type == 0 and isinstance(val, int):
            return str(val)
        if field_num == 8 and wire_type == 0 and isinstance(val, int):
            return "true" if val != 0 else "false"
    return None


def _pb_decode_item(buf: bytes) -> str | None:
    for field_num, wire_type, val in _pb_iter_fields(buf):
        if wire_type != 2 or not isinstance(val, bytes):
            continue
        if field_num in {2, 3, 4, 5}:
            for inner_num, inner_wire, inner_val in _pb_iter_fields(val):
                if inner_num == 1 and inner_wire == 2 and isinstance(inner_val, bytes):
                    return inner_val.decode("utf-8", errors="replace")
        elif field_num == 7:
            return _pb_decode_primitive(val)
    return None


def _pb_decode_xml_element(buf: bytes) -> ET.Element:
    namespace_uri = ""
    name = ""
    attrs: list[tuple[str, str, str]] = []
    children: list[ET.Element] = []

    for field_num, wire_type, val in _pb_iter_fields(buf):
        if wire_type != 2 or not isinstance(val, bytes):
            continue
        if field_num == 2:
            namespace_uri = val.decode("utf-8", errors="replace")
        elif field_num == 3:
            name = val.decode("utf-8", errors="replace")
        elif field_num == 4:
            attr_ns = ""
            attr_name = ""
            attr_val = ""
            compiled_val: str | None = None
            for a_num, a_wire, a_val in _pb_iter_fields(val):
                if a_wire != 2 or not isinstance(a_val, bytes):
                    continue
                if a_num == 1:
                    attr_ns = a_val.decode("utf-8", errors="replace")
                elif a_num == 2:
                    attr_name = a_val.decode("utf-8", errors="replace")
                elif a_num == 3:
                    attr_val = a_val.decode("utf-8", errors="replace")
                elif a_num == 5:
                    compiled_val = _pb_decode_item(a_val)
            effective_val = attr_val if attr_val != "" else (compiled_val or "")
            if attr_name:
                attrs.append((attr_ns, attr_name, effective_val))
        elif field_num == 5:
            child_el = _pb_decode_xml_node(val)
            if child_el is not None:
                children.append(child_el)

    if not name:
        raise AndroidVerificationError("AAB manifest protobuf element missing name")
    tag = f"{{{namespace_uri}}}{name}" if namespace_uri else name
    element = ET.Element(tag)
    for attr_ns, attr_name, attr_val in attrs:
        key = f"{{{attr_ns}}}{attr_name}" if attr_ns else attr_name
        element.attrib[key] = attr_val
    for child in children:
        element.append(child)
    return element


def _pb_decode_xml_node(buf: bytes) -> ET.Element | None:
    for field_num, wire_type, val in _pb_iter_fields(buf):
        if field_num == 1 and wire_type == 2 and isinstance(val, bytes):
            return _pb_decode_xml_element(val)
    return None


def decode_aab_manifest_bytes(raw: bytes) -> ET.Element:
    """Decode plain XML or AOSP ``aapt.pb.XmlNode`` protobuf manifest bytes."""
    stripped = raw.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<"):
        try:
            return ET.fromstring(raw.decode("utf-8"))
        except (UnicodeDecodeError, ET.ParseError) as exc:
            raise AndroidVerificationError("AAB manifest XML is invalid") from exc
    root = _pb_decode_xml_node(raw)
    if root is None or root.tag.rsplit("}", 1)[-1] != "manifest":
        raise AndroidVerificationError("AAB manifest protobuf is invalid")
    return root


# ---------------------------------------------------------------------------
# ELF64 16 KB Page-Size Alignment & DEX String Pool Scanners
# ---------------------------------------------------------------------------


def verify_elf_16kb_alignment(elf_bytes: bytes, *, library_name: str, abi: str) -> None:
    """Verify 16 KB (0x4000) ``PT_LOAD`` ``p_align`` for every 64-bit ELF library."""
    if len(elf_bytes) < 52 or elf_bytes[:4] != b"\x7fELF":
        raise AndroidVerificationError(
            f"Native library {library_name} is not a valid ELF binary"
        )
    ei_class = elf_bytes[4]
    ei_data = elf_bytes[5]
    if ei_data == 1:
        endian = "<"
    elif ei_data == 2:
        endian = ">"
    else:
        raise AndroidVerificationError(
            f"Native library {library_name} has invalid ELF endianness"
        )

    if abi in _SIXTY_FOUR_BIT_ABIS and ei_class != 2:
        raise AndroidVerificationError(
            f"64-bit ABI library {library_name} ({abi}) must be ELF64"
        )

    if ei_class == 1:
        # 32-bit ELF (e.g. armeabi-v7a): validate program header table structure only.
        e_phoff, = struct.unpack_from(f"{endian}I", elf_bytes, 28)
        e_phentsize, e_phnum = struct.unpack_from(f"{endian}HH", elf_bytes, 42)
        if e_phnum == 0 or e_phentsize < 32 or e_phoff + e_phentsize * e_phnum > len(elf_bytes):
            raise AndroidVerificationError(
                f"Native library {library_name} has invalid ELF32 program headers"
            )
        return

    if ei_class != 2 or len(elf_bytes) < 64:
        raise AndroidVerificationError(
            f"Native library {library_name} has invalid ELF64 header"
        )

    (e_phoff,) = struct.unpack_from(f"{endian}Q", elf_bytes, 32)
    e_phentsize, e_phnum = struct.unpack_from(f"{endian}HH", elf_bytes, 54)
    if e_phnum == 0 or e_phentsize < 56 or e_phoff + e_phentsize * e_phnum > len(elf_bytes):
        raise AndroidVerificationError(
            f"Native library {library_name} has invalid ELF64 program headers"
        )

    pt_load_count = 0
    for idx in range(e_phnum):
        offset = e_phoff + idx * e_phentsize
        (p_type,) = struct.unpack_from(f"{endian}I", elf_bytes, offset)
        if p_type != 1:  # PT_LOAD
            continue
        pt_load_count += 1
        (p_align,) = struct.unpack_from(f"{endian}Q", elf_bytes, offset + 48)
        if p_align < _ELF_16KB_ALIGN or (p_align % _ELF_16KB_ALIGN) != 0:
            raise AndroidVerificationError(
                f"ELF64 library {library_name} PT_LOAD segment #{pt_load_count} "
                f"has p_align=0x{p_align:x} (< 0x{_ELF_16KB_ALIGN:x} / 16 KB)"
            )
    if pt_load_count == 0:
        raise AndroidVerificationError(
            f"ELF64 library {library_name} has no PT_LOAD segments"
        )


def _skip_uleb128(data: bytes, pos: int) -> int:
    limit = len(data)
    for _ in range(5):
        if pos >= limit:
            raise AndroidVerificationError("DEX ULEB128 exceeds file bounds")
        byte = data[pos]
        pos += 1
        if (byte & 0x80) == 0:
            return pos
    raise AndroidVerificationError("DEX ULEB128 is malformed")


def extract_dex_strings(dex_bytes: bytes, *, dex_name: str = "classes.dex") -> list[str]:
    """Parse string pool entries from a DEX binary."""
    if (
        len(dex_bytes) < 0x70
        or dex_bytes[:4] != b"dex\n"
        or dex_bytes[7:8] != b"\x00"
    ):
        raise AndroidVerificationError(f"DEX file {dex_name} has invalid DEX header")
    (endian_tag,) = struct.unpack_from("<I", dex_bytes, 40)
    if endian_tag == 0x12345678:
        endian = "<"
    elif endian_tag == 0x78563412:
        endian = ">"
    else:
        raise AndroidVerificationError(f"DEX file {dex_name} has invalid endian tag")

    string_ids_size, string_ids_off = struct.unpack_from(f"{endian}II", dex_bytes, 56)
    if string_ids_size == 0:
        return []
    if string_ids_off + (4 * string_ids_size) > len(dex_bytes):
        raise AndroidVerificationError(f"DEX file {dex_name} string_ids table out of bounds")

    strings: list[str] = []
    for idx in range(string_ids_size):
        (string_data_off,) = struct.unpack_from(
            f"{endian}I", dex_bytes, string_ids_off + (4 * idx)
        )
        if string_data_off >= len(dex_bytes):
            raise AndroidVerificationError(f"DEX file {dex_name} string offset out of bounds")
        start = _skip_uleb128(dex_bytes, string_data_off)
        end = dex_bytes.find(b"\x00", start)
        if end == -1:
            raise AndroidVerificationError(f"DEX file {dex_name} unterminated string entry")
        strings.append(dex_bytes[start:end].decode("utf-8", errors="replace"))
    return strings


def _extract_native_libraries(
    archive: zipfile.ZipFile, *, is_aab: bool
) -> list[tuple[str, str]]:
    """Return ``[(zip_member_name, abi), ...]`` for all packaged ``.so`` files."""
    libs: list[tuple[str, str]] = []
    for name in archive.namelist():
        if not name.endswith(".so"):
            continue
        parts = name.split("/")
        if is_aab:
            if len(parts) == 4 and parts[1] == "lib" and parts[2]:
                libs.append((name, parts[2]))
        else:
            if len(parts) == 3 and parts[0] == "lib" and parts[1]:
                libs.append((name, parts[1]))
    return libs


def _read_embedded_distribution_channel(archive: zipfile.ZipFile) -> dict[str, object] | None:
    """Locate and parse ``distribution-channel.json`` inside an APK/AAB (or nested app.zip)."""
    direct_candidates = [
        name
        for name in archive.namelist()
        if name == "distribution-channel.json" or name.endswith("/distribution-channel.json")
    ]
    for member in sorted(direct_candidates):
        try:
            payload = json.loads(archive.read(member).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
            raise AndroidVerificationError(
                f"Embedded distribution-channel.json ({member}) is invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise AndroidVerificationError(
                f"Embedded distribution-channel.json ({member}) must be a JSON object"
            )
        return payload

    nested_zips = [
        name
        for name in archive.namelist()
        if name == "assets/app.zip"
        or name.endswith("/assets/app.zip")
        or name.endswith("/app/app.zip")
        or name.endswith("flutter_assets/app/app.zip")
    ]
    for nested_name in sorted(nested_zips):
        try:
            with zipfile.ZipFile(io.BytesIO(archive.read(nested_name))) as nested:
                inner_candidates = [
                    item
                    for item in nested.namelist()
                    if item == "distribution-channel.json"
                    or item.endswith("/distribution-channel.json")
                ]
                for inner in sorted(inner_candidates):
                    payload = json.loads(nested.read(inner).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise AndroidVerificationError(
                            "Embedded distribution-channel.json inside app.zip must be an object"
                        )
                    return payload
        except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AndroidVerificationError(
                f"Failed to inspect nested app bundle {nested_name}"
            ) from exc
    return None


def _verify_office_viewer_assets_in_app_zip(
    archive: zipfile.ZipFile,
    *,
    require: bool,
) -> bool:
    """Verify bundled ``assets/office_viewer/*`` files inside nested Flet ``app.zip``."""
    nested_zips = [
        name
        for name in archive.namelist()
        if name == "assets/app.zip"
        or name.endswith("/assets/app.zip")
        or name.endswith("/app/app.zip")
        or name.endswith("flutter_assets/app/app.zip")
    ]
    if not nested_zips:
        if require:
            raise AndroidVerificationError(
                "Android artifact is missing nested Flet app.zip containing office_viewer assets"
            )
        return False

    verified_any = False
    for nested_name in sorted(nested_zips):
        try:
            with zipfile.ZipFile(io.BytesIO(archive.read(nested_name))) as nested:
                names = set(nested.namelist())
                has_office_or_release = any(
                    item == "assets/release-version"
                    or item.endswith("/assets/release-version")
                    or "assets/office_viewer/" in item
                    for item in names
                )
                if not require and not has_office_or_release:
                    continue
                for asset_name in _REQUIRED_OFFICE_VIEWER_ASSETS:
                    candidates = (
                        f"assets/office_viewer/{asset_name}",
                        f"app/assets/office_viewer/{asset_name}",
                    )
                    matched = next((c for c in candidates if c in names), None)
                    if matched is None:
                        raise AndroidVerificationError(
                            f"Nested app.zip ({nested_name}) is missing office_viewer asset: {asset_name}"
                        )
                    if nested.getinfo(matched).file_size <= 0:
                        raise AndroidVerificationError(
                            f"Nested app.zip ({nested_name}) has empty office_viewer asset: {asset_name}"
                        )
                for asset_name in _REQUIRED_PDF_VIEWER_ASSETS:
                    candidates = (
                        f"assets/pdf_viewer/{asset_name}",
                        f"app/assets/pdf_viewer/{asset_name}",
                    )
                    matched = next((c for c in candidates if c in names), None)
                    if matched is None:
                        raise AndroidVerificationError(
                            f"Nested app.zip ({nested_name}) is missing pdf_viewer asset: {asset_name}"
                        )
                    if nested.getinfo(matched).file_size <= 0:
                        raise AndroidVerificationError(
                            f"Nested app.zip ({nested_name}) has empty pdf_viewer asset: {asset_name}"
                        )
                verified_any = True
        except zipfile.BadZipFile as exc:
            raise AndroidVerificationError(
                f"Failed to inspect nested app bundle {nested_name}"
            ) from exc

    if require and not verified_any:
        raise AndroidVerificationError(
            "Android artifact is missing office_viewer assets inside nested app.zip"
        )
    return verified_any


def _verify_quiz_and_webview_packages(
    archive: zipfile.ZipFile,
    *,
    require: bool,
) -> bool:
    """Verify ``flutter_inappwebview`` DEX classes plus ``flet_uth_quiz`` & ``flet_webview`` Python packages."""
    archive_names = archive.namelist()
    has_sitepackages_archive = any(
        name.rsplit("/", 1)[-1] in {"sitepackages.zip", "site-packages.zip"}
        for name in archive_names
    )
    if not require and not has_sitepackages_archive:
        return False

    dex_entries = [name for name in archive_names if name.endswith(".dex")]
    has_inappwebview_dex = False
    for dex_name in sorted(dex_entries):
        dex_strings = extract_dex_strings(archive.read(dex_name), dex_name=dex_name)
        if any("flutter_inappwebview" in item.lower() for item in dex_strings):
            has_inappwebview_dex = True
            break
    if not has_inappwebview_dex:
        raise AndroidVerificationError(
            "Android artifact DEX is missing flutter_inappwebview plugin classes"
        )

    has_flet_uth_quiz = False
    has_flet_webview = False

    def _scan_paths(paths: Sequence[str]) -> None:
        nonlocal has_flet_uth_quiz, has_flet_webview
        for raw_name in paths:
            if not raw_name.endswith((".py", ".pyc")):
                continue
            parts = raw_name.replace("\\", "/").split("/")
            parents = parts[:-1]
            if "flet_uth_quiz" in parents:
                has_flet_uth_quiz = True
            if "flet_webview" in parents:
                has_flet_webview = True

    _scan_paths(archive_names)
    nested_zips = [name for name in archive_names if name.endswith(".zip")]
    for nested_name in sorted(nested_zips):
        info = archive.getinfo(nested_name)
        if info.file_size > 256 * 1024 * 1024:
            raise AndroidVerificationError(
                f"Nested Python archive exceeds size limit: {nested_name}"
            )
        try:
            with zipfile.ZipFile(io.BytesIO(archive.read(nested_name))) as nested:
                _scan_paths(nested.namelist())
        except zipfile.BadZipFile as exc:
            raise AndroidVerificationError(
                f"Failed to inspect nested Python archive {nested_name}"
            ) from exc

    if not has_flet_uth_quiz:
        raise AndroidVerificationError(
            "Android artifact is missing packaged flet_uth_quiz Python extension"
        )
    if not has_flet_webview:
        raise AndroidVerificationError(
            "Android artifact is missing packaged flet_webview Python extension"
        )
    return True


def _verify_chplay_manifest_policy(manifest_root: ET.Element) -> None:
    """Enforce Google Play Store manifest compliance policy."""
    target_sdks: list[int] = []
    for element in manifest_root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "uses-sdk":
            raw_target = _android_attr(element, "targetSdkVersion")
            if raw_target is not None:
                try:
                    target_sdks.append(int(raw_target))
                except ValueError as exc:
                    raise AndroidVerificationError(
                        f"CH Play manifest has non-integer targetSdkVersion: {raw_target!r}"
                    ) from exc

    if not target_sdks or min(target_sdks) < _CHPLAY_MIN_TARGET_SDK:
        raise AndroidVerificationError(
            f"CH Play targetSdkVersion must be >= {_CHPLAY_MIN_TARGET_SDK}; found {target_sdks}"
        )

    for element in manifest_root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if _tools_attr(element, "node") == "remove":
            continue
        if tag in {"uses-permission", "uses-permission-sdk-23"}:
            perm_name = (_android_attr(element, "name") or "").strip()
            if perm_name in _CHPLAY_PROHIBITED_PERMISSIONS or perm_name.startswith(
                "android.permission.FOREGROUND_SERVICE_"
            ):
                raise AndroidVerificationError(
                    f"CH Play manifest contains prohibited permission: {perm_name}"
                )
        elif tag == "provider":
            provider_name = (_android_attr(element, "name") or "").strip()
            authorities = (_android_attr(element, "authorities") or "").strip()
            if "UthUpdateFileProvider" in provider_name or authorities.endswith(".uthupdates"):
                raise AndroidVerificationError(
                    f"CH Play manifest contains prohibited updater provider: {provider_name}"
                )
        elif tag == "service":
            service_name = (_android_attr(element, "name") or "").strip()
            fg_type = (_android_attr(element, "foregroundServiceType") or "").strip()
            if fg_type or "ForegroundService" in service_name:
                raise AndroidVerificationError(
                    f"CH Play manifest contains prohibited foreground service: {service_name or fg_type}"
                )

    applications = [
        element
        for element in manifest_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "application"
    ]
    if not applications:
        raise AndroidVerificationError("CH Play manifest is missing <application> element")
    app = applications[0]
    allow_backup = _android_attr(app, "allowBackup")
    if allow_backup is None or allow_backup.strip().lower() != "false":
        raise AndroidVerificationError(
            "CH Play manifest must explicitly set android:allowBackup='false'"
        )
    cleartext = _android_attr(app, "usesCleartextTraffic")
    if cleartext is not None and cleartext.strip().lower() == "true":
        raise AndroidVerificationError(
            "CH Play manifest must not enable android:usesCleartextTraffic='true'"
        )
    extract_native_libs = _android_attr(app, "extractNativeLibs")
    if extract_native_libs is not None and extract_native_libs.strip().lower() == "true":
        raise AndroidVerificationError(
            "CH Play manifest must not enable android:extractNativeLibs='true'"
        )


def _verify_aab_bundle_config(archive: zipfile.ZipFile) -> None:
    """Verify ``BundleConfig.pb`` enforces uncompressed, 16 KB-aligned native libraries."""
    if "BundleConfig.pb" not in archive.namelist():
        raise AndroidVerificationError("AAB is missing BundleConfig.pb")
    raw = archive.read("BundleConfig.pb")
    optimizations_bytes: bytes | None = None
    for field_num, wire_type, val in _pb_iter_fields(raw):
        if field_num == 2 and wire_type == 2 and isinstance(val, bytes):
            optimizations_bytes = val
            break
    if optimizations_bytes is None:
        raise AndroidVerificationError(
            "AAB BundleConfig.pb is missing optimizations configuration"
        )
    uncompress_bytes: bytes | None = None
    for field_num, wire_type, val in _pb_iter_fields(optimizations_bytes):
        if field_num == 2 and wire_type == 2 and isinstance(val, bytes):
            uncompress_bytes = val
            break
    if uncompress_bytes is None:
        raise AndroidVerificationError(
            "AAB BundleConfig.pb is missing uncompress_native_libraries optimization"
        )
    enabled: int | None = None
    alignment: int | None = None
    for field_num, wire_type, val in _pb_iter_fields(uncompress_bytes):
        if field_num == 1 and wire_type == 0 and isinstance(val, int):
            enabled = val
        elif field_num == 2 and wire_type == 0 and isinstance(val, int):
            alignment = val
    if enabled != 1:
        raise AndroidVerificationError(
            "AAB BundleConfig.pb must enable uncompress_native_libraries"
        )
    if alignment is None or alignment < 2:
        raise AndroidVerificationError(
            f"AAB BundleConfig.pb native library page alignment must be >= 16 KB (alignment={alignment})"
        )


def _verify_apk_native_lib_zip_alignment(
    artifact: Path,
    archive: zipfile.ZipFile,
    native_libs: list[tuple[str, str]],
) -> None:
    """Verify CH Play APKs store native libraries uncompressed and 16 KB offset-aligned."""
    with artifact.open("rb") as raw_zip:
        for so_name, abi in sorted(native_libs):
            info = archive.getinfo(so_name)
            if info.compress_type != zipfile.ZIP_STORED:
                raise AndroidVerificationError(
                    f"CH Play APK native library {so_name} must use ZIP_STORED"
                )
            raw_zip.seek(info.header_offset)
            lfh = raw_zip.read(30)
            if len(lfh) != 30 or lfh[:4] != b"PK\x03\x04":
                raise AndroidVerificationError(
                    f"CH Play APK native library {so_name} has invalid ZIP local header"
                )
            filename_len, extra_len = struct.unpack_from("<HH", lfh, 26)
            data_offset = info.header_offset + 30 + filename_len + extra_len
            if abi in _SIXTY_FOUR_BIT_ABIS and (data_offset % _ELF_16KB_ALIGN) != 0:
                raise AndroidVerificationError(
                    f"CH Play APK 64-bit native library {so_name} ({abi}) data offset "
                    f"0x{data_offset:x} is not 16 KB aligned"
                )


def _verify_chplay_archive_contents(
    archive: zipfile.ZipFile,
    *,
    artifact: Path,
    is_aab: bool,
    expected_package_id: str,
    native_libs: list[tuple[str, str]],
) -> None:
    """Enforce CH Play resource, multidex DEX, 16 KB ELF, and channel config policies."""
    for name in archive.namelist():
        if name == "uth_update_paths.xml" or name.endswith("/uth_update_paths.xml"):
            raise AndroidVerificationError(
                f"CH Play artifact must not package updater FileProvider paths: {name}"
            )

    dex_entries = [name for name in archive.namelist() if name.endswith(".dex")]
    if not dex_entries:
        raise AndroidVerificationError("CH Play artifact does not contain any DEX files")
    for dex_name in sorted(dex_entries):
        dex_bytes = archive.read(dex_name)
        dex_strings = extract_dex_strings(dex_bytes, dex_name=dex_name)
        for text in dex_strings:
            for forbidden in _CHPLAY_PROHIBITED_DEX_STRINGS:
                if forbidden in text:
                    raise AndroidVerificationError(
                        f"CH Play DEX {dex_name} contains prohibited updater symbol: {forbidden}"
                    )

    if not native_libs:
        raise AndroidVerificationError("CH Play artifact does not contain native libraries")
    for so_name, abi in sorted(native_libs):
        verify_elf_16kb_alignment(archive.read(so_name), library_name=so_name, abi=abi)
    if is_aab:
        _verify_aab_bundle_config(archive)
    else:
        _verify_apk_native_lib_zip_alignment(artifact, archive, native_libs)

    channel_payload = _read_embedded_distribution_channel(archive)
    if channel_payload is None:
        raise AndroidVerificationError(
            "CH Play artifact is missing embedded distribution-channel.json"
        )
    actual_channel = str(channel_payload.get("channel") or "").strip().lower()
    if actual_channel != "chplay":
        raise AndroidVerificationError(
            f"CH Play distribution-channel.json channel mismatch: {actual_channel!r}"
        )
    embedded_pkg = channel_payload.get("android_package_id")
    if embedded_pkg is not None and str(embedded_pkg).strip() != expected_package_id:
        raise AndroidVerificationError(
            f"CH Play distribution-channel.json package mismatch: {embedded_pkg!r}"
        )


def _verify_aab_signature(
    artifact: Path,
    *,
    expected_fingerprint: str,
    apksigner: Path | None,
) -> None:
    """Verify AAB upload signature via ``apksigner`` (test shims) or ``jarsigner``/``keytool``."""
    if apksigner is not None and apksigner.is_file():
        try:
            signature_output = _run((apksigner, "verify", "--verbose", "--print-certs", artifact))
            signer_fingerprints = [
                _normalize_fingerprint(match.group(1))
                for match in _CERTIFICATE_LINE.finditer(signature_output)
            ]
            if signer_fingerprints:
                if set(signer_fingerprints) != {expected_fingerprint}:
                    raise AndroidVerificationError(
                        "AAB signing certificate does not match pinned identity"
                    )
                return
        except AndroidVerificationError as exc:
            # Real Android SDK apksigner does not inspect JAR-signed AABs; fall back to jarsigner.
            if "does not match pinned identity" in str(exc):
                raise

    jarsigner = shutil.which("jarsigner")
    keytool = shutil.which("keytool")
    if jarsigner and keytool:
        _run((jarsigner, "-verify", "-strict", artifact))
        cert_output = _run((keytool, "-printcert", "-jarfile", artifact))
        fingerprints = [
            _normalize_fingerprint(match.group(1))
            for match in _KEYTOOL_SHA256_LINE.finditer(cert_output)
        ]
        if fingerprints and set(fingerprints) == {expected_fingerprint}:
            return
    raise AndroidVerificationError("AAB signing certificate does not match pinned identity")


def _extract_aab_manifest_and_metadata(
    artifact: Path,
    *,
    archive: zipfile.ZipFile,
    apkanalyzer: Path | None,
) -> tuple[str, str, str, ET.Element]:
    """Read package/version metadata and XML manifest tree for an ``.aab``."""
    if apkanalyzer is not None and apkanalyzer.is_file():
        try:
            actual_package = _run((apkanalyzer, "manifest", "application-id", artifact))
            actual_version = _run((apkanalyzer, "manifest", "version-name", artifact))
            actual_build_number = _run((apkanalyzer, "manifest", "version-code", artifact))
            manifest_text = _run((apkanalyzer, "manifest", "print", artifact))
            manifest_root = ET.fromstring(manifest_text)
            return actual_package, actual_version, actual_build_number, manifest_root
        except (AndroidVerificationError, ET.ParseError):
            # Real apkanalyzer rejects .aab containers; decode base/manifest/AndroidManifest.xml directly.
            pass

    if "base/manifest/AndroidManifest.xml" not in archive.namelist():
        raise AndroidVerificationError("AAB is missing base/manifest/AndroidManifest.xml")
    manifest_root = decode_aab_manifest_bytes(archive.read("base/manifest/AndroidManifest.xml"))
    actual_package = (manifest_root.attrib.get("package") or "").strip()
    actual_version = (_android_attr(manifest_root, "versionName") or "").strip()
    actual_build_number = (_android_attr(manifest_root, "versionCode") or "").strip()
    return actual_package, actual_version, actual_build_number, manifest_root


def verify_android_release(
    *,
    apk: Path | None = None,
    aab: Path | None = None,
    channel: str | None = None,
    version: str,
    build_number: int,
    package_id: str,
    certificate_sha256: str = "",
    commit_sha: str,
    workflow_run_id: str,
    output: Path,
    android_home: Path | None = None,
    architecture: str = "universal",
    allow_unsigned: bool = False,
    require_office_viewer: bool = False,
    require_quiz_webview: bool = False,
) -> dict[str, object]:
    if (apk is None) == (aab is None):
        raise AndroidVerificationError("Provide exactly one of apk or aab")
    is_aab = aab is not None
    artifact = (aab if is_aab else apk).resolve()
    resolved_channel = (channel or ("chplay" if is_aab else "apk_lts")).strip().lower()
    if resolved_channel not in {"apk_lts", "chplay"}:
        raise AndroidVerificationError(f"Unsupported Android channel: {resolved_channel!r}")
    if resolved_channel == "apk_lts" and is_aab:
        raise AndroidVerificationError("apk_lts channel requires an APK artifact")
    if allow_unsigned and resolved_channel != "chplay":
        raise AndroidVerificationError(
            "--allow-unsigned is only supported for chplay diagnostic verification"
        )

    suffix = "-arm64-v8a" if architecture == "arm64" else ""
    if architecture not in {"universal", "arm64"}:
        raise AndroidVerificationError("Unsupported Android architecture")
    if is_aab:
        if not artifact.is_file() or artifact.suffix.lower() != ".aab":
            raise AndroidVerificationError("Android AAB artifact path is invalid")
    else:
        if not artifact.is_file() or artifact.name != f"UTHelper-{version}{suffix}.apk":
            raise AndroidVerificationError("Android release APK filename is not canonical")

    if not _VERSION.fullmatch(version) or build_number <= 0:
        raise AndroidVerificationError("Android release version metadata is invalid")
    try:
        canonical_build_number = release_build_number(version)
    except ReleaseMetadataError as exc:
        raise AndroidVerificationError("Android release version metadata is invalid") from exc
    if build_number != canonical_build_number:
        raise AndroidVerificationError("Android build number is not canonical for version")
    if package_id != "com.uthelper.uthelper":
        raise AndroidVerificationError("Android package ID is not canonical")
    if not _COMMIT.fullmatch(commit_sha) or not workflow_run_id.strip():
        raise AndroidVerificationError("Android provenance metadata is invalid")

    expected_fingerprint = ""
    if not allow_unsigned:
        expected_fingerprint = _normalize_fingerprint(certificate_sha256)

    with artifact.open("rb") as stream:
        if stream.read(4) != b"PK\x03\x04":
            raise AndroidVerificationError(
                f"Android {'AAB' if is_aab else 'APK'} ZIP magic is invalid"
            )

    apksigner: Path | None = None
    apkanalyzer: Path | None = None
    if not is_aab:
        if android_home is None:
            sdk_value = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
            if not sdk_value:
                raise AndroidVerificationError("ANDROID_HOME is not configured")
            android_home = Path(sdk_value)
        apksigner, apkanalyzer = _resolve_tools(Path(android_home))
    else:
        resolved_sdk = android_home
        if resolved_sdk is None:
            sdk_value = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
            if sdk_value:
                resolved_sdk = Path(sdk_value)
        if resolved_sdk is not None:
            try:
                apksigner, apkanalyzer = _resolve_tools(Path(resolved_sdk))
            except AndroidVerificationError:
                apksigner, apkanalyzer = None, None

    if not is_aab:
        assert apksigner is not None and apkanalyzer is not None
        signature_output = _run((apksigner, "verify", "--verbose", "--print-certs", artifact))
        signer_fingerprints = [
            _normalize_fingerprint(match.group(1))
            for match in _CERTIFICATE_LINE.finditer(signature_output)
        ]
        if not signer_fingerprints or set(signer_fingerprints) != {expected_fingerprint}:
            raise AndroidVerificationError("APK signing certificate does not match pinned identity")

        actual_package = _run((apkanalyzer, "manifest", "application-id", artifact))
        actual_version = _run((apkanalyzer, "manifest", "version-name", artifact))
        actual_build_number = _run((apkanalyzer, "manifest", "version-code", artifact))
        manifest = _run((apkanalyzer, "manifest", "print", artifact))
        if actual_package != package_id:
            raise AndroidVerificationError("APK package ID mismatch")
        if actual_version != version:
            raise AndroidVerificationError("APK versionName mismatch")
        if actual_build_number != str(build_number):
            raise AndroidVerificationError("APK versionCode mismatch")
        try:
            manifest_root = ET.fromstring(manifest)
        except ET.ParseError as exc:
            raise AndroidVerificationError("APK manifest XML is invalid") from exc
    else:
        if not allow_unsigned:
            _verify_aab_signature(
                artifact,
                expected_fingerprint=expected_fingerprint,
                apksigner=apksigner,
            )
        try:
            with zipfile.ZipFile(artifact) as archive:
                names = set(archive.namelist())
                if (
                    "BundleConfig.pb" not in names
                    or "base/manifest/AndroidManifest.xml" not in names
                ):
                    raise AndroidVerificationError("Android AAB container layout is invalid")
                actual_package, actual_version, actual_build_number, manifest_root = (
                    _extract_aab_manifest_and_metadata(
                        artifact,
                        archive=archive,
                        apkanalyzer=apkanalyzer,
                    )
                )
        except zipfile.BadZipFile as exc:
            raise AndroidVerificationError("Android AAB archive is invalid") from exc

        if actual_package != package_id:
            raise AndroidVerificationError("AAB package ID mismatch")
        if actual_version != version:
            raise AndroidVerificationError("AAB versionName mismatch")
        if actual_build_number != str(build_number):
            raise AndroidVerificationError("AAB versionCode mismatch")

    android_name = f"{{{_ANDROID_NS}}}name"
    receiver_names = tuple(
        element.attrib.get(android_name, element.attrib.get("name", ""))
        for element in manifest_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "receiver"
    )
    invalid_receivers = [
        name for name in _RECEIVERS if receiver_names.count(name) != 1
    ]
    if invalid_receivers:
        raise AndroidVerificationError(
            f"{'AAB' if is_aab else 'APK'} notification receiver wiring is incomplete"
        )

    if resolved_channel == "chplay":
        _verify_chplay_manifest_policy(manifest_root)

    try:
        with zipfile.ZipFile(artifact) as archive:
            native_libs = _extract_native_libraries(archive, is_aab=is_aab)
            abis = {abi for _name, abi in native_libs}
            expected_abis = (
                {"arm64-v8a"}
                if architecture == "arm64"
                else {"arm64-v8a", "armeabi-v7a", "x86_64"}
            )
            if abis != expected_abis:
                raise AndroidVerificationError(
                    f"{'AAB' if is_aab else 'APK'} native ABI inventory does not match architecture"
                )
            if resolved_channel == "chplay":
                _verify_chplay_archive_contents(
                    archive,
                    artifact=artifact,
                    is_aab=is_aab,
                    expected_package_id=package_id,
                    native_libs=native_libs,
                )
            office_viewer_verified = _verify_office_viewer_assets_in_app_zip(
                archive,
                require=require_office_viewer,
            )
            quiz_webview_verified = _verify_quiz_and_webview_packages(
                archive,
                require=require_quiz_webview,
            )
    except zipfile.BadZipFile as exc:
        raise AndroidVerificationError(
            f"{'AAB' if is_aab else 'APK'} native library archive is invalid"
        ) from exc

    with artifact.open("rb") as stream:
        artifact_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()

    if resolved_channel == "apk_lts":
        # Preserve exact schema & check names required by scripts/release_inventory.py.
        evidence: dict[str, object] = {
            "schema_version": 2,
            "platform": "android",
            "asset_name": artifact.name,
            "sha256": artifact_sha256,
            "version": version,
            "product_id": package_id,
            "architecture": architecture,
            "signature_kind": "apk-pinned",
            "signer_identity": package_id,
            "certificate_fingerprint": expected_fingerprint,
            "signature_valid": True,
            "timestamp_valid": None,
            "checks": sorted(
                (
                    "apk_signature",
                    "native_abis",
                    "package_id",
                    "version_name",
                    "version_code",
                    "notification_receivers",
                    "sha256",
                    *(("office_viewer_assets",) if office_viewer_verified else ()),
                )
            ),
            "commit_sha": commit_sha.lower(),
            "workflow_run_id": workflow_run_id.strip(),
        }
    else:
        checks = [
            "distribution_channel",
            "elf_16kb_alignment",
            "native_abis",
            "notification_receivers",
            "package_id",
            "play_dex_scan",
            "play_manifest_policy",
            "sha256",
            "target_sdk",
            "uncompressed_native_libs",
            "version_code",
            "version_name",
        ]
        if not allow_unsigned:
            checks.append("aab_signature" if is_aab else "apk_signature")
        if office_viewer_verified:
            checks.append("office_viewer_assets")
        if quiz_webview_verified:
            checks.append("quiz_webview_packages")
        evidence = {
            "schema_version": 2,
            "platform": "android",
            "channel": "chplay",
            "asset_name": artifact.name,
            "sha256": artifact_sha256,
            "version": version,
            "product_id": package_id,
            "architecture": architecture,
            "signature_kind": (
                "unsigned-diagnostic" if allow_unsigned else "aab-upload-pinned"
            ),
            "signer_identity": "" if allow_unsigned else package_id,
            "certificate_fingerprint": "" if allow_unsigned else expected_fingerprint,
            "signature_valid": not allow_unsigned,
            "timestamp_valid": None,
            "checks": sorted(checks),
            "commit_sha": commit_sha.lower(),
            "workflow_run_id": workflow_run_id.strip(),
        }

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        temporary.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    artifact_group = parser.add_mutually_exclusive_group(required=True)
    artifact_group.add_argument("--apk", type=Path, help="Android APK artifact path")
    artifact_group.add_argument("--aab", type=Path, help="Android App Bundle (.aab) artifact path")
    parser.add_argument(
        "--channel",
        choices=("apk_lts", "chplay"),
        default=None,
        help="Distribution channel (defaults to chplay for --aab and apk_lts for --apk)",
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-number", required=True, type=int)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--certificate-sha256", default="")
    parser.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="Allow unsigned diagnostic chplay AAB verification (PR CI only)",
    )
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--architecture", choices=("universal", "arm64"), default="universal")
    parser.add_argument(
        "--require-office-viewer",
        action="store_true",
        help="Require office_viewer static assets inside nested Flet app.zip",
    )
    parser.add_argument(
        "--require-quiz-webview",
        action="store_true",
        help="Require flutter_inappwebview DEX classes plus flet_uth_quiz and flet_webview packages",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    verify_android_release(
        apk=args.apk,
        aab=args.aab,
        channel=args.channel,
        version=args.version,
        build_number=args.build_number,
        package_id=args.package_id,
        certificate_sha256=args.certificate_sha256,
        commit_sha=args.commit_sha,
        workflow_run_id=args.workflow_run_id,
        output=args.output,
        architecture=args.architecture,
        allow_unsigned=args.allow_unsigned,
        require_office_viewer=args.require_office_viewer,
        require_quiz_webview=args.require_quiz_webview,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
