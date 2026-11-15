"""Tests for CourseCatalogService merge, delta sync, THNN lock, and error guards."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.courses.cache_store import CourseCacheStore
from core.courses.catalog_service import CourseCatalogService, CourseSyncResult
from core.courses.models import (
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSummary,
    RecentMaterialEntry,
)
from core.courses.preview_cache import PreviewFileCache
from core.courses.providers import CourseProviderError, ThnnSessionError


class _FakeCoursesProvider:
    source = "courses"

    def __init__(self) -> None:
        self.delta_updates: set[int] = set()
        self.sections_calls = 0
        self.grades_calls = 0
        self.download_calls = 0
        self.courses_error: Exception | None = None
        self.sections_error: Exception | None = None
        self.updates_error: Exception | None = None
        self.grades_error: Exception | None = None
        self.download_result: bytes | None = b"pdf-bytes"
        self.completion_result = True
        self.courses = [
            CourseSummary(
                id=10,
                source="courses",
                fullname="CS 101",
                shortname="CS101",
                display_name="CS 101",
                semester_label="HKI 2026-2027",
                semester_sort_key=(2026, 3),
            )
        ]
        self.sections = [
            CourseSection(
                id=1,
                course_id=10,
                source="courses",
                section_num=1,
                name="Buổi 1",
                modules=(
                    CourseModuleItem(
                        cmid=100,
                        instance_id=1,
                        modname="resource",
                        name="Intro",
                        section_id=1,
                        completion_mode=1,
                        completion_state=0,
                    ),
                ),
            )
        ]
        self.grades: list[CourseGradeItem] = []

    def fetch_courses(self) -> list[CourseSummary]:
        if self.courses_error is not None:
            raise self.courses_error
        return list(self.courses)

    def fetch_course_sections(self, course_id: int) -> list[CourseSection]:
        self.sections_calls += 1
        if self.sections_error is not None:
            raise self.sections_error
        return list(self.sections)

    def fetch_updates_since(self, course_id: int, since: int) -> set[int]:
        if self.updates_error is not None:
            raise self.updates_error
        return set(self.delta_updates)

    def fetch_course_grades(self, course_id: int) -> list[CourseGradeItem]:
        self.grades_calls += 1
        if self.grades_error is not None:
            raise self.grades_error
        return list(self.grades)

    def set_manual_completion(self, cmid: int, completed: bool) -> bool:
        return self.completion_result

    def download_file(self, url: str) -> bytes | None:
        self.download_calls += 1
        return self.download_result


class _FakeThnnProvider:
    source = "thnn"

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.sections_calls = 0
        self.grades_calls = 0
        self.download_calls = 0
        self.resolve_calls = 0
        self.courses_error: Exception | None = None
        self.sections_error: Exception | None = None
        self.grades_error: Exception | None = None
        self.download_error: Exception | None = None
        self.resolve_error: Exception | None = None
        self.resolved_url = "https://meet.google.com/thnn-resolved"
        self.download_result: bytes | None = b"thnn-bytes"
        self.courses = [
            CourseSummary(
                id=4458,
                source="thnn",
                fullname="Anh văn 4",
                shortname="AV4",
                display_name="Anh văn 4",
                semester_label="HKII 2025-2026",
                semester_sort_key=(2025, 2),
            )
        ]
        self.sections: list[CourseSection] = []
        self.grades: list[CourseGradeItem] = []

        self._probe_lock = threading.Lock()
        self.active_calls = 0
        self.max_concurrent_calls = 0

    def _track_enter(self) -> None:
        with self._probe_lock:
            self.active_calls += 1
            if self.active_calls > self.max_concurrent_calls:
                self.max_concurrent_calls = self.active_calls
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

    def _track_exit(self) -> None:
        with self._probe_lock:
            self.active_calls -= 1

    def fetch_courses(self) -> list[CourseSummary]:
        self._track_enter()
        try:
            if self.courses_error is not None:
                raise self.courses_error
            return list(self.courses)
        finally:
            self._track_exit()

    def fetch_course_sections(self, course_id: int) -> list[CourseSection]:
        self._track_enter()
        try:
            self.sections_calls += 1
            if self.sections_error is not None:
                raise self.sections_error
            return list(self.sections)
        finally:
            self._track_exit()

    def fetch_course_grades(self, course_id: int) -> list[CourseGradeItem]:
        self._track_enter()
        try:
            self.grades_calls += 1
            if self.grades_error is not None:
                raise self.grades_error
            return list(self.grades)
        finally:
            self._track_exit()

    def download_file(self, url: str) -> bytes | None:
        self._track_enter()
        try:
            self.download_calls += 1
            if self.download_error is not None:
                raise self.download_error
            return self.download_result
        finally:
            self._track_exit()

    def resolve_module_external_url(self, module: CourseModuleItem) -> str:
        self._track_enter()
        try:
            self.resolve_calls += 1
            if self.resolve_error is not None:
                raise self.resolve_error
            return self.resolved_url
        finally:
            self._track_exit()


def test_catalog_service_merges_sorts_and_detects_in_class_delta(tmp_path):
    now = [1000]
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="test")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    t_prov = _FakeThnnProvider()

    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        thnn_provider=t_prov,
        time_fn=lambda: now[0],
    )

    catalog = svc.refresh_catalog(include_thnn=True)
    assert [c.course_key for c in catalog] == ["courses:10", "thnn:4458"]

    # First open fetches sections
    res1 = svc.sync_course_delta("courses", 10, force=False)
    assert c_prov.sections_calls == 1
    assert len(res1.sections) == 1

    # Within debounce (<15s), skips network
    now[0] += 5
    res2 = svc.sync_course_delta("courses", 10, force=False)
    assert c_prov.sections_calls == 1
    assert res2.changed is False

    # After debounce (>15s), delta detects new attendance module!
    now[0] += 30
    c_prov.delta_updates = {101}
    c_prov.sections = [
        CourseSection(
            id=1,
            course_id=10,
            source="courses",
            section_num=1,
            name="Buổi 1",
            modules=(
                *c_prov.sections[0].modules,
                CourseModuleItem(
                    cmid=101,
                    instance_id=2,
                    modname="attendance",
                    name="Điểm danh 16/11",
                    section_id=1,
                    open_strategy="webview_activity",
                ),
            ),
        )
    ]
    res3 = svc.sync_course_delta("courses", 10, force=False)
    assert c_prov.sections_calls == 2
    assert res3.changed is True
    assert res3.sections[0].modules[-1].is_new is True


def test_pinning_moves_course_first_and_completion_updates_cache(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="test2")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    t_prov = _FakeThnnProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        thnn_provider=t_prov,
        time_fn=lambda: 2000,
    )
    svc.refresh_catalog(include_thnn=True)
    updated = svc.toggle_pin("thnn:4458")
    assert updated[0].course_key == "thnn:4458"
    assert updated[0].is_pinned is True

    svc.sync_course_delta("courses", 10, force=True)
    ok = svc.toggle_completion("courses", 10, cmid=100, completed=True)
    assert ok is True
    detail = svc.open_course_cached("courses", 10)
    assert detail.sections[0].modules[0].completion_state == 1


def test_refresh_catalog_preserves_thnn_cache_on_session_and_provider_errors(tmp_path, caplog):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="thnn-err")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    t_prov = _FakeThnnProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        thnn_provider=t_prov,
        time_fn=lambda: 5000,
    )

    initial = svc.refresh_catalog(include_thnn=True)
    assert [c.course_key for c in initial] == ["courses:10", "thnn:4458"]
    assert svc.last_thnn_error == ""

    with caplog.at_level(logging.WARNING):
        t_prov.courses_error = ThnnSessionError("session expired")
        after_session_err = svc.refresh_catalog(include_thnn=True)
    assert [c.course_key for c in after_session_err] == ["courses:10", "thnn:4458"]
    assert "session expired" in svc.last_thnn_error
    assert any("THNN" in rec.message for rec in caplog.records)

    t_prov.courses_error = CourseProviderError("timeout")
    after_provider_err = svc.refresh_catalog(include_thnn=True)
    assert [c.course_key for c in after_provider_err] == ["courses:10", "thnn:4458"]
    assert "timeout" in svc.last_thnn_error

    # Recovers cleanly on next successful fetch
    t_prov.courses_error = None
    recovered = svc.refresh_catalog(include_thnn=True)
    assert [c.course_key for c in recovered] == ["courses:10", "thnn:4458"]
    assert svc.last_thnn_error == ""


def test_refresh_catalog_preserves_courses_cache_when_empty_or_raising(tmp_path, caplog):
    now = [1000]
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="courses-err")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    t_prov = _FakeThnnProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        thnn_provider=t_prov,
        time_fn=lambda: now[0],
    )

    svc.refresh_catalog(include_thnn=True)

    # Empty response preserves cached courses
    now[0] = 2000
    c_prov.courses = []
    with caplog.at_level(logging.WARNING):
        after_empty = svc.refresh_catalog(include_thnn=False)
    assert [c.course_key for c in after_empty] == ["courses:10", "thnn:4458"]

    # Exception preserves cached courses without wiping catalog
    now[0] = 3000
    c_prov.courses_error = CourseProviderError("WS down")
    after_err = svc.refresh_catalog(include_thnn=False)
    assert [c.course_key for c in after_err] == ["courses:10", "thnn:4458"]
    assert "WS down" in svc.last_courses_error
    _, _, synced_at = svc.load_cached_catalog()
    assert synced_at == 1000


def test_sync_course_delta_thnn_ttl_and_error_recovery(tmp_path):
    now = [1000]
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="thnn-ttl")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    t_prov = _FakeThnnProvider()
    t_prov.sections = [
        CourseSection(
            id=11,
            course_id=4458,
            source="thnn",
            section_num=1,
            name="Unit 1",
            modules=(
                CourseModuleItem(
                    cmid=501,
                    instance_id=0,
                    modname="resource",
                    name="Lesson 1",
                    section_id=11,
                ),
            ),
        )
    ]
    t_prov.grades = [CourseGradeItem(id=1, item_name="Midterm", grade_formatted="9.0")]

    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        thnn_provider=t_prov,
        time_fn=lambda: now[0],
    )
    res1 = svc.sync_course_delta("thnn", 4458, force=False)
    assert isinstance(res1, CourseSyncResult)
    assert res1.changed is True
    assert res1.error == ""
    assert t_prov.sections_calls == 1

    # Seed grades in cache to verify error recovery preserves both sections and grades
    grades = svc.load_course_grades("thnn", 4458, force=True)
    assert len(grades) == 1

    # Past 15s debounce, still within 300s TTL -> skips network
    now[0] += 120
    res2 = svc.sync_course_delta("thnn", 4458, force=False)
    assert t_prov.sections_calls == 1
    assert res2.changed is False
    assert len(res2.sections) == 1
    assert len(res2.grades) == 1

    # Past 300s TTL -> calls network; on ThnnSessionError returns cached data + error
    now[0] += 250
    t_prov.sections_error = ThnnSessionError("THNN cookie expired")
    res3 = svc.sync_course_delta("thnn", 4458, force=False)
    assert t_prov.sections_calls == 2
    assert res3.changed is False
    assert "THNN cookie expired" in res3.error
    assert len(res3.sections) == 1
    assert len(res3.grades) == 1
    assert "THNN cookie expired" in svc.last_thnn_error

    # On CourseProviderError with force=True, still preserves cache
    t_prov.sections_error = CourseProviderError("THNN 502")
    res4 = svc.sync_course_delta("thnn", 4458, force=True)
    assert res4.changed is False
    assert "THNN 502" in res4.error
    cached = svc.open_course_cached("thnn", 4458)
    assert len(cached.sections) == 1
    assert len(cached.grades) == 1


def test_sync_course_delta_empty_course_then_new_module_and_courses_error(tmp_path):
    now = [1000]
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="empty-then-new")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    c_prov.sections = []

    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: now[0],
    )
    svc.refresh_catalog(include_thnn=False)

    # Initial sync of empty course records last_synced_at > 0 and known_cmids == []
    res1 = svc.sync_course_delta("courses", 10, force=False)
    assert res1.changed is True
    assert res1.new_cmids == set()
    assert res1.last_synced_at == 1000

    # Later, module 202 is added; delta detects it as new even though known_cmids was empty!
    now[0] += 30
    c_prov.delta_updates = {202}
    c_prov.sections = [
        CourseSection(
            id=1,
            course_id=10,
            source="courses",
            section_num=1,
            name="Week 1",
            modules=(
                CourseModuleItem(
                    cmid=202,
                    instance_id=5,
                    modname="assign",
                    name="Lab 1",
                    section_id=1,
                ),
            ),
        )
    ]
    res2 = svc.sync_course_delta("courses", 10, force=False)
    assert res2.changed is True
    assert res2.new_cmids == {202}
    assert res2.sections[0].modules[0].is_new is True

    # Provider failure on delta check preserves existing cache cleanly
    now[0] += 30
    c_prov.updates_error = CourseProviderError("delta failed")
    res3 = svc.sync_course_delta("courses", 10, force=False)
    assert res3.changed is False
    assert "delta failed" in res3.error
    assert res3.sections[0].modules[0].cmid == 202


def test_thnn_lock_serializes_all_concurrent_thnn_operations(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="thnn-lock")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    t_prov = _FakeThnnProvider(delay_seconds=0.02)
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        thnn_provider=t_prov,
        time_fn=lambda: 1000,
    )
    mod = CourseModuleItem(
        cmid=77,
        instance_id=0,
        modname="url",
        name="Meet",
        section_id=1,
        url="https://thnn.ut.edu.vn/mod/url/view.php?id=77",
        web_url="https://thnn.ut.edu.vn/mod/url/view.php?id=77",
    )

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(svc.refresh_catalog, True),
            pool.submit(svc.sync_course_delta, "thnn", 4458, True),
            pool.submit(svc.load_course_grades, "thnn", 4458, True),
            pool.submit(
                svc.fetch_or_cache_preview_file,
                "thnn",
                "https://thnn.ut.edu.vn/pluginfile.php/1/a.pdf",
                100,
                "a.pdf",
            ),
            pool.submit(svc.resolve_module_url, "thnn", mod),
        ]
        for fut in futures:
            fut.result()

    assert t_prov.max_concurrent_calls == 1


def test_fetch_or_cache_preview_file_hits_cache_and_handles_missing(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="preview")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview_files")
    c_prov = _FakeCoursesProvider()
    t_prov = _FakeThnnProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        thnn_provider=t_prov,
    )

    p1 = svc.fetch_or_cache_preview_file(
        "courses", "https://courses.ut.edu.vn/f.pdf", 123, "slides.pdf"
    )
    assert p1 is not None and p1.read_bytes() == b"pdf-bytes"
    assert c_prov.download_calls == 1

    # Hit returns cached path without invoking download_file again
    p2 = svc.fetch_or_cache_preview_file(
        "courses", "https://courses.ut.edu.vn/f.pdf", 123, "slides.pdf"
    )
    assert p2 == p1
    assert c_prov.download_calls == 1

    # Empty / None bytes return None
    c_prov.download_result = b""
    assert (
        svc.fetch_or_cache_preview_file(
            "courses", "https://courses.ut.edu.vn/empty.pdf", 1, "e.pdf"
        )
        is None
    )
    c_prov.download_result = None
    assert (
        svc.fetch_or_cache_preview_file(
            "courses", "https://courses.ut.edu.vn/none.pdf", 1, "n.pdf"
        )
        is None
    )

    # Provider error returns None without crashing
    t_prov.download_error = CourseProviderError("forbidden")
    assert (
        svc.fetch_or_cache_preview_file(
            "thnn", "https://thnn.ut.edu.vn/err.pdf", 1, "err.pdf"
        )
        is None
    )

    # Missing provider returns None
    svc_no_prov = CourseCatalogService(cache_store=cache, preview_cache=preview)
    assert (
        svc_no_prov.fetch_or_cache_preview_file(
            "courses", "https://courses.ut.edu.vn/x.pdf", 1, "x.pdf"
        )
        is None
    )


def test_resolve_module_url_uses_thnn_resolver_and_falls_back(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="resolve")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    t_prov = _FakeThnnProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        thnn_provider=t_prov,
    )

    thnn_mod = CourseModuleItem(
        cmid=1,
        instance_id=0,
        modname="url",
        name="Meet",
        section_id=1,
        url="",
        web_url="https://thnn.ut.edu.vn/mod/url/view.php?id=1",
    )
    assert svc.resolve_module_url("thnn", thnn_mod) == "https://meet.google.com/thnn-resolved"
    assert t_prov.resolve_calls == 1

    t_prov.resolve_error = ThnnSessionError("expired")
    assert svc.resolve_module_url("thnn", thnn_mod) == "https://thnn.ut.edu.vn/mod/url/view.php?id=1"

    courses_mod = CourseModuleItem(
        cmid=2,
        instance_id=0,
        modname="url",
        name="Doc",
        section_id=1,
        url="https://drive.google.com/file/d/123/view",
        web_url="https://courses.ut.edu.vn/mod/url/view.php?id=2",
    )
    assert svc.resolve_module_url("courses", courses_mod) == "https://drive.google.com/file/d/123/view"


def test_enrich_contact_escapes_html_computes_progress_and_preserves_pins(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="enrich")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    c_prov.sections = [
        CourseSection(
            id=1,
            course_id=10,
            source="courses",
            section_num=0,
            name="Thông tin chung",
            modules=(
                CourseModuleItem(
                    cmid=100,
                    instance_id=1,
                    modname="label",
                    name='Giảng viên: Cô Lan <A&B> - Email: lan@uth.edu.vn',
                    section_id=1,
                    completion_mode=1,
                    completion_state=1,
                ),
                CourseModuleItem(
                    cmid=101,
                    instance_id=2,
                    modname="url",
                    name="Phòng học online",
                    section_id=1,
                    url="https://meet.google.com/abc-defg-hij?authuser=0&pli=1",
                    completion_mode=2,
                    completion_state=0,
                ),
            ),
        )
    ]

    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: 3000,
    )
    svc.refresh_catalog(include_thnn=False)
    cache.set_unseen("courses:10", True)
    svc.toggle_pin("courses:10")

    svc.sync_course_delta("courses", 10, force=True)
    courses, _, _ = svc.load_cached_catalog()
    course = courses[0]
    assert course.is_pinned is True
    assert course.has_unseen_updates is True
    assert course.teacher_name == "Cô Lan <A&B>"
    assert course.teacher_email == "lan@uth.edu.vn"
    assert course.meet_url == "https://meet.google.com/abc-defg-hij?authuser=0&pli=1"
    assert course.completed_count == 1
    assert course.total_trackable == 2
    assert course.progress == pytest.approx(0.5)


def test_sync_course_delta_does_not_clobber_concurrent_pin_or_seen(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="concurrent-pin")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    entered_fetch = threading.Event()
    allow_finish = threading.Event()

    class _SlowCoursesProvider(_FakeCoursesProvider):
        def fetch_course_sections(self, course_id: int) -> list[CourseSection]:
            entered_fetch.set()
            assert allow_finish.wait(timeout=2.0)
            return super().fetch_course_sections(course_id)

    c_prov = _SlowCoursesProvider()
    c_prov.sections = [
        CourseSection(
            id=1,
            course_id=10,
            source="courses",
            section_num=0,
            name="Intro",
            modules=(
                CourseModuleItem(
                    cmid=100,
                    instance_id=1,
                    modname="label",
                    name="Giảng viên: Thầy Minh - Email: minh@uth.edu.vn",
                    section_id=1,
                    completion_mode=1,
                    completion_state=1,
                ),
            ),
        )
    ]
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: 4000,
    )
    svc.refresh_catalog(include_thnn=False)
    cache.set_unseen("courses:10", True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        sync_fut = pool.submit(svc.sync_course_delta, "courses", 10, True)
        assert entered_fetch.wait(timeout=2.0)
        svc.toggle_pin("courses:10")
        svc.mark_course_seen("courses:10")
        allow_finish.set()
        res = sync_fut.result()

    assert res.changed is True
    courses, _, _ = svc.load_cached_catalog()
    assert courses[0].is_pinned is True
    assert courses[0].has_unseen_updates is False
    assert courses[0].teacher_name == "Thầy Minh"
    assert courses[0].completed_count == 1
    assert courses[0].progress == pytest.approx(1.0)


def test_fetch_attachment_bytes_success_and_error(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="att-bytes")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
    )
    assert svc.fetch_attachment_bytes("courses", "https://courses.ut.edu.vn/a.pdf") == b"pdf-bytes"
    c_prov.download_result = None
    with pytest.raises(CourseProviderError):
        svc.fetch_attachment_bytes("courses", "https://courses.ut.edu.vn/a.pdf")


def test_staleness_mark_seen_grades_recents_and_validation_guards(tmp_path):
    now = [1000]
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="misc")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    c_prov.grades = [CourseGradeItem(id=1, item_name="Quiz 1", grade_formatted="8.0")]
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: now[0],
    )

    assert svc.is_catalog_stale(6) is True
    svc.refresh_catalog(include_thnn=False)
    assert svc.is_catalog_stale(6) is False

    now[0] += 6 * 3600
    assert svc.is_catalog_stale(6) is True

    # Clock skew backward & non-finite clock values do not crash
    now[0] = 500
    assert svc.is_catalog_stale(6) is True
    svc_nan = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: float("nan"),  # type: ignore[arg-type, return-value]
    )
    assert isinstance(svc_nan.is_catalog_stale(float("nan")), bool)  # type: ignore[arg-type]

    # Mark course seen clears unseen flag
    cache.set_unseen("courses:10", True)
    courses_unseen, _, _ = svc.load_cached_catalog()
    assert courses_unseen[0].has_unseen_updates is True
    seen_list = svc.mark_course_seen("courses:10")
    assert seen_list[0].has_unseen_updates is False

    # Grades: fetch once (preserving last_synced_at=0 before section sync), then hit cache, then force refresh
    grades1 = svc.load_course_grades("courses", 10, force=False)
    assert len(grades1) == 1
    assert c_prov.grades_calls == 1
    assert svc.open_course_cached("courses", 10).last_synced_at == 0

    c_prov.grades = [CourseGradeItem(id=1, item_name="Quiz 1", grade_formatted="9.5")]
    grades2 = svc.load_course_grades("courses", 10, force=False)
    assert grades2[0].grade_formatted == "8.0"
    assert c_prov.grades_calls == 1

    grades3 = svc.load_course_grades("courses", 10, force=True)
    assert grades3[0].grade_formatted == "9.5"
    assert c_prov.grades_calls == 2

    # Recent materials roundtrip
    recent = RecentMaterialEntry(
        course_key="courses:10",
        course_name="CS 101",
        cmid=100,
        title="Intro",
        attachment_url="https://courses.ut.edu.vn/intro.pdf",
        file_name="intro.pdf",
        opened_at=1000,
    )
    svc.record_recent(recent)
    _, recents, _ = svc.load_cached_catalog()
    assert len(recents) == 1
    assert recents[0].file_name == "intro.pdf"

    # Negative course_id and invalid source guards
    with pytest.raises(ValueError):
        svc.open_course_cached("courses", -1)
    with pytest.raises(ValueError):
        svc.sync_course_delta("courses", -5)
    with pytest.raises(ValueError):
        svc.load_course_grades("courses", -2)
    with pytest.raises(ValueError):
        svc.toggle_completion("courses", -3, cmid=100, completed=True)
    with pytest.raises(ValueError):
        svc.fetch_or_cache_preview_file("invalid", "https://x/a.pdf", 1, "a.pdf")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        svc.resolve_module_url("invalid", CourseModuleItem(1, 1, "url", "x", 1))  # type: ignore[arg-type]
