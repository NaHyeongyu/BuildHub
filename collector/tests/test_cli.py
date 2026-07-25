from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import cli
from cli import (
    CLAUDE_HOOKS,
    CODEX_HOOKS,
    LOGIN_CALLBACK_HTML,
    build_parser,
    _apply_profile_defaults,
    _parse_profiles,
    install_hooks,
    main,
)
from runtime_install import install_runtime
from uploader.client import EventBatchConflict
from uploader.queue import JSONLQueue


def test_parser_exposes_expected_commands() -> None:
    parser = build_parser()

    for command in (
        "capture",
        "capture-changes",
        "context",
        "doctor",
        "init",
        "install-hooks",
        "mcp",
        "uninstall-hooks",
        "upload",
    ):
        args = parser.parse_args([command])
        assert args.command == command


def test_main_returns_parser_error_for_missing_command() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2


@pytest.mark.parametrize("command", ("doctor", "init", "install-hooks"))
@pytest.mark.parametrize("tool", ("cursor", "gemini-cli"))
def test_hook_commands_only_offer_verified_integrations(command: str, tool: str) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([command, "--tool", tool])

    assert exc_info.value.code == 2


def test_capture_keeps_legacy_adapter_compatibility() -> None:
    args = build_parser().parse_args(["capture", "--tool", "cursor"])

    assert args.tool == "cursor"


@pytest.mark.parametrize(
    ("profile", "app_url", "api_url"),
    (
        ("dev", "http://127.0.0.1:5173", "http://127.0.0.1:8011"),
        ("prod", "https://promty.org", "https://api.promty.org"),
    ),
)
def test_profile_uses_isolated_config_queue_and_uploader_files(
    profile: str,
    app_url: str,
    api_url: str,
) -> None:
    args = build_parser().parse_args(["init", "--profile", profile])

    _apply_profile_defaults(args)

    profile_root = Path.home() / ".promty" / "profiles" / profile
    assert args.app_url == app_url
    assert args.api_url == api_url
    assert args.config_path == str(profile_root / "config.json")
    assert args.queue_path == str(profile_root / "events")
    assert args.pid_path == str(profile_root / "uploader.pid")
    assert args.log_path == str(profile_root / "uploader.log")


def test_init_defaults_to_production_profile() -> None:
    args = build_parser().parse_args(["init"])

    _apply_profile_defaults(args)

    profile_root = Path.home() / ".promty" / "profiles" / "prod"
    assert args.profile == "prod"
    assert args.app_url == "https://promty.org"
    assert args.api_url == "https://api.promty.org"
    assert args.config_path == str(profile_root / "config.json")


def test_multi_profile_selection_is_not_overwritten_by_default_profile() -> None:
    args = build_parser().parse_args(["init", "--profiles", "dev,prod"])

    _apply_profile_defaults(args)

    assert args.profiles == ("dev", "prod")
    assert args.app_url is None
    assert args.api_url is None
    assert args.config_path is None


@pytest.mark.parametrize("command", ("context", "mcp"))
def test_agent_context_commands_accept_profile_during_execution(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    expected_config_path = str(
        Path.home() / ".promty" / "profiles" / "prod" / "config.json"
    )
    monkeypatch.setattr(cli, "migrate_legacy_data_root", lambda: None)

    if command == "context":
        monkeypatch.setattr(
            cli,
            "project_id_for_context",
            lambda **_kwargs: "project-id",
        )
        monkeypatch.setattr(
            cli,
            "resolve_token",
            lambda _token, config_path: seen.update(config_path=config_path) or "token",
        )
        monkeypatch.setattr(
            cli,
            "fetch_project_context",
            lambda **kwargs: seen.update(kwargs) or {},
        )
        monkeypatch.setattr(cli, "render_project_context", lambda _payload: "")
    else:
        monkeypatch.setattr(
            cli,
            "PromtyMCPServer",
            lambda **kwargs: seen.update(kwargs) or object(),
        )
        monkeypatch.setattr(cli, "run_mcp_server", lambda _server: 0)

    assert main([command, "--profile", "prod"]) == 0
    assert seen["api_url"] == "https://api.promty.org"
    assert seen["config_path"] == expected_config_path


def test_automatic_updates_require_explicit_opt_in() -> None:
    parser = build_parser()

    assert parser.parse_args(["upload"]).no_auto_update is True
    assert parser.parse_args(["start-uploader"]).no_auto_update is True
    assert parser.parse_args(["start-uploader"]).restart is False
    assert parser.parse_args(["start-uploader", "--restart"]).restart is True
    assert parser.parse_args(["init"]).no_auto_update is True
    assert parser.parse_args(["upload", "--auto-update"]).no_auto_update is False


def test_profile_does_not_override_explicit_urls_or_paths() -> None:
    args = build_parser().parse_args(
        [
            "init",
            "--profile",
            "prod",
            "--api-url",
            "https://api.example.test",
            "--config-path",
            "/tmp/promty-test.json",
        ]
    )

    _apply_profile_defaults(args)

    assert args.api_url == "https://api.example.test"
    assert args.config_path == "/tmp/promty-test.json"


def test_profile_queue_path_is_written_into_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repository"
    repo_root.mkdir()
    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    args = build_parser().parse_args(
        [
            "install-hooks",
            "--tool",
            "codex-cli",
            "--hook-command",
            "promty",
            "--repo-root",
            str(repo_root),
            "--profile",
            "dev",
        ]
    )
    _apply_profile_defaults(args)

    assert install_hooks(args) == 0

    config = json.loads((repo_root / ".codex" / "hooks.json").read_text())
    command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    expected_queue = Path.home() / ".promty" / "profiles" / "dev" / "events"
    assert command == f"promty capture --tool codex-cli --queue-path {expected_queue}"


def test_profiles_parse_comma_separated_values_without_duplicates() -> None:
    assert _parse_profiles("dev,prod,dev") == ("dev", "prod")


def test_profiles_write_primary_and_mirror_queues_into_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repository"
    repo_root.mkdir()
    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    args = build_parser().parse_args(
        [
            "install-hooks",
            "--tool",
            "codex-cli",
            "--hook-command",
            "promty",
            "--repo-root",
            str(repo_root),
            "--profiles",
            "dev,prod",
        ]
    )

    assert install_hooks(args) == 0

    config = json.loads((repo_root / ".codex" / "hooks.json").read_text())
    command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    dev_queue = Path.home() / ".promty" / "profiles" / "dev" / "events"
    prod_queue = Path.home() / ".promty" / "profiles" / "prod" / "events"
    assert command == (
        f"promty capture --tool codex-cli --queue-path {dev_queue} --mirror-queue-path {prod_queue}"
    )
    assert cli._hook_status(
        repo_root,
        "codex-cli",
        expected_queue_paths=[str(dev_queue), str(prod_queue)],
    )[0]


def test_doctor_profiles_checks_each_profile_independently(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checked_profiles: list[tuple[str, Path, Path]] = []

    monkeypatch.setattr(
        cli,
        "_doctor_hook_checks",
        lambda *_args, **_kwargs: [("hooks", True, "installed")],
    )

    def fake_runtime_checks(
        args: argparse.Namespace,
        *,
        prefix: str = "",
    ) -> list[tuple[str, bool, str]]:
        checked_profiles.append((prefix, Path(args.queue_path), Path(args.pid_path)))
        return [(f"{prefix}backend", True, "ok")]

    monkeypatch.setattr(cli, "_doctor_runtime_checks", fake_runtime_checks)
    args = build_parser().parse_args(["doctor", "--profiles", "dev,prod"])

    assert args.func(args) == 0
    assert checked_profiles == [
        (
            "dev/",
            Path.home() / ".promty" / "profiles" / "dev" / "events",
            Path.home() / ".promty" / "profiles" / "dev" / "uploader.pid",
        ),
        (
            "prod/",
            Path.home() / ".promty" / "profiles" / "prod" / "events",
            Path.home() / ".promty" / "profiles" / "prod" / "uploader.pid",
        ),
    ]
    output = capsys.readouterr().out
    assert "dev/backend: ok" in output
    assert "prod/backend: ok" in output


def test_doctor_reports_invalid_ingest_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "_doctor_hook_checks",
        lambda *_args, **_kwargs: [("hooks", True, "installed")],
    )
    monkeypatch.setattr(cli, "read_config", lambda _: {"auth": {}})
    monkeypatch.setattr(cli, "resolve_token", lambda *_: "collector-token")
    monkeypatch.setattr(cli, "_queue_status", lambda _: (True, "empty"))
    monkeypatch.setattr(cli, "_queue_conflict_status", lambda _: (True, "none"))
    monkeypatch.setattr(cli, "_health_status", lambda *_: (True, "ok"))
    monkeypatch.setattr(cli, "_ingest_auth_status", lambda *_: (False, "HTTP 401"))
    monkeypatch.setattr(cli, "_read_pid", lambda _: 123)
    monkeypatch.setattr(cli, "_pid_is_running", lambda _: True)
    args = build_parser().parse_args(["doctor", "--profile", "dev"])

    assert args.func(args) == 1
    output = capsys.readouterr().out
    assert "ingest-auth: needs-action - HTTP 401" in output


def test_doctor_reports_quarantined_conflicting_events(tmp_path: Path) -> None:
    queue_path = tmp_path / "events"
    conflict_path = tmp_path / "events.conflicts.jsonl"
    conflict_path.write_text('{"event":{"id":"one"}}\n{"event":{"id":"two"}}\n')

    assert cli._queue_conflict_status(str(queue_path)) == (
        False,
        f"2 quarantined event(s) in {conflict_path}",
    )


def test_profile_capture_restarts_a_stopped_uploader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_root = tmp_path / "profiles" / "dev"
    profile_root.mkdir(parents=True)
    (profile_root / "config.json").write_text("{}", encoding="utf-8")
    started: list[argparse.Namespace] = []
    monkeypatch.setattr(cli, "_read_pid", lambda _path: None)
    monkeypatch.setattr(cli, "start_uploader", lambda args: started.append(args) or 0)

    cli._ensure_uploader_for_capture_queue(profile_root / "events")

    assert len(started) == 1
    assert Path(started[0].config_path) == profile_root / "config.json"
    assert Path(started[0].queue_path) == profile_root / "events"
    assert Path(started[0].pid_path) == profile_root / "uploader.pid"


def test_profile_capture_does_not_restart_a_running_uploader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_root = tmp_path / "profiles" / "dev"
    profile_root.mkdir(parents=True)
    (profile_root / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "_read_pid", lambda _path: 123)
    monkeypatch.setattr(cli, "_pid_is_running", lambda _pid: True)
    monkeypatch.setattr(
        cli,
        "start_uploader",
        lambda _args: pytest.fail("running uploader must not be restarted"),
    )

    cli._ensure_uploader_for_capture_queue(profile_root / "events")


def test_profile_capture_keeps_event_safe_when_restart_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_root = tmp_path / "profiles" / "dev"
    profile_root.mkdir(parents=True)
    (profile_root / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "_read_pid", lambda _path: None)
    monkeypatch.setattr(
        cli,
        "start_uploader",
        lambda _args: (_ for _ in ()).throw(RuntimeError("spawn failed")),
    )

    cli._ensure_uploader_for_capture_queue(profile_root / "events")

    assert "spawn failed" in capsys.readouterr().err


def test_start_uploader_reads_profile_config_without_inherited_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_root = tmp_path / "profiles" / "dev"
    config_path = profile_root / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "api_url": "https://api.config.test",
                "token": "current-config-token",
            }
        ),
        encoding="utf-8",
    )
    pid_path = profile_root / "uploader.pid"
    log_path = profile_root / "uploader.log"
    seen: dict[str, object] = {}

    monkeypatch.setenv("PROMTY_API_TOKEN", "stale-canonical-token")
    monkeypatch.setenv("PROMPTHUB_API_TOKEN", "stale-legacy-token")
    monkeypatch.setenv("PROMTY_API_URL", "https://stale-canonical.test")
    monkeypatch.setenv("PROMPTHUB_API_URL", "https://stale-legacy.test")
    monkeypatch.setattr(cli, "_read_pid", lambda _path: None)

    def fake_popen(command: list[str], **kwargs: object) -> SimpleNamespace:
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return SimpleNamespace(pid=321)

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    args = build_parser().parse_args(
        [
            "start-uploader",
            "--config-path",
            str(config_path),
            "--pid-path",
            str(pid_path),
            "--log-path",
            str(log_path),
            "--queue-path",
            str(profile_root / "events"),
        ]
    )

    assert args.func(args) == 0

    command = seen["command"]
    env = seen["env"]
    assert isinstance(command, list)
    assert isinstance(env, dict)
    assert command[command.index("--config-path") + 1] == str(config_path)
    assert "--token" not in command
    assert env["PROMTY_CONFIG_PATH"] == str(config_path)
    assert "PROMTY_API_TOKEN" not in env
    assert "PROMPTHUB_API_TOKEN" not in env
    assert "PROMTY_API_URL" not in env
    assert "PROMPTHUB_API_URL" not in env
    assert pid_path.read_text(encoding="utf-8") == "321\n"


def test_start_uploader_restart_stops_existing_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped: list[int] = []
    profile_root = tmp_path / "profiles" / "dev"
    profile_root.mkdir(parents=True)
    monkeypatch.setattr(cli, "_read_pid", lambda _path: 123)
    monkeypatch.setattr(cli, "_pid_is_running", lambda pid: pid == 123)
    monkeypatch.setattr(cli, "_stop_uploader_process", stopped.append)
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=456),
    )
    args = build_parser().parse_args(
        [
            "start-uploader",
            "--restart",
            "--config-path",
            str(profile_root / "config.json"),
            "--pid-path",
            str(profile_root / "uploader.pid"),
            "--log-path",
            str(profile_root / "uploader.log"),
        ]
    )

    assert args.func(args) == 0
    assert stopped == [123]
    assert (profile_root / "uploader.pid").read_text(encoding="utf-8") == "456\n"


def test_start_uploader_restart_reports_stop_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_root = tmp_path / "profiles" / "dev"
    profile_root.mkdir(parents=True)
    monkeypatch.setattr(cli, "_read_pid", lambda _path: 123)
    monkeypatch.setattr(cli, "_pid_is_running", lambda pid: pid == 123)

    def fail_to_stop(_pid: int) -> None:
        raise RuntimeError("Promty uploader did not stop in time")

    monkeypatch.setattr(cli, "_stop_uploader_process", fail_to_stop)
    args = build_parser().parse_args(
        [
            "start-uploader",
            "--restart",
            "--config-path",
            str(profile_root / "config.json"),
            "--pid-path",
            str(profile_root / "uploader.pid"),
            "--log-path",
            str(profile_root / "uploader.log"),
        ]
    )

    assert args.func(args) == 1
    assert "Promty uploader did not stop in time" in capsys.readouterr().err


def test_conflicting_event_isolated_without_blocking_valid_events(
    tmp_path: Path,
) -> None:
    queue = JSONLQueue(tmp_path / "events.jsonl")
    events = [
        {
            "id": f"event-{sequence}",
            "project_id": "project",
            "session_id": "session",
            "sequence": sequence,
        }
        for sequence in (1, 2, 3)
    ]
    queue.path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    class UploaderStub:
        def upload_events(self, batch: list[dict[str, object]]) -> list[str]:
            if any(event["id"] == "event-2" for event in batch):
                raise EventBatchConflict("Event sequence already exists")
            return [str(event["id"]) for event in batch]

    uploaded_count = cli._upload_event_batch(queue, UploaderStub(), events)  # type: ignore[arg-type]

    assert uploaded_count == 2
    assert queue.read_batch(10) == []
    conflict_records = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl.conflicts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [record["event"]["id"] for record in conflict_records] == ["event-2"]


def test_init_retirement_stops_only_verified_legacy_uploader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_root = tmp_path / ".prompthub"
    profile_root = legacy_root / "profiles" / "prod"
    profile_root.mkdir(parents=True)
    queue_path = profile_root / "events"
    queue_path.write_text('{"id":"event-1"}\n', encoding="utf-8")
    (profile_root / "uploader.pid").write_text("94591\n", encoding="utf-8")
    stopped: list[int] = []

    monkeypatch.setattr(cli, "LEGACY_DATA_ROOT", legacy_root)
    monkeypatch.setattr(cli, "_pid_is_running", lambda pid: pid == 94591)
    monkeypatch.setattr(
        cli,
        "_process_command",
        lambda _pid: (
            "python /tmp/runtime/cli.py upload --watch --interval 2 "
            f"--queue-path {queue_path}"
        ),
    )
    monkeypatch.setattr(cli, "_stop_uploader_process", stopped.append)

    assert cli._retire_legacy_uploader("prod")
    assert stopped == [94591]
    assert not queue_path.exists()
    assert (
        profile_root / "events.conflict-legacy-94591.jsonl"
    ).read_text(encoding="utf-8") == '{"id":"event-1"}\n'
    assert not (profile_root / "uploader.pid").exists()
    assert (profile_root / "uploader.pid.stopped-94591").exists()


def test_init_retirement_refuses_unrelated_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    legacy_root = tmp_path / ".prompthub"
    profile_root = legacy_root / "profiles" / "prod"
    profile_root.mkdir(parents=True)
    (profile_root / "uploader.pid").write_text("123\n", encoding="utf-8")
    stopped: list[int] = []

    monkeypatch.setattr(cli, "LEGACY_DATA_ROOT", legacy_root)
    monkeypatch.setattr(cli, "_pid_is_running", lambda _pid: True)
    monkeypatch.setattr(cli, "_process_command", lambda _pid: "python unrelated.py")
    monkeypatch.setattr(cli, "_stop_uploader_process", stopped.append)

    assert not cli._retire_legacy_uploader("prod")
    assert stopped == []
    assert "Refusing to stop pid 123" in capsys.readouterr().err


def test_runtime_launcher_uses_a_durable_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promty_home = tmp_path / "home with spaces" / ".promty"
    monkeypatch.setenv("PROMTY_HOME", str(promty_home))
    monkeypatch.delenv("PROMTY_PYTHON", raising=False)
    monkeypatch.delenv("PROMTY_HOOK_PYTHON", raising=False)

    launcher_path = install_runtime()
    launcher_text = launcher_path.read_text(encoding="utf-8")
    runtime_directories = [path for path in (promty_home / "runtime").iterdir() if path.is_dir()]

    assert launcher_path == promty_home / "bin" / ("promty.cmd" if os.name == "nt" else "promty")
    assert len(runtime_directories) == 1
    runtime_path = runtime_directories[0]
    assert (runtime_path / "cli.py").is_file()
    assert (runtime_path / "adapters" / "codex" / "hook.py").is_file()
    assert str(runtime_path / "cli.py") in launcher_text
    assert str(Path(sys.executable)) in launcher_text
    assert str(Path(cli.__file__).resolve()) not in launcher_text
    if os.name != "nt":
        assert launcher_path.stat().st_mode & 0o111

    command = (
        ["cmd", "/c", str(launcher_path), "--help"]
        if os.name == "nt"
        else [str(launcher_path), "--help"]
    )
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Promty local AI activity collector" in result.stdout
    assert install_runtime() == launcher_path


@pytest.mark.parametrize(
    ("tool", "settings_path", "specs"),
    (
        ("codex-cli", Path(".codex/hooks.json"), CODEX_HOOKS),
        ("claude-code", Path(".claude/settings.local.json"), CLAUDE_HOOKS),
    ),
)
def test_install_hooks_uses_durable_launcher(
    tool: str,
    settings_path: Path,
    specs: tuple[dict[str, object], ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repository"
    repo_root.mkdir()
    promty_home = tmp_path / "promty-home"
    monkeypatch.setenv("PROMTY_HOME", str(promty_home))
    monkeypatch.setenv("PROMTY_PYTHON", sys.executable)
    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    args = build_parser().parse_args(
        ["install-hooks", "--tool", tool, "--repo-root", str(repo_root)]
    )

    assert install_hooks(args) == 0

    config = json.loads((repo_root / settings_path).read_text(encoding="utf-8"))
    launcher_path = promty_home / "bin" / ("promty.cmd" if os.name == "nt" else "promty")
    for spec in specs:
        groups = config["hooks"][spec["event"]]
        commands = [hook["command"] for group in groups for hook in group["hooks"]]
        assert len(commands) == 1
        assert str(launcher_path) in commands[0]
        assert "collector/src/cli.py" not in commands[0]
        assert str(Path(cli.__file__).resolve()) not in commands[0]


def test_init_installs_codex_and_claude_hooks_when_explicitly_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repository"
    repo_root.mkdir()
    launcher_path = tmp_path / "promty-home" / "bin" / "promty"
    runtime_install_count = 0

    def fake_install_runtime() -> Path:
        nonlocal runtime_install_count
        runtime_install_count += 1
        return launcher_path

    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    monkeypatch.setattr(cli, "install_runtime", fake_install_runtime)
    args = build_parser().parse_args(
        [
            "init",
            "--tool",
            "all",
            "--repo-root",
            str(repo_root),
            "--skip-login",
            "--skip-uploader",
        ]
    )

    assert args.tool == "all"
    assert args.func(args) == 0
    assert runtime_install_count == 1
    assert args.func(args) == 0
    assert runtime_install_count == 2

    output = capsys.readouterr().out
    assert f"Repository scope: Promty hooks apply only to {repo_root}." in output
    assert "other repositories are not collected automatically" in output

    for tool, settings_path, specs in (
        ("codex-cli", Path(".codex/hooks.json"), CODEX_HOOKS),
        ("claude-code", Path(".claude/settings.local.json"), CLAUDE_HOOKS),
    ):
        config = json.loads((repo_root / settings_path).read_text(encoding="utf-8"))
        for spec in specs:
            groups = config["hooks"][spec["event"]]
            commands = [hook["command"] for group in groups for hook in group["hooks"]]
            assert len(commands) == 1
            assert str(launcher_path) in commands[0]
            assert f"--tool {tool}" in commands[0]


def test_init_defaults_to_codex_without_changing_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repository"
    repo_root.mkdir()
    monkeypatch.setenv("PROMTY_HOME", str(tmp_path / "promty-home"))
    monkeypatch.setenv("PROMTY_PYTHON", sys.executable)
    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    args = build_parser().parse_args(
        [
            "init",
            "--repo-root",
            str(repo_root),
            "--skip-login",
            "--skip-uploader",
        ]
    )

    assert args.tool == "codex-cli"
    assert args.func(args) == 0
    assert (repo_root / ".codex" / "hooks.json").is_file()
    assert not (repo_root / ".claude" / "settings.local.json").exists()
    assert "Claude Code settings will not be changed" in capsys.readouterr().out


def test_init_profiles_install_one_mirrored_hook_and_start_two_uploaders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed_hooks: list[argparse.Namespace] = []
    started_uploaders: list[argparse.Namespace] = []

    def fake_install_hooks(args: argparse.Namespace) -> int:
        installed_hooks.append(args)
        return 0

    def fake_start_uploader(args: argparse.Namespace) -> int:
        started_uploaders.append(args)
        return 0

    monkeypatch.setattr(cli, "install_hooks", fake_install_hooks)
    monkeypatch.setattr(cli, "start_uploader", fake_start_uploader)
    args = build_parser().parse_args(["init", "--profiles", "dev,prod", "--skip-login"])

    assert args.func(args) == 0

    assert len(installed_hooks) == 1
    assert installed_hooks[0].profiles == ("dev", "prod")
    assert installed_hooks[0].queue_path is None
    assert [Path(item.queue_path) for item in started_uploaders] == [
        Path.home() / ".promty" / "profiles" / "dev" / "events",
        Path.home() / ".promty" / "profiles" / "prod" / "events",
    ]
    assert [Path(item.pid_path) for item in started_uploaders] == [
        Path.home() / ".promty" / "profiles" / "dev" / "uploader.pid",
        Path.home() / ".promty" / "profiles" / "prod" / "uploader.pid",
    ]
    assert all(item.restart is True for item in started_uploaders)
    assert all(item.token is None for item in started_uploaders)


@pytest.mark.parametrize(
    ("tool", "expected_path", "unexpected_path"),
    (
        ("codex-cli", Path(".codex/hooks.json"), Path(".claude/settings.local.json")),
        ("claude-code", Path(".claude/settings.local.json"), Path(".codex/hooks.json")),
    ),
)
def test_init_keeps_explicit_single_tool_installation(
    tool: str,
    expected_path: Path,
    unexpected_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repository"
    repo_root.mkdir()
    monkeypatch.setenv("PROMTY_HOME", str(tmp_path / "promty-home"))
    monkeypatch.setenv("PROMTY_PYTHON", sys.executable)
    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    args = build_parser().parse_args(
        [
            "init",
            "--tool",
            tool,
            "--repo-root",
            str(repo_root),
            "--skip-login",
            "--skip-uploader",
        ]
    )

    assert args.func(args) == 0
    assert (repo_root / expected_path).is_file()
    assert not (repo_root / unexpected_path).exists()


def test_init_attempts_both_hooks_and_fails_on_partial_installation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repository"
    codex_hooks_path = repo_root / ".codex" / "hooks.json"
    codex_hooks_path.parent.mkdir(parents=True)
    codex_hooks_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("PROMTY_HOME", str(tmp_path / "promty-home"))
    monkeypatch.setenv("PROMTY_PYTHON", sys.executable)
    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    uploader_start_count = 0

    def fake_start_uploader(_: object) -> int:
        nonlocal uploader_start_count
        uploader_start_count += 1
        return 0

    monkeypatch.setattr(cli, "start_uploader", fake_start_uploader)
    args = build_parser().parse_args(
        [
            "init",
            "--tool",
            "all",
            "--repo-root",
            str(repo_root),
            "--skip-login",
        ]
    )

    assert args.func(args) == 1
    assert uploader_start_count == 1
    output = capsys.readouterr()
    assert "codex-cli hook installation failed" in output.err
    assert "Promty hook installation incomplete" in output.err
    assert "Promty init incomplete" in output.err
    assert "Promty init complete" not in output.out
    assert (repo_root / ".claude" / "settings.local.json").is_file()


def test_uninstall_hooks_removes_only_selected_promty_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repository"
    claude_settings_path = repo_root / ".claude" / "settings.local.json"
    codex_hooks_path = repo_root / ".codex" / "hooks.json"
    claude_settings_path.parent.mkdir(parents=True)
    codex_hooks_path.parent.mkdir(parents=True)
    claude_settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "command": "promty capture --tool claude-code",
                                    "timeout": 5,
                                    "type": "command",
                                }
                            ]
                        }
                    ],
                    "UserPromptSubmit": [
                        {
                            "matcher": "",
                            "hooks": [
                                {
                                    "command": "promty capture --tool claude-code",
                                    "timeout": 5,
                                    "type": "command",
                                },
                                {
                                    "command": "./scripts/custom-hook",
                                    "timeout": 3,
                                    "type": "command",
                                },
                            ],
                        }
                    ],
                },
                "permissions": {"allow": ["Bash(git status:*)"]},
            }
        ),
        encoding="utf-8",
    )
    codex_original = {"hooks": {"Stop": [{"hooks": [{"command": "codex-custom"}]}]}}
    codex_hooks_path.write_text(json.dumps(codex_original), encoding="utf-8")
    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    args = build_parser().parse_args(
        ["uninstall-hooks", "--tool", "claude-code", "--repo-root", str(repo_root)]
    )

    assert args.func(args) == 0
    updated = json.loads(claude_settings_path.read_text(encoding="utf-8"))
    assert "SessionStart" not in updated["hooks"]
    assert updated["hooks"]["UserPromptSubmit"] == [
        {
            "hooks": [
                {
                    "command": "./scripts/custom-hook",
                    "timeout": 3,
                    "type": "command",
                }
            ],
            "matcher": "",
        }
    ]
    assert updated["permissions"] == {"allow": ["Bash(git status:*)"]}
    assert json.loads(codex_hooks_path.read_text(encoding="utf-8")) == codex_original
    output = capsys.readouterr().out
    assert "Claude Code: removed 2 Promty hooks" in output
    assert "Codex CLI settings were not changed" in output


def test_doctor_can_check_both_hook_integrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repository"
    repo_root.mkdir()
    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    install_args = build_parser().parse_args(
        [
            "install-hooks",
            "--tool",
            "all",
            "--hook-command",
            "promty",
            "--repo-root",
            str(repo_root),
        ]
    )
    assert install_hooks(install_args) == 0
    capsys.readouterr()

    monkeypatch.setattr(cli, "read_config", lambda _: {"auth": {}})
    monkeypatch.setattr(cli, "resolve_token", lambda *_: "collector-token")
    monkeypatch.setattr(cli, "_queue_status", lambda _: (True, "empty"))
    monkeypatch.setattr(cli, "_queue_conflict_status", lambda _: (True, "none"))
    monkeypatch.setattr(cli, "_health_status", lambda *_: (True, "ok"))
    monkeypatch.setattr(cli, "_ingest_auth_status", lambda *_: (True, "authenticated"))
    monkeypatch.setattr(cli, "_read_pid", lambda _: 123)
    monkeypatch.setattr(cli, "_pid_is_running", lambda _: True)
    doctor_args = build_parser().parse_args(
        ["doctor", "--tool", "all", "--repo-root", str(repo_root)]
    )

    assert doctor_args.func(doctor_args) == 0
    output = capsys.readouterr().out
    assert "hooks/codex-cli: ok" in output
    assert "hooks/claude-code: ok" in output


def test_install_hooks_migrates_legacy_prompthub_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repository"
    hooks_path = repo_root / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "python3 collector/src/cli.py capture --tool codex-cli"
                                    ),
                                    "statusMessage": "Capturing Promty event",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROMTY_HOME", str(tmp_path / ".promty"))
    monkeypatch.setenv("PROMTY_PYTHON", sys.executable)
    monkeypatch.setattr(cli, "_git_root", lambda _: repo_root)
    args = build_parser().parse_args(
        ["install-hooks", "--tool", "codex-cli", "--repo-root", str(repo_root)]
    )

    assert install_hooks(args) == 0

    config = json.loads(hooks_path.read_text(encoding="utf-8"))
    groups = config["hooks"]["UserPromptSubmit"]
    hooks = [hook for group in groups for hook in group["hooks"]]
    assert len(hooks) == 1
    assert ".promty" in hooks[0]["command"]
    assert hooks[0]["statusMessage"] == "Capturing Promty event"


def test_user_facing_brand_is_promty() -> None:
    assert "PromptHub" not in LOGIN_CALLBACK_HTML
    assert "Promty" in LOGIN_CALLBACK_HTML
    assert all("PromptHub" not in spec.get("statusMessage", "") for spec in CODEX_HOOKS)
    assert all("Promty" in spec.get("statusMessage", "") for spec in CODEX_HOOKS)
