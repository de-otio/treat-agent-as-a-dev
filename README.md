# treat-agent-as-a-dev

Agent-driven setup that gives each developer's AI coding agent its own
GitHub App identity.

> **Status:** scaffolding only. Runbook and scripts coming shortly.

## What this repo is

A reference for setting up a per-developer, per-engagement GitHub App
so that an AI coding agent's commits and pull requests are attributed
to a distinct `[bot]` identity, not to the developer who runs it. The
flow is designed to be driven by the agent itself, with a small number
of unavoidable human-in-browser steps.

## What this repo isn't

A community open-source project. It's published publicly so devs and
agents can clone or fetch it by tag/SHA. External pull requests will
be politely closed. Issues are disabled. If you find a security
problem, see [SECURITY.md](SECURITY.md). If you want to use this for
your own engagements, fork it.

## Pinning

When pointing your agent at this repo, **pin to a tag or SHA**, not
to `main`. Tags matching `v*` are immutable (enforced via repository
ruleset), so `v0.1` means the same thing forever.

## License

MIT — see [LICENSE](LICENSE).
