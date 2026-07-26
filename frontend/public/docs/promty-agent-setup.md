# Promty collector setup instructions for AI agents

Use this document when a user asks you to connect a Git repository to Promty through Codex or Claude Code hooks.

Human-readable guide: `https://promty.org/docs/collector`
Interactive AI guide: `https://promty.org/docs/collector/ai`

## Objective

Install the Promty collector in the user's intended Git repository, configure the selected AI tool's repository hooks, start the background uploader, and verify that integration. Do not change another tool's settings unless the user explicitly selects both tools.

## Safety and authority

- Work from the repository the user placed in scope. Do not initialize hooks in a parent directory or a different checkout.
- Preserve existing hook entries and unrelated working-tree changes.
- When this task is running in Codex, select `codex-cli`. When it is running in Claude Code, select `claude-code`.
- Select `all` only when the user explicitly asks to connect both tools.
- Run exactly one `init` command with the environment or environments selected by the user.
- Do not display, read aloud, commit, or paste collector tokens, `~/.promty/profiles/*/config.json`, raw event queues, or private uploader logs.
- Do not claim completion while a diagnostic reports `needs-action`.
- GitHub authorization, Codex repository trust, and any tool permission prompt require the user's review and confirmation.
- Promty may capture prompts, responses, repository paths, and code changes. Tell the user to use non-sensitive test content.

## Prerequisites

Confirm these are available:

- Node.js 20 or newer
- Python 3.12 or newer
- Git
- A GitHub account for collector authorization
- A Git repository containing the project to connect

If the repository has a GitHub `origin` remote, Promty uses it to associate captured activity with the repository.

## Select the AI tool

Use exactly one tool value:

- Codex: `--tool codex-cli`
- Claude Code: `--tool claude-code`
- Both tools, only after explicit user selection: `--tool all`

## Select environments

Production:

```bash
npx promty-collector@latest init --tool <selected-tool> --profile prod
```

Local development:

```bash
npx promty-collector@latest init --tool <selected-tool> --profile dev
```

If the user supplied different app and API URLs, pass them together with a profile. Production and local profiles keep their credentials, queues, logs, and uploader processes separate.

To save the same captured events to local development and production, use the explicit multi-profile form:

```bash
npx promty-collector@latest init --tool <selected-tool> --profiles dev,prod
```

Multi-profile mode writes the same event ID to independent queues and runs an uploader for each destination. Do not combine `--profiles` with singular URL, path, or token overrides; configure each profile separately first.

Each uploader sends a lightweight heartbeat every minute. If a reboot stops the background process, the next captured event restarts the matching profile uploader automatically. Event capture is persisted to disk first, so a restart or network failure does not discard the local event.

## Installation procedure

1. Confirm the current working directory belongs to the intended Git repository.
2. Confirm the prerequisites without changing unrelated dependencies.
3. Run the selected single-profile or multi-profile `init` command with the explicit `--tool` value.
4. If `npx` asks to install `promty-collector`, accept the package installation.
5. If browser-based GitHub authorization starts, ask the user to complete it. Continue only after authorization returns control to the terminal.
6. Confirm the command exits with status `0` and prints `Promty init complete`.
7. Confirm the selected repository file exists. Do not print secret-bearing configuration values:
   - Codex: `.codex/hooks.json`
   - Claude Code: `.claude/settings.local.json`
   - Both: both files
   Confirm unselected tool settings were not changed.
8. For Codex, tell the user to open `/hooks`, review the Promty hook commands, and trust the repository.
9. Tell the user to exit and restart the selected AI tool session if it was open during installation.
10. Run the verification command:

```bash
npx promty-collector@latest doctor --profile <dev-or-prod> --tool <selected-tool>
```

For multi-profile installations, run:

```bash
npx promty-collector@latest doctor --profiles dev,prod --tool <selected-tool>
```

11. Report the status of `config`, `login`, the selected hook check, `queue`, `backend`, and `uploader`.
12. Ask the user to submit one small, non-sensitive prompt in the selected tool and confirm that the new activity appears in Promty.
13. When completed work is ready, ask the user to review and approve Project Memory in Promty.
14. From a later session in the same repository, verify the approved handoff:

```bash
npx promty-collector@latest context --profile <dev-or-prod>
```

For MCP clients, configure the read-only server with:

```json
{
  "command": "npx",
  "args": ["-y", "promty-collector@latest", "mcp", "--profile", "<dev-or-prod>"]
}
```

The server exposes the read-only `get_project_context` tool for the latest approved Project
Memory. Raw prompts, responses, and patch bodies are never returned through the agent context
boundary.

## Updates

Automatic updates are disabled by default. Add `--auto-update` to `init` or `start-uploader` only when the user explicitly wants the uploader to check npm every six hours, install a newer release, and restart with the same profile and queue. Otherwise update manually with `npx promty-collector@latest init --tool <selected-tool> --profile <profile>`.

## Expected hook coverage

Codex writes `.codex/hooks.json` with:

- `UserPromptSubmit`: captures the submitted prompt
- `Stop`: captures the response and changed files

Claude Code writes `.claude/settings.local.json` with:

- `SessionStart`: starts the session timeline
- `UserPromptSubmit`: captures the submitted prompt
- `Stop`: captures the response and changed files
- `SessionEnd`: closes the session timeline

## Environment switching

Named `dev` and `prod` profiles use separate configuration, queue, PID, and log paths. Re-running `init --tool <selected-tool> --profile ...` updates the selected repository hooks to that single profile; `init --tool <selected-tool> --profiles dev,prod` updates them to write to both queues.

When using custom app and API URLs, keep an explicit profile so the matching credential,
queue, uploader, diagnostics, context command, and MCP server continue to use one destination.
Re-run the selected profile's `init` command and restart the selected AI tool session.

## Troubleshooting

### Codex does not capture prompts

Ask the user to open `/hooks`, confirm the Promty hooks are enabled and trusted, and start a new Codex session in the repository.

### Claude Code misses `SessionStart`

Exit the Claude Code session that was open during installation and launch a new one from the repository root.

### Backend or uploader reports `needs-action`

Confirm the selected API is reachable. The user may inspect `~/.promty/profiles/<profile>/uploader.log` locally. Do not paste private log contents into the conversation. Start the uploader if needed:

```bash
npx promty-collector@latest start-uploader --profile <dev-or-prod>
```

Then run `npx promty-collector@latest doctor --profile <dev-or-prod> --tool <selected-tool>` again.

### Events go to the wrong environment

Stop the existing uploader before changing URLs, run one `init` command for the intended environment, and restart the selected AI tool session.

## Completion report

Report all of the following:

- selected app URL and API URL
- selected AI tool and confirmation that unselected tool settings were unchanged
- `init` exit status
- hook files created or updated
- Codex hook trust required or confirmed
- selected tool session restart required or completed
- every selected-tool `doctor` result
- Project Memory review and read-only context verification required or completed
- remaining user action, if any

Do not report the integration as complete until installation succeeded, the selected hook configuration exists, unselected tool settings remained unchanged, and all selected diagnostics are healthy. Treat manual hook trust and test prompts as explicit follow-up actions when they have not yet been confirmed.
