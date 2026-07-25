from __future__ import annotations

import os
from pathlib import Path
import shutil

from file_lock import locked_file


LEGACY_ENV_PREFIX = "PROMPTHUB_"
CANONICAL_ENV_PREFIX = "PROMTY_"
LEGACY_DATA_ROOT = Path("~/.prompthub").expanduser()
LEGACY_MIGRATION_MARKER = ".legacy-data-migration-complete"
LEGACY_MIGRATION_LOCK = ".legacy-data-migration.lock"
LEGACY_ACTIVE_QUEUE_NAMES = {"events", "events.jsonl"}


def promote_legacy_environment() -> None:
    """Make legacy collector variables available through the Promty names."""

    for name, value in tuple(os.environ.items()):
        if not name.startswith(LEGACY_ENV_PREFIX):
            continue
        canonical_name = f"{CANONICAL_ENV_PREFIX}{name[len(LEGACY_ENV_PREFIX) :]}"
        os.environ.setdefault(canonical_name, value)


def _copy_missing_tree(source: Path, destination: Path) -> None:
    """Copy missing legacy files without replacing active configured queues."""

    destination_is_configured = (destination / "config.json").is_file()
    destination.mkdir(parents=True, exist_ok=True)
    for source_path in source.iterdir():
        if source_path.is_symlink():
            continue
        if destination_is_configured and source_path.name in LEGACY_ACTIVE_QUEUE_NAMES:
            continue
        destination_path = destination / source_path.name
        if source_path.is_dir():
            _copy_missing_tree(source_path, destination_path)
        elif source_path.is_file() and not destination_path.exists():
            shutil.copy2(source_path, destination_path)


def migrate_legacy_data_root() -> None:
    """Copy legacy collector data into ~/.promty exactly once."""

    destination = Path(os.environ.get("PROMTY_HOME", "~/.promty")).expanduser()
    if not LEGACY_DATA_ROOT.is_dir() or destination == LEGACY_DATA_ROOT:
        return
    marker = destination / LEGACY_MIGRATION_MARKER
    with locked_file(destination / LEGACY_MIGRATION_LOCK):
        if marker.exists():
            return
        _copy_missing_tree(LEGACY_DATA_ROOT, destination)
        marker.touch(mode=0o600, exist_ok=True)
        marker.chmod(0o600)


promote_legacy_environment()
