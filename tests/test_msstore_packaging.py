"""Tests for Microsoft Store MSIX asset generation, packaging, verification, and docs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image
import pytest

from scripts.generate_msix_assets import (
    MSIX_ASSET_SPECS,
    MsixAssetError,
    generate_msix_assets,
    inspect_msix_assets,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_MSIX_PS1 = ROOT / "scripts" / "package_msix.ps1"
VERIFY_MSIX_PS1 = ROOT / "scripts" / "verify_msix_release.ps1"
ASSET_SCRIPT_PY = ROOT / "scripts" / "generate_msix_assets.py"
SOURCE_ICON = ROOT / "src" / "assets" / "icon.png"

_VALID_MANIFEST_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap desktop rescap">
  <Identity Name="UTHelperOrg.UTHelper" Publisher="CN=UTHelper" Version="2.5.1.0" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>UTHelper</DisplayName>
    <PublisherDisplayName>UTHelper</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Resources>
    <Resource Language="en-us" />
    <Resource Language="vi-vn" />
  </Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Applications>
    <Application Id="UTHelper" Executable="UTHelper.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="UTHelper" Description="UTH activity and deadline assistant"
                          BackgroundColor="transparent"
                          Square44x44Logo="Assets\\Square44x44Logo.png"
                          Square150x150Logo="Assets\\Square150x150Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" />
      </uap:VisualElements>
      <Extensions>
        <desktop:Extension Category="windows.startupTask"
                           Executable="UTHelperAutostart.exe"
                           EntryPoint="Windows.FullTrustApplication">
          <desktop:StartupTask TaskId="UTHelperStartup" Enabled="false" DisplayName="UTHelper" />
        </desktop:Extension>
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
    <Capability Name="internetClient" />
  </Capabilities>
</Package>
"""


def _find_makeappx() -> Path | None:
    kits_bin = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    if not kits_bin.is_dir():
        return None
    matches = sorted(kits_bin.glob("**/x64/makeappx.exe"), reverse=True)
    return matches[0] if matches else None


def _make_dummy_pe(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"MZ" + b"\x00" * 126)


def _create_workspace_bundle(*, include_flutter_assets: bool = False) -> tuple[Path, Path, Path]:
    stage_root = ROOT / "build" / f"test-msstore-{uuid.uuid4().hex[:10]}"
    bundle_dir = stage_root / "bundle"
    out_dir = stage_root / "out"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    _make_dummy_pe(bundle_dir / "UTHelper.exe")
    _make_dummy_pe(bundle_dir / "UTHelperAutostart.exe")
    (bundle_dir / "app" / "assets").mkdir(parents=True, exist_ok=True)
    if include_flutter_assets:
        (bundle_dir / "data" / "flutter_assets").mkdir(parents=True, exist_ok=True)
    return stage_root, bundle_dir, out_dir


def _build_staged_msix_dir(
    root_dir: Path,
    *,
    manifest_xml: str = _VALID_MANIFEST_TEMPLATE,
    tile_overrides: dict[str, tuple[int, int]] | None = None,
    channel: str | None = "msstore",
    store_product_id: str | None = "9NBLGGH4NNS1",
) -> Path:
    assets_dir = root_dir / "Assets"
    app_assets_dir = root_dir / "app" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    app_assets_dir.mkdir(parents=True, exist_ok=True)
    _make_dummy_pe(root_dir / "UTHelper.exe")
    _make_dummy_pe(root_dir / "UTHelperAutostart.exe")
    (root_dir / "AppxManifest.xml").write_text(manifest_xml, encoding="utf-8")

    sizes = dict(MSIX_ASSET_SPECS)
    if tile_overrides:
        sizes.update(tile_overrides)
    for filename, size in sizes.items():
        Image.new("RGBA", size, (15, 23, 42, 255)).save(assets_dir / filename, format="PNG")

    if channel is not None:
        payload = {
            "schema_version": 1,
            "channel": channel,
            "store_product_id": store_product_id,
            "android_package_id": "io.github.chouwzi.uthelper",
        }
        (app_assets_dir / "distribution-channel.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    return root_dir


def _run_ps_script(script_path: Path, args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script_path),
            *args,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Unit tests: scripts/generate_msix_assets.py
# ---------------------------------------------------------------------------


def test_generate_msix_assets_produces_exact_deterministic_pngs(tmp_path: Path) -> None:
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"

    generated_a = generate_msix_assets(SOURCE_ICON, out_a)
    generated_b = generate_msix_assets(SOURCE_ICON, out_b)

    assert set(generated_a) == set(MSIX_ASSET_SPECS)
    assert inspect_msix_assets(out_a) == ()

    for filename, expected_size in MSIX_ASSET_SPECS.items():
        path_a = generated_a[filename]
        path_b = generated_b[filename]
        with Image.open(path_a) as img:
            assert img.format == "PNG"
            assert img.mode == "RGBA"
            assert img.size == expected_size
            assert img.size != (697, 744)
        assert hashlib.sha256(path_a.read_bytes()).digest() == hashlib.sha256(
            path_b.read_bytes()
        ).digest()


def test_generate_msix_assets_cli_runs_in_isolated_python_and_verifies(tmp_path: Path) -> None:
    out_dir = tmp_path / "Assets"
    gen_proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ASSET_SCRIPT_PY),
            "--source",
            str(SOURCE_ICON),
            "--output-dir",
            str(out_dir),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert gen_proc.returncode == 0, gen_proc.stderr

    verify_proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ASSET_SCRIPT_PY),
            "--output-dir",
            str(out_dir),
            "--verify-only",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert verify_proc.returncode == 0, verify_proc.stderr


def test_inspect_msix_assets_detects_missing_and_wrong_dimensions(tmp_path: Path) -> None:
    assets_dir = tmp_path / "bad_assets"
    generate_msix_assets(SOURCE_ICON, assets_dir)

    Image.new("RGBA", (697, 744), (1, 2, 3, 255)).save(assets_dir / "Wide310x150Logo.png")
    (assets_dir / "StoreLogo.png").unlink()

    issues = inspect_msix_assets(assets_dir)
    assert any("StoreLogo.png" in issue for issue in issues)
    assert any("Wide310x150Logo.png" in issue and "697x744" in issue for issue in issues)


def test_generate_msix_assets_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(MsixAssetError, match="Source icon does not exist"):
        generate_msix_assets(tmp_path / "does-not-exist.png", tmp_path / "out")


# ---------------------------------------------------------------------------
# PowerShell 5.1 AST syntax verification
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell 5.1 AST check requires Windows")
def test_msix_powershell_scripts_parse_cleanly_under_ps51() -> None:
    script_list = ",".join(f"'{p}'" for p in (PACKAGE_MSIX_PS1, VERIFY_MSIX_PS1))
    cmd = (
        f"foreach ($p in @({script_list})) {{ "
        "$tokens = $null; $errors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.ToString() }; exit 1 } "
        "}"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", cmd],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ---------------------------------------------------------------------------
# End-to-end MSIX packaging & verification (real makeappx.exe)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows makeappx.exe")
def test_package_and_verify_msix_store_end_to_end_without_repo_mutation(tmp_path: Path) -> None:
    makeappx = _find_makeappx()
    if makeappx is None:
        pytest.skip("makeappx.exe not available on host")

    repo_channel_asset = ROOT / "src" / "assets" / "distribution-channel.json"
    existed_before = repo_channel_asset.exists()

    stage_root, bundle_dir, out_dir = _create_workspace_bundle(include_flutter_assets=True)
    try:
        output_msix = out_dir / "UTHelper-msstore.msix"
        evidence_json = tmp_path / "msix.verification.json"

        pack_proc = _run_ps_script(
            PACKAGE_MSIX_PS1,
            [
                "-BundleDir",
                str(bundle_dir),
                "-IdentityName",
                "UTHelperOrg.UTHelper",
                "-Publisher",
                "CN=UTHelper & Education, O=UTH",
                "-StoreProductId",
                "9NBLGGH4NNS1",
                "-Channel",
                "msstore",
                "-SignMode",
                "none",
                "-Output",
                str(output_msix),
                "-PythonExe",
                sys.executable,
            ],
        )
        assert pack_proc.returncode == 0, f"stdout={pack_proc.stdout}\nstderr={pack_proc.stderr}"
        assert output_msix.is_file()
        assert repo_channel_asset.exists() is existed_before

        # Verify Version was auto-derived from pyproject.toml as 4-part X.Y.Z.0
        unpacked = tmp_path / "unpacked"
        unpack_proc = subprocess.run(
            [str(makeappx), "unpack", "/p", str(output_msix), "/d", str(unpacked), "/o"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert unpack_proc.returncode == 0, unpack_proc.stderr
        root_xml = ET.fromstring((unpacked / "AppxManifest.xml").read_text(encoding="utf-8"))
        ns = {"f": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
        derived_version = root_xml.find("f:Identity", ns).attrib["Version"]
        assert re.fullmatch(r"\d+\.\d+\.\d+\.0", derived_version)

        # Verify both Flet app/assets and data/flutter_assets/assets received distribution-channel.json
        for rel in (
            Path("app/assets/distribution-channel.json"),
            Path("data/flutter_assets/assets/distribution-channel.json"),
        ):
            payload = json.loads((unpacked / rel).read_text(encoding="utf-8"))
            assert payload == {
                "schema_version": 1,
                "channel": "msstore",
                "store_product_id": "9NBLGGH4NNS1",
                "android_package_id": "io.github.chouwzi.uthelper",
            }

        verify_proc = _run_ps_script(
            VERIFY_MSIX_PS1,
            [
                "-MsixPath",
                str(output_msix),
                "-Channel",
                "msstore",
                "-IdentityName",
                "UTHelperOrg.UTHelper",
                "-Publisher",
                "CN=UTHelper & Education, O=UTH",
                "-Version",
                derived_version,
                "-StoreProductId",
                "9NBLGGH4NNS1",
                "-Output",
                str(evidence_json),
            ],
        )
        assert verify_proc.returncode == 0, (
            f"stdout={verify_proc.stdout}\nstderr={verify_proc.stderr}"
        )
        evidence = json.loads(evidence_json.read_text(encoding="utf-8"))
        assert evidence["platform"] == "windows"
        assert evidence["package_type"] == "msix"
        assert evidence["channel"] == "msstore"
        assert evidence["identity_name"] == "UTHelperOrg.UTHelper"
        assert evidence["publisher"] == "CN=UTHelper & Education, O=UTH"
        assert evidence["version"] == derived_version
        assert evidence["store_product_id"] == "9NBLGGH4NNS1"
        assert evidence["wack_executed"] is False
        assert evidence["visual_assets"] == {
            "Assets/Square44x44Logo.png": {"width": 44, "height": 44},
            "Assets/StoreLogo.png": {"width": 50, "height": 50},
            "Assets/Square150x150Logo.png": {"width": 150, "height": 150},
            "Assets/Wide310x150Logo.png": {"width": 310, "height": 150},
        }
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
@pytest.mark.parametrize(
    ("case_name", "mutator"),
    [
        (
            "unscaled_697x744_logos",
            lambda d: _build_staged_msix_dir(
                d,
                tile_overrides={k: (697, 744) for k in MSIX_ASSET_SPECS},
            ),
        ),
        (
            "mis_sized_wide_tile",
            lambda d: _build_staged_msix_dir(
                d,
                tile_overrides={"Wide310x150Logo.png": (150, 150)},
            ),
        ),
        (
            "missing_startup_task",
            lambda d: _build_staged_msix_dir(
                d,
                manifest_xml=re.sub(
                    r"\s*<Extensions>.*?</Extensions>",
                    "",
                    _VALID_MANIFEST_TEMPLATE,
                    flags=re.DOTALL,
                ),
            ),
        ),
        (
            "missing_run_full_trust",
            lambda d: _build_staged_msix_dir(
                d,
                manifest_xml=_VALID_MANIFEST_TEMPLATE.replace(
                    '<rescap:Capability Name="runFullTrust" />', ""
                ),
            ),
        ),
        (
            "missing_internet_client",
            lambda d: _build_staged_msix_dir(
                d,
                manifest_xml=_VALID_MANIFEST_TEMPLATE.replace(
                    '<Capability Name="internetClient" />', ""
                ),
            ),
        ),
        (
            "forbidden_unvirtualized_resources",
            lambda d: _build_staged_msix_dir(
                d,
                manifest_xml=_VALID_MANIFEST_TEMPLATE.replace(
                    '<Capability Name="internetClient" />',
                    '<Capability Name="internetClient" />\n    <rescap:Capability Name="unvirtualizedResources" />',
                ),
            ),
        ),
        (
            "disabled_filesystem_virtualization",
            lambda d: _build_staged_msix_dir(
                d,
                manifest_xml=_VALID_MANIFEST_TEMPLATE.replace(
                    'EntryPoint="Windows.FullTrustApplication">',
                    'EntryPoint="Windows.FullTrustApplication" FileSystemWriteVirtualization="disabled">',
                    1,
                ),
            ),
        ),
        (
            "uap10_parameters_leakage",
            lambda d: _build_staged_msix_dir(
                d,
                manifest_xml=_VALID_MANIFEST_TEMPLATE.replace(
                    'Executable="UTHelperAutostart.exe"',
                    'Executable="UTHelperAutostart.exe" uap10:Parameters="--autostart"',
                ),
            ),
        ),
        (
            "missing_distribution_channel",
            lambda d: _build_staged_msix_dir(d, channel=None),
        ),
        (
            "mismatched_distribution_channel",
            lambda d: _build_staged_msix_dir(d, channel="exe_lts"),
        ),
        (
            "missing_en_us_resource_language",
            lambda d: _build_staged_msix_dir(
                d,
                manifest_xml=_VALID_MANIFEST_TEMPLATE.replace(
                    '<Resource Language="en-us" />', ""
                ),
            ),
        ),
    ],
)
def test_verify_msix_release_rejects_invalid_staged_packages(
    tmp_path: Path,
    case_name: str,
    mutator,
) -> None:
    staged = tmp_path / case_name
    mutator(staged)
    proc = _run_ps_script(
        VERIFY_MSIX_PS1,
        [
            "-MsixPath",
            str(staged),
            "-Channel",
            "msstore",
            "-IdentityName",
            "UTHelperOrg.UTHelper",
            "-Publisher",
            "CN=UTHelper",
            "-Version",
            "2.5.1.0",
            "-StoreProductId",
            "9NBLGGH4NNS1",
        ],
    )
    assert proc.returncode != 0, f"Expected failure for {case_name}, got stdout={proc.stdout}"


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
def test_verify_msix_release_rejects_plain_zip_forged_as_msix(tmp_path: Path) -> None:
    staged = _build_staged_msix_dir(tmp_path / "stage")
    forged = tmp_path / "forged.msix"
    with zipfile.ZipFile(forged, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(staged.rglob("*")):
            if item.is_file():
                zf.write(item, arcname=item.relative_to(staged).as_posix())

    proc = _run_ps_script(VERIFY_MSIX_PS1, ["-MsixPath", str(forged)])
    assert proc.returncode != 0


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows")
@pytest.mark.parametrize(
    ("bad_args", "label"),
    [
        (["-Version", "2.5.1", "-Publisher", "CN=UTHelper"], "3_part_version"),
        (["-Version", "2.5.1.0", "-Publisher", "   "], "empty_publisher"),
        (
            ["-Version", "2.5.1.0", "-Publisher", "CN=UTHelper", "-IdentityName", "bad name!"],
            "invalid_identity_name",
        ),
        (
            ["-Version", "2.5.1.0", "-Publisher", "CN=UTHelper", "-StoreProductId", "bad!"],
            "invalid_store_product_id",
        ),
    ],
)
def test_package_msix_rejects_invalid_parameters(bad_args: list[str], label: str) -> None:
    stage_root, bundle_dir, out_dir = _create_workspace_bundle()
    try:
        proc = _run_ps_script(
            PACKAGE_MSIX_PS1,
            [
                "-BundleDir",
                str(bundle_dir),
                "-Output",
                str(out_dir / f"{label}.msix"),
                "-PythonExe",
                sys.executable,
                *bad_args,
            ],
        )
        assert proc.returncode != 0, f"Expected rejection for {label}"
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Store ADR & documentation alignment tests
# ---------------------------------------------------------------------------


def test_store_adrs_and_guides_document_all_channels_and_deferred_appstore() -> None:
    adr3 = (ROOT / "docs" / "adr" / "0003-signed-release-update-channel.md").read_text(
        encoding="utf-8"
    )
    adr4 = (ROOT / "docs" / "adr" / "0004-multi-channel-store-and-lts-distribution.md").read_text(
        encoding="utf-8"
    )
    store_guide = (ROOT / "docs" / "guides" / "store-distribution-submission.md").read_text(
        encoding="utf-8"
    )
    win_guide = (ROOT / "docs" / "guides" / "windows-packaging.md").read_text(encoding="utf-8")
    privacy = (ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    terms = (ROOT / "docs" / "TERMS.md").read_text(encoding="utf-8")

    assert "0004-multi-channel-store-and-lts-distribution.md" in adr3
    for token in ("chplay", "apk_lts", "msstore", "exe_lts", "Apple App Store"):
        assert token in adr4
        assert token in store_guide

    assert "WACK" in store_guide and "runFullTrust" in store_guide
    assert "AppData" in store_guide and "virtualiz" in store_guide.lower()
    assert "16 KB" in store_guide and "REQUEST_INSTALL_PACKAGES" in store_guide
    assert "verify_msix_release.ps1" in win_guide
    assert "generate_msix_assets.py" in win_guide

    assert "Settings → Privacy" in privacy
    assert "FLET_APP_STORAGE_DATA" in privacy
    assert "Google Play" in privacy and "Data Safety" in privacy
    assert "Microsoft Store" in privacy
    assert "What a report may contain" in privacy
    assert "What is forbidden from remote reports" in privacy

    assert "Google Play" in terms and "Microsoft Store" in terms
    assert "Moodle" in terms and "Sentry" in terms and "PolyForm" in terms


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
def test_verify_msix_release_rejects_missing_office_viewer_assets(tmp_path: Path) -> None:
    staged = _build_staged_msix_dir(tmp_path / "missing-office-msix")
    proc = _run_ps_script(
        VERIFY_MSIX_PS1,
        [
            "-MsixPath",
            str(staged),
            "-Channel",
            "msstore",
            "-IdentityName",
            "UTHelperOrg.UTHelper",
            "-Publisher",
            "CN=UTHelper",
            "-Version",
            "2.5.1.0",
            "-StoreProductId",
            "9NBLGGH4NNS1",
            "-RequireOfficeViewer",
        ],
    )
    assert proc.returncode != 0
    assert "office_viewer" in (proc.stdout + proc.stderr)
