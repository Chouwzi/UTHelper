"""Unit tests for the 12 allow-listed PostHog event schemas, validators, and buckets."""

from __future__ import annotations

import json
from uuid import uuid4

from pydantic import ValidationError
import pytest

from analytics.models import (
    AnalyticsConfig,
    AnalyticsConfigError,
    AnalyticsEvent,
    CourseActivityOpenedProperties,
    DeviceClass,
    DistributionChannelName,
    DocumentFormat,
    DocumentOpenedProperties,
    DurationBucket,
    EVENT_PROPERTIES_MODELS,
    EventName,
    FilterAppliedProperties,
    GradesInteractionProperties,
    NotificationSummaryProperties,
    OsFamily,
    PackageFormatName,
    QuizActionProperties,
    ReleaseChannel,
    ScreenViewedProperties,
    SessionEndedProperties,
    SessionHeartbeatProperties,
    SessionStartedProperties,
    SubmissionActionProperties,
    SyncCompletedProperties,
    channels_bitmask,
    classify_document_format,
    delivered_count_bucket,
    duration_bucket,
    duration_ms_bucket,
    file_count_bucket,
    heartbeat_elapsed_bucket,
    item_count_bucket,
    scheduled_count_bucket,
    screens_visited_bucket,
    session_duration_bucket,
    validate_posthog_api_key,
    validate_posthog_host,
)
from analytics.uuid7 import uuid7_from_ms

VALID_KEY = "phc_0123456789abcdefghijKLMNOPQRST"
VALID_HOST = "https://us.i.posthog.com"
SESSION_ID = uuid7_from_ms(1_764_318_000_000)
DISTINCT_ID = str(uuid4())
TIMESTAMP = "2026-11-28T08:15:23.412Z"


def _sample_config() -> AnalyticsConfig:
    return AnalyticsConfig(
        api_key=VALID_KEY,
        host=VALID_HOST,
        app_version="2.7.0",
        release_channel=ReleaseChannel.RELEASE,
        distribution_channel=DistributionChannelName.CHPLAY,
        package_format=PackageFormatName.AAB,
        os_family=OsFamily.ANDROID,
        device_class=DeviceClass.MOBILE,
    )


@pytest.mark.parametrize(
    "event_name,props",
    [
        (
            EventName.SESSION_STARTED,
            SessionStartedProperties(
                launch_trigger="cold_foreground",
                initial_screen="home",
            ),
        ),
        (
            EventName.SESSION_HEARTBEAT,
            SessionHeartbeatProperties(
                elapsed_bucket="5m_15m",
                active_screen="courses",
            ),
        ),
        (
            EventName.SESSION_ENDED,
            SessionEndedProperties(
                end_reason="background_timeout",
                duration_bucket="5m_15m",
                screens_visited_bucket="2_3",
            ),
        ),
        (
            EventName.SCREEN_VIEWED,
            ScreenViewedProperties(
                screen="grades",
                nav_source="tab_bar",
            ),
        ),
        (
            EventName.FILTER_APPLIED,
            FilterAppliedProperties(
                surface="home",
                filter_kind="urgency",
            ),
        ),
        (
            EventName.SYNC_COMPLETED,
            SyncCompletedProperties(
                provider="activities",
                trigger="manual",
                outcome="succeeded",
                duration_bucket="250ms_1s",
                item_count_bucket="11_50",
            ),
        ),
        (
            EventName.DOCUMENT_OPENED,
            DocumentOpenedProperties(
                doc_format="pdf",
                viewer_kind="pdfjs_webview",
                source_site="courses",
                cache_hit=True,
                load_duration_bucket="lt_250ms",
            ),
        ),
        (
            EventName.COURSE_ACTIVITY_OPENED,
            CourseActivityOpenedProperties(
                activity_kind="assignment",
                source_site="thnn",
            ),
        ),
        (
            EventName.QUIZ_ACTION,
            QuizActionProperties(
                action="player_closed",
                source_site="courses",
                duration_bucket="1m_5m",
            ),
        ),
        (
            EventName.SUBMISSION_ACTION,
            SubmissionActionProperties(
                action="finalize",
                outcome="succeeded",
                file_count_bucket="2_3",
            ),
        ),
        (
            EventName.GRADES_INTERACTION,
            GradesInteractionProperties(
                action="radar_metric_opened",
            ),
        ),
        (
            EventName.NOTIFICATION_SUMMARY,
            NotificationSummaryProperties(
                kind="activity_deadline",
                delivered_bucket="1_3",
                scheduled_bucket="1_5",
                had_channel_failure=False,
                channels_mask=5,
            ),
        ),
    ],
)
def test_all_12_allowlisted_events_serialize_with_mandatory_privacy_flags(
    event_name: EventName,
    props,
):
    assert len(EVENT_PROPERTIES_MODELS) == 12
    cfg = _sample_config()
    built = AnalyticsEvent.build(
        event=event_name,
        event_properties=props,
        distinct_id=DISTINCT_ID,
        session_id=SESSION_ID,
        timestamp=TIMESTAMP,
        config=cfg,
    )
    wire = built.to_wire_dict()
    encoded = json.dumps(wire)
    reparsed = json.loads(encoded)

    assert reparsed["event"] == event_name.value
    assert reparsed["distinct_id"] == DISTINCT_ID
    assert reparsed["timestamp"] == TIMESTAMP
    p = reparsed["properties"]
    assert p["$session_id"] == SESSION_ID
    assert p["$process_person_profile"] is False
    assert p["$geoip_disable"] is True
    assert "$ip" in p and p["$ip"] is None
    assert p["$lib"] == "uthelper-python"
    assert p["$lib_version"] == "2.7.0"
    assert p["schema_version"] == 1
    assert p["app_version"] == "2.7.0"
    assert p["release_channel"] == "release"
    assert p["distribution_channel"] == "chplay"
    assert p["package_format"] == "aab"
    assert p["os_family"] == "Android"
    assert p["device_class"] == "mobile"


@pytest.mark.parametrize(
    "forbidden_field,forbidden_val",
    [
        ("username", "2251120001"),
        ("course_id", 9912),
        ("cmid", 4421),
        ("$current_url", "https://courses.ut.edu.vn/mod/assign/view.php?id=1"),
        ("$set", {"email": "student@ut.edu.vn"}),
        ("cpu_description", "Intel Core i7"),
        ("gpu", "NVIDIA RTX"),
        ("screen_width_pixels", 1920),
        ("search_query", "lap trinh mang"),
        ("filename", "baitap_nhom_1.docx"),
    ],
)
def test_extra_forbid_blocks_pii_urls_paths_and_hardware_fingerprints(
    forbidden_field: str,
    forbidden_val,
):
    cfg = _sample_config()
    base = AnalyticsEvent.build(
        event=EventName.SCREEN_VIEWED,
        event_properties={"screen": "home", "nav_source": "tab_bar"},
        distinct_id=DISTINCT_ID,
        session_id=SESSION_ID,
        timestamp=TIMESTAMP,
        config=cfg,
    ).model_dump()

    base["properties"][forbidden_field] = forbidden_val
    with pytest.raises(ValidationError):
        AnalyticsEvent.model_validate(base)


def test_event_rejects_person_profile_true_geoip_enabled_ip_string_and_non_uuid_ids():
    cfg = _sample_config()
    valid = AnalyticsEvent.build(
        event=EventName.GRADES_INTERACTION,
        event_properties={"action": "gpa_planner_opened"},
        distinct_id=DISTINCT_ID,
        session_id=SESSION_ID,
        timestamp=TIMESTAMP,
        config=cfg,
    ).model_dump()

    bad_person = {**valid, "properties": {**valid["properties"], "$process_person_profile": True}}
    with pytest.raises(ValidationError):
        AnalyticsEvent.model_validate(bad_person)

    bad_geoip = {**valid, "properties": {**valid["properties"], "$geoip_disable": False}}
    with pytest.raises(ValidationError):
        AnalyticsEvent.model_validate(bad_geoip)

    bad_ip = {**valid, "properties": {**valid["properties"], "$ip": "203.0.113.5"}}
    with pytest.raises(ValidationError):
        AnalyticsEvent.model_validate(bad_ip)

    missing_ip = {**valid, "properties": {k: v for k, v in valid["properties"].items() if k != "$ip"}}
    with pytest.raises(ValidationError):
        AnalyticsEvent.model_validate(missing_ip)

    # MSSV / username as distinct_id is strictly forbidden (must be UUIDv4)
    with pytest.raises(ValidationError):
        AnalyticsEvent.model_validate({**valid, "distinct_id": "2251120001"})

    # UUIDv4 as $session_id is strictly forbidden (must be RFC 9562 UUIDv7)
    bad_session = {**valid, "properties": {**valid["properties"], "$session_id": str(uuid4())}}
    with pytest.raises(ValidationError):
        AnalyticsEvent.model_validate(bad_session)


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "phc_short",
        " phc_0123456789abcdefghijKLMNOPQRST ",
        "phx_0123456789abcdefghijKLMNOPQRST",
        "phs_0123456789abcdefghijKLMNOPQRST",
        "sntrys_0123456789abcdefghijKLMNOPQRST",
        "phc_0123456789_secret_token_abcd",
        "phc_0123456789_auth_token_abcdef",
        "phc_0123456789_personal_key_abcd",
        "phc_0123456789abcdefghij\n",
    ],
)
def test_validate_posthog_api_key_rejects_secrets_and_malformed_keys(bad_key: str):
    assert validate_posthog_api_key(VALID_KEY) == VALID_KEY
    with pytest.raises(AnalyticsConfigError):
        validate_posthog_api_key(bad_key)


@pytest.mark.parametrize(
    "good_host,expected",
    [
        ("https://us.i.posthog.com", "https://us.i.posthog.com"),
        ("https://eu.i.posthog.com/", "https://eu.i.posthog.com"),
        ("https://ingest.example.edu:8443/ph/", "https://ingest.example.edu:8443/ph"),
    ],
)
def test_validate_posthog_host_normalizes_clean_https_origins(
    good_host: str,
    expected: str,
):
    assert validate_posthog_host(good_host) == expected


@pytest.mark.parametrize(
    "bad_host",
    [
        "",
        "http://us.i.posthog.com",
        "https://app.posthog.com",
        "https://us.posthog.com",
        "https://eu.posthog.com",
        "https://US.I.POSTHOG.COM",
        "https://user:pass@us.i.posthog.com",
        "https://us.i.posthog.com?api_key=phc_123",
        "https://us.i.posthog.com#frag",
        "https://us.i.posthog.com/../admin",
        "https://us.i.posthog.com/%2e%2e/admin",
        "https://us.i.posthog.com//double",
        "https://localhost:8000",
        "https://posthog.local",
        "https://sub.posthog.localhost",
        "https://127.0.0.1:8000",
        "https://[::ffff:127.0.0.1]:8000",
        "https://10.0.0.5",
        "https://192.168.1.1",
        "https://169.254.169.254",
        "https://0.0.0.0:8000",
        "https://[ff02::1]:8000",
        "https://ingest.example.edu/~tilde",
    ],
)
def test_validate_posthog_host_rejects_insecure_ui_and_private_hosts(bad_host: str):
    with pytest.raises(AnalyticsConfigError):
        validate_posthog_host(bad_host, allow_private_dev=False)


def test_validate_posthog_host_allows_private_dev_only_when_explicit():
    assert (
        validate_posthog_host("https://localhost:8443/ingest/", allow_private_dev=True)
        == "https://localhost:8443/ingest"
    )
    assert (
        validate_posthog_host("https://posthog.local:8443/ingest", allow_private_dev=True)
        == "https://posthog.local:8443/ingest"
    )
    assert (
        validate_posthog_host("https://127.0.0.1:8443", allow_private_dev=True)
        == "https://127.0.0.1:8443"
    )
    # Unspecified and multicast IPs remain rejected even when allow_private_dev=True!
    for always_bad in (
        "https://0.0.0.0:8443",
        "https://[::]:8443",
        "https://[::ffff:0.0.0.0]:8443",
        "https://224.0.0.1:8443",
        "https://[ff02::1]:8443",
    ):
        with pytest.raises(AnalyticsConfigError):
            validate_posthog_host(always_bad, allow_private_dev=True)


def test_bucket_helpers_and_document_format_classifier():
    assert duration_bucket(0.05) is DurationBucket.LT_250MS
    assert duration_bucket(0.5) is DurationBucket.MS_250_1S
    assert duration_bucket(2.5) is DurationBucket.S_1_5
    assert duration_bucket(9.0) is DurationBucket.GTE_5S
    assert duration_ms_bucket(120) is DurationBucket.LT_250MS
    assert duration_ms_bucket(1500) is DurationBucket.S_1_5

    assert session_duration_bucket(3).value == "lt_10s"
    assert session_duration_bucket(45).value == "10s_1m"
    assert session_duration_bucket(180).value == "1m_5m"
    assert session_duration_bucket(600).value == "5m_15m"
    assert session_duration_bucket(1200).value == "15m_30m"
    assert session_duration_bucket(2400).value == "gte_30m"

    assert heartbeat_elapsed_bucket(300).value == "5m_15m"
    assert heartbeat_elapsed_bucket(1000).value == "15m_30m"
    assert heartbeat_elapsed_bucket(2000).value == "30m_60m"
    assert heartbeat_elapsed_bucket(4000).value == "gte_60m"

    assert screens_visited_bucket(1).value == "1"
    assert screens_visited_bucket(3).value == "2_3"
    assert screens_visited_bucket(5).value == "4_5"
    assert screens_visited_bucket(8).value == "gte_6"

    assert item_count_bucket(0).value == "0"
    assert item_count_bucket(5).value == "1_10"
    assert item_count_bucket(25).value == "11_50"
    assert item_count_bucket(51).value == "gt_50"

    assert file_count_bucket(1).value == "1"
    assert file_count_bucket(2).value == "2_3"
    assert file_count_bucket(4).value == "gte_4"

    assert delivered_count_bucket(0).value == "0"
    assert delivered_count_bucket(2).value == "1_3"
    assert delivered_count_bucket(4).value == "gte_4"

    assert scheduled_count_bucket(0).value == "0"
    assert scheduled_count_bucket(5).value == "1_5"
    assert scheduled_count_bucket(6).value == "gte_6"

    assert channels_bitmask(os_toast=True, telegram=True) == 0b0101
    assert classify_document_format("C:/secret/student_225112/lecture_01.PDF") is DocumentFormat.PDF
    assert classify_document_format("report.docx") is DocumentFormat.DOCX
    assert classify_document_format("slides.pptx") is DocumentFormat.PPTX
    assert classify_document_format("bundle.zip") is DocumentFormat.ARCHIVE
    assert classify_document_format("unknown.xyz") is DocumentFormat.OTHER
