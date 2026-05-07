# treat-agent-as-a-dev

Agent-driven setup that gives each developer's AI coding agent its own
GitHub App identity.

In short: an AI coding agent should commit, push, and open PRs as a
distinct `[bot]` principal — not as you. That gives auditable
attribution, prevents the human reviewer from accidentally
self-approving the agent's PR, and contains the blast radius if a
secret leaks. This repo automates the setup.

## How a developer uses this repo

Pin to a tag (see [Pinning](#pinning) — never `main`) and prompt your
agent:

> Follow the runbook at <https://github.com/de-otio/treat-agent-as-a-dev>
> at tag `<tag>`. Set up a GitHub App for engagement `<engagement>`
> using my GitHub user `<your-handle>`. Target repo `<owner>/<repo>`.

**"Engagement"** here means the specific customer project or piece of
client work the bot is for. It's used as a short slug (e.g. `acme`,
`acme-q3`) to namespace this bot's name, keychain entry, and target
repo so work for different customers stays cleanly separated. If
you're not doing client work, pick any short label that identifies
the project.

By default the bot is named `<engagement>-<dev>-bot`. To pick a
different name, add: "name the bot `<bot-name>`."

The agent reads [RUNBOOK.md](RUNBOOK.md) and drives. You'll be asked
to do three things in your browser (create the App, install it on the
repo, approve the smoke-test PR); everything else is automated. Total
wall time: about 10 minutes.

## What's in this repo

- [`RUNBOOK.md`](RUNBOOK.md) — the playbook the agent follows
- [`scripts/manifest-flow.py`](scripts/manifest-flow.py) — drives the
  GitHub App manifest flow (browser + local callback) and writes the
  PEM directly to the OS secret store
- [`templates/bin/`](templates/bin) — token-minting wrapper, env
  loader, and the agent CLI launcher that get copied into the
  engagement repo at setup
- [`templates/git-hooks/pre-commit`](templates/git-hooks/pre-commit)
  — refuses commits authored by the developer's human account

## Pinning

When pointing your agent at this repo, **pin to a tag or SHA**, not
to `main`. Tags matching `v*` are immutable (enforced via repository
ruleset and signed-tag requirement), so `v0.1` means the same thing
forever. The setup scripts touch your keychain and create GitHub Apps
under your account; you don't want them tracking a moving branch.

## What this repo isn't

A community open-source project. It's published publicly so devs and
agents can clone or fetch it by tag/SHA. External pull requests will
be politely closed. Issues are disabled. If you find a security
problem, see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
