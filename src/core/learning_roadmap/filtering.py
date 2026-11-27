"""Search / filter for the roadmap - built to help during course registration."""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from core.learning_roadmap.models import CourseStatus, RoadmapSemester
from core.learning_roadmap.parsers import fold

FILTER_ALL = "all"
FILTER_ACTIONABLE = "actionable"   # failed + not taken: what you may need to register

_FILTER_STATUSES: dict[str, frozenset[CourseStatus]] = {
    FILTER_ACTIONABLE: frozenset({CourseStatus.FAILED, CourseStatus.NOT_TAKEN}),
    **{s.value: frozenset({s}) for s in CourseStatus},
}

VALID_FILTERS = (FILTER_ALL, FILTER_ACTIONABLE, *(s.value for s in CourseStatus))


def filter_semesters(
    semesters: Iterable[RoadmapSemester],
    query: str = "",
    status_filter: str = FILTER_ALL,
) -> list[RoadmapSemester]:
    """Return semesters reduced to matching courses; empty semesters are dropped.

    ``query`` is accent- and case-insensitive; every whitespace-separated term
    must appear in ``"<code> <name>"`` ("co so du lieu" finds "Cơ sở dữ liệu").
    """
    allowed = _FILTER_STATUSES.get(status_filter)
    terms = fold(query).split()
    out: list[RoadmapSemester] = []
    for sem in semesters:
        kept = tuple(
            c for c in sem.courses
            if (allowed is None or c.status in allowed)
            and all(t in c.search_text for t in terms)
        )
        if kept:
            out.append(replace(sem, courses=kept))
    return out
