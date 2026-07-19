from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_release_workflow_pins_expected_artifact_and_signing_certificates():
    workflow = _read(".github/workflows/release.yml")

    assert "APK_CANDIDATES" in workflow
    assert "Expected exactly one Flet release APK" in workflow
    assert "find build -name '*.apk' -type f | head -1" not in workflow
    assert "ANDROID_SIGNING_CERT_SHA256" in workflow
    assert "WINDOWS_SIGNING_CERT_SHA256" in workflow
    assert "apksigner\" verify --verbose --print-certs" in workflow
    assert '"$APKANALYZER" manifest print' in workflow
    assert "Get-AuthenticodeSignature" in workflow


def test_msix_packaging_requires_identity_match_timestamp_and_verification():
    script = _read("scripts/package_msix.ps1")

    assert "does not match certificate subject" in script
    assert "/tr $TimestampUrl /td SHA256" in script
    assert "signtool verification failed" in script
    assert "makeappx validation failed" in script


def test_appinstaller_uses_repository_name_in_stable_pages_url():
    script = _read("scripts/generate_appinstaller.ps1")

    assert 'pagesRepository = $repositoryParts[1]' in script
    assert "github.io/$pagesRepository/UTHelper.appinstaller" in script
    assert "Generated AppInstaller XML is invalid" in script
