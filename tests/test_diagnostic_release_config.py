"""Tests for the generated, public diagnostics and analytics runtime configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagnostics.release_config import (
    DEFAULT_DEV_POSTHOG_HOST,
    PUBLIC_CONFIG_SCHEMA_V2,
    PUBLIC_CONFIG_SCHEMA_V3,
    PUBLIC_CONFIG_SCHEMA_VERSION,
    PublicConfigError,
    PublicDiagnosticsConfig,
    generate_public_config,
    load_public_config,
    load_public_dsn,
    load_runtime_public_config,
    load_runtime_public_dsn,
    validate_public_config_bytes,
    validate_public_posthog_host,
    validate_public_posthog_key,
)
from scripts.generate_public_runtime_config import main as generate_config_main

VALID_DSN = "https://0123456789abcdef@o123.ingest.sentry.io/456"
VALID_POSTHOG_KEY = "phc_AbCdEf0123456789_-GhIjKlMnOp"
VALID_POSTHOG_HOST = "https://us.i.posthog.com"


def test_packaged_dsn_is_loaded_from_local_asset_not_build_environment(tmp_path):
    asset = tmp_path / "diagnostics-config.json"
    asset.write_text(
        json.dumps({"schema_version": 1, "sentry_dsn": VALID_DSN}),
        encoding="utf-8",
    )

    assert load_public_dsn(
        asset,
        development=False,
        environ={"UTH_SENTRY_DSN": "https://otherkey@example.invalid/99"},
    ) == VALID_DSN


def test_development_override_requires_explicit_flag(tmp_path):
    missing = tmp_path / "missing.json"

    assert load_public_dsn(
        missing,
        development=False,
        environ={"UTH_SENTRY_DSN": VALID_DSN},
    ) is None
    assert load_public_dsn(
        missing,
        development=True,
        environ={"UTH_SENTRY_DSN": VALID_DSN},
    ) == VALID_DSN


def test_packaged_runtime_resolves_flet_asset_directory(tmp_path):
    asset = tmp_path / "diagnostics-config.json"
    generate_public_config(asset, VALID_DSN)

    assert load_runtime_public_dsn(
        development=False,
        environ={
            "FLET_ASSETS_DIR": str(tmp_path),
            "UTH_SENTRY_DSN": "https://otherkey@example.invalid/99",
        },
    ) == VALID_DSN


def test_generated_asset_is_ignored_under_configured_flet_app_path():
    repository = Path(__file__).resolve().parent.parent

    assert 'path = "src"' in (repository / "pyproject.toml").read_text("utf-8")
    ignored = (repository / ".gitignore").read_text("utf-8").splitlines()
    assert "src/assets/diagnostics-config.json" in ignored


@pytest.mark.parametrize(
    "dsn",
    (
        "http://0123456789abcdef@example.invalid/1",
        "https://user:password@example.invalid/1",
        "https://0123456789abcdef@example.invalid/1?auth_token=secret",
        "https://0123456789abcdef@example.invalid/1#management-token",
        "https://sntrys_management_token@example.invalid/1",
        "https://0123456789abcdef@example.invalid/not-a-project-id",
        "https://0123456789abcdef@example.invalid/%2e%2e/1",
    ),
)
def test_generator_rejects_non_ingestion_or_management_dsn(tmp_path, dsn):
    with pytest.raises(PublicConfigError):
        generate_public_config(tmp_path / "config.json", dsn)


def test_generator_writes_only_sorted_public_fields_atomically(tmp_path):
    output = tmp_path / "diagnostics-config.json"

    generate_public_config(output, VALID_DSN)

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "sentry_dsn": VALID_DSN,
    }
    assert output.read_text(encoding="utf-8") == (
        '{"schema_version":1,"sentry_dsn":'
        f'{json.dumps(VALID_DSN)}}}\n'
    )
    assert list(tmp_path.glob(".*.tmp")) == []


def test_empty_build_value_generates_truthful_unconfigured_asset(tmp_path):
    output = tmp_path / "diagnostics-config.json"

    generate_public_config(output, "")

    assert load_public_dsn(output, development=False, environ={}) is None


def test_cli_accepts_missing_powershell_empty_value_as_unconfigured(tmp_path):
    output = tmp_path / "diagnostics-config.json"

    assert generate_config_main(["--sentry-dsn", "--output", str(output)]) == 0

    assert json.loads(output.read_text("utf-8")) == {
        "schema_version": 1,
        "sentry_dsn": "",
    }


def test_cli_release_gate_rejects_an_unconfigured_dsn(tmp_path, capsys):
    output = tmp_path / "diagnostics-config.json"

    with pytest.raises(SystemExit) as exc_info:
        generate_config_main(
            [
                "--require-configured",
                "--sentry-dsn",
                "--output",
                str(output),
            ]
        )

    assert exc_info.value.code == 2
    assert "configured Sentry DSN is required" in capsys.readouterr().err
    assert not output.exists()


def test_atomic_generator_failure_preserves_previous_asset(tmp_path, monkeypatch):
    output = tmp_path / "diagnostics-config.json"
    output.write_text("previous", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("diagnostics.release_config.os.replace", fail_replace)

    with pytest.raises(PublicConfigError):
        generate_public_config(output, VALID_DSN)

    assert output.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.parametrize(
    "payload",
    (
        {"schema_version": 2, "sentry_dsn": VALID_DSN},
        {"schema_version": 3, "sentry_dsn": VALID_DSN},
        {"schema_version": 4, "sentry_dsn": VALID_DSN},
        {"schema_version": True, "sentry_dsn": VALID_DSN},
        {"schema_version": 1, "sentry_dsn": VALID_DSN, "token": "secret"},
        {"schema_version": 1, "sentry_dsn": "http://key@example.invalid/1"},
        [],
        "not-json",
    ),
)
def test_malformed_or_unknown_packaged_config_fails_closed(tmp_path, payload):
    asset = tmp_path / "diagnostics-config.json"
    if isinstance(payload, str):
        asset.write_text(payload, encoding="utf-8")
    else:
        asset.write_text(json.dumps(payload), encoding="utf-8")

    assert load_public_dsn(asset, development=False, environ={}) is None


# ---------------------------------------------------------------------------
# PostHog Key, Host & Schema v3 Contract Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valid_key",
    (
        "phc_" + "a" * 20,
        "phc_" + "Z" * 128,
        VALID_POSTHOG_KEY,
    ),
)
def test_validate_public_posthog_key_accepts_public_project_keys(valid_key: str):
    assert validate_public_posthog_key(valid_key) == valid_key


@pytest.mark.parametrize(
    "bad_key",
    (
        "",
        "phc_" + "a" * 19,
        "phc_" + "a" * 129,
        "phx_" + "a" * 32,
        "phs_" + "a" * 32,
        "sntrys_" + "a" * 32,
        "PHC_" + "a" * 24,
        "phc_contains_secret_word_123456",
        "phc_contains_auth_token_1234567",
        "phc_contains_personal_key_12345",
        "phc_contains_phx_marker_1234567",
        "phc_contains_phs_marker_1234567",
        "phc_contains_sntrys_marker_1234",
        "phc_" + "a" * 20 + " ",
        " phc_" + "a" * 20,
        "phc_" + "a" * 20 + "\n",
        "phc_" + "a" * 20 + "\x00",
        "phc_" + "a" * 20 + "\x7f",
        "phc_" + "ä" * 24,
    ),
)
def test_validate_public_posthog_key_rejects_secret_or_malformed_tokens(bad_key: str):
    with pytest.raises(PublicConfigError):
        validate_public_posthog_key(bad_key)


def test_validate_public_posthog_key_rejects_non_string():
    with pytest.raises(PublicConfigError):
        validate_public_posthog_key(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw_host", "expected"),
    (
        ("https://us.i.posthog.com", "https://us.i.posthog.com"),
        ("https://us.i.posthog.com/", "https://us.i.posthog.com"),
        ("https://eu.i.posthog.com/", "https://eu.i.posthog.com"),
        ("https://proxy.example.com/ingest", "https://proxy.example.com/ingest"),
        ("https://proxy.example.com/ingest/", "https://proxy.example.com/ingest"),
        (
            "https://telemetry.example.edu.vn:8443/ph/v1.0/ingest/",
            "https://telemetry.example.edu.vn:8443/ph/v1.0/ingest",
        ),
        (
            "https://[2606:4700:4700::1111]/ingest/",
            "https://[2606:4700:4700::1111]/ingest",
        ),
        (
            "https://[2001:4860:4860::8888]:8443/",
            "https://[2001:4860:4860::8888]:8443",
        ),
    ),
)
def test_validate_public_posthog_host_normalizes_https_ingestion_hosts(
    raw_host: str,
    expected: str,
):
    assert validate_public_posthog_host(raw_host) == expected


@pytest.mark.parametrize(
    "bad_host",
    (
        "",
        "http://us.i.posthog.com",
        "https://US.I.POSTHOG.COM",
        "https://app.posthog.com",
        "https://us.posthog.com",
        "https://eu.posthog.com",
        "https://app.posthog.com/ingest",
        "https://us.posthog.com:443",
        "https://app.posthog.com.",
        "https://user:pass@us.i.posthog.com",
        "https://user@us.i.posthog.com",
        "https://@us.i.posthog.com",
        "https://us.i.posthog.com?ip=1",
        "https://us.i.posthog.com?",
        "https://us.i.posthog.com#frag",
        "https://us.i.posthog.com#",
        "https://proxy.example.com/./ingest",
        "https://proxy.example.com/../ingest",
        "https://proxy.example.com/a..b/ingest",
        "https://proxy.example.com/%2e%2e/ingest",
        "https://proxy.example.com//ingest",
        "https://proxy.example.com/ingest;param=1",
        "https://proxy.example.com\\ingest",
        "https://us.i.posthog.com:",
        "https://us.i.posthog.com:/ingest",
        "https://us.i.posthog.com:0",
        "https://[2606:4700:4700::1111]:",
        "https://[2606:4700:4700::1111]:0",
        "https://127.0.0.1:8000",
        "https://[::1]:8000",
        "https://[::127.0.0.1]:8000",
        "https://[::ffff:127.0.0.1]:8000",
        "https://[::ffff:8.8.8.8]:8000",
        "https://[::ffff:0:127.0.0.1]:8000",
        "https://[::ffff:0:8.8.8.8]:8000",
        "https://[64:ff9b::127.0.0.1]",
        "https://[64:ff9b::10.0.0.1]",
        "https://[64:ff9b::8.8.8.8]",
        "https://[64:ff9b:1::1]",
        "https://[2002:7f00:1::1]",
        "https://[2002:0808:0808::1]",
        "https://[2001:0000:4136:e378:8000:63bf:3fff:fdd2]",
        "https://[2001:2::1]",
        "https://[100::1]",
        "https://[2001:db8::1]",
        "https://[fc00::1]",
        "https://[fe80::1]",
        "https://10.0.0.5",
        "https://172.16.0.1",
        "https://192.168.1.10",
        "https://169.254.169.254",
        "https://100.64.0.1",
        "https://0.0.0.0",
        "https://localhost:8443",
        "https://dev.localhost",
        "https://internal.local",
        "https://intranet-only",
        "https://127.1",
        "https://0x7f000001",
    ),
)
def test_validate_public_posthog_host_rejects_unsafe_or_webui_hosts(bad_host: str):
    with pytest.raises(PublicConfigError):
        validate_public_posthog_host(bad_host, allow_private_dev=False)


def test_validate_public_posthog_host_private_dev_flag_allows_local_https_only():
    assert (
        validate_public_posthog_host(
            "https://127.0.0.1:8443/ingest/",
            allow_private_dev=True,
        )
        == "https://127.0.0.1:8443/ingest"
    )
    assert (
        validate_public_posthog_host(
            "https://[::1]:8443/",
            allow_private_dev=True,
        )
        == "https://[::1]:8443"
    )
    assert (
        validate_public_posthog_host(
            "https://localhost:8443/ph/",
            allow_private_dev=True,
        )
        == "https://localhost:8443/ph"
    )
    assert (
        validate_public_posthog_host(
            "https://192.168.1.20:8443",
            allow_private_dev=True,
        )
        == "https://192.168.1.20:8443"
    )

    # Even in dev, HTTP, web UI hosts, empty/zero ports, unspecified 0.0.0.0, and multicast stay forbidden
    for still_forbidden in (
        "http://127.0.0.1:8443",
        "https://app.posthog.com",
        "https://us.posthog.com",
        "https://eu.posthog.com",
        "https://localhost:",
        "https://[::1]:",
        "https://localhost:0",
        "https://0.0.0.0:8443",
        "https://224.0.0.1:8443",
        "https://[::ffff:0.0.0.0]:8443",
        "https://[64:ff9b::224.0.0.1]:8443",
    ):
        with pytest.raises(PublicConfigError):
            validate_public_posthog_host(still_forbidden, allow_private_dev=True)

    with pytest.raises(PublicConfigError):
        generate_public_config(
            Path("unused.json"),
            "https://0123456789abcdef@o123.ingest.sentry.io:/456",
        )


def test_schema_v3_roundtrip_and_v1_v2_backward_compatibility(tmp_path):
    assert PUBLIC_CONFIG_SCHEMA_VERSION == 1
    assert PUBLIC_CONFIG_SCHEMA_V2 == 2
    assert PUBLIC_CONFIG_SCHEMA_V3 == 3

    # Schema v1 loader compat
    v1_file = tmp_path / "v1.json"
    v1_file.write_text(
        json.dumps({"schema_version": 1, "sentry_dsn": VALID_DSN}),
        encoding="utf-8",
    )
    v1_cfg = load_public_config(v1_file, development=False, environ={})
    assert v1_cfg == PublicDiagnosticsConfig(
        dsn=VALID_DSN,
        build_environment="release",
        package_format=None,
        posthog_project_api_key=None,
        posthog_host=None,
    )
    assert v1_cfg.posthog_configured is False

    # Schema v2 loader compat
    v2_file = tmp_path / "v2.json"
    v2_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "aab",
            }
        ),
        encoding="utf-8",
    )
    v2_cfg = load_public_config(v2_file, development=False, environ={})
    assert v2_cfg == PublicDiagnosticsConfig(
        dsn=VALID_DSN,
        build_environment="release",
        package_format="aab",
        posthog_project_api_key=None,
        posthog_host=None,
    )
    assert v2_cfg.posthog_configured is False

    # Schema v3 generation & roundtrip
    v3_file = tmp_path / "v3" / "diagnostics-config.json"
    generate_public_config(
        v3_file,
        VALID_DSN,
        build_environment="release",
        package_format="aab",
        posthog_project_api_key=VALID_POSTHOG_KEY,
        posthog_host="https://eu.i.posthog.com/",
    )
    parsed_v3 = json.loads(v3_file.read_text(encoding="utf-8"))
    assert parsed_v3 == {
        "build_environment": "release",
        "package_format": "aab",
        "posthog_host": "https://eu.i.posthog.com",
        "posthog_project_api_key": VALID_POSTHOG_KEY,
        "schema_version": 3,
        "sentry_dsn": VALID_DSN,
    }

    v3_cfg = load_runtime_public_config(
        development=False,
        environ={
            "FLET_ASSETS_DIR": str(v3_file.parent),
            "UTH_POSTHOG_KEY": "phc_" + "x" * 24,
            "UTH_POSTHOG_HOST": "https://us.i.posthog.com",
        },
    )
    assert v3_cfg == PublicDiagnosticsConfig(
        dsn=VALID_DSN,
        build_environment="release",
        package_format="aab",
        posthog_project_api_key=VALID_POSTHOG_KEY,
        posthog_host="https://eu.i.posthog.com",
    )
    assert v3_cfg.posthog_configured is True


def test_schema_v3_asymmetric_posthog_fields_rejected_and_empty_pair_builds_cleanly(
    tmp_path,
    capsys,
):
    # Symmetric unconfigured Schema v3 (both "") builds cleanly and disables PostHog at runtime
    both_empty = tmp_path / "both-empty.json"
    generate_public_config(
        both_empty,
        VALID_DSN,
        build_environment="develop",
        package_format="apk",
        posthog_project_api_key="",
        posthog_host="",
    )
    assert validate_public_config_bytes(both_empty.read_bytes()) == PublicDiagnosticsConfig(
        dsn=VALID_DSN,
        build_environment="develop",
        package_format="apk",
        posthog_project_api_key=None,
        posthog_host=None,
    )
    loaded_empty = load_public_config(both_empty, development=False, environ={})
    assert loaded_empty.dsn == VALID_DSN
    assert loaded_empty.posthog_project_api_key is None
    assert loaded_empty.posthog_host is None
    assert loaded_empty.posthog_configured is False

    # Asymmetric Schema v3 configs (key without host or host without key) fail closed in generator & verifier
    for idx, (asym_key, asym_host) in enumerate(
        (
            (VALID_POSTHOG_KEY, ""),
            ("", VALID_POSTHOG_HOST),
        )
    ):
        with pytest.raises(PublicConfigError):
            generate_public_config(
                tmp_path / f"asym-{idx}.json",
                VALID_DSN,
                build_environment="develop",
                package_format="apk",
                posthog_project_api_key=asym_key,
                posthog_host=asym_host,
            )

        raw_asym = json.dumps(
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "apk",
                "posthog_project_api_key": asym_key,
                "posthog_host": asym_host,
            }
        ).encode("utf-8")
        with pytest.raises(PublicConfigError):
            validate_public_config_bytes(raw_asym)

        # Runtime loader degrades tampered asymmetric PostHog fields to None while preserving valid Sentry DSN
        disk_asym = tmp_path / f"disk-asym-{idx}.json"
        disk_asym.write_bytes(raw_asym)
        loaded_asym = load_public_config(disk_asym, development=False, environ={})
        assert loaded_asym.dsn == VALID_DSN
        assert loaded_asym.posthog_project_api_key is None
        assert loaded_asym.posthog_host is None
        assert loaded_asym.posthog_configured is False

    with pytest.raises(SystemExit) as exc_info:
        generate_config_main(
            [
                "--sentry-dsn",
                VALID_DSN,
                "--posthog-key",
                VALID_POSTHOG_KEY,
                "--posthog-host",
                "",
                "--build-environment",
                "release",
                "--package-format",
                "apk",
                "--output",
                str(tmp_path / "cli-asym.json"),
            ]
        )
    assert exc_info.value.code == 2
    assert "posthog_project_api_key and posthog_host" in capsys.readouterr().err

    with pytest.raises(PublicConfigError):
        generate_public_config(
            tmp_path / "require-both.json",
            VALID_DSN,
            build_environment="release",
            package_format="apk",
            posthog_project_api_key="",
            posthog_host="",
            require_posthog_configured=True,
        )


def test_tampered_schema_v3_posthog_fields_fail_closed_in_loader_and_verifier(tmp_path):
    for bad_key, bad_host in (
        ("phx_" + "a" * 28, VALID_POSTHOG_HOST),
        ("phs_" + "a" * 28, VALID_POSTHOG_HOST),
        (VALID_POSTHOG_KEY, "https://app.posthog.com"),
        (VALID_POSTHOG_KEY, "https://127.0.0.1:8443"),
        (VALID_POSTHOG_KEY, "http://us.i.posthog.com"),
    ):
        raw = json.dumps(
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "apk",
                "posthog_project_api_key": bad_key,
                "posthog_host": bad_host,
            }
        ).encode("utf-8")
        with pytest.raises(PublicConfigError):
            validate_public_config_bytes(raw)

        f = tmp_path / "tampered.json"
        f.write_bytes(raw)
        cfg = load_public_config(f, development=False, environ={})
        assert cfg.posthog_project_api_key is None
        assert cfg.posthog_host is None
        assert cfg.posthog_configured is False


def test_dev_overrides_resolve_sentry_and_posthog_independently(tmp_path):
    missing = tmp_path / "missing.json"

    # 1. UTH_POSTHOG_KEY alone in dev defaults host to https://us.i.posthog.com
    dev_key_only = load_public_config(
        missing,
        development=True,
        environ={"UTH_POSTHOG_KEY": VALID_POSTHOG_KEY},
    )
    assert dev_key_only.dsn is None
    assert dev_key_only.posthog_project_api_key == VALID_POSTHOG_KEY
    assert dev_key_only.posthog_host == DEFAULT_DEV_POSTHOG_HOST
    assert dev_key_only.posthog_configured is True
    assert dev_key_only.build_environment == "development"
    assert dev_key_only.package_format == "source"

    # 2. Invalid UTH_POSTHOG_KEY does not break valid UTH_SENTRY_DSN
    bad_ph = load_public_config(
        missing,
        development=True,
        environ={
            "UTH_SENTRY_DSN": VALID_DSN,
            "UTH_POSTHOG_KEY": "phx_" + "a" * 28,
        },
    )
    assert bad_ph.dsn == VALID_DSN
    assert bad_ph.posthog_project_api_key is None
    assert bad_ph.posthog_host is None
    assert bad_ph.posthog_configured is False

    # 3. Invalid UTH_SENTRY_DSN does not break valid UTH_POSTHOG_KEY + private dev host
    bad_sentry = load_public_config(
        missing,
        development=True,
        environ={
            "UTH_SENTRY_DSN": "http://insecure@example.invalid/1",
            "UTH_POSTHOG_KEY": VALID_POSTHOG_KEY,
            "UTH_POSTHOG_HOST": "https://127.0.0.1:8443/ingest/",
        },
    )
    assert bad_sentry.dsn is None
    assert bad_sentry.posthog_project_api_key == VALID_POSTHOG_KEY
    assert bad_sentry.posthog_host == "https://127.0.0.1:8443/ingest"
    assert bad_sentry.posthog_configured is True

    # 4. Packaged mode (development=False) ignores all env overrides completely
    packaged = load_public_config(
        missing,
        development=False,
        environ={
            "UTH_SENTRY_DSN": VALID_DSN,
            "UTH_POSTHOG_KEY": VALID_POSTHOG_KEY,
            "UTH_POSTHOG_HOST": VALID_POSTHOG_HOST,
        },
    )
    assert packaged.dsn is None
    assert packaged.posthog_configured is False


def test_cli_supports_posthog_flags_empty_powershell_args_and_require_gate(
    tmp_path,
    capsys,
):
    out_empty = tmp_path / "empty-v3.json"
    assert (
        generate_config_main(
            [
                "--sentry-dsn",
                "--posthog-key",
                "--posthog-host",
                "--build-environment",
                "develop",
                "--package-format",
                "apk",
                "--output",
                str(out_empty),
            ]
        )
        == 0
    )
    assert json.loads(out_empty.read_text(encoding="utf-8")) == {
        "build_environment": "develop",
        "package_format": "apk",
        "posthog_host": "",
        "posthog_project_api_key": "",
        "schema_version": 3,
        "sentry_dsn": "",
    }

    out_configured = tmp_path / "configured-v3.json"
    assert (
        generate_config_main(
            [
                "--require-configured",
                "--require-posthog-configured",
                "--sentry-dsn",
                VALID_DSN,
                "--posthog-key",
                VALID_POSTHOG_KEY,
                "--posthog-host",
                "https://us.i.posthog.com/",
                "--build-environment",
                "release",
                "--package-format",
                "aab",
                "--output",
                str(out_configured),
            ]
        )
        == 0
    )
    loaded = load_public_config(out_configured, development=False, environ={})
    assert loaded.dsn == VALID_DSN
    assert loaded.posthog_project_api_key == VALID_POSTHOG_KEY
    assert loaded.posthog_host == "https://us.i.posthog.com"
    assert loaded.posthog_configured is True

    out_missing = tmp_path / "missing-ph.json"
    with pytest.raises(SystemExit) as exc_info:
        generate_config_main(
            [
                "--require-posthog-configured",
                "--posthog-key",
                VALID_POSTHOG_KEY,
                "--posthog-host",
                "--build-environment",
                "release",
                "--package-format",
                "aab",
                "--output",
                str(out_missing),
            ]
        )
    assert exc_info.value.code == 2
    assert "PostHog" in capsys.readouterr().err
    assert not out_missing.exists()


def test_posthog_generation_without_build_env_or_package_format_defaults_cleanly(
    tmp_path,
):
    direct_out = tmp_path / "direct-default-v3.json"
    generate_public_config(
        direct_out,
        VALID_DSN,
        posthog_project_api_key=VALID_POSTHOG_KEY,
        posthog_host="https://us.i.posthog.com/",
    )
    assert json.loads(direct_out.read_text(encoding="utf-8")) == {
        "build_environment": "release",
        "package_format": "source",
        "posthog_host": "https://us.i.posthog.com",
        "posthog_project_api_key": VALID_POSTHOG_KEY,
        "schema_version": 3,
        "sentry_dsn": VALID_DSN,
    }
    direct_loaded = load_public_config(direct_out, development=False, environ={})
    assert direct_loaded == PublicDiagnosticsConfig(
        dsn=VALID_DSN,
        build_environment="release",
        package_format="source",
        posthog_project_api_key=VALID_POSTHOG_KEY,
        posthog_host="https://us.i.posthog.com",
    )
    assert direct_loaded.posthog_configured is True

    # Partial (only one of build_environment / package_format) still fails closed
    for partial_kwargs in (
        {"build_environment": "release", "package_format": None},
        {"build_environment": None, "package_format": "apk"},
    ):
        with pytest.raises(PublicConfigError):
            generate_public_config(
                tmp_path / "partial.json",
                VALID_DSN,
                posthog_project_api_key=VALID_POSTHOG_KEY,
                posthog_host=VALID_POSTHOG_HOST,
                **partial_kwargs,
            )

    cli_out = tmp_path / "cli-default-v3.json"
    assert (
        generate_config_main(
            [
                "--sentry-dsn",
                VALID_DSN,
                "--posthog-key",
                VALID_POSTHOG_KEY,
                "--posthog-host",
                "https://eu.i.posthog.com/",
                "--output",
                str(cli_out),
            ]
        )
        == 0
    )
    assert json.loads(cli_out.read_text(encoding="utf-8")) == {
        "build_environment": "release",
        "package_format": "source",
        "posthog_host": "https://eu.i.posthog.com",
        "posthog_project_api_key": VALID_POSTHOG_KEY,
        "schema_version": 3,
        "sentry_dsn": VALID_DSN,
    }


def test_main_appdata_dir_falls_back_from_flet_storage_to_flet_app_data_then_home(
    tmp_path,
):
    import src.main as application

    home = tmp_path / "home"
    win_appdata = tmp_path / "Roaming"
    storage_dir = tmp_path / "flet_storage"
    legacy_app_data = tmp_path / "flet_app_data"

    assert (
        application._resolve_appdata_dir(
            platform_name="win32",
            environ={"APPDATA": str(win_appdata)},
            home_dir=home,
        )
        == win_appdata / "UTHelper"
    )
    assert (
        application._resolve_appdata_dir(
            platform_name="linux",
            environ={
                "FLET_APP_STORAGE_DATA": str(storage_dir),
                "FLET_APP_DATA": str(legacy_app_data),
            },
            home_dir=home,
        )
        == storage_dir
    )
    assert (
        application._resolve_appdata_dir(
            platform_name="linux",
            environ={
                "FLET_APP_STORAGE_DATA": "   ",
                "FLET_APP_DATA": str(legacy_app_data),
            },
            home_dir=home,
        )
        == legacy_app_data
    )
    assert (
        application._resolve_appdata_dir(
            platform_name="linux",
            environ={},
            home_dir=home,
        )
        == home / ".uthelper"
    )

