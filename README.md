<p align="center">
  <a href="https://promty.org">
    <img src="./promty.svg" alt="Promty logo" width="112" />
  </a>
</p>

<h1 align="center">Promty</h1>

<p align="center">
  <strong>Your AI tools can read the code. Promty remembers why it became this code.</strong>
</p>

<p align="center">
  Continuous, reviewable context for AI-assisted software development.
</p>

<p align="center">
  <a href="https://promty.org"><strong>Open Promty</strong></a>
  ·
  <a href="#quick-start">Quick start</a>
  ·
  <a href="./docs/agent-context.md">Agent Context</a>
</p>

---

AI coding tools understand the task in front of them, but the reasoning behind a
project is often scattered across sessions:

- Why was this architecture chosen?
- Which approaches were tried and rejected?
- What is still unresolved?
- What should the next human or agent do first?

Promty captures AI-assisted work from the repositories you choose, connects interrupted
and resumed tasks, and turns reviewed evidence into structured **Project Memory**. The
result is a concise, source-linked context layer—not another transcript archive.

> **Stop re-explaining the project. Continue with the decision trail intact.**

<p align="center">
  <img
    src="./frontend/public/marketing/promty-product-memory.png"
    alt="Promty Project Memory review interface"
    width="100%"
  />
</p>

## What Promty gives you

| Capability | What it means |
|---|---|
| **One continuous timeline** | Prompts, responses, interruptions, continuations, and changed files stay connected to the same body of work. |
| **Evidence-backed memory** | Decisions, rejected paths, assumptions, open questions, and next steps remain linked to their sources. |
| **Human review** | You can remove source activity before generation and separately approve generated memory for agent use. |
| **Tool-independent context** | Reviewed memory can move between Codex CLI, Claude Code, and other MCP-compatible agents. |
| **Repository-level control** | Promty runs only in repositories where you explicitly install it. |

## How it works

| Step | Stage | What happens |
|---:|---|---|
| **01** | **Capture** | Repository hooks save prompts, responses, file changes, and session events to a durable local queue. |
| **02** | **Trace** | Related activity—including interrupted and resumed work—is organized into one readable timeline. |
| **03** | **Review** | Source activity is reviewed before generation, and generated Project Memory is approved separately. |
| **04** | **Continue** | The latest approved memory becomes available through the CLI and owner-scoped, read-only MCP. |

Promty records a submitted prompt immediately. If the network or backend is unavailable,
the uploader retries without blocking the coding tool. A later continuation is linked to
the original task instead of rewriting its history.

## Quick start

### Requirements

- Node.js 20+
- Python 3.12+
- A Git repository

Run setup inside the repository you want Promty to remember:

```bash
npx promty-collector@latest init --tool codex-cli --profile prod
```

For Claude Code:

```bash
npx promty-collector@latest init --tool claude-code --profile prod
```

The production profile connects to `https://promty.org` and `https://api.promty.org`.
Setup opens Promty sign-in, stores a revocable collector credential locally, installs
repository hooks, and starts the background uploader.

Verify the installation:

```bash
npx promty-collector@latest doctor --tool codex-cli --profile prod
```

After Project Memory has been generated and approved for agents, read it from the
repository:

```bash
npx promty-collector@latest context --profile prod
```

Promty installs hooks only in the repository where `init` runs. Repeat the command in
each additional repository you want to connect.

## Agent Context bridge

Promty can provide the latest approved Project Memory to an MCP-compatible coding agent.
Configure the client to start:

```json
{
  "command": "npx",
  "args": ["-y", "promty-collector@latest", "mcp", "--profile", "prod"]
}
```

The server exposes the owner-scoped, read-only `get_project_context` tool. It returns
approved Project Memory without exposing raw prompts, responses, or patch bodies. See
the [Agent Context guide](./docs/agent-context.md) for setup details.

## Supported integrations

| Tool | Status |
|---|---|
| **Codex CLI** | Supported |
| **Claude Code** | Supported |

If a connected repository has a GitHub `origin`, Promty links it automatically. Both SSH
and HTTPS GitHub remote formats are supported.

## Privacy and control

- **Selected repositories only.** Unrelated projects are not scanned automatically.
- **Durable local queue.** Hook execution does not depend on backend availability.
- **Encrypted sensitive text.** Raw prompts, responses, and unified diffs are encrypted at rest.
- **Human approval.** Agent Context exposes only Project Memory approved for agents.
- **Owner-scoped, read-only access.** MCP can retrieve reviewed memory but cannot change it.

Learn more in the [Memory Architecture](./docs/memory-architecture.md),
[Privacy Policy](./docs/privacy-policy.md), and [Security Policy](./SECURITY.md).

---

<p align="center">
  <strong>Capture the work. Preserve the reasoning. Continue with intent.</strong>
</p>
