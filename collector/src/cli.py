from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import secrets
import signal
import shlex
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from urllib import error, parse, request
import webbrowser

from environment import migrate_legacy_data_root

from adapters import normalize_collector_event, should_ignore_collector_event
from change_tracking import ChangeBaselineStore, derive_prompt_lineage, detect_changes
from config import (
    DEFAULT_UPLOADER_LOG_PATH,
    DEFAULT_UPLOADER_PID_PATH,
    read_config,
    resolve_api_url,
    resolve_app_url,
    resolve_token,
    write_config,
)
from context_client import fetch_project_context, project_id_for_context, render_project_context
from events import (
    SUPPORTED_EVENT_TYPES,
    TOOL_ALIASES,
    BaseEvent,
    FilesChangedPayload,
    PromptSubmittedPayload,
    SupportedTool,
    normalize_event_type,
    normalize_tool,
)
from file_lock import locked_file
from payloads import (
    PROJECT_CONTEXT_KEYS,
    SESSION_ID_KEYS,
    TURN_ID_KEYS,
    WORKSPACE_KEYS,
    get_first_string,
    get_first_value,
)
from runtime_install import install_runtime, launcher_path, quote_command_path
from mcp_server import PromtyMCPServer, run_mcp_server
from sequence import SequenceStore
from session_index import SessionIndex
from secure_storage import ensure_private_parent, open_private_text, write_private_text_atomic
from uploader.client import PromtyUploader
from uploader.queue import JSONLQueue
from updater import auto_update
from version import COLLECTOR_VERSION

InstallTarget = Literal["all", "claude-code", "codex-cli"]
Profile = Literal["dev", "prod"]
INSTALL_TOOLS: tuple[SupportedTool, ...] = ("codex-cli", "claude-code")
INSTALL_TOOL_LABELS: dict[SupportedTool, str] = {
    "codex-cli": "Codex CLI",
    "claude-code": "Claude Code",
}
INSTALL_TOOL_PATHS: dict[SupportedTool, Path] = {
    "codex-cli": Path(".codex/hooks.json"),
    "claude-code": Path(".claude/settings.local.json"),
}
INSTALL_TOOL_ALIASES: dict[str, InstallTarget] = {
    "all": "all",
    "claude": "claude-code",
    "claude-code": "claude-code",
    "codex": "codex-cli",
    "codex-cli": "codex-cli",
}
PROFILE_URLS: dict[Profile, tuple[str, str]] = {
    "dev": ("http://127.0.0.1:5173", "http://127.0.0.1:8011"),
    "prod": ("https://promty.org", "https://api.promty.org"),
}
PROFILE_NAMES: tuple[Profile, ...] = tuple(PROFILE_URLS)
DEFAULT_INIT_PROFILE: Profile = "prod"
AUTO_UPDATE_INTERVAL_SECONDS = 6 * 60 * 60
HEARTBEAT_INTERVAL_SECONDS = 60
UPLOADER_STOP_TIMEOUT_SECONDS = 5.0
CODEX_HOOKS: tuple[dict[str, Any], ...] = (
    {
        "event": "UserPromptSubmit",
        "subcommand": "capture",
        "timeout": 5,
        "statusMessage": "Capturing Promty event",
    },
    {
        "event": "Stop",
        "subcommand": "capture-changes",
        "timeout": 10,
        "statusMessage": "Capturing Promty AI activity",
    },
)
CLAUDE_HOOKS: tuple[dict[str, Any], ...] = (
    {
        "event": "SessionStart",
        "subcommand": "capture --event-type SessionStarted",
        "timeout": 5,
    },
    {
        "event": "UserPromptSubmit",
        "subcommand": "capture",
        "timeout": 5,
    },
    {
        "event": "Stop",
        "subcommand": "capture-changes",
        "timeout": 10,
    },
    {
        "event": "SessionEnd",
        "subcommand": "capture --event-type SessionEnded",
        "timeout": 5,
    },
)
LOGIN_CALLBACK_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Promty connected</title>
    <style>
      body {
        margin: 0;
        background: #09090b;
        color: #fafafa;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        display: grid;
        min-height: 100vh;
        place-items: center;
      }
      main {
        width: min(440px, calc(100vw - 40px));
        border: 1px solid #2a2f38;
        border-radius: 8px;
        padding: 24px;
        background: #111113;
      }
      h1 {
        margin: 0 0 8px;
        font-size: 22px;
      }
      p {
        margin: 0;
        color: #c4cad4;
        line-height: 1.5;
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Promty connected</h1>
      <p>You can close this window and return to your terminal.</p>
    </main>
  </body>
</html>
"""


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("Expected hook JSON on stdin")

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Expected hook JSON object on stdin")
    return payload


def capture_raw(args: argparse.Namespace) -> int:
    payload = _read_stdin_json()
    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
    return 0


def _has_project_context(payload: dict[str, Any]) -> bool:
    if os.environ.get("PROMTY_PROJECT_ID"):
        return True
    return get_first_string(payload, PROJECT_CONTEXT_KEYS) is not None


def _normalize_required_tool(args: argparse.Namespace) -> SupportedTool:
    tool = args.source or args.tool
    if not tool:
        raise ValueError("Expected --tool")
    return normalize_tool(tool)


def _normalize_install_target(args: argparse.Namespace) -> InstallTarget:
    tool = getattr(args, "source", None) or getattr(args, "tool", None)
    if not tool:
        raise ValueError("Expected --tool")
    try:
        return INSTALL_TOOL_ALIASES[tool]
    except KeyError as exc:
        raise ValueError(f"Unsupported hook integration: {tool}") from exc


def _tools_for_install_target(target: InstallTarget) -> tuple[SupportedTool, ...]:
    if target == "all":
        return INSTALL_TOOLS
    return (target,)


def _apply_profile_defaults(args: argparse.Namespace) -> None:
    if getattr(args, "profiles", None):
        return
    profile = getattr(args, "profile", None)
    if not profile:
        return

    profile_root = Path("~/.promty/profiles").expanduser() / profile
    app_url, api_url = PROFILE_URLS[profile]
    defaults = {
        "app_url": app_url,
        "api_url": api_url,
        "config_path": str(profile_root / "config.json"),
        "queue_path": str(profile_root / "events"),
        "pid_path": str(profile_root / "uploader.pid"),
        "log_path": str(profile_root / "uploader.log"),
    }
    for name, value in defaults.items():
        if hasattr(args, name) and getattr(args, name) is None:
            setattr(args, name, value)


def _parse_profiles(value: str) -> tuple[Profile, ...]:
    profiles: list[Profile] = []
    for raw_profile in value.split(","):
        profile = raw_profile.strip()
        if not profile:
            continue
        if profile not in PROFILE_URLS:
            choices = ", ".join(PROFILE_NAMES)
            raise argparse.ArgumentTypeError(
                f"unsupported profile '{profile}' (choose from {choices})"
            )
        if profile not in profiles:
            profiles.append(profile)  # type: ignore[arg-type]
    if not profiles:
        raise argparse.ArgumentTypeError("at least one profile is required")
    return tuple(profiles)


def _selected_profiles(args: argparse.Namespace) -> tuple[Profile, ...]:
    profiles = getattr(args, "profiles", None)
    if profiles:
        return tuple(profiles)
    profile = getattr(args, "profile", None)
    return (profile,) if profile else ()


def _profile_args(args: argparse.Namespace, profile: Profile) -> argparse.Namespace:
    profile_args = argparse.Namespace(**vars(args))
    profile_args.profile = profile
    profile_args.profiles = None
    for name in (
        "app_url",
        "api_url",
        "config_path",
        "queue_path",
        "pid_path",
        "log_path",
    ):
        if hasattr(profile_args, name):
            setattr(profile_args, name, None)
    _apply_profile_defaults(profile_args)
    return profile_args


def _validate_multi_profile_options(args: argparse.Namespace) -> None:
    conflicting = [
        name.replace("_", "-")
        for name in (
            "app_url",
            "api_url",
            "config_path",
            "queue_path",
            "pid_path",
            "log_path",
            "token",
        )
        if getattr(args, name, None) is not None
    ]
    if conflicting:
        options = ", ".join(f"--{name}" for name in conflicting)
        raise ValueError(
            f"{options} cannot be combined with --profiles; configure each profile separately first"
        )


def _queue_paths_for_profiles(profiles: Sequence[Profile]) -> list[str]:
    paths: list[str] = []
    for profile in profiles:
        profile_args = _profile_args(argparse.Namespace(queue_path=None), profile)
        paths.append(profile_args.queue_path)
    return paths


def _apply_known_session(
    *,
    index: SessionIndex,
    tool: SupportedTool,
    external_session_id: str | None,
    event: BaseEvent,
    raw_payload: dict[str, Any],
) -> None:
    if external_session_id is None or _has_project_context(raw_payload):
        return

    record = index.lookup(tool, external_session_id)
    if not record:
        return

    project_id = record.get("project_id")
    session_id = record.get("session_id")
    if isinstance(project_id, str) and isinstance(session_id, str):
        event.project_id = project_id
        event.session_id = session_id


def _remember_session(
    *,
    index: SessionIndex,
    tool: SupportedTool,
    external_session_id: str | None,
    event: BaseEvent,
    raw_payload: dict[str, Any],
) -> None:
    if external_session_id is None:
        return
    index.observe(
        tool=tool,
        external_session_id=external_session_id,
        event=event,
        cwd=get_first_string(raw_payload, WORKSPACE_KEYS),
    )


def _capture_queue_paths(args: argparse.Namespace) -> list[str | Path | None]:
    paths: list[str | Path | None] = [getattr(args, "queue_path", None)]
    paths.extend(getattr(args, "mirror_queue_paths", None) or [])
    unique_paths: list[str | Path | None] = []
    resolved: set[str] = set()
    for path in paths:
        queue = JSONLQueue(path)
        key = str(queue.path.resolve())
        if key in resolved:
            continue
        resolved.add(key)
        unique_paths.append(path)
    return unique_paths


def _push_captured_event(args: argparse.Namespace, event: BaseEvent) -> None:
    errors: list[str] = []
    queue_paths = _capture_queue_paths(args)
    for path in queue_paths:
        queue = JSONLQueue(path)
        try:
            queue.push(event)
        except Exception as exc:
            errors.append(f"{queue.path}: {exc}")
    if errors:
        raise RuntimeError("Failed to persist one or more Promty queues: " + "; ".join(errors))
    for path in queue_paths:
        _ensure_uploader_for_capture_queue(path)


def _ensure_uploader_for_capture_queue(queue_path: str | Path | None) -> None:
    """Restart a profile uploader after reboot without risking event capture."""
    if queue_path is None:
        return
    queue = JSONLQueue(queue_path)
    profile_root = queue.path.parent
    config_path = profile_root / "config.json"
    if queue.path.name != "events" or not config_path.is_file():
        return

    pid_path = profile_root / "uploader.pid"
    pid = _read_pid(pid_path)
    if pid is not None and _pid_is_running(pid):
        return

    try:
        start_uploader(
            argparse.Namespace(
                api_url=None,
                config_path=str(config_path),
                interval=2,
                log_path=str(profile_root / "uploader.log"),
                no_auto_update=True,
                pid_path=str(pid_path),
                queue_path=str(queue.path),
                token=None,
            )
        )
    except Exception as exc:
        print(
            f"Promty uploader could not restart for {queue.path}: {exc}",
            file=sys.stderr,
        )


def capture(args: argparse.Namespace) -> int:
    payload = _read_stdin_json()
    normalized_tool = _normalize_required_tool(args)
    event_type = normalize_event_type(args.event_type) if args.event_type else None
    session_index = SessionIndex(args.session_index_path)
    external_session_id = get_first_string(payload, SESSION_ID_KEYS)
    if should_ignore_collector_event(normalized_tool, payload, event_type):
        if external_session_id is not None:
            session_index.ignore(
                normalized_tool,
                external_session_id,
                cwd=get_first_string(payload, WORKSPACE_KEYS),
            )
        return 0

    event = normalize_collector_event(normalized_tool, payload, event_type)
    _apply_known_session(
        index=session_index,
        tool=normalized_tool,
        external_session_id=external_session_id,
        event=event,
        raw_payload=payload,
    )
    baseline_store = ChangeBaselineStore(args.change_baseline_path)
    previous_baseline = None
    prompt_lineage: tuple[str | None, str | None] | None = None
    if event.event_type == "PromptSubmitted":
        previous_baseline = baseline_store.find_latest(
            tool=normalized_tool,
            external_session_id=external_session_id,
            cwd=get_first_string(payload, WORKSPACE_KEYS) or os.getcwd(),
        )
        prompt_lineage = derive_prompt_lineage(event.id, previous_baseline)
        if isinstance(event.payload, PromptSubmittedPayload):
            event.payload.submission_context = (
                "during_output" if previous_baseline is not None else "idle"
            )
            event.payload.delivery_mode = "unknown"
            (
                event.payload.continuation_of,
                event.payload.root_prompt_event_id,
            ) = prompt_lineage
    SequenceStore(args.sequence_path).assign(event)
    _push_captured_event(args, event)
    _remember_session(
        index=session_index,
        tool=normalized_tool,
        external_session_id=external_session_id,
        event=event,
        raw_payload=payload,
    )
    if event.event_type == "PromptSubmitted":
        baseline_store.observe_prompt(
            tool=normalized_tool,
            event=event,
            raw_payload=payload,
            external_session_id=external_session_id,
            cwd=get_first_string(payload, WORKSPACE_KEYS) or os.getcwd(),
            previous_baseline=previous_baseline,
            lineage=prompt_lineage,
        )
    return 0


def capture_changes(args: argparse.Namespace) -> int:
    payload = _read_stdin_json()
    normalized_tool = _normalize_required_tool(args)
    external_session_id = get_first_string(payload, SESSION_ID_KEYS)
    cwd = get_first_string(payload, WORKSPACE_KEYS) or os.getcwd()
    session_index = SessionIndex(args.session_index_path)
    if external_session_id and session_index.is_ignored(
        normalized_tool,
        external_session_id,
    ):
        return 0
    sequence_store = SequenceStore(args.sequence_path)

    response_event = normalize_collector_event(
        normalized_tool,
        payload,
        "ResponseReceived",
    )
    _apply_known_session(
        index=session_index,
        tool=normalized_tool,
        external_session_id=external_session_id,
        event=response_event,
        raw_payload=payload,
    )
    sequence_store.assign(response_event)
    _push_captured_event(args, response_event)
    _remember_session(
        index=session_index,
        tool=normalized_tool,
        external_session_id=external_session_id,
        event=response_event,
        raw_payload=payload,
    )

    store = ChangeBaselineStore(args.change_baseline_path)
    baseline = store.find_for_turn(
        tool=normalized_tool,
        external_session_id=external_session_id,
        cwd=cwd,
        turn_id=get_first_value(payload, TURN_ID_KEYS),
    )
    if not baseline:
        return 0

    result = detect_changes(baseline, cwd)
    store.mark_consumed_with_ancestors(str(baseline["id"]))
    if result is None:
        return 0

    event = BaseEvent(
        tool=normalized_tool,
        event_type="FilesChanged",
        payload=FilesChangedPayload(**result.payload),
        project_id=str(baseline["project_id"]),
        session_id=str(baseline["session_id"]),
        sequence=0,
    )
    sequence_store.assign(event)
    _push_captured_event(args, event)
    return 0


def _upload_queued_events(args: argparse.Namespace) -> int:
    queue = JSONLQueue(args.queue_path)
    events = queue.read_batch(args.limit)
    if not events:
        return 0

    uploader = PromtyUploader(
        api_url=resolve_api_url(args.api_url, args.config_path),
        token=resolve_token(args.token, args.config_path),
        timeout=args.timeout,
    )
    uploaded_ids = uploader.upload_events(events)
    queue.ack(set(uploaded_ids))
    return len(uploaded_ids)


def _send_heartbeat(args: argparse.Namespace) -> None:
    PromtyUploader(
        api_url=resolve_api_url(args.api_url, args.config_path),
        token=resolve_token(args.token, args.config_path),
        timeout=args.timeout,
    ).heartbeat()


def upload(args: argparse.Namespace) -> int:
    api_url = resolve_api_url(args.api_url, args.config_path)
    if not args.watch:
        uploaded_count = _upload_queued_events(args)
        if uploaded_count:
            print(f"Uploaded {uploaded_count} events")
        else:
            print("No events queued")
        return 0

    interval = max(args.interval, 0.25)
    print(f"Watching queue {JSONLQueue(args.queue_path).path} -> {api_url} every {interval:g}s")
    next_update_check = 0.0
    next_heartbeat = 0.0
    while True:
        try:
            now = time.monotonic()
            if not args.no_auto_update and now >= next_update_check:
                next_update_check = now + AUTO_UPDATE_INTERVAL_SECONDS
                updated_version = auto_update()
                if updated_version:
                    print(
                        f"Promty updated {COLLECTOR_VERSION} -> {updated_version}; restarting",
                        flush=True,
                    )
                    launcher = launcher_path()
                    os.execv(str(launcher), [str(launcher), *sys.argv[1:]])
            if now >= next_heartbeat:
                next_heartbeat = now + HEARTBEAT_INTERVAL_SECONDS
                _send_heartbeat(args)
            uploaded_count = _upload_queued_events(args)
            if uploaded_count:
                print(f"Uploaded {uploaded_count} events", flush=True)
        except KeyboardInterrupt:
            print("Stopped uploader watch")
            return 0
        except Exception as exc:
            print(f"Upload failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(interval)

    return 0


class _LoginCallbackHandler(BaseHTTPRequestHandler):
    server: "_LoginCallbackServer"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = parse.urlparse(self.path)
        query = parse.parse_qs(parsed.query)
        state = query.get("state", [""])[0]
        token = query.get("token", query.get("access_token", [""]))[0]
        api_url = query.get("api_url", [""])[0]
        username = query.get("username", [""])[0]
        error_message = query.get("error", [""])[0]

        if parsed.path != "/callback":
            self.send_error(404)
            return

        if not secrets.compare_digest(state, self.server.expected_state):
            self.server.error_message = "Login callback state did not match"
            self.send_error(400)
            return

        if error_message:
            self.server.error_message = error_message
            self.send_error(400)
            return

        if not token:
            self.server.error_message = "Login callback did not include a token"
            self.send_error(400)
            return

        self.server.result = {
            "token": token,
            "api_url": api_url or self.server.default_api_url,
            "username": username or None,
        }
        body = LOGIN_CALLBACK_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _LoginCallbackServer(HTTPServer):
    expected_state: str
    default_api_url: str
    result: dict[str, Any] | None
    error_message: str | None


def _build_login_url(
    *,
    app_url: str,
    api_url: str,
    callback_url: str,
    state: str,
) -> str:
    query = parse.urlencode(
        {
            "source": "collector",
            "provider": "github",
            "state": state,
            "redirect_uri": callback_url,
            "api_url": api_url,
        }
    )
    return f"{app_url.rstrip('/')}/cli/login?{query}"


def _wait_for_login_callback(
    *,
    app_url: str,
    api_url: str,
    callback_port: int,
    timeout: float,
    open_browser: bool,
) -> dict[str, Any]:
    state = secrets.token_urlsafe(32)
    with _LoginCallbackServer(
        ("127.0.0.1", callback_port),
        _LoginCallbackHandler,
    ) as server:
        server.expected_state = state
        server.default_api_url = api_url
        server.result = None
        server.error_message = None
        server.timeout = 0.5
        actual_port = server.server_address[1]
        callback_url = f"http://127.0.0.1:{actual_port}/callback"
        login_url = _build_login_url(
            app_url=app_url,
            api_url=api_url,
            callback_url=callback_url,
            state=state,
        )

        print(f"Open Promty login: {login_url}", flush=True)
        if open_browser:
            webbrowser.open(login_url)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and server.result is None:
            server.handle_request()
            if server.error_message:
                raise RuntimeError(server.error_message)

        if server.result is None:
            raise TimeoutError("Timed out waiting for Promty login callback")
        return server.result


def login(args: argparse.Namespace) -> int:
    app_url = resolve_app_url(args.app_url, args.config_path)
    api_url = resolve_api_url(args.api_url, args.config_path)
    if args.token:
        auth = {
            "provider": "github",
            "username": args.username,
            "logged_in_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        write_config(
            {
                "app_url": app_url,
                "api_url": api_url,
                "token": args.token,
                "auth": auth,
            },
            args.config_path,
        )
        print(f"Promty login saved for {api_url}")
        return 0

    callback = _wait_for_login_callback(
        app_url=app_url,
        api_url=api_url,
        callback_port=args.callback_port,
        timeout=args.timeout,
        open_browser=not args.no_browser,
    )
    username = callback.get("username")
    auth = {
        "provider": "github",
        "username": username,
        "logged_in_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_config(
        {
            "app_url": app_url,
            "api_url": callback.get("api_url") or api_url,
            "token": callback["token"],
            "auth": auth,
        },
        args.config_path,
    )
    suffix = f" as {username}" if username else ""
    print(f"Promty login saved{suffix}")
    return 0


def _git_root(path: str | Path | None = None) -> Path:
    start = Path(path or os.getcwd()).expanduser()
    if not start.exists():
        raise FileNotFoundError(f"Path does not exist: {start}")
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Not inside a git repository: {start}")
    return Path(result.stdout.strip()).resolve()


def _read_hooks_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected hooks object in {path}")
    return payload


def _is_promty_hook(hook: dict[str, Any], subcommand: str) -> bool:
    command = str(hook.get("command", ""))
    status_message = str(hook.get("statusMessage", ""))
    return (
        hook.get("type") == "command"
        and subcommand in command
        and (
            "promty" in command.lower()
            or "prompthub" in command.lower()
            or "collector/src/cli.py" in command
            or "promty" in status_message.lower()
            or "prompthub" in status_message.lower()
        )
    )


def _is_any_promty_hook(hook: dict[str, Any]) -> bool:
    command = str(hook.get("command", ""))
    status_message = str(hook.get("statusMessage", ""))
    return hook.get("type") == "command" and (
        "promty" in command.lower()
        or "prompthub" in command.lower()
        or "collector/src/cli.py" in command
        or "promty" in status_message.lower()
        or "prompthub" in status_message.lower()
    )


def _upsert_command_hook(
    config: dict[str, Any],
    *,
    event: str,
    hook: dict[str, Any],
    subcommand: str,
) -> str:
    hooks_by_event = config.setdefault("hooks", {})
    groups = hooks_by_event.setdefault(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"Expected hooks.{event} to be a list")

    for group in groups:
        if not isinstance(group, dict):
            continue
        group_hooks = group.get("hooks")
        if not isinstance(group_hooks, list):
            continue
        for existing in group_hooks:
            if not isinstance(existing, dict):
                continue
            if existing.get("command") == hook["command"] or _is_promty_hook(
                existing,
                subcommand,
            ):
                existing.update(hook)
                return "updated"

    groups.append({"hooks": [hook]})
    return "installed"


def _write_hooks_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def _remove_promty_hooks(path: Path) -> int:
    if not path.exists():
        return 0

    config = _read_hooks_config(path)
    hooks_by_event = config.get("hooks", {})
    removed = 0
    empty_events: list[str] = []

    for event, groups in hooks_by_event.items():
        if not isinstance(groups, list):
            continue
        remaining_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                remaining_groups.append(group)
                continue
            group_hooks = group.get("hooks")
            if not isinstance(group_hooks, list):
                remaining_groups.append(group)
                continue
            remaining_hooks = [
                hook
                for hook in group_hooks
                if not (isinstance(hook, dict) and _is_any_promty_hook(hook))
            ]
            removed += len(group_hooks) - len(remaining_hooks)
            if remaining_hooks:
                remaining_groups.append({**group, "hooks": remaining_hooks})
        if remaining_groups:
            hooks_by_event[event] = remaining_groups
        else:
            empty_events.append(event)

    for event in empty_events:
        hooks_by_event.pop(event, None)

    if removed:
        _write_hooks_config(path, config)
    return removed


def _install_codex_hooks(
    *,
    command_prefix: str,
    normalized_tool: SupportedTool,
    repo_root: Path,
    queue_paths: Sequence[str] = (),
) -> int:
    hooks_path = repo_root / ".codex" / "hooks.json"
    config = _read_hooks_config(hooks_path)
    results: list[str] = []

    for spec in CODEX_HOOKS:
        command = f"{command_prefix} {spec['subcommand']} --tool {normalized_tool}"
        if queue_paths:
            command += " --queue-path " + shlex.quote(str(Path(queue_paths[0]).expanduser()))
            for mirror_queue_path in queue_paths[1:]:
                command += " --mirror-queue-path " + shlex.quote(
                    str(Path(mirror_queue_path).expanduser())
                )
        hook = {
            "type": "command",
            "command": command,
            "timeout": spec["timeout"],
            "statusMessage": spec["statusMessage"],
        }
        result = _upsert_command_hook(
            config,
            event=spec["event"],
            hook=hook,
            subcommand=spec["subcommand"],
        )
        results.append(f"{spec['event']}: {result}")

    _write_hooks_config(hooks_path, config)
    print(f"Codex hooks ready: {hooks_path}")
    for result in results:
        print(f"- {result}")
    print("Open Codex /hooks and trust this repository's Promty hooks.")
    return 0


def _install_claude_hooks(
    *,
    command_prefix: str,
    normalized_tool: SupportedTool,
    repo_root: Path,
    queue_paths: Sequence[str] = (),
) -> int:
    settings_path = repo_root / ".claude" / "settings.local.json"
    config = _read_hooks_config(settings_path)
    results: list[str] = []

    for spec in CLAUDE_HOOKS:
        command = f"{command_prefix} {spec['subcommand']} --tool {normalized_tool}"
        if queue_paths:
            command += " --queue-path " + shlex.quote(str(Path(queue_paths[0]).expanduser()))
            for mirror_queue_path in queue_paths[1:]:
                command += " --mirror-queue-path " + shlex.quote(
                    str(Path(mirror_queue_path).expanduser())
                )
        hook = {
            "type": "command",
            "command": command,
            "timeout": spec["timeout"],
        }
        result = _upsert_command_hook(
            config,
            event=spec["event"],
            hook=hook,
            subcommand=spec["subcommand"],
        )
        results.append(f"{spec['event']}: {result}")

    _write_hooks_config(settings_path, config)
    print(f"Claude hooks ready: {settings_path}")
    for result in results:
        print(f"- {result}")
    print("Claude Code will run these Promty hooks from this repository.")
    return 0


def install_hooks(args: argparse.Namespace) -> int:
    target = _normalize_install_target(args)
    repo_root = _git_root(args.repo_root)
    selected_tools = _tools_for_install_target(target)
    selected_labels = ", ".join(INSTALL_TOOL_LABELS[tool] for tool in selected_tools)
    integration_label = "integration" if len(selected_tools) == 1 else "integrations"
    print(f"Selected Promty {integration_label}: {selected_labels}")
    print(f"Repository: {repo_root}")
    if target != "all":
        other_tool = next(tool for tool in INSTALL_TOOLS if tool not in selected_tools)
        print(f"{INSTALL_TOOL_LABELS[other_tool]} settings will not be changed.")
    if args.hook_command:
        command_prefix = args.hook_command
    else:
        launcher_path = install_runtime()
        command_prefix = quote_command_path(launcher_path)
        print(f"Promty runtime ready: {launcher_path}")

    profiles = _selected_profiles(args)
    if len(profiles) > 1:
        _validate_multi_profile_options(args)
        queue_paths = _queue_paths_for_profiles(profiles)
    elif profiles and getattr(args, "queue_path", None) is None:
        queue_paths = _queue_paths_for_profiles(profiles)
    else:
        queue_path = getattr(args, "queue_path", None)
        queue_paths = [queue_path] if queue_path else []

    failures: list[tuple[SupportedTool, Exception]] = []
    for normalized_tool in selected_tools:
        try:
            if normalized_tool == "codex-cli":
                _install_codex_hooks(
                    command_prefix=command_prefix,
                    normalized_tool=normalized_tool,
                    repo_root=repo_root,
                    queue_paths=queue_paths,
                )
            elif normalized_tool == "claude-code":
                _install_claude_hooks(
                    command_prefix=command_prefix,
                    normalized_tool=normalized_tool,
                    repo_root=repo_root,
                    queue_paths=queue_paths,
                )
            else:
                raise AssertionError(f"Unexpected hook tool: {normalized_tool}")
        except Exception as exc:
            failures.append((normalized_tool, exc))
            print(f"{normalized_tool} hook installation failed: {exc}", file=sys.stderr)

    if failures:
        print("Promty hook installation incomplete", file=sys.stderr)
        return 1
    print(f"Repository scope: Promty hooks apply only to {repo_root}.")
    print(
        "Run the same init command from each additional repository; "
        "other repositories are not collected automatically."
    )
    return 0


def uninstall_hooks(args: argparse.Namespace) -> int:
    target = _normalize_install_target(args)
    repo_root = _git_root(args.repo_root)
    selected_tools = _tools_for_install_target(target)
    print(f"Repository: {repo_root}")

    for tool in selected_tools:
        settings_path = repo_root / INSTALL_TOOL_PATHS[tool]
        removed = _remove_promty_hooks(settings_path)
        if removed:
            print(
                f"{INSTALL_TOOL_LABELS[tool]}: removed {removed} Promty hook"
                f"{'s' if removed != 1 else ''} from {settings_path}"
            )
        else:
            print(f"{INSTALL_TOOL_LABELS[tool]}: no Promty hooks found; no changes made")

    if target != "all":
        other_tool = next(tool for tool in INSTALL_TOOLS if tool not in selected_tools)
        print(f"{INSTALL_TOOL_LABELS[other_tool]} settings were not changed.")
    return 0


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _stop_uploader_process(
    pid: int,
    *,
    timeout: float = UPLOADER_STOP_TIMEOUT_SECONDS,
) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise RuntimeError(f"Unable to stop Promty uploader pid {pid}: {exc}") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return
        time.sleep(0.05)
    raise RuntimeError(f"Promty uploader pid {pid} did not stop within {timeout:g}s")


def _read_pid(path: Path) -> int | None:
    try:
        with open_private_text(path, "r") as file:
            return int(file.read().strip())
    except (OSError, ValueError):
        return None


def start_uploader(args: argparse.Namespace) -> int:
    pid_path = Path(args.pid_path).expanduser() if args.pid_path else DEFAULT_UPLOADER_PID_PATH
    log_path = Path(args.log_path).expanduser() if args.log_path else DEFAULT_UPLOADER_LOG_PATH
    config_path = (
        str(Path(args.config_path).expanduser()) if getattr(args, "config_path", None) else None
    )
    restart = bool(getattr(args, "restart", False))
    with locked_file(pid_path.with_name(f"{pid_path.name}.start.lock")):
        existing_pid = _read_pid(pid_path)
        if existing_pid is not None and _pid_is_running(existing_pid):
            if not restart:
                print(f"Promty uploader already running: pid {existing_pid}")
                return 0
            print(f"Restarting Promty uploader: pid {existing_pid}")
            try:
                _stop_uploader_process(existing_pid)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1

        env = os.environ.copy()
        if config_path:
            # A profile config is the live source of truth. Do not let credentials or
            # endpoints inherited from the parent shell shadow later config updates.
            for name in (
                "PROMTY_API_TOKEN",
                "PROMPTHUB_API_TOKEN",
                "PROMTY_API_URL",
                "PROMPTHUB_API_URL",
            ):
                env.pop(name, None)
            env["PROMTY_CONFIG_PATH"] = config_path
            env.pop("PROMPTHUB_CONFIG_PATH", None)
        if getattr(args, "token", None):
            # An explicit --token remains an intentional fixed override. Tokens loaded
            # from config are deliberately not copied into the process environment.
            env["PROMTY_API_TOKEN"] = args.token

        ensure_private_parent(log_path)
        ensure_private_parent(pid_path)
        log_file = open_private_text(log_path, "a")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "upload",
            "--watch",
            "--interval",
            str(max(args.interval, 0.25)),
        ]
        if config_path:
            command.extend(["--config-path", config_path])
        if getattr(args, "api_url", None):
            command.extend(["--api-url", str(args.api_url).rstrip("/")])
        if getattr(args, "queue_path", None):
            command.extend(["--queue-path", str(Path(args.queue_path).expanduser())])
        if not getattr(args, "no_auto_update", True):
            command.append("--auto-update")
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
        log_file.close()
        write_private_text_atomic(pid_path, f"{process.pid}\n")
    print(f"Promty uploader started: pid {process.pid}")
    print(f"Uploader log: {log_path}")
    return 0


def _health_status(api_url: str, timeout: float) -> tuple[bool, str]:
    try:
        with request.urlopen(f"{api_url.rstrip('/')}/health", timeout=timeout) as response:
            if response.status == 200:
                return True, "ok"
            return False, f"HTTP {response.status}"
    except error.URLError as exc:
        return False, str(exc.reason)
    except OSError as exc:
        return False, str(exc)


def _ingest_auth_status(
    api_url: str,
    token: str | None,
    timeout: float,
) -> tuple[bool, str]:
    if not token:
        return False, "collector token missing"
    try:
        PromtyUploader(api_url=api_url, token=token, timeout=timeout).heartbeat()
        return True, "authenticated"
    except error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except error.URLError as exc:
        return False, str(exc.reason)
    except OSError as exc:
        return False, str(exc)


def _hook_status(
    repo_root: Path,
    tool: SupportedTool,
    expected_queue_paths: Sequence[str] = (),
) -> tuple[bool, str]:
    if tool == "codex-cli":
        hooks_path = repo_root / ".codex" / "hooks.json"
        specs = CODEX_HOOKS
        success = "installed; Codex trust must be confirmed in /hooks"
    elif tool == "claude-code":
        hooks_path = repo_root / ".claude" / "settings.local.json"
        specs = CLAUDE_HOOKS
        success = "installed in .claude/settings.local.json"
    else:
        return False, f"{tool} hooks are not supported"

    if not hooks_path.exists():
        return False, f"missing {hooks_path.relative_to(repo_root)}"
    try:
        config = _read_hooks_config(hooks_path)
    except Exception as exc:
        return False, str(exc)

    hooks_by_event = config.get("hooks", {})
    for spec in specs:
        groups = hooks_by_event.get(spec["event"], [])
        found_command: str | None = None
        if isinstance(groups, list):
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for hook in group.get("hooks", []):
                    if isinstance(hook, dict) and _is_promty_hook(
                        hook,
                        spec["subcommand"],
                    ):
                        found_command = str(hook.get("command", ""))
                        break
                if found_command is not None:
                    break
        if found_command is None:
            return False, f"missing {spec['event']} {tool} hook"
        expanded_command = found_command.replace("${HOME}", str(Path.home())).replace(
            "$HOME",
            str(Path.home()),
        )
        for queue_path in expected_queue_paths:
            expanded_path = str(Path(queue_path).expanduser())
            if expanded_path not in expanded_command:
                return False, f"{spec['event']} hook missing queue {expanded_path}"
    return True, success


def _queue_status(queue_path: str | None) -> tuple[bool, str]:
    queue = JSONLQueue(queue_path)
    if queue.root is None:
        if queue.path.exists():
            return True, str(queue.path)
        return True, f"{queue.path} (empty)"
    queue_files = sorted(queue.path.glob("*/*/events.jsonl")) if queue.path.exists() else []
    return True, f"{len(queue_files)} session queue files under {queue.path}"


def _doctor_hook_checks(
    args: argparse.Namespace,
    *,
    expected_queue_paths: Sequence[str] = (),
) -> list[tuple[str, bool, str]]:
    target = _normalize_install_target(args)
    target_tools = _tools_for_install_target(target)
    try:
        repo_root = _git_root(args.repo_root)
        hook_results = [
            (
                tool,
                _hook_status(
                    repo_root,
                    tool,
                    expected_queue_paths=expected_queue_paths,
                ),
            )
            for tool in target_tools
        ]
    except Exception as exc:
        hook_results = [(tool, (False, f"git root error: {exc}")) for tool in target_tools]

    checks: list[tuple[str, bool, str]] = []
    for tool, hooks_status in hook_results:
        check_name = "hooks" if target != "all" else f"hooks/{tool}"
        checks.append((check_name, *hooks_status))
    return checks


def _doctor_runtime_checks(
    args: argparse.Namespace,
    *,
    prefix: str = "",
) -> list[tuple[str, bool, str]]:
    api_url = resolve_api_url(args.api_url, args.config_path)
    token = resolve_token(args.token, args.config_path)
    config = read_config(args.config_path)
    checks: list[tuple[str, bool, str]] = []
    checks.append(
        (
            f"{prefix}config",
            bool(config),
            str(args.config_path or "~/.promty/config.json"),
        )
    )
    checks.append(
        (
            f"{prefix}login",
            token is not None,
            "token saved" if token else "not logged in",
        )
    )
    checks.append((f"{prefix}queue", *_queue_status(args.queue_path)))
    checks.append((f"{prefix}backend", *_health_status(api_url, args.timeout)))
    checks.append(
        (
            f"{prefix}ingest-auth",
            *_ingest_auth_status(api_url, token, args.timeout),
        )
    )
    pid_path = Path(args.pid_path).expanduser() if args.pid_path else DEFAULT_UPLOADER_PID_PATH
    pid = _read_pid(pid_path)
    checks.append(
        (
            f"{prefix}uploader",
            pid is not None and _pid_is_running(pid),
            f"pid {pid}" if pid is not None and _pid_is_running(pid) else "not running",
        )
    )
    return checks


def doctor(args: argparse.Namespace) -> int:
    profiles = _selected_profiles(args)
    if getattr(args, "profiles", None):
        _validate_multi_profile_options(args)
        expected_queue_paths = _queue_paths_for_profiles(profiles)
        checks = _doctor_hook_checks(
            args,
            expected_queue_paths=expected_queue_paths,
        )
        for profile in profiles:
            profile_args = _profile_args(args, profile)
            checks.extend(_doctor_runtime_checks(profile_args, prefix=f"{profile}/"))
    else:
        _apply_profile_defaults(args)
        expected_queue_paths = [args.queue_path] if args.queue_path else []
        checks = _doctor_hook_checks(
            args,
            expected_queue_paths=expected_queue_paths,
        )
        checks.extend(_doctor_runtime_checks(args))

    failed = False
    for name, ok, message in checks:
        status = "ok" if ok else "needs-action"
        print(f"{name}: {status} - {message}")
        failed = failed or not ok
    return 1 if failed else 0


def _login_for_init(args: argparse.Namespace) -> None:
    if not args.skip_login and not resolve_token(args.token, args.config_path):
        login_args = argparse.Namespace(
            app_url=args.app_url,
            api_url=args.api_url,
            callback_port=args.callback_port,
            config_path=args.config_path,
            no_browser=args.no_browser,
            timeout=args.login_timeout,
            token=None,
            username=None,
        )
        login(login_args)
    elif args.token:
        login_args = argparse.Namespace(
            app_url=args.app_url,
            api_url=args.api_url,
            callback_port=args.callback_port,
            config_path=args.config_path,
            no_browser=True,
            timeout=args.login_timeout,
            token=args.token,
            username=args.username,
        )
        login(login_args)


def _start_uploader_for_init(args: argparse.Namespace) -> None:
    uploader_args = argparse.Namespace(
        api_url=args.api_url,
        config_path=args.config_path,
        interval=args.upload_interval,
        log_path=args.log_path,
        pid_path=args.pid_path,
        queue_path=args.queue_path,
        no_auto_update=args.no_auto_update,
        restart=True,
        # login() persists an explicit init token in the profile config. Let the
        # uploader reload that file instead of freezing the token in its environment.
        token=None,
    )
    start_uploader(uploader_args)


def _install_hooks_for_init(
    args: argparse.Namespace,
    *,
    profiles: Sequence[Profile] = (),
) -> int:
    install_args = argparse.Namespace(
        tool=args.tool,
        source=None,
        repo_root=args.repo_root,
        hook_command=args.hook_command,
        profile=None,
        profiles=tuple(profiles) or None,
        queue_path=None if profiles else args.queue_path,
    )
    return install_hooks(install_args)


def _init_single_profile(args: argparse.Namespace) -> int:
    _apply_profile_defaults(args)
    _login_for_init(args)

    hooks_result = _install_hooks_for_init(args)

    if not args.skip_uploader:
        _start_uploader_for_init(args)

    if hooks_result != 0:
        print(
            "Promty init incomplete; fix the hook error and run this command again.",
            file=sys.stderr,
        )
        return 1

    print("Promty init complete")
    profile_option = f" --profile {args.profile}" if args.profile else ""
    print(f"Verify setup: npx promty-collector@latest doctor --tool {args.tool}{profile_option}")
    print(f"After Project Memory is reviewed: npx promty-collector@latest context{profile_option}")
    return 0


def _init_multiple_profiles(
    args: argparse.Namespace,
    profiles: Sequence[Profile],
) -> int:
    _validate_multi_profile_options(args)
    per_profile_args = [_profile_args(args, profile) for profile in profiles]

    for profile, profile_args in zip(profiles, per_profile_args, strict=True):
        print(f"Preparing Promty profile: {profile}")
        _login_for_init(profile_args)

    hooks_result = _install_hooks_for_init(args, profiles=profiles)

    if not args.skip_uploader:
        for profile, profile_args in zip(profiles, per_profile_args, strict=True):
            print(f"Starting Promty uploader: {profile}")
            _start_uploader_for_init(profile_args)

    if hooks_result != 0:
        print(
            "Promty multi-profile init incomplete; fix the hook error and run this command again.",
            file=sys.stderr,
        )
        return 1

    profile_list = ", ".join(profiles)
    print(f"Promty multi-profile init complete: {profile_list}")
    print(
        "Verify setup: "
        "npx promty-collector@latest doctor "
        f"--tool {args.tool} --profiles {','.join(profiles)}"
    )
    context_profile = "prod" if "prod" in profiles else profiles[0]
    print(
        "After Project Memory is reviewed: "
        f"npx promty-collector@latest context --profile {context_profile}"
    )
    return 0


def init(args: argparse.Namespace) -> int:
    profiles = _selected_profiles(args)
    if getattr(args, "profiles", None):
        if len(profiles) == 1:
            _validate_multi_profile_options(args)
            return _init_single_profile(_profile_args(args, profiles[0]))
        return _init_multiple_profiles(args, profiles)
    return _init_single_profile(args)


def context(args: argparse.Namespace) -> int:
    project_id = project_id_for_context(cwd=args.cwd, project_id=args.project_id)
    payload = fetch_project_context(
        api_url=resolve_api_url(args.api_url, args.config_path),
        token=resolve_token(args.token, args.config_path),
        project_id=project_id,
        timeout=args.timeout,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_project_context(payload), end="")
    return 0


def mcp(args: argparse.Namespace) -> int:
    return run_mcp_server(
        PromtyMCPServer(
            api_url=args.api_url,
            token=args.token,
            config_path=args.config_path,
            cwd=args.cwd,
            project_id=args.project_id,
            timeout=args.timeout,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promty",
        description="Promty local AI activity collector",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument(
        "--tool",
        choices=sorted(TOOL_ALIASES),
        help="AI tool that produced the hook payload",
    )
    capture_parser.add_argument(
        "--source",
        choices=("claude", "codex"),
        help="Deprecated alias for --tool",
    )
    capture_parser.add_argument(
        "--event-type",
        choices=SUPPORTED_EVENT_TYPES,
        help="Promty event type. Defaults to payload event_type or PromptSubmitted.",
    )
    capture_parser.add_argument("--queue-path")
    capture_parser.add_argument(
        "--mirror-queue-path",
        dest="mirror_queue_paths",
        action="append",
        default=[],
        help="Also persist the same event to this independent queue (repeatable).",
    )
    capture_parser.add_argument("--sequence-path")
    capture_parser.add_argument("--session-index-path")
    capture_parser.add_argument("--change-baseline-path")
    capture_parser.set_defaults(func=capture)

    capture_changes_parser = subparsers.add_parser("capture-changes")
    capture_changes_parser.add_argument(
        "--tool",
        choices=sorted(TOOL_ALIASES),
        help="AI tool that produced the stop hook payload",
    )
    capture_changes_parser.add_argument(
        "--source",
        choices=("claude", "codex"),
        help="Deprecated alias for --tool",
    )
    capture_changes_parser.add_argument("--queue-path")
    capture_changes_parser.add_argument(
        "--mirror-queue-path",
        dest="mirror_queue_paths",
        action="append",
        default=[],
        help="Also persist the same event to this independent queue (repeatable).",
    )
    capture_changes_parser.add_argument("--sequence-path")
    capture_changes_parser.add_argument("--session-index-path")
    capture_changes_parser.add_argument("--change-baseline-path")
    capture_changes_parser.set_defaults(func=capture_changes)

    capture_raw_parser = subparsers.add_parser("capture-raw")
    capture_raw_parser.add_argument(
        "--output",
        default="docs/real-codex-payload.json",
        help="Path where the raw hook JSON should be written.",
    )
    capture_raw_parser.set_defaults(func=capture_raw)

    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument(
        "--api-url",
        default=None,
    )
    upload_parser.add_argument("--token", default=None)
    upload_parser.add_argument("--config-path")
    upload_parser.add_argument("--queue-path")
    upload_parser.add_argument("--profile", choices=sorted(PROFILE_URLS))
    upload_updates = upload_parser.add_mutually_exclusive_group()
    upload_updates.add_argument(
        "--auto-update",
        action="store_false",
        dest="no_auto_update",
        help="Allow the background uploader to install newer npm releases.",
    )
    upload_updates.add_argument(
        "--no-auto-update",
        action="store_true",
        dest="no_auto_update",
        help=argparse.SUPPRESS,
    )
    upload_parser.add_argument("--limit", type=int, default=100)
    upload_parser.add_argument("--timeout", type=float, default=10)
    upload_parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously upload queued events without blocking hooks.",
    )
    upload_parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("PROMTY_UPLOAD_INTERVAL", "2")),
        help="Seconds between queue checks in watch mode.",
    )
    upload_parser.set_defaults(func=upload, no_auto_update=True)

    login_parser = subparsers.add_parser("login")
    login_parser.add_argument("--app-url")
    login_parser.add_argument("--api-url")
    login_parser.add_argument("--callback-port", type=int, default=0)
    login_parser.add_argument("--config-path")
    login_parser.add_argument("--profile", choices=sorted(PROFILE_URLS))
    login_parser.add_argument("--no-browser", action="store_true")
    login_parser.add_argument("--timeout", type=float, default=180)
    login_parser.add_argument("--token")
    login_parser.add_argument("--username")
    login_parser.set_defaults(func=login)

    install_hooks_parser = subparsers.add_parser("install-hooks")
    install_hooks_parser.add_argument(
        "--tool",
        choices=sorted(INSTALL_TOOL_ALIASES),
        default="codex-cli",
        help="Hook integration to install (Codex CLI, Claude Code, or all).",
    )
    install_hooks_parser.add_argument(
        "--source",
        choices=("claude", "codex"),
        help="Deprecated alias for --tool",
    )
    install_hooks_parser.add_argument("--repo-root")
    install_hooks_profiles = install_hooks_parser.add_mutually_exclusive_group()
    install_hooks_profiles.add_argument("--profile", choices=PROFILE_NAMES)
    install_hooks_profiles.add_argument(
        "--profiles",
        type=_parse_profiles,
        metavar="PROFILE[,PROFILE...]",
        help="Install hooks that persist each event to every listed profile queue.",
    )
    install_hooks_parser.add_argument("--queue-path")
    install_hooks_parser.add_argument(
        "--hook-command",
        help="Command prefix that the AI tool should call before the hook subcommand.",
    )
    install_hooks_parser.set_defaults(func=install_hooks)

    uninstall_hooks_parser = subparsers.add_parser("uninstall-hooks")
    uninstall_hooks_parser.add_argument(
        "--tool",
        choices=sorted(INSTALL_TOOL_ALIASES),
        default="codex-cli",
        help="Hook integration to remove (Codex CLI, Claude Code, or all).",
    )
    uninstall_hooks_parser.add_argument("--repo-root")
    uninstall_hooks_parser.set_defaults(func=uninstall_hooks)

    start_uploader_parser = subparsers.add_parser("start-uploader")
    start_uploader_parser.add_argument("--api-url")
    start_uploader_parser.add_argument("--config-path")
    start_uploader_parser.add_argument("--interval", type=float, default=2)
    start_uploader_parser.add_argument("--log-path")
    start_uploader_parser.add_argument("--pid-path")
    start_uploader_parser.add_argument("--profile", choices=sorted(PROFILE_URLS))
    start_uploader_parser.add_argument("--queue-path")
    start_uploader_parser.add_argument(
        "--restart",
        action="store_true",
        help="Restart an existing uploader so it uses the current runtime and config.",
    )
    start_uploader_updates = start_uploader_parser.add_mutually_exclusive_group()
    start_uploader_updates.add_argument(
        "--auto-update",
        action="store_false",
        dest="no_auto_update",
        help="Allow the background uploader to install newer npm releases.",
    )
    start_uploader_updates.add_argument(
        "--no-auto-update",
        action="store_true",
        dest="no_auto_update",
        help=argparse.SUPPRESS,
    )
    start_uploader_parser.add_argument("--token")
    start_uploader_parser.set_defaults(func=start_uploader, no_auto_update=True)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--api-url")
    doctor_parser.add_argument("--config-path")
    doctor_parser.add_argument("--pid-path")
    doctor_parser.add_argument("--queue-path")
    doctor_parser.add_argument("--repo-root")
    doctor_parser.add_argument("--timeout", type=float, default=3)
    doctor_parser.add_argument("--token")
    doctor_profiles = doctor_parser.add_mutually_exclusive_group()
    doctor_profiles.add_argument("--profile", choices=PROFILE_NAMES)
    doctor_profiles.add_argument(
        "--profiles",
        type=_parse_profiles,
        metavar="PROFILE[,PROFILE...]",
        help="Check hooks and runtime state for every listed profile.",
    )
    doctor_parser.add_argument(
        "--tool",
        choices=sorted(INSTALL_TOOL_ALIASES),
        default="codex-cli",
    )
    doctor_parser.set_defaults(func=doctor)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--app-url")
    init_parser.add_argument("--api-url")
    init_parser.add_argument("--callback-port", type=int, default=0)
    init_parser.add_argument("--config-path")
    init_parser.add_argument("--hook-command")
    init_parser.add_argument("--login-timeout", type=float, default=180)
    init_parser.add_argument("--log-path")
    init_parser.add_argument("--no-browser", action="store_true")
    init_parser.add_argument("--pid-path")
    init_profiles = init_parser.add_mutually_exclusive_group()
    init_profiles.add_argument(
        "--profile",
        choices=PROFILE_NAMES,
        default=DEFAULT_INIT_PROFILE,
        help="Destination profile (defaults to production).",
    )
    init_profiles.add_argument(
        "--profiles",
        type=_parse_profiles,
        metavar="PROFILE[,PROFILE...]",
        help="Persist every captured event to each listed profile independently.",
    )
    init_parser.add_argument("--queue-path")
    init_updates = init_parser.add_mutually_exclusive_group()
    init_updates.add_argument(
        "--auto-update",
        action="store_false",
        dest="no_auto_update",
        help="Allow the background uploader to install newer npm releases.",
    )
    init_updates.add_argument(
        "--no-auto-update",
        action="store_true",
        dest="no_auto_update",
        help=argparse.SUPPRESS,
    )
    init_parser.add_argument("--repo-root")
    init_parser.add_argument("--skip-login", action="store_true")
    init_parser.add_argument("--skip-uploader", action="store_true")
    init_parser.add_argument("--token")
    init_parser.add_argument(
        "--tool",
        choices=sorted(INSTALL_TOOL_ALIASES),
        default="codex-cli",
        help="Hook integration to install (defaults to Codex CLI only).",
    )
    init_parser.add_argument("--username")
    init_parser.add_argument("--upload-interval", type=float, default=2)
    init_parser.set_defaults(func=init, no_auto_update=True)

    update_runtime_parser = subparsers.add_parser("update-runtime")
    update_runtime_parser.set_defaults(func=lambda _: _update_runtime())

    context_parser = subparsers.add_parser(
        "context",
        help="Read the current repository's Project Memory for an AI agent.",
    )
    context_parser.add_argument("--api-url")
    context_parser.add_argument("--config-path")
    context_parser.add_argument("--cwd")
    context_parser.add_argument("--project-id")
    context_parser.add_argument("--profile", choices=PROFILE_NAMES)
    context_parser.add_argument("--timeout", type=float, default=10)
    context_parser.add_argument("--token")
    context_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    context_parser.set_defaults(func=context)

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run the read-only Promty Agent Context MCP server over stdio.",
    )
    mcp_parser.add_argument("--api-url")
    mcp_parser.add_argument("--config-path")
    mcp_parser.add_argument("--cwd")
    mcp_parser.add_argument("--project-id")
    mcp_parser.add_argument("--profile", choices=PROFILE_NAMES)
    mcp_parser.add_argument("--timeout", type=float, default=10)
    mcp_parser.add_argument("--token")
    mcp_parser.set_defaults(func=mcp)

    return parser


def _update_runtime() -> int:
    path = install_runtime()
    print(f"Promty {COLLECTOR_VERSION} runtime ready: {path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    migrate_legacy_data_root()
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_profile_defaults(args)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Promty collector error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
