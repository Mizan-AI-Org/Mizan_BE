"""
Backwards-compatible safety models module.

Historically this project had multiple safety/task model definitions which caused Django
model registry conflicts (same model names under the same app label).

The canonical schema for the `staff` app is defined in `staff/models_task.py` (and matches
the existing migrations). This module re-exports those models so older imports keep working
without registering duplicate models.
"""

from .models_task import (  # noqa: F401
    StandardOperatingProcedure,
    SafetyChecklist,
    ScheduleTask,
    SafetyConcernReport,
    SafetyRecognition,
)

__all__ = [
    "StandardOperatingProcedure",
    "SafetyChecklist",
    "ScheduleTask",
    "SafetyConcernReport",
    "SafetyRecognition",
]