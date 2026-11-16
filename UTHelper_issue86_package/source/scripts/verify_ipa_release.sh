#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 9 ]]; then
  echo "usage: verify_ipa_release.sh IPA VERSION BUILD_NUMBER BUNDLE_ID TEAM_ID CERT_SHA256 COMMIT_SHA WORKFLOW_RUN_ID EVIDENCE_JSON" >&2
  exit 2
fi

IPA=$1
VERSION=$2
BUILD_NUMBER=$3
BUNDLE_ID=$4
TEAM_ID=$5
EXPECTED_CERTIFICATE=$(printf '%s' "$6" | tr -cd '[:xdigit:]' | tr '[:lower:]' '[:upper:]')
COMMIT_SHA=$7
WORKFLOW_RUN_ID=$8
EVIDENCE_JSON=$9

[[ -f "$IPA" && "$(basename "$IPA")" == "UTHelper-$VERSION.ipa" ]]
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
[[ "$BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]]
[[ "$BUNDLE_ID" == "com.uthelper.UTHelper" ]]
[[ "$TEAM_ID" =~ ^[A-Z0-9]{10}$ ]]
[[ "$EXPECTED_CERTIFICATE" =~ ^[A-F0-9]{64}$ ]]
[[ "$COMMIT_SHA" =~ ^[A-Fa-f0-9]{40}$ ]]
[[ -n "$WORKFLOW_RUN_ID" ]]
[[ "$(od -An -tx1 -N4 "$IPA" | tr -d ' \n' | tr '[:upper:]' '[:lower:]')" == "504b0304" ]]

TEMP_BASE=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
VERIFY_ROOT=$(mktemp -d "$TEMP_BASE/ipa-verify-${WORKFLOW_RUN_ID}.XXXXXX")
cleanup() {
  case "$VERIFY_ROOT" in
    "$TEMP_BASE"/ipa-verify-*) rm -rf -- "$VERIFY_ROOT" ;;
    *) echo "Refusing unsafe IPA verification cleanup" >&2; return 1 ;;
  esac
}
trap cleanup EXIT

unzip -q "$IPA" -d "$VERIFY_ROOT"
shopt -s nullglob
APPS=("$VERIFY_ROOT"/Payload/*.app)
shopt -u nullglob
test "${#APPS[@]}" -eq 1
APP=${APPS[0]}
test -f "$APP/Info.plist"
test -f "$APP/embedded.mobileprovision"

codesign --verify --deep --strict --verbose=4 "$APP"
codesign -d --entitlements :- "$APP" > "$VERIFY_ROOT/entitlements.plist"
security cms -D -i "$APP/embedded.mobileprovision" > "$VERIFY_ROOT/profile.plist"
codesign -d --extract-certificates "$VERIFY_ROOT/leaf" "$APP"
test -f "$VERIFY_ROOT/leaf0"
ACTUAL_CERTIFICATE=$(openssl x509 -inform DER -in "$VERIFY_ROOT/leaf0" -noout -fingerprint -sha256 | cut -d= -f2 | tr -cd '[:xdigit:]' | tr '[:lower:]' '[:upper:]')
test "$ACTUAL_CERTIFICATE" = "$EXPECTED_CERTIFICATE"

EXECUTABLE=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Info.plist")
test -n "$EXECUTABLE"
ARCHITECTURES=$(lipo -archs "$APP/$EXECUTABLE")
[[ " $ARCHITECTURES " == *" arm64 "* ]]
[[ " $ARCHITECTURES " != *" x86_64 "* ]]

mkdir -p "$(dirname "$EVIDENCE_JSON")"
python3 - "$IPA" "$APP/Info.plist" "$VERIFY_ROOT/profile.plist" \
  "$VERIFY_ROOT/entitlements.plist" "$VERSION" "$BUILD_NUMBER" "$BUNDLE_ID" \
  "$TEAM_ID" "$EXPECTED_CERTIFICATE" "$COMMIT_SHA" "$WORKFLOW_RUN_ID" \
  "$EVIDENCE_JSON" <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sys

(
    ipa_arg,
    info_arg,
    profile_arg,
    entitlements_arg,
    version,
    build_number,
    bundle_id,
    team_id,
    certificate_fingerprint,
    commit_sha,
    workflow_run_id,
    evidence_arg,
) = sys.argv[1:]

def load_plist(path: str):
    with Path(path).open("rb") as stream:
        return plistlib.load(stream)

info = load_plist(info_arg)
profile = load_plist(profile_arg)
entitlements = load_plist(entitlements_arg)
expected_application_id = f"{team_id}.{bundle_id}"
major, minor, patch = (int(component) for component in version.split("."))
if any(component > 999 for component in (major, minor, patch)):
    raise SystemExit("IPA version components exceed canonical build-number bounds")
if int(build_number) != major * 1_000_000 + minor * 1_000 + patch:
    raise SystemExit("IPA build number is not canonical for version")

if info.get("CFBundleIdentifier") != bundle_id:
    raise SystemExit("IPA bundle identifier mismatch")
if info.get("CFBundleShortVersionString") != version:
    raise SystemExit("IPA short version mismatch")
if str(info.get("CFBundleVersion")) != build_number:
    raise SystemExit("IPA build number mismatch")
if profile.get("TeamIdentifier") != [team_id]:
    raise SystemExit("Distribution profile TeamIdentifier mismatch")
if profile.get("Entitlements", {}).get("application-identifier") != expected_application_id:
    raise SystemExit("Distribution profile application identifier mismatch")
if entitlements.get("application-identifier") != expected_application_id:
    raise SystemExit("Signed entitlements application identifier mismatch")
if profile.get("Entitlements", {}).get("com.apple.developer.team-identifier") != team_id:
    raise SystemExit("Distribution profile team entitlement mismatch")
if entitlements.get("com.apple.developer.team-identifier") != team_id:
    raise SystemExit("Signed team entitlement mismatch")
if entitlements.get("get-task-allow") is not False:
    raise SystemExit("get-task-allow must be false for distribution")
if "ProvisionedDevices" in profile or profile.get("ProvisionsAllDevices"):
    raise SystemExit("ProvisionedDevices is forbidden for the public App Store channel")
if not isinstance(profile.get("UUID"), str) or not profile["UUID"].strip():
    raise SystemExit("Distribution profile UUID is missing")
expiration = profile.get("ExpirationDate")
if not isinstance(expiration, datetime):
    raise SystemExit("Distribution profile expiration is missing")
if expiration.tzinfo is None:
    expiration = expiration.replace(tzinfo=timezone.utc)
if expiration <= datetime.now(timezone.utc):
    raise SystemExit("Distribution profile is expired")
profile_certificates = {
    hashlib.sha256(bytes(certificate)).hexdigest().upper()
    for certificate in profile.get("DeveloperCertificates", [])
}
if certificate_fingerprint not in profile_certificates:
    raise SystemExit("Leaf signing certificate is not authorized by the profile DeveloperCertificates")

ipa = Path(ipa_arg).resolve()
with ipa.open("rb") as stream:
    ipa_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
evidence = {
    "schema_version": 1,
    "platform": "ios",
    "asset_name": ipa.name,
    "sha256": ipa_sha256,
    "version": version,
    "product_id": bundle_id,
    "architecture": "arm64",
    "signer_identity": team_id,
    "certificate_fingerprint": certificate_fingerprint,
    "signature_valid": True,
    "timestamp_valid": None,
    "checks": sorted(
        (
            "build_number",
            "bundle_id",
            "certificate_fingerprint",
            "codesign",
            "distribution_profile",
            "entitlements",
            "ipa_container",
            "sha256",
            "version",
        )
    ),
    "commit_sha": commit_sha.lower(),
    "workflow_run_id": workflow_run_id,
}
output = Path(evidence_arg).resolve()
temporary = output.with_name(f".{output.name}.tmp")
try:
    temporary.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
finally:
    temporary.unlink(missing_ok=True)
PY
