"""Tests for the generated, public diagnostics runtime configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagnostics.release_config import (
    PublicConfigError,
    generate_public_config,
    load_public_dsn,
    load_runtime_public_dsn,
)
from scripts.generate_public_runtime_config import main as generate_config_main

VALID_DSN = "https://0123456789abcdef@o123.ingest.sentry.io/456"


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
