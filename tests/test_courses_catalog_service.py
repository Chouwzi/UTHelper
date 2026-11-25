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
        self.delta_updates: set[int] | None = set()
        self.sections_calls = 0
        self.grades_calls = 0
        self.download_calls = 0
        self.completion_calls = 0
        self.courses_error: Exception | None = None
        self.sections_error: Exception | None = None
        self.updates_error: Exception | None = None
        self.grades_error: Exception | None = None
        self.completion_error: Exception | None = None
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

    def fetch_updates_since(self, course_id: int, since: int) -> set[int] | None:
        if self.updates_error is not None:
            raise self.updates_error
        if self.delta_updates is None:
            return None
        return set(self.delta_updates)

    def fetch_course_grades(self, course_id: int) -> list[CourseGradeItem]:
        self.grades_calls += 1
        if self.grades_error is not None:
            raise self.grades_error
        return list(self.grades)

    def set_manual_completion(self, cmid: int, completed: bool) -> bool:
        self.completion_calls += 1
        if self.completion_error is not None:
            raise self.completion_error
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
        self.completion_calls = 0
        self.courses_error: Exception | None = None
        self.sections_error: Exception | None = None
        self.grades_error: Exception | None = None
        self.download_error: Exception | None = None
        self.resolve_error: Exception | None = None
        self.completion_error: Exception | None = None
        self.completion_result = True
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

    def set_manual_completion(self, cmid: int, completed: bool) -> bool:
        self._track_enter()
        try:
            self.completion_calls += 1
            if self.completion_error is not None:
                raise self.completion_error
            return self.completion_result
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

    with ThreadPoolExecutor(max_workers=7) as pool:
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
            pool.submit(svc.toggle_completion, "thnn", 4458, 77, True),
            pool.submit(
                svc.fetch_attachment_bytes,
                "thnn",
                "https://thnn.ut.edu.vn/pluginfile.php/1/b.pdf",
            ),
        ]
        for fut in futures:
            fut.result()

    assert t_prov.max_concurrent_calls == 1
    assert t_prov.completion_calls == 1
    assert t_prov.download_calls == 2


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

    # Provider error returns None without crashing and records error
    t_prov.download_error = CourseProviderError("forbidden")
    assert (
        svc.fetch_or_cache_preview_file(
            "thnn", "https://thnn.ut.edu.vn/err.pdf", 1, "err.pdf"
        )
        is None
    )
    assert "forbidden" in svc.last_thnn_error

    # Subsequent successful preview download clears last_thnn_error
    t_prov.download_error = None
    ok_thnn = svc.fetch_or_cache_preview_file(
        "thnn", "https://thnn.ut.edu.vn/ok.pdf", 1, "ok.pdf"
    )
    assert ok_thnn is not None and ok_thnn.read_bytes() == b"thnn-bytes"
    assert svc.last_thnn_error == ""

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
    assert "expired" in svc.last_thnn_error

    t_prov.resolve_error = None
    assert svc.resolve_module_url("thnn", thnn_mod) == "https://meet.google.com/thnn-resolved"
    assert svc.last_thnn_error == ""

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
    assert "Failed to download attachment" in svc.last_courses_error
    c_prov.download_result = b"pdf-bytes"
    assert svc.fetch_attachment_bytes("courses", "https://courses.ut.edu.vn/a.pdf") == b"pdf-bytes"
    assert svc.last_courses_error == ""


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


def test_sync_course_delta_handles_empty_set_vs_none_updates_and_clears_error(tmp_path):
    now = [1000]
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="delta-none")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: now[0],
    )
    svc.refresh_catalog(include_thnn=False)
    svc.sync_course_delta("courses", 10, force=False)
    assert c_prov.sections_calls == 1

    # Seed a prior courses error to verify set() delta check clears it.
    now[0] += 30
    c_prov.updates_error = CourseProviderError("transient delta error")
    err_res = svc.sync_course_delta("courses", 10, force=False)
    assert err_res.changed is False
    assert "transient delta error" in svc.last_courses_error

    # Returning set() skips fetch_course_sections and clears last_courses_error.
    c_prov.updates_error = None
    c_prov.delta_updates = set()
    no_change = svc.sync_course_delta("courses", 10, force=False)
    assert no_change.changed is False
    assert no_change.error == ""
    assert c_prov.sections_calls == 1
    assert svc.last_courses_error == ""

    # Immediate re-open within 15s is debounced because set() recorded check time.
    now[0] += 5
    debounced = svc.sync_course_delta("courses", 10, force=False)
    assert debounced.changed is False
    assert c_prov.sections_calls == 1

    # Returning None (unsupported/soft fail) falls back to fetch_course_sections.
    now[0] += 30
    c_prov.delta_updates = None
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
                    cmid=102,
                    instance_id=3,
                    modname="resource",
                    name="Slide 2",
                    section_id=1,
                ),
            ),
        )
    ]
    fallback_res = svc.sync_course_delta("courses", 10, force=False)
    assert c_prov.sections_calls == 2
    assert fallback_res.changed is True
    assert fallback_res.new_cmids == {102}


def test_sync_course_delta_preserves_and_clears_is_new_across_sync_cycles(tmp_path):
    now = [1000]
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="is-new-cycle")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: now[0],
    )
    svc.refresh_catalog(include_thnn=False)

    res1 = svc.sync_course_delta("courses", 10, force=False)
    assert res1.sections[0].modules[0].is_new is False

    # Add module 101 -> marked is_new=True
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
                    modname="resource",
                    name="New Slide",
                    section_id=1,
                ),
            ),
        )
    ]
    res2 = svc.sync_course_delta("courses", 10, force=False)
    assert res2.new_cmids == {101}
    assert res2.sections[0].modules[0].is_new is False
    assert res2.sections[0].modules[1].is_new is True

    # Debounced check preserves is_new=True
    now[0] += 5
    res3 = svc.sync_course_delta("courses", 10, force=False)
    assert res3.changed is False
    assert res3.sections[0].modules[1].is_new is True

    # No-change delta check (set()) preserves is_new=True without calling sections
    now[0] += 30
    c_prov.delta_updates = set()
    calls_before = c_prov.sections_calls
    res4 = svc.sync_course_delta("courses", 10, force=False)
    assert c_prov.sections_calls == calls_before
    assert res4.changed is False
    assert res4.sections[0].modules[1].is_new is True

    # Subsequent full section fetch with same cmids clears is_new=False
    now[0] += 30
    c_prov.delta_updates = {101}
    res5 = svc.sync_course_delta("courses", 10, force=False)
    assert c_prov.sections_calls == calls_before + 1
    assert res5.changed is True
    assert res5.new_cmids == set()
    assert all(not mod.is_new for mod in res5.sections[0].modules)
    persisted = svc.open_course_cached("courses", 10)
    assert all(not mod.is_new for mod in persisted.sections[0].modules)


def test_sync_course_delta_and_load_grades_preserve_nonempty_cache_on_empty_response(
    tmp_path, caplog
):
    now = [1000]
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="empty-preserve")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    c_prov.grades = [CourseGradeItem(id=1, item_name="Midterm", grade_formatted="8.5")]
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: now[0],
    )
    svc.refresh_catalog(include_thnn=False)
    svc.sync_course_delta("courses", 10, force=True)
    assert len(svc.load_course_grades("courses", 10, force=True)) == 1

    # Provider returns [] on force sync when cache already has sections/grades
    now[0] = 2000
    c_prov.sections = []
    c_prov.grades = []
    with caplog.at_level(logging.WARNING):
        res = svc.sync_course_delta("courses", 10, force=True)
        grades = svc.load_course_grades("courses", 10, force=True)

    assert res.changed is False
    assert res.last_synced_at == 1000
    assert len(res.sections) == 1
    assert res.sections[0].modules[0].cmid == 100
    assert len(res.grades) == 1
    assert len(grades) == 1
    assert grades[0].grade_formatted == "8.5"
    cached = svc.open_course_cached("courses", 10)
    assert cached.last_synced_at == 1000
    assert cached.known_cmids == [100]
    assert len(cached.sections) == 1
    assert len(cached.grades) == 1
    assert any("preserving cached sections" in rec.message for rec in caplog.records)
    assert any("preserving cached grades" in rec.message for rec in caplog.records)


def test_toggle_completion_uncheck_invalid_cmid_errors_and_empty_cache_guard(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="toggle-guard")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    c_prov.courses.append(
        CourseSummary(
            id=20,
            source="courses",
            fullname="Empty Detail Course",
            shortname="EDC",
            display_name="Empty Detail Course",
            completed_count=2,
            total_trackable=4,
            progress=0.5,
        )
    )
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: 2000,
    )
    svc.refresh_catalog(include_thnn=False)
    svc.sync_course_delta("courses", 10, force=True)

    # Check -> catalog completed_count=1, progress=1.0
    assert svc.toggle_completion("courses", 10, cmid=100, completed=True) is True
    courses, _, _ = svc.load_cached_catalog()
    c10 = next(c for c in courses if c.id == 10)
    assert c10.completed_count == 1
    assert c10.total_trackable == 1
    assert c10.progress == pytest.approx(1.0)

    # Uncheck -> detail state=0, catalog completed_count=0, progress=0.0
    assert svc.toggle_completion("courses", 10, cmid=100, completed=False) is True
    detail = svc.open_course_cached("courses", 10)
    assert detail.sections[0].modules[0].completion_state == 0
    courses, _, _ = svc.load_cached_catalog()
    c10 = next(c for c in courses if c.id == 10)
    assert c10.completed_count == 0
    assert c10.total_trackable == 1
    assert c10.progress == pytest.approx(0.0)

    # Invalid cmid values do not contact provider
    calls_before = c_prov.completion_calls
    for bad_cmid in (0, -1, True, False):
        assert svc.toggle_completion("courses", 10, cmid=bad_cmid, completed=True) is False
    assert c_prov.completion_calls == calls_before

    # Provider CourseProviderError records error and leaves cache untouched
    c_prov.completion_error = CourseProviderError("completion WS failed")
    assert svc.toggle_completion("courses", 10, cmid=100, completed=True) is False
    assert "completion WS failed" in svc.last_courses_error
    assert svc.open_course_cached("courses", 10).sections[0].modules[0].completion_state == 0

    # Provider returning False leaves cache untouched
    c_prov.completion_error = None
    c_prov.completion_result = False
    assert svc.toggle_completion("courses", 10, cmid=100, completed=True) is False
    assert svc.open_course_cached("courses", 10).sections[0].modules[0].completion_state == 0

    # Empty course detail or missing cmid does not zero out catalog progress/counts
    c_prov.completion_result = True
    assert svc.toggle_completion("courses", 20, cmid=999, completed=True) is True
    assert svc.open_course_cached("courses", 20).sections == []
    courses, _, _ = svc.load_cached_catalog()
    c20 = next(c for c in courses if c.id == 20)
    assert c20.completed_count == 2
    assert c20.total_trackable == 4
    assert c20.progress == pytest.approx(0.5)


def test_unsafe_urls_invalid_filenames_and_preview_put_bytes_oserror(
    tmp_path, monkeypatch, caplog
):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="url-safety")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    t_prov = _FakeThnnProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        thnn_provider=t_prov,
    )

    unsafe_urls = (
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://courses.ut.edu.vn/a\nb.pdf",
        "https://courses.ut.edu.vn/a b.pdf",
        "https://[bad",
        "",
        "   ",
        None,
    )
    for bad_url in unsafe_urls:
        assert (
            svc.fetch_or_cache_preview_file("courses", bad_url, 1, "doc.pdf")  # type: ignore[arg-type]
            is None
        )
        with pytest.raises(CourseProviderError):
            svc.fetch_attachment_bytes("courses", bad_url)  # type: ignore[arg-type]
        assert svc.last_courses_error != ""

    assert c_prov.download_calls == 0

    # Invalid file_name returns None without touching provider
    for bad_name in ("", "   ", None):
        assert (
            svc.fetch_or_cache_preview_file(
                "courses",
                "https://courses.ut.edu.vn/valid.pdf",
                1,
                bad_name,  # type: ignore[arg-type]
            )
            is None
        )
    assert c_prov.download_calls == 0

    # put_bytes OSError logs warning and returns None
    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(preview, "put_bytes", _boom)
    with caplog.at_level(logging.WARNING):
        res = svc.fetch_or_cache_preview_file(
            "courses", "https://courses.ut.edu.vn/valid.pdf", 1, "valid.pdf"
        )
    assert res is None
    assert any("disk full" in rec.message for rec in caplog.records)

    # resolve_module_url filters unsafe module URLs and unsafe THNN resolver results
    mod_unsafe_primary = CourseModuleItem(
        cmid=1,
        instance_id=0,
        modname="url",
        name="Link",
        section_id=1,
        url="javascript:alert(1)",
        web_url="https://courses.ut.edu.vn/mod/url/view.php?id=1",
    )
    assert (
        svc.resolve_module_url("courses", mod_unsafe_primary)
        == "https://courses.ut.edu.vn/mod/url/view.php?id=1"
    )

    mod_all_unsafe = CourseModuleItem(
        cmid=2,
        instance_id=0,
        modname="url",
        name="Bad",
        section_id=1,
        url="file:///etc/passwd",
        web_url="https://[bad",
    )
    assert svc.resolve_module_url("courses", mod_all_unsafe) == ""

    thnn_mod = CourseModuleItem(
        cmid=3,
        instance_id=0,
        modname="url",
        name="THNN Meet",
        section_id=1,
        url="",
        web_url="https://thnn.ut.edu.vn/mod/url/view.php?id=3",
    )
    for bad_resolved in ("javascript:evil()", "https://thnn.ut.edu.vn/a\nb", "https://[bad"):
        t_prov.resolved_url = bad_resolved
        assert (
            svc.resolve_module_url("thnn", thnn_mod)
            == "https://thnn.ut.edu.vn/mod/url/view.php?id=3"
        )


def test_refresh_catalog_preserves_metadata_unseen_flag_and_catalog_synced_at_on_courses_failure(
    tmp_path,
):
    now = [1000]
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="refresh-meta")
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

    seeded_course = CourseSummary(
        id=10,
        source="courses",
        fullname="CS 101",
        shortname="CS101",
        display_name="CS 101",
        semester_label="HKI 2026-2027",
        semester_sort_key=(2026, 3),
        teacher_name="Cô Lan",
        teacher_email="lan@uth.edu.vn",
        teacher_phone="0901234567",
        meet_url="https://meet.google.com/abc-defg-hij",
        progress=0.75,
        completed_count=3,
        total_trackable=4,
        last_synced_at=900,
        has_unseen_updates=True,
    )
    cache.save_catalog([seeded_course], synced_at=1000)

    # Fresh summary without cached metadata preserves teacher/meet/progress/counts/synced/unseen
    now[0] = 2000
    refreshed = svc.refresh_catalog(include_thnn=True)
    courses_item = next(c for c in refreshed if c.course_key == "courses:10")
    assert courses_item.teacher_name == "Cô Lan"
    assert courses_item.teacher_email == "lan@uth.edu.vn"
    assert courses_item.teacher_phone == "0901234567"
    assert courses_item.meet_url == "https://meet.google.com/abc-defg-hij"
    assert courses_item.progress == pytest.approx(0.75)
    assert courses_item.completed_count == 3
    assert courses_item.total_trackable == 4
    assert courses_item.last_synced_at == 900
    assert courses_item.has_unseen_updates is True
    assert svc.load_cached_catalog()[2] == 2000

    # Primary courses provider fails while THNN succeeds -> catalog_synced_at stays 2000!
    now[0] = 7000
    c_prov.courses_error = CourseProviderError("courses WS down")
    t_prov.courses = [
        CourseSummary(
            id=4459,
            source="thnn",
            fullname="Anh văn 5",
            shortname="AV5",
            display_name="Anh văn 5",
            semester_label="HKII 2025-2026",
            semester_sort_key=(2025, 2),
        )
    ]
    partial = svc.refresh_catalog(include_thnn=True)
    assert {c.course_key for c in partial} == {"courses:10", "thnn:4459"}
    _, _, synced_at = svc.load_cached_catalog()
    assert synced_at == 2000
    assert svc.is_catalog_stale(1) is True


def test_thnn_lock_prevents_older_error_from_overwriting_newer_success(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="thnn-error-order")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    first_entered = threading.Event()
    release_first = threading.Event()

    class _OrderedThnnProvider(_FakeThnnProvider):
        def fetch_courses(self) -> list[CourseSummary]:
            if not first_entered.is_set():
                first_entered.set()
                assert release_first.wait(timeout=2.0)
                raise ThnnSessionError("older failure")
            return list(self.courses)

    t_prov = _OrderedThnnProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        thnn_provider=t_prov,
        time_fn=lambda: 1000,
    )

    lock_states: list[tuple[str, str, bool]] = []
    orig_record = svc._record_provider_error
    orig_clear = svc._clear_provider_error

    def _wrapped_record(source, context, exc):
        locked = svc._thnn_lock.locked()
        lock_states.append(("record", source, locked))
        if source == "thnn" and not locked:
            # Expose the race window if error recording ever escapes _thnn_lock.
            time.sleep(0.05)
        return orig_record(source, context, exc)

    def _wrapped_clear(source):
        lock_states.append(("clear", source, svc._thnn_lock.locked()))
        return orig_clear(source)

    svc._record_provider_error = _wrapped_record  # type: ignore[method-assign]
    svc._clear_provider_error = _wrapped_clear  # type: ignore[method-assign]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_fut = pool.submit(svc.refresh_catalog, True)
        assert first_entered.wait(timeout=2.0)
        second_fut = pool.submit(svc.refresh_catalog, True)
        release_first.set()
        first_fut.result()
        second_fut.result()

    assert svc.last_thnn_error == ""
    thnn_states = [held for _, src, held in lock_states if src == "thnn"]
    assert thnn_states and all(thnn_states)


def test_sync_course_delta_computes_new_cmids_and_grades_against_latest_detail(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="latest-detail-race")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    entered_fetch = threading.Event()
    allow_finish = threading.Event()

    class _SlowSectionsProvider(_FakeCoursesProvider):
        def __init__(self) -> None:
            super().__init__()
            self.pause_next_fetch = False

        def fetch_course_sections(self, course_id: int) -> list[CourseSection]:
            if self.pause_next_fetch:
                self.pause_next_fetch = False
                entered_fetch.set()
                assert allow_finish.wait(timeout=2.0)
            return super().fetch_course_sections(course_id)

    c_prov = _SlowSectionsProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        time_fn=lambda: 1000,
    )
    svc.refresh_catalog(include_thnn=False)
    # Initial sync seeds known_cmids=[100]
    svc.sync_course_delta("courses", 10, force=True)

    # Next sync sees 101 in sections; pause mid-network while another call updates cache to know 101 + grade
    c_prov.pause_next_fetch = True
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
                    modname="resource",
                    name="Mid-flight Slide",
                    section_id=1,
                ),
            ),
        )
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut = pool.submit(svc.sync_course_delta, "courses", 10, True)
        assert entered_fetch.wait(timeout=2.0)
        cache.save_course_detail(
            "courses",
            10,
            c_prov.sections,
            [CourseGradeItem(id=1, item_name="Lab", grade_formatted="9.0")],
            synced_at=999,
            known_cmids=[100, 101],
        )
        allow_finish.set()
        res = fut.result()

    assert res.changed is True
    assert res.new_cmids == set()
    assert len(res.grades) == 1
    assert res.grades[0].grade_formatted == "9.0"
    assert all(not mod.is_new for mod in res.sections[0].modules)


def test_parallel_catalog_refresh_singleflight_download_ram_cache_and_idle_prefetch(tmp_path):
    """Catalog refreshes providers in parallel, deduplicates concurrent downloads, caches RAM state, and prefetches politely."""
    import asyncio
    from types import SimpleNamespace

    from core.courses.archive_service import ArchiveService
    from core.courses.local_preview_server import LocalPreviewServer
    from core.courses.models import CourseAttachment
    from gui.controllers.courses_coordinator import CoursesCoordinator

    cache = CourseCacheStore(cache_dir=tmp_path, namespace="perf-suite")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")

    both_entered = threading.Barrier(2, timeout=2.0)

    class _ParallelCoursesProvider(_FakeCoursesProvider):
        def fetch_courses(self) -> list[CourseSummary]:
            both_entered.wait()
            return super().fetch_courses()

        def download_file(self, url: str, *, progress_callback=None) -> bytes | None:
            self.download_calls += 1
            time.sleep(0.06)
            payload = b"streamed-lecture-bytes"
            if callable(progress_callback):
                progress_callback(len(payload) // 2, len(payload))
                progress_callback(len(payload), len(payload))
            return payload

    class _ParallelThnnProvider(_FakeThnnProvider):
        def fetch_courses(self) -> list[CourseSummary]:
            both_entered.wait()
            return super().fetch_courses()

    c_prov = _ParallelCoursesProvider()
    t_prov = _ParallelThnnProvider()
    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        thnn_provider=t_prov,
        time_fn=lambda: 5000,
    )

    # 1. Parallel refresh_catalog: Barrier(2) only succeeds if both providers run concurrently!
    merged = svc.refresh_catalog(include_thnn=True)
    assert {c.course_key for c in merged} == {"courses:10", "thnn:4458"}

    # 2. CourseCacheStore RAM cache hit returns without re-reading JSON, and invalidates on save
    cat_a = cache.load_catalog()
    cat_b = cache.load_catalog()
    assert cat_a is cat_b
    cache.save_course_detail("courses", 10, c_prov.sections, [], synced_at=5000)
    det_a = cache.load_course_detail("courses", 10)
    det_b = cache.load_course_detail("courses", 10)
    assert det_a is det_b

    # 3. SingleFlight concurrent download deduplication + progress forwarding
    progress_events: list[tuple[int, int]] = []
    start_gate = threading.Barrier(4, timeout=2.0)
    target_url = "https://courses.ut.edu.vn/pluginfile.php/10/mod_resource/slide.pdf"

    def _concurrent_download() -> bytes:
        start_gate.wait()
        return svc.fetch_attachment_bytes(
            "courses",
            target_url,
            progress_callback=lambda done, total: progress_events.append((done, total)),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_concurrent_download) for _ in range(4)]
        results = [f.result() for f in futs]

    assert c_prov.download_calls == 1
    assert results == [b"streamed-lecture-bytes"] * 4
    assert (22, 22) in progress_events

    # 4. Polite idle attachment prefetch warms small <=1.5MB courses files & skips large files
    small_att = CourseAttachment(
        file_name="small.docx",
        file_url="https://courses.ut.edu.vn/pluginfile.php/10/small.docx",
        preview_kind="docx",
        file_size=120_000,
        time_modified=100,
    )
    huge_att = CourseAttachment(
        file_name="huge_video.mp4",
        file_url="https://courses.ut.edu.vn/pluginfile.php/10/huge.mp4",
        preview_kind="unknown",
        file_size=25_000_000,
        time_modified=100,
    )
    sections_with_atts = [
        CourseSection(
            id=1,
            course_id=10,
            source="courses",
            section_num=1,
            name="Tuần 1",
            modules=(
                CourseModuleItem(
                    cmid=200,
                    instance_id=1,
                    modname="resource",
                    name="Tài liệu nhỏ",
                    section_id=1,
                    attachments=(small_att, huge_att),
                ),
            ),
        )
    ]
    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
        run_task=lambda *_a: None,
    )
    server = LocalPreviewServer()
    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=svc,
        preview_cache=preview,
        archive_service=ArchiveService(),
        preview_server=server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        coord.courses_view._state.selected_course_key = c_prov.courses[0].course_key
        c_prov.download_calls = 0
        asyncio.run(coord._prefetch_course_attachments_async(c_prov.courses[0], sections_with_atts))
        assert c_prov.download_calls == 1
        assert preview.get_cached_path(small_att.file_url, 100, small_att.file_name) is not None
        assert preview.get_cached_path(huge_att.file_url, 100, huge_att.file_name) is None
    finally:
        coord.close()


def test_portal_current_semester_enrichment_retake_guard_and_cache_persistence(tmp_path):
    from core.courses.shortname_parser import parse_portal_semester_label

    assert parse_portal_semester_label("Học kỳ 1 năm học 2026-2027") == (
        "HKI 2026-2027",
        (2026, 3),
    )
    assert parse_portal_semester_label("Học kỳ hè năm học 2025-2026") == (
        "HK Hè 2025-2026",
        (2025, 1),
    )

    cache = CourseCacheStore(cache_dir=tmp_path, namespace="portal-cur")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    c_prov = _FakeCoursesProvider()
    c_prov.courses = [
        CourseSummary(
            id=101,
            source="courses",
            fullname="[012000510704] - Chủ nghĩa xã hội khoa học",
            shortname="TT_HK1-2026-2027_012000510704",
            display_name="Chủ nghĩa xã hội khoa học",
            course_code="012000510704",
            semester_label="HKI 2026-2027",
            semester_sort_key=(2026, 3),
            program_tag="TT",
        ),
        # Older retake of the SAME 10-digit maHocPhan in HKII 2025-2026 -> must NOT be marked current!
        CourseSummary(
            id=102,
            source="courses",
            fullname="[012000510701] - Chủ nghĩa xã hội khoa học",
            shortname="TT_HK2-2025-2026_012000510701",
            display_name="Chủ nghĩa xã hội khoa học",
            course_code="012000510701",
            semester_label="HKII 2025-2026",
            semester_sort_key=(2025, 2),
            program_tag="TT",
        ),
        # Unregistered HKI 2026-2027 course -> must NOT be marked current when Portal specs exist!
        CourseSummary(
            id=103,
            source="courses",
            fullname="[012099999901] - Môn tự chọn cũ",
            shortname="TT_HK1-2026-2027_012099999901",
            display_name="Môn tự chọn cũ",
            course_code="012099999901",
            semester_label="HKI 2026-2027",
            semester_sort_key=(2026, 3),
            program_tag="TT",
        ),
    ]
    t_prov = _FakeThnnProvider()
    t_prov.courses = [
        CourseSummary(
            id=4458,
            source="thnn",
            fullname="[TT04] Anh văn chuyên ngành",
            shortname="TT04",
            display_name="Anh văn chuyên ngành",
            semester_label="Khác",
            semester_sort_key=(0, 0),
        )
    ]

    portal_specs = [
        {
            "maHocPhan": "0120005107",
            "tenMonHoc": "Chủ nghĩa xã hội khoa học",
            "soTinChi": 2,
        },
        {
            "maHocPhan": "TT04",
            "tenMonHoc": "Anh văn chuyên ngành",
            "soTinChi": 3,
        },
    ]

    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=c_prov,
        thnn_provider=t_prov,
        portal_courses_fetcher=lambda: ("Học kỳ 1 năm học 2026-2027", portal_specs),
        time_fn=lambda: 12345,
    )
    catalog = svc.refresh_catalog(include_thnn=True)
    by_key = {c.course_key: c for c in catalog}

    assert svc.has_portal_current_term is True
    assert by_key["courses:101"].is_current_term is True
    assert by_key["courses:101"].credits == 2
    assert by_key["courses:102"].is_current_term is False
    assert by_key["courses:103"].is_current_term is False
    assert by_key["thnn:4458"].is_current_term is True
    assert by_key["thnn:4458"].credits == 3

    # Fresh service instance loading from disk cache restores Portal current-term metadata
    svc_reloaded = CourseCatalogService(
        cache_store=CourseCacheStore(cache_dir=tmp_path, namespace="portal-cur"),
        preview_cache=preview,
    )
    cached_courses, _, _ = svc_reloaded.load_cached_catalog()
    reloaded_by_key = {c.course_key: c for c in cached_courses}
    assert svc_reloaded.has_portal_current_term is True
    assert reloaded_by_key["courses:101"].is_current_term is True
    assert reloaded_by_key["courses:101"].credits == 2
    assert reloaded_by_key["courses:102"].is_current_term is False


def test_refresh_catalog_discards_result_after_namespace_switch(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|alice")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    started = threading.Event()
    release = threading.Event()

    class _BlockingProvider(_FakeCoursesProvider):
        def fetch_courses(self):
            started.set()
            assert release.wait(2.0)
            return [
                CourseSummary(
                    id=901,
                    source="courses",
                    fullname="Alice Secret Course",
                    shortname="ALICE901",
                    display_name="Alice Secret Course",
                )
            ]

    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=_BlockingProvider(),
        time_fn=lambda: 5000,
    )

    out: list = []
    worker = threading.Thread(target=lambda: out.append(svc.refresh_catalog(include_thnn=False)))
    worker.start()
    assert started.wait(1.0)

    cache.set_namespace("https://courses.ut.edu.vn|bob")
    release.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    assert out == [[]]
    bob_snap = cache.load_catalog()
    assert bob_snap.courses == []


def test_sync_course_delta_and_grades_discard_stale_data_after_namespace_switch(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|alice")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    sec_started = threading.Event()
    sec_release = threading.Event()
    grade_started = threading.Event()
    grade_release = threading.Event()

    class _BlockingDetailProvider(_FakeCoursesProvider):
        def fetch_course_sections(self, course_id: int):
            sec_started.set()
            assert sec_release.wait(2.0)
            return [
                CourseSection(
                    id=1,
                    course_id=course_id,
                    source="courses",
                    section_num=1,
                    name="Alice Secret Section",
                    modules=(
                        CourseModuleItem(
                            cmid=777,
                            instance_id=1,
                            modname="resource",
                            name="Alice PDF",
                            section_id=1,
                        ),
                    ),
                )
            ]

        def fetch_course_grades(self, course_id: int):
            grade_started.set()
            assert grade_release.wait(2.0)
            return [CourseGradeItem(id=1, item_name="Alice Midterm", grade_formatted="10.0")]

    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=_BlockingDetailProvider(),
        time_fn=lambda: 6000,
    )

    sec_out: list = []
    t1 = threading.Thread(target=lambda: sec_out.append(svc.sync_course_delta("courses", 10, force=True)))
    t1.start()
    assert sec_started.wait(1.0)
    cache.set_namespace("https://courses.ut.edu.vn|bob")
    sec_release.set()
    t1.join(timeout=2.0)

    assert sec_out[0].changed is False
    assert sec_out[0].sections == []
    assert cache.load_course_detail("courses", 10).sections == []

    grade_out: list = []
    t2 = threading.Thread(target=lambda: grade_out.append(svc.load_course_grades("courses", 10, force=True)))
    t2.start()
    assert grade_started.wait(1.0)
    cache.set_namespace("https://courses.ut.edu.vn|charlie")
    grade_release.set()
    t2.join(timeout=2.0)

    assert grade_out == [[]]
    assert cache.load_course_detail("courses", 10).grades == []


def test_fetch_or_cache_preview_file_isolates_inflight_and_discards_commit_after_namespace_switch(tmp_path):
    cache = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|alice")
    preview = PreviewFileCache(cache_dir=tmp_path / "preview")
    alice_started = threading.Event()
    alice_release = threading.Event()
    call_counter = {"n": 0}

    class _StreamingProvider(_FakeCoursesProvider):
        def download_file_to_path(self, url: str, handle, progress_callback=None):
            call_counter["n"] += 1
            idx = call_counter["n"]
            if idx == 1:
                alice_started.set()
                assert alice_release.wait(2.0)
                handle.write(b"ALICE-SECRET-PDF")
                return True
            handle.write(b"BOB-FRESH-PDF")
            return True

    svc = CourseCatalogService(
        cache_store=cache,
        preview_cache=preview,
        courses_provider=_StreamingProvider(),
        time_fn=lambda: 7000,
    )
    url = "https://courses.ut.edu.vn/pluginfile.php/1/shared.pdf"

    alice_out: list = []
    t_alice = threading.Thread(
        target=lambda: alice_out.append(
            svc.fetch_or_cache_preview_file("courses", url, 100, "shared.pdf")
        )
    )
    t_alice.start()
    assert alice_started.wait(1.0)

    # Switch to Bob and wipe preview cache while Alice's download is still in flight
    cache.set_namespace("https://courses.ut.edu.vn|bob")
    preview.clear()

    # Bob requests the same URL -> must NOT coalesce onto Alice's in-flight download
    bob_path = svc.fetch_or_cache_preview_file("courses", url, 100, "shared.pdf")
    assert bob_path is not None
    assert bob_path.read_bytes() == b"BOB-FRESH-PDF"

    # Now release Alice's stale download -> must NOT overwrite Bob's file or return Alice's bytes
    alice_release.set()
    t_alice.join(timeout=2.0)
    assert not t_alice.is_alive()

    assert alice_out == [None]
    assert bob_path.read_bytes() == b"BOB-FRESH-PDF"
    assert call_counter["n"] == 2


