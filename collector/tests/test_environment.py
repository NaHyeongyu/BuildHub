from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import environment


def test_promote_legacy_environment_preserves_canonical_value(monkeypatch) -> None:
    monkeypatch.setenv("PROMPTHUB_API_URL", "https://legacy.example")
    monkeypatch.setenv("PROMPTHUB_API_TOKEN", "legacy-token")
    monkeypatch.setenv("PROMTY_API_URL", "https://promty.example")
    monkeypatch.delenv("PROMTY_API_TOKEN", raising=False)

    environment.promote_legacy_environment()

    assert environment.os.environ["PROMTY_API_URL"] == "https://promty.example"
    assert environment.os.environ["PROMTY_API_TOKEN"] == "legacy-token"


def test_migrate_legacy_data_root_copies_only_missing_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / ".prompthub"
    promty_root = tmp_path / ".promty"
    (legacy_root / "profiles" / "prod").mkdir(parents=True)
    (legacy_root / "profiles" / "prod" / "config.json").write_text(
        "legacy",
        encoding="utf-8",
    )
    promty_root.mkdir()
    (promty_root / "keep.txt").write_text("current", encoding="utf-8")
    monkeypatch.setattr(environment, "LEGACY_DATA_ROOT", legacy_root)
    monkeypatch.setenv("PROMTY_HOME", str(promty_root))

    environment.migrate_legacy_data_root()

    assert (promty_root / "profiles" / "prod" / "config.json").read_text(
        encoding="utf-8",
    ) == "legacy"
    assert (promty_root / "keep.txt").read_text(encoding="utf-8") == "current"
    assert (promty_root / environment.LEGACY_MIGRATION_MARKER).exists()


def test_migrate_legacy_data_root_does_not_restore_removed_queue(
    monkeypatch,
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / ".prompthub"
    promty_root = tmp_path / ".promty"
    legacy_queue = legacy_root / "profiles" / "prod" / "events"
    legacy_queue.parent.mkdir(parents=True)
    legacy_queue.write_text("stale-event\n", encoding="utf-8")
    monkeypatch.setattr(environment, "LEGACY_DATA_ROOT", legacy_root)
    monkeypatch.setenv("PROMTY_HOME", str(promty_root))

    environment.migrate_legacy_data_root()
    canonical_queue = promty_root / "profiles" / "prod" / "events"
    assert canonical_queue.read_text(encoding="utf-8") == "stale-event\n"

    canonical_queue.unlink()
    environment.migrate_legacy_data_root()

    assert not canonical_queue.exists()


def test_migrate_legacy_data_root_skips_queue_for_configured_profile(
    monkeypatch,
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / ".prompthub"
    promty_root = tmp_path / ".promty"
    legacy_profile = legacy_root / "profiles" / "prod"
    legacy_profile.mkdir(parents=True)
    (legacy_profile / "config.json").write_text("legacy", encoding="utf-8")
    (legacy_profile / "events").write_text("stale-event\n", encoding="utf-8")
    canonical_profile = promty_root / "profiles" / "prod"
    canonical_profile.mkdir(parents=True)
    (canonical_profile / "config.json").write_text("current", encoding="utf-8")
    monkeypatch.setattr(environment, "LEGACY_DATA_ROOT", legacy_root)
    monkeypatch.setenv("PROMTY_HOME", str(promty_root))

    environment.migrate_legacy_data_root()

    assert (canonical_profile / "config.json").read_text(
        encoding="utf-8",
    ) == "current"
    assert not (canonical_profile / "events").exists()


def test_migrate_legacy_data_root_rechecks_marker_after_lock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / ".prompthub"
    promty_root = tmp_path / ".promty"
    legacy_root.mkdir()
    marker = promty_root / environment.LEGACY_MIGRATION_MARKER
    monkeypatch.setattr(environment, "LEGACY_DATA_ROOT", legacy_root)
    monkeypatch.setenv("PROMTY_HOME", str(promty_root))

    @contextmanager
    def complete_migration_while_waiting(_path: Path) -> Iterator[None]:
        marker.parent.mkdir(parents=True)
        marker.touch()
        yield

    def fail_if_copy_runs(_source: Path, _destination: Path) -> None:
        raise AssertionError("migration copy ran after another process completed it")

    monkeypatch.setattr(environment, "locked_file", complete_migration_while_waiting)
    monkeypatch.setattr(environment, "_copy_missing_tree", fail_if_copy_runs)

    environment.migrate_legacy_data_root()
