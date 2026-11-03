"""Canonical output locations and read-only compatibility with earlier archives.

The profile root, database filename and credential namespace never move here.
Old captures stay where they were recorded; only new outputs use the new layout.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArchivePaths:
    profile: Path

    @property
    def data(self) -> Path:
        return self.profile / "data"

    @property
    def cloud(self) -> Path:
        return self.data / "raw" / "cloud"

    @property
    def bluetooth(self) -> Path:
        return self.data / "raw" / "bluetooth"

    @property
    def bluetooth_roots(self) -> tuple[Path, Path]:
        # New captures take precedence over a legacy copy with the same ID.
        return self.bluetooth, self.data / "bluetooth"

    @property
    def patterns(self) -> Path:
        return self.data / "processed" / "patterns"

    @property
    def quality(self) -> Path:
        return self.data / "processed" / "quality"

    @property
    def discovery(self) -> Path:
        return self.data / "processed" / "discovery"

    @property
    def activity_migrations(self) -> Path:
        return self.data / "processed" / "activity-migrations"

    @property
    def activity_backfill(self) -> Path:
        return self.data / "processed" / "activity-backfill"

    def snapshots(self) -> list[Path]:
        """Newest names first, across both layouts; never move or rewrite inputs."""
        found = {}
        for parent in (self.cloud, self.data):
            for path in parent.glob("export-*"):
                if path.is_dir():
                    found.setdefault(path.name, path)
        return [found[name] for name in sorted(found, reverse=True)]

    def pattern_report(self, filename: str, *, for_write: bool = False) -> Path:
        current = self.patterns / filename
        legacy = self.data / "patterns" / filename
        return legacy if not for_write and not current.is_file() and legacy.is_file() else current
