"""Persistence for the graduation plan (user data - kept apart from the
Portal cache, which is rewritten on every refresh)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from core.learning_roadmap.planner import PlanState
from core.safe_file_io import SafeFileIO

logger = logging.getLogger(__name__)

PLAN_FILE_NAME = "learning_plan.json"


class PlanStore:
    """One plan per Portal account. A plan saved for another account is
    ignored (and overwritten on the next save) instead of leaking across."""

    def __init__(self, directory: Optional[Path] = None, owner: str = "") -> None:
        if directory is None:
            from config import get_data_dir  # lazy: keeps this module import-light
            directory = get_data_dir()
        self._file = Path(directory) / PLAN_FILE_NAME
        self.owner = (owner or "").strip().lower()

    def set_owner(self, owner: str) -> None:
        self.owner = (owner or "").strip().lower()

    def load(self) -> PlanState:
        data = SafeFileIO.read_json_safe(self._file, default_factory=dict)
        if not isinstance(data, dict) or data.get("owner", "") != self.owner:
            return PlanState()
        return PlanState.from_json(data)

    def save(self, state: PlanState) -> bool:
        payload = state.to_json()
        payload["owner"] = self.owner
        try:
            ok = bool(SafeFileIO.write_json_atomic(self._file, payload))
        except Exception as exc:  # the plan is best-effort; never crash the UI
            logger.warning("Plan save failed: %s", exc)
            return False
        if not ok:
            logger.warning("Plan save reported failure")
        return ok
