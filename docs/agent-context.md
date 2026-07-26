# Agent Context bridge

Promty exposes the existing compiled Project Memory to coding agents without adding a new
storage model or changing the capture pipeline.

## CLI

After production setup, run this inside a captured repository:

```bash
npx promty-collector@latest context --profile prod
npx promty-collector@latest context --profile prod --format json
```

The CLI derives the same deterministic project UUID used by event capture. Use
`--project-id <uuid>` only when the working directory cannot identify the repository.
For local development, replace `--profile prod` with `--profile dev`.

## MCP

Configure an MCP client to start the following stdio server:

```json
{
  "command": "npx",
  "args": ["-y", "promty-collector@latest", "mcp", "--profile", "prod"]
}
```

It publishes one read-only tool:

- `get_project_context` returns the latest approved Project Memory as Markdown plus structured
  JSON. It accepts optional `cwd`, `project_id`, and `format` arguments.

## API and security

`GET /api/agent/projects/{project_id}/context` accepts only an active, user-owned collector
token. It intentionally rejects the global ingest token and anonymous ingest mode. Project
ownership is checked before private memory is returned.
