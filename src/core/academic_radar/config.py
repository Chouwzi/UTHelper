from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RadarConfig:
    """Configuration and calibration parameters for Academic Radar."""

    min_score: float = 0.0
    max_score: float = 100.0
    tail_fraction: float = 1.0 / 3.0
    rhythm_penalty: float = 15.0
    complete_coverage: float = 0.8
    minimum_coverage: float = 0.5
    minimum_metrics_for_polygon: int = 4
    scoring_version: int = 1


RADAR_CONFIG = RadarConfig()
