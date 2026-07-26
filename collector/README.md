# Promty Collector

Promty turns completed AI coding sessions into durable, reviewable **Project
Memory**. The collector connects an explicitly selected Git repository to Promty,
captures structured session activity from supported coding tools, and keeps a local
queue so hook execution does not depend on the service being online.

## Install

Requirements: Node.js 20+, Python 3.12+, and a Git repository.

Run the setup command inside the repository you want Promty to remember:

```bash
npx promty-collector@latest init --tool codex-cli --profile prod
```

For Claude Code:

```bash
npx promty-collector@latest init --tool claude-code --profile prod
```

Setup opens Promty sign-in, stores a revocable collector credential on your
machine, installs repository-local hooks, and starts the background uploader.
Check the installation with:

```bash
npx promty-collector@latest doctor --tool codex-cli --profile prod
```

The public setup supports Codex CLI and Claude Code. Other adapters are
experimental and are not exposed by the setup command.

After Project Memory has been generated and reviewed, read it from the connected
repository or expose it to an MCP-compatible agent:

```bash
npx promty-collector@latest context --profile prod
npx promty-collector@latest context --profile prod --format json
```

```json
{
  "command": "npx",
  "args": ["-y", "promty-collector@latest", "mcp", "--profile", "prod"]
}
```

The MCP server provides `get_project_context` for the latest approved memory. The tool is
read-only and never includes raw prompts, responses, or patch bodies.

## Collection and privacy boundaries

- Collection starts only in a repository where you explicitly run `init`.
- Promty receives session events needed to build Project Memory, including prompts,
  AI responses, file-change metadata, commit metadata, timestamps, and statuses.
- Sensitive prompt, response, and unified-diff text is encrypted at rest after
  upload. Local queued events remain on your machine until upload or removal.
- Project Memory is reviewable, and the MCP bridge is owner-scoped and read-only.
- External AI processing for Project Memory generation is optional and requires a
  separate confirmation. Do not submit secrets, credentials, regulated personal
  information, or third-party data you are not authorised to process.

Promty is not a general-purpose secret store. Initialise each additional repository
separately, and remove hooks when you no longer want collection there:

```bash
npx promty-collector@latest uninstall-hooks --tool codex-cli
```

## Learn more

- [Collector documentation](https://promty.org/docs/collector)
- [Privacy policy](https://promty.org/privacy)
- [Project Memory architecture](https://github.com/NaHyeongyu/promty/blob/master/docs/memory-architecture.md)
- [Agent Context and MCP guide](https://github.com/NaHyeongyu/promty/blob/master/docs/agent-context.md)
- [Source code and issues](https://github.com/NaHyeongyu/promty)

For account or privacy help, use [Promty Support](https://promty.org/app?view=support)
or email [support@promty.org](mailto:support@promty.org). Report security issues
privately to [security@promty.org](mailto:security@promty.org), not in a public issue.
