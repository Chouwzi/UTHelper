"""Measure Flet control-tree cost without opening a native window.

This benchmark covers Python patch generation and wire payload size. It does
not replace frame-time measurements on real Windows and Android hardware.
"""

from __future__ import annotations

import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import flet as ft
from flet.controls.base_control import BaseControl
from flet.messaging.connection import Connection, PubSubHub
from flet.messaging.protocol import (
    configure_encode_object_for_msgpack,
    msgpack,
)
from flet.messaging.session import Session

from gui.components.calendar_view import CalendarView
from gui.components.detail_view import DetailView
from gui.components.grade_overview_view import GradeOverviewView
from gui.components.settings_view import SettingsView


class _Connection(Connection):
    def __init__(self) -> None:
        super().__init__()
        self.messages = []

    def send_message(self, message) -> None:
        self.messages.append(message)

    def get_upload_url(self, *_args) -> str:
        return ""

    def oauth_authorize(self, *_args) -> None:
        return None


class _Orchestrator:
    @staticmethod
    def get_cached_details_snapshot() -> dict:
        return {}


def _runtime() -> tuple[_Connection, Session, ft.Page, ThreadPoolExecutor]:
    loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    connection = _Connection()
    connection.loop = loop
    connection.executor = executor
    connection.pubsubhub = PubSubHub(loop, executor)
    session = Session(connection)
    return connection, session, session.page, executor


def _encode(value) -> bytes:
    return msgpack.packb(
        value,
        default=configure_encode_object_for_msgpack(BaseControl),
    )


def _measure_view(name: str, factory) -> dict[str, float | int | str]:
    _connection, session, page, executor = _runtime()
    try:
        started = time.perf_counter()
        view = factory(page)
        construct_ms = (time.perf_counter() - started) * 1000
        view.visible = True
        page.controls.append(view)
        started = time.perf_counter()
        snapshot = session.get_page_patch()
        patch_ms = (time.perf_counter() - started) * 1000
        return {
            "name": name,
            "construct_ms": construct_ms,
            "patch_ms": patch_ms,
            "wire_kib": len(_encode(snapshot)) / 1024,
            "controls": max(0, len(list(session.index.keys())) - 6),
        }
    finally:
        executor.shutdown(wait=False)


def _measure_active_host() -> dict[str, float | int | str]:
    connection, session, page, executor = _runtime()
    try:
        dashboard = ft.Text("dashboard")
        settings = SettingsView(page, _Orchestrator(), lambda: None)
        host = ft.Stack(controls=[dashboard])
        page.controls.append(host)
        initial = session.get_page_patch()
        initial_wire_kib = len(_encode(initial)) / 1024
        initial_controls = max(0, len(list(session.index.keys())) - 6)

        connection.messages.clear()
        settings.visible = True
        host.controls = [settings]
        started = time.perf_counter()
        page.update()
        patch_ms = (time.perf_counter() - started) * 1000
        message = connection.messages[-1]
        patch_wire_kib = len(_encode([message.action.value, message.body])) / 1024
        return {
            "name": "active-host",
            "construct_ms": 0.0,
            "patch_ms": patch_ms,
            "wire_kib": initial_wire_kib,
            "controls": initial_controls,
            "settings_patch_kib": patch_wire_kib,
        }
    finally:
        executor.shutdown(wait=False)


def main() -> int:
    results = [
        _measure_view("detail", lambda page: DetailView(page, lambda: None)),
        _measure_view("calendar", lambda page: CalendarView(page)),
        _measure_view("grades", lambda _page: GradeOverviewView()),
        _measure_view(
            "settings",
            lambda page: SettingsView(page, _Orchestrator(), lambda: None),
        ),
        _measure_active_host(),
    ]
    for result in results:
        extra = (
            f" settings_patch_kib={result['settings_patch_kib']:.1f}"
            if "settings_patch_kib" in result
            else ""
        )
        print(
            f"{result['name']}: construct_ms={result['construct_ms']:.1f} "
            f"patch_ms={result['patch_ms']:.1f} "
            f"wire_kib={result['wire_kib']:.1f} "
            f"controls={result['controls']}{extra}"
        )

    settings = next(item for item in results if item["name"] == "settings")
    host = next(item for item in results if item["name"] == "active-host")
    within_budget = (
        settings["wire_kib"] <= 12
        and settings["controls"] <= 120
        and host["wire_kib"] <= 1
        and host["controls"] <= 10
        and host["settings_patch_kib"] <= 12
    )
    return 0 if within_budget else 1


if __name__ == "__main__":
    raise SystemExit(main())
