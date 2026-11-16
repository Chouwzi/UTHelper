"""Measure fresh installs of the isolated benchmark app, preserving UTHelper data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid


PACKAGE = "com.uthelper.benchmark"


def package_identity(badging: str) -> tuple[str, str]:
    match = re.search(r"^package: name='([^']+)' versionCode='(\d+)'", badging, re.M)
    if not match or match[1] != PACKAGE:
        raise ValueError("Only com.uthelper.benchmark APKs may be installed or removed")
    return match[1], match[2]


def launch_fields(output: str) -> dict:
    fields = dict(re.findall(r"^(Status|LaunchState|TotalTime|WaitTime):\s*(.+)$", output, re.M))
    if fields.get("Status") != "ok" or not fields.get("TotalTime", "").isdigit():
        raise ValueError("Android did not report a successful measured launch")
    return fields


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--aapt", type=Path, required=True)
    parser.add_argument("--android-cli", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--universal", type=Path, required=True)
    parser.add_argument("--modern", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def run(command: list[str], timeout: int = 120) -> str:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Command failed ({command[0]}): {result.stderr[:300]}")
        return result.stdout

    def adb(*parts: str) -> str:
        return run(["adb", "-s", args.serial, *parts])

    if adb("shell", "am", "get-current-user").strip() != "0":
        raise RuntimeError("Measure only in the inspected primary profile")
    # Refuse existing data even if the package is currently uninstalled for user 0.
    prior = adb("shell", "dumpsys", "package", PACKAGE)
    if "Unable to find package" not in prior:
        raise RuntimeError("Existing benchmark package/data must not be overwritten")
    apks = {"universal": args.universal, "modern": args.modern, "legacy": args.legacy}
    versions = {}
    result = {"schema_version": 1, "package": PACKAGE, "variants": {}, "samples": []}
    for name, apk in apks.items():
        _, versions[name] = package_identity(run([str(args.aapt), "dump", "badging", str(apk)]))
        with apk.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        result["variants"][name] = {"bytes": apk.stat().st_size, "sha256": digest}
    if len(set(versions.values())) != 1:
        raise ValueError("All candidates must use the same version code")
    result["scope"] = (
        "Primary-user clean installs of an isolated package; staged APK install time excludes USB transfer. "
        "am start TotalTime is Android initial display, not Python UI readiness. "
        "Login UI is separately verified. No authenticated data is removed."
    )
    remote = "/data/local/tmp/uthelper-bench-" + uuid.uuid4().hex
    adb("shell", "mkdir", remote)
    active = False
    staged = []
    try:
        for name, apk in apks.items():
            destination = f"{remote}/{name}.apk"
            start = time.perf_counter()
            adb("push", str(apk), destination)
            result["variants"][name]["usb_push_seconds"] = time.perf_counter() - start
            staged.append(destination)
        # Rotate order to reduce systematic warm-cache/thermal ordering effects.
        for round_index, order in enumerate((
            ("universal", "modern", "legacy"),
            ("legacy", "universal", "modern"),
            ("modern", "legacy", "universal"),
        )):
            for name in order:
                thermal = adb("shell", "dumpsys", "thermalservice")
                start = time.perf_counter()
                installed = adb("shell", "pm", "install", "--user", "0", "-t", f"{remote}/{name}.apk")
                if installed.strip() != "Success":
                    raise RuntimeError("Clean benchmark installation failed")
                active = True
                sample = {"round": round_index + 1, "variant": name,
                          "install_seconds": time.perf_counter() - start}
                status = re.search(r"^Thermal Status:\s*(\d+)", thermal, re.M)
                sample["thermal_status_before"] = int(status[1]) if status else None
                state = adb("shell", "dumpsys", "package", PACKAGE)
                code = re.search(r"^\s*codePath=(/data/app/[\w/.~+=-]+)$", state, re.M)
                if not code:
                    raise RuntimeError("Unexpected installed benchmark code path")
                # Production Android can hide ART/oat directories from shell.
                # Keep a partial scan explicitly bounded, never call it total storage.
                disk = subprocess.run(
                    ["adb", "-s", args.serial, "shell", "du", "-sk", code[1]],
                    capture_output=True, text=True, encoding="utf-8", timeout=30,
                )
                size = re.match(r"(\d+)\s", disk.stdout)
                sample["shell_visible_code_kib"] = int(size[1]) if size else None
                sample["code_scan_complete"] = disk.returncode == 0
                sample["extract_native_libs"] = "extractNativeLibs=true" in state
                adb("shell", "pm", "grant", "--user", "0", PACKAGE, "android.permission.POST_NOTIFICATIONS")
                sample["initial_display"] = launch_fields(adb(
                    "shell", "am", "start", "-W", "--user", "0", "-n", PACKAGE + "/.MainActivity",
                ))
                time.sleep(7)
                # CLI console encoding follows the Windows host locale. Its
                # explicit JSON output file is UTF-8 and also retains private
                # UI evidence without printing other apps' notification text.
                layout_path = args.output.parent / f"layout-{round_index + 1}-{name}.json"
                layout_path.parent.mkdir(parents=True, exist_ok=True)
                run([
                    str(args.android_cli), "--no-metrics", "--sdk", str(args.sdk),
                    "layout", "--device", args.serial, "--flat", "--output", str(layout_path),
                ])
                layout = json.loads(layout_path.read_text(encoding="utf-8"))
                if not any(
                    node.get("content-desc") == "Đăng nhập UTH"
                    for window in layout if window.get("type") == "APPLICATION"
                    for node in window.get("content", [])
                ):
                    raise RuntimeError("The Python login UI did not become available")
                sample["login_ui_verified"] = True
                adb("shell", "am", "force-stop", "--user", "0", PACKAGE)
                start = time.perf_counter()
                removed = adb("shell", "pm", "uninstall", "--user", "0", "--versionCode", versions[name], PACKAGE)
                if removed.strip() != "Success":
                    raise RuntimeError("Benchmark uninstall failed")
                active = False
                sample["uninstall_seconds"] = time.perf_counter() - start
                result["samples"].append(sample)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(sample), flush=True)
    finally:
        if active:
            adb("shell", "pm", "uninstall", "--user", "0", "--versionCode", next(iter(versions.values())), PACKAGE)
        for path in staged:
            adb("shell", "rm", "-f", path)
        adb("shell", "rmdir", remote)


if __name__ == "__main__":
    main()
