"""Tests for Courses & THNN cache store, LRU preview cache, and sync config."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from config import Settings
from core.courses.cache_store import CourseCacheStore
from core.courses.models import (
    CourseAttachment,
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSummary,
    RecentMaterialEntry,
)
from core.courses.preview_cache import PreviewFileCache


def test_course_cache_store_roundtrip_and_namespace_isolation(tmp_path):
    store_a = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|sv1")
    store_b = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|sv2")

    course = CourseSummary(
        id=101,
        source="courses",
        fullname="Kỹ thuật phần mềm",
        shortname="KTPM",
        display_name="Kỹ thuật phần mềm",
        course_code="012012",
        semester_label="HKI 2026-2027",
        semester_sort_key=(2026, 3),
    )
    store_a.save_catalog([course], synced_at=1700000100)
    catalog_a = store_a.load_catalog()
    assert len(catalog_a.courses) == 1
    assert catalog_a.courses[0].course_key == "courses:101"
    assert catalog_a.catalog_synced_at == 1700000100
    assert store_b.load_catalog().courses == []

    store_a.set_pinned("courses:101", True)
    assert "courses:101" in store_a.load_catalog().pinned_keys

    recent = RecentMaterialEntry(
        course_key="courses:101",
        course_name="Kỹ thuật phần mềm",
        cmid=77,
        title="Slide 1",
        attachment_url="https://courses.ut.edu.vn/f.pdf",
        file_name="s1.pdf",
        opened_at=1700000200,
    )
    store_a.record_recent(recent)
    assert store_a.load_catalog().recent_items[0].file_name == "s1.pdf"

    att = CourseAttachment(
        file_name="s1.pdf",
        file_url="https://courses.ut.edu.vn/f.pdf",
        file_size=123,
        extension=".pdf",
        preview_kind="pdf",
    )
    sec = CourseSection(
        id=1,
        course_id=101,
        source="courses",
        section_num=0,
        name="Chủ đề 0",
        modules=(
            CourseModuleItem(
                cmid=77,
                instance_id=5,
                modname="resource",
                name="Slide 1",
                section_id=1,
                attachments=(att,),
                open_strategy="preview_file",
            ),
        ),
    )
    grade = CourseGradeItem(id=9, item_name="Giữa kỳ", grade_formatted="8.5")
    store_a.save_course_detail("courses", 101, [sec], [grade], synced_at=1700000300)
    detail = store_a.load_course_detail("courses", 101)
    assert detail.last_synced_at == 1700000300
    assert detail.sections[0].modules[0].attachments[0].file_name == "s1.pdf"
    assert detail.grades[0].grade_formatted == "8.5"


def test_preview_file_cache_lru_eviction(tmp_path):
    cache = PreviewFileCache(cache_dir=tmp_path, max_bytes=100)
    p1 = cache.put_bytes("https://x/1.pdf", 100, "a.pdf", b"x" * 60)
    p2 = cache.put_bytes("https://x/2.pdf", 100, "b.pdf", b"y" * 60)
    assert not p1.exists()
    assert p2.exists()
    assert cache.get_cached_path("https://x/2.pdf", 100, "b.pdf") == p2


def test_courses_sync_interval_hours_default_and_override():
    default_settings = Settings()
    assert default_settings.COURSES_SYNC_INTERVAL_HOURS == 6

    custom_settings = Settings(COURSES_SYNC_INTERVAL_HOURS=12)
    restored = Settings.model_validate_json(custom_settings.model_dump_json())
    assert restored.COURSES_SYNC_INTERVAL_HOURS == 12


def test_catalog_pin_unseen_recent_and_save_preserve_other_fields(tmp_path):
    store = CourseCacheStore(cache_dir=tmp_path / "nested", namespace="  https://courses.ut.edu.vn|SV1  ")
    same_store = CourseCacheStore(cache_dir=tmp_path / "nested", namespace="https://courses.ut.edu.vn|sv1")

    assert store.set_pinned("courses:101", True) == ["courses:101"]
    assert store.set_pinned("thnn:202", True) == ["thnn:202", "courses:101"]
    assert store.set_pinned("courses:101", True) == ["courses:101", "thnn:202"]
    assert store.set_pinned("thnn:202", False) == ["courses:101"]

    assert store.set_unseen("courses:101", True) == ["courses:101"]
    assert store.set_unseen("thnn:202", True) == ["courses:101", "thnn:202"]
    assert store.set_unseen("courses:101", True) == ["thnn:202", "courses:101"]
    assert store.set_unseen("courses:101", False) == ["thnn:202"]

    r1 = RecentMaterialEntry(
        course_key="courses:101",
        course_name="SE",
        cmid=1,
        title="Slide 1",
        attachment_url="https://courses.ut.edu.vn/1.pdf",
        file_name="1.pdf",
        opened_at=100,
    )
    r2 = RecentMaterialEntry(
        course_key="courses:101",
        course_name="SE",
        cmid=2,
        title="Slide 2",
        attachment_url="https://courses.ut.edu.vn/2.pdf",
        file_name="2.pdf",
        opened_at=200,
    )
    r3 = RecentMaterialEntry(
        course_key="courses:101",
        course_name="SE",
        cmid=3,
        title="Slide 3",
        attachment_url="https://courses.ut.edu.vn/3.pdf",
        file_name="3.pdf",
        opened_at=300,
    )
    store.record_recent(r1, limit=2)
    store.record_recent(r2, limit=2)
    # Re-opening r1 moves it to index 0 without duplication.
    reopened_r1 = RecentMaterialEntry(
        course_key="courses:101",
        course_name="SE",
        cmid=1,
        title="Slide 1",
        attachment_url="https://courses.ut.edu.vn/1.pdf",
        file_name="1.pdf",
        opened_at=250,
    )
    recent_list = store.record_recent(reopened_r1, limit=2)
    assert [r.attachment_url for r in recent_list] == [
        "https://courses.ut.edu.vn/1.pdf",
        "https://courses.ut.edu.vn/2.pdf",
    ]
    assert recent_list[0].opened_at == 250

    trimmed = store.record_recent(r3, limit=2)
    assert [r.attachment_url for r in trimmed] == [
        "https://courses.ut.edu.vn/3.pdf",
        "https://courses.ut.edu.vn/1.pdf",
    ]

    # save_catalog must preserve pinned_keys, unseen_keys, and recent_items.
    course = CourseSummary(
        id=101,
        source="courses",
        fullname="SE",
        shortname="SE",
        display_name="SE",
    )
    store.save_catalog([course], synced_at=500)
    loaded = same_store.load_catalog()
    assert loaded.pinned_keys == ["courses:101"]
    assert loaded.unseen_keys == ["thnn:202"]
    assert [r.file_name for r in loaded.recent_items] == ["3.pdf", "1.pdf"]
    assert loaded.catalog_synced_at == 500


def test_course_cache_store_tolerates_schema_drift_and_malformed_entries(tmp_path):
    store = CourseCacheStore(cache_dir=tmp_path, namespace="drift")
    store._catalog_path.write_text(
        json.dumps(
            {
                "version": 99,
                "catalog_synced_at": "1700000999",
                "pinned_keys": ["courses:10", 123, None, ""],
                "unseen_keys": "not-a-list",
                "courses": [
                    "not-a-dict",
                    {"id": "bad-int", "source": "courses"},
                    {"id": 99, "source": "invalid_source"},
                    {
                        "id": 10,
                        "source": "courses",
                        "fullname": "Clean Course",
                        "shortname": "CC",
                        "display_name": "Clean Course",
                        "semester_sort_key": "2026",
                        "progress": True,
                        "future_field": {"nested": [1, 2]},
                    },
                    {
                        "id": 20,
                        "source": "thnn",
                        "fullname": "THNN Course",
                        "shortname": "TC",
                        "display_name": "THNN Course",
                        "semester_sort_key": [2026, "3"],
                        "progress": 0.75,
                    },
                ],
                "recent_items": [
                    "corrupt-entry",
                    {"missing_required": True},
                    {
                        "course_key": "courses:10",
                        "course_name": "Clean Course",
                        "cmid": 5,
                        "title": "Notes",
                        "attachment_url": "https://courses.ut.edu.vn/n.txt",
                        "file_name": "n.txt",
                        "opened_at": 1700000111,
                        "source": "unknown_site",
                        "extra_future_col": "ignored",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    catalog = store.load_catalog()
    assert catalog.catalog_synced_at == 1700000999
    assert catalog.pinned_keys == ["courses:10"]
    assert catalog.unseen_keys == []
    assert [c.id for c in catalog.courses] == [10, 20]
    assert catalog.courses[0].semester_sort_key == (0, 0)
    assert catalog.courses[0].progress is None
    assert catalog.courses[1].semester_sort_key == (2026, 3)
    assert catalog.courses[1].progress == 0.75
    assert len(catalog.recent_items) == 1
    assert catalog.recent_items[0].source == "courses"

    # record_recent should also survive existing non-dict items in recent_items.
    added = RecentMaterialEntry(
        course_key="courses:10",
        course_name="Clean Course",
        cmid=6,
        title="New",
        attachment_url="https://courses.ut.edu.vn/new.pdf",
        file_name="new.pdf",
        opened_at=1700000222,
    )
    updated_recents = store.record_recent(added, limit=5)
    assert [item.file_name for item in updated_recents] == ["new.pdf", "n.txt"]

    # Detail schema drift + malformed entries.
    detail_path = store._detail_path("courses", 10)
    detail_path.write_text(
        json.dumps(
            {
                "version": 42,
                "last_synced_at": 1700000500,
                "known_cmids": [5, "bad", True, 7],
                "sections": [
                    "bad-section",
                    {"id": 1, "course_id": 10, "source": "bad"},
                    {
                        "id": 2,
                        "course_id": 10,
                        "source": "courses",
                        "section_num": 1,
                        "name": "Week 1",
                        "extra_sec_key": "v2",
                        "modules": [
                            "bad-mod",
                            {"cmid": "bad"},
                            {
                                "cmid": 5,
                                "instance_id": 11,
                                "modname": "resource",
                                "name": "Slide",
                                "section_id": 2,
                                "open_strategy": "future_strategy",
                                "extra_mod_key": True,
                                "attachments": [
                                    "bad-att",
                                    {
                                        "file_name": "a.pdf",
                                        "file_url": "https://courses.ut.edu.vn/a.pdf",
                                        "preview_kind": "future_preview",
                                        "extra_att_key": 999,
                                    },
                                ],
                            },
                        ],
                    },
                ],
                "grades": [
                    "bad-grade",
                    {"item_name": "no-id"},
                    {"id": 8, "item_name": "Final", "grade_formatted": "9.0", "extra_g": 1},
                ],
            }
        ),
        encoding="utf-8",
    )

    detail = store.load_course_detail("courses", 10)
    assert detail.last_synced_at == 1700000500
    assert detail.known_cmids == [5, 7]
    assert len(detail.sections) == 1
    assert len(detail.sections[0].modules) == 1
    mod = detail.sections[0].modules[0]
    assert mod.open_strategy == "web_fallback"
    assert len(mod.attachments) == 1
    assert mod.attachments[0].preview_kind == "unsupported"
    assert len(detail.grades) == 1
    assert detail.grades[0].grade_formatted == "9.0"


def test_course_cache_store_handles_corrupt_or_non_dict_json_and_validates_keys(tmp_path):
    store = CourseCacheStore(cache_dir=tmp_path, namespace="corrupt")
    store._catalog_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert store.load_catalog().courses == []

    # Writing a pin on top of a non-dict JSON file recovers cleanly.
    assert store.set_pinned("courses:1", True) == ["courses:1"]

    detail_path = store._detail_path("thnn", 4458)
    detail_path.write_text("{not valid json", encoding="utf-8")
    assert store.load_course_detail("thnn", 4458).sections == []

    # Explicit empty known_cmids must be preserved instead of regenerated.
    sec = CourseSection(
        id=1,
        course_id=4458,
        source="thnn",
        section_num=0,
        name="Topic",
        modules=(
            CourseModuleItem(
                cmid=88,
                instance_id=1,
                modname="resource",
                name="File",
                section_id=1,
            ),
        ),
    )
    store.save_course_detail("thnn", 4458, [sec], [], synced_at=1234, known_cmids=[])
    assert store.load_course_detail("thnn", 4458).known_cmids == []

    with pytest.raises(ValueError):
        store.load_course_detail("evil", 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.save_course_detail("courses", -1, [], [], synced_at=1)
    with pytest.raises(ValueError):
        store.load_course_detail("courses", True)  # type: ignore[arg-type]


def test_course_cache_store_concurrent_catalog_updates_do_not_lose_mutations(tmp_path):
    store = CourseCacheStore(cache_dir=tmp_path, namespace="concurrent")
    courses = [
        CourseSummary(
            id=1,
            source="courses",
            fullname="Course 1",
            shortname="C1",
            display_name="Course 1",
        )
    ]
    store.save_catalog(courses, synced_at=100)

    def pin_worker(idx: int) -> None:
        store.set_pinned(f"courses:{idx}", True)

    def unseen_worker(idx: int) -> None:
        store.set_unseen(f"thnn:{idx}", True)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(pin_worker, i) for i in range(10)] + [
            pool.submit(unseen_worker, i) for i in range(10)
        ]
        for fut in futures:
            fut.result()

    snap = store.load_catalog()
    assert len(snap.courses) == 1
    assert set(snap.pinned_keys) == {f"courses:{i}" for i in range(10)}
    assert set(snap.unseen_keys) == {f"thnn:{i}" for i in range(10)}


def test_preview_file_cache_touch_lru_and_expiry_and_clear(tmp_path):
    cache = PreviewFileCache(cache_dir=tmp_path, max_bytes=120, max_age_seconds=60)
    p1 = cache.put_bytes("https://x/1.pdf", 1, "../../dir/a.pdf", b"a" * 50)
    p2 = cache.put_bytes("https://x/2.pdf", 1, "b?.pdf", b"b" * 50)

    # Set distinct recent mtimes: p1 older than p2, then touch p1 via get_cached_path.
    now = time.time()
    os.utime(p1, (now - 20, now - 20))
    os.utime(p2, (now - 10, now - 10))
    assert cache.get_cached_path("https://x/1.pdf", 1, "../../dir/a.pdf") == p1
    os.utime(p2, (now - 10, now - 10))

    # Adding p3 (50 bytes) exceeds 120 bytes -> p2 (oldest) evicted, p1 & p3 kept.
    p3 = cache.put_bytes("https://x/3.pdf", 1, "c.pdf", b"c" * 50)
    assert p1.exists()
    assert not p2.exists()
    assert p3.exists()

    # Large file > max_bytes is kept as `keep`, evicting older entries.
    big = cache.put_bytes("https://x/big.pdf", 1, "big.pdf", b"z" * 150)
    assert big.exists()
    assert not p1.exists()
    assert not p3.exists()

    # Expired file is evicted and returns None on get_cached_path.
    expired_time = time.time() - 120
    os.utime(big, (expired_time, expired_time))
    assert cache.get_cached_path("https://x/big.pdf", 1, "big.pdf") is None
    assert not big.exists()

    # Stale .tmp cleanup and clear() remove everything including temp files.
    kept = cache.put_bytes("https://x/k.pdf", 1, "k.pdf", b"k" * 10)
    stale_tmp = tmp_path / "stale.pdf.1.1.tmp"
    stale_tmp.write_bytes(b"stale")
    os.utime(stale_tmp, (time.time() - 600, time.time() - 600))
    cache.evict_if_needed()
    assert not stale_tmp.exists()
    assert kept.exists()

    fresh_tmp = tmp_path / "orphan.tmp"
    fresh_tmp.write_bytes(b"tmp")
    cache.clear()
    assert list(tmp_path.iterdir()) == []


def test_preview_file_cache_concurrent_puts_leave_no_temp_files(tmp_path):
    cache = PreviewFileCache(cache_dir=tmp_path, max_bytes=1024)
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            url = f"https://x/{idx % 4}.pdf"
            path = cache.put_bytes(url, 100, f"f{idx % 4}.pdf", f"data-{idx}".encode())
            assert path.exists()
            cache.get_cached_path(url, 100, f"f{idx % 4}.pdf")
        except Exception as exc:  # pragma: no cover - defensive capture
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


def test_default_user_data_dir_and_filename_sanitization(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)

    default_store = CourseCacheStore()
    whitespace_store = CourseCacheStore(namespace="   ")
    assert default_store._catalog_path == whitespace_store._catalog_path
    assert default_store._catalog_path.parent == tmp_path

    default_preview = PreviewFileCache()
    assert default_preview.cache_dir == tmp_path / "preview_cache"

    dot_path = default_preview.put_bytes("https://x/dot", 0, "..", b"ok")
    assert dot_path.name.endswith("_file")

    long_name = ("long_segment_" * 15) + ".pdf"
    long_path = default_preview.put_bytes("https://x/long", 0, long_name, b"pdf")
    assert long_path.name.endswith(".pdf")
    assert len(long_path.name) <= len(long_path.name.split("_", 1)[0]) + 1 + 120


def test_course_cache_store_concurrent_reads_and_writes_across_instances(tmp_path):
    ns = "https://courses.ut.edu.vn|concurrent-rw"
    seed_store = CourseCacheStore(cache_dir=tmp_path, namespace=ns)
    seeded_course = CourseSummary(
        id=101,
        source="courses",
        fullname="Kỹ thuật phần mềm",
        shortname="KTPM",
        display_name="Kỹ thuật phần mềm",
    )
    seeded_section = CourseSection(
        id=1,
        course_id=101,
        source="courses",
        section_num=0,
        name="Chủ đề 0",
        modules=(
            CourseModuleItem(
                cmid=77,
                instance_id=5,
                modname="resource",
                name="Slide 1",
                section_id=1,
            ),
        ),
    )
    seed_store.save_catalog([seeded_course], synced_at=100)
    seed_store.save_course_detail("courses", 101, [seeded_section], [], synced_at=100)

    barrier = threading.Barrier(8)
    observed_empty_catalog: list[int] = []
    observed_empty_detail: list[int] = []

    def writer_pins(worker_id: int) -> None:
        store = CourseCacheStore(cache_dir=tmp_path, namespace=ns)
        barrier.wait()
        for step in range(8):
            store.set_pinned(f"courses:{worker_id}_{step}", True)
            store.set_unseen(f"thnn:{worker_id}_{step}", True)

    def writer_catalog_and_detail(worker_id: int) -> None:
        store = CourseCacheStore(cache_dir=tmp_path, namespace=ns)
        barrier.wait()
        for step in range(8):
            store.save_catalog([seeded_course], synced_at=200 + step)
            store.record_recent(
                RecentMaterialEntry(
                    course_key="courses:101",
                    course_name="Kỹ thuật phần mềm",
                    cmid=77,
                    title=f"Slide {worker_id}-{step}",
                    attachment_url=f"https://courses.ut.edu.vn/{worker_id}_{step}.pdf",
                    file_name=f"{worker_id}_{step}.pdf",
                    opened_at=300 + step,
                ),
                limit=20,
            )
            store.save_course_detail(
                "courses", 101, [seeded_section], [], synced_at=200 + step
            )

    def reader_loop() -> None:
        store = CourseCacheStore(cache_dir=tmp_path, namespace=ns)
        barrier.wait()
        for _ in range(25):
            cat = store.load_catalog()
            if len(cat.courses) != 1:
                observed_empty_catalog.append(len(cat.courses))
            detail = store.load_course_detail("courses", 101)
            if len(detail.sections) != 1:
                observed_empty_detail.append(len(detail.sections))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(writer_pins, 0),
            pool.submit(writer_pins, 1),
            pool.submit(writer_catalog_and_detail, 2),
            pool.submit(writer_catalog_and_detail, 3),
            pool.submit(reader_loop),
            pool.submit(reader_loop),
            pool.submit(reader_loop),
            pool.submit(reader_loop),
        ]
        for fut in futures:
            fut.result()

    assert observed_empty_catalog == []
    assert observed_empty_detail == []

    final_snap = seed_store.load_catalog()
    assert len(final_snap.courses) == 1
    assert set(final_snap.pinned_keys) == {
        f"courses:{w}_{s}" for w in (0, 1) for s in range(8)
    }
    assert set(final_snap.unseen_keys) == {
        f"thnn:{w}_{s}" for w in (0, 1) for s in range(8)
    }
    assert len(final_snap.recent_items) == 16
