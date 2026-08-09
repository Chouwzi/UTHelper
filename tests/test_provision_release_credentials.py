from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provision_release_credentials.ps1"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            *arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_dry_run_lists_public_contract_without_creating_credentials(tmp_path):
    backup = tmp_path / "recovery"

    result = _run(
        "-BackupDirectory",
        str(backup),
        "-Repository",
        "Chouwzi/UTHelper",
        "-Environment",
        "release",
        "-DryRun",
    )

    assert result.returncode == 0, result.stderr
    assert not backup.exists()
    assert "ANDROID_KEYSTORE_BASE64" in result.stdout
    assert "WINDOWS_PFX_BASE64" in result.stdout
    assert "SENTRY_DSN" not in result.stdout
    assert "WIX_EULA_ACCEPTED" not in result.stdout
    assert "password" not in result.stdout.casefold()


def test_repository_contained_backup_is_rejected():
    result = _run(
        "-BackupDirectory",
        str(ROOT / ".release-credentials"),
        "-DryRun",
    )

    assert result.returncode != 0
    assert "outside the repository" in result.stderr


def test_existing_nonempty_backup_is_rejected_even_in_dry_run(tmp_path):
    backup = tmp_path / "existing"
    backup.mkdir()
    (backup / "do-not-touch.txt").write_text("owner data", encoding="utf-8")

    result = _run("-BackupDirectory", str(backup), "-DryRun")

    assert result.returncode != 0
    assert "empty" in result.stderr
    assert (backup / "do-not-touch.txt").read_text(encoding="utf-8") == "owner data"


def test_script_keeps_secrets_off_process_arguments_and_plaintext_files():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "RedirectStandardInput = $true" in source
    assert "ReadToEndAsync()" in source
    assert "WaitForExit($TimeoutSeconds * 1000)" in source
    assert "-storepass:env" in source
    assert "-keypass:env" in source
    assert "CredWriteW" in source
    assert "CredDeleteW" in source
    assert "Export-PfxCertificate" in source
    assert "RandomNumberGenerator" in source
    assert "ANDROID_KEYSTORE_PASSWORD" in source
    assert "ANDROID_KEY_PASSWORD" in source
    assert "WINDOWS_PFX_PASSWORD" in source
    assert "SENTRY_DSN" not in source
    assert "WIX_EULA_ACCEPTED" not in source
    assert "password.txt" not in source.casefold()
