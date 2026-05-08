# Architecture — `taaad`

For contributors and reviewers. End users live in
[../README.md](../README.md) and [../RUNBOOK.md](../RUNBOOK.md).

## Component view

```
                     ┌──────────────────────────┐
                     │  pipx-installed `taaad`  │
                     │   (one per machine)      │
                     └──────────┬───────────────┘
                                │
   ┌────────────────────┬───────┴───────┬─────────────────────┐
   ▼                    ▼               ▼                     ▼
┌────────┐      ┌──────────────┐   ┌────────────┐     ┌──────────────────┐
│ engmt. │      │ engmt.       │   │ OS secret  │     │  GitHub API      │
│ A repo │      │ B repo       │   │ store      │     │ /app-manifests   │
│        │      │              │   │ (PEMs)     │     │ /app/installs    │
│ git    │      │ git config:  │   │            │     │ /apps/<slug>     │
│ config:│      │ bot.app=B    │   │ keychain_  │     │                  │
│ bot.app│      │              │   │ key:       │     │ Mints:           │
│ =A     │      │ user.email=… │   │ github-app-│     │ - App JWT (RS256)│
│        │      │              │   │ <slug>-pem │     │ - install token  │
└───┬────┘      └──────┬───────┘   └────────────┘     └──────────────────┘
    │                  │                ▲
    │                  │                │ keyring (per-user;
    │                  │                │ macOS scoped via -T ACL)
    │                  ▼                │
    │   ┌──────────────────────────┐    │
    │   │ <config-dir>/taaad/      │────┘  reads PEM
    │   │   apps/<slug>.toml       │
    │   │   hooks/pre-commit       │
    │   │   used-by-<slug>.txt     │
    │   │   config.toml            │   non-secret only
    │   └──────────────────────────┘
    │
    └─→ at git push time, credential.helper invokes
        `<abs-taaad> credential-helper <slug>` → fresh
        installation token on stdout (one shot, per push).
```

## Module map

| Module                                          | Responsibility |
|-------------------------------------------------|----------------|
| [`src/taaad/cli.py`](../src/taaad/cli.py)         | argparse wiring; lazy-imports each subcommand |
| [`src/taaad/config.py`](../src/taaad/config.py)   | config-dir layout, app TOML I/O, used-by registry, allowlist defaults |
| [`src/taaad/secrets.py`](../src/taaad/secrets.py) | `keyring` wrapper; refuses Plaintext/Null/Fail backends; macOS ACL helper |
| [`src/taaad/github.py`](../src/taaad/github.py)   | App-API client (manifest conversion, JWT, installations, tokens) |
| [`src/taaad/git.py`](../src/taaad/git.py)         | git config and remote-URL helpers (subprocess, argv lists only) |
| [`src/taaad/identity.py`](../src/taaad/identity.py) | resolve slug from explicit / repo / global config; mint token; build env |
| [`src/taaad/paths.py`](../src/taaad/paths.py)     | resolve `taaad` binary path; verify ownership/mode |
| [`src/taaad/slug.py`](../src/taaad/slug.py)       | GitHub-slug regex validation |
| [`src/taaad/hook_template.py`](../src/taaad/hook_template.py) | source of `<config>/hooks/pre-commit` |
| [`src/taaad/commands/*`](../src/taaad/commands)   | one module per subcommand |

## Trust boundaries

```
       ┌─────────┐     ┌────────────┐     ┌─────────┐     ┌────────┐
       │  agent  │ →   │  taaad     │ →   │ keyring │ →   │ secret │
       │ (LLM)   │     │  (this CLI)│     │ (lib)   │     │ store  │
       └─────────┘     └────────────┘     └─────────┘     └────────┘
            │                │                  │              │
            │                │                  │              │
       sees: env vars,    sees: PEM      sees: PEM      stores: PEM
       outputs of         transiently    transiently   user-scoped
       taaad commands     in process     in process    encrypted
                          memory only    memory only   at rest
```

The agent has no path to the PEM that doesn't go through `taaad`,
which is the only sanctioned consumer. RUNBOOK Operating Rule 1
encodes this as a behaviour rule (don't echo, don't pipe, don't
encode); the architecture encodes it structurally (no command
prints PEM bytes).

## State

| Name                               | Where                                                 | Secret? | Lifetime |
|------------------------------------|-------------------------------------------------------|---------|----------|
| App PEM                            | OS secret store (keychain key `github-app-<slug>-pem`) | yes     | until rotation / removal |
| App ID, install ID, slug, account  | `<config-dir>/apps/<slug>.toml`                       | no      | until `taaad apps remove` |
| Repo registry                      | `<config-dir>/used-by-<slug>.txt`                     | no (paths) | until `taaad apps remove` |
| Allowlist override                 | `<config-dir>/config.toml`                            | no      | persistent |
| Per-repo wiring                    | `.git/config` of the engagement repo                  | no      | until `taaad uninit` |
| Installation token                 | env (`GH_TOKEN`) or stdout (credential helper)        | yes     | 1 hour (GitHub TTL); never persisted |
| App JWT (`Bearer eyJ…`)            | `taaad` process memory                                | yes     | 10 minutes; in-memory only |

## Security postures encoded in code

- Slug validation in
  [`slug.validate`](../src/taaad/slug.py) blocks shell-injection in
  every entry point (design §2).
- `secrets.assert_safe_backend()` in
  [`secrets.py`](../src/taaad/secrets.py) refuses plaintext fallback
  (design §7).
- `paths.assert_path_safe()` in
  [`paths.py`](../src/taaad/paths.py) re-verifies the credential
  helper's own binary at every invocation (design §12).
- `commands/agent.py` enforces the allowlist and rejects argv
  containing `$GH_TOKEN` or the literal token (design §13).
- `commands/register.py` validates `Host`, `Origin`, and
  `Sec-Fetch-Site` on the local callback (design §6).
- `commands/uninit.py` value-matches before unsetting git config
  keys (design §20).
- `commands/rotate.py` writes the new PEM, mints a token to
  verify, only then shreds the source file (design §18).
- `doctor.py` checks `core.hooksPath` precedence via
  `git config --show-origin` (design §19).

## Where to extend

- New subcommand: `src/taaad/commands/<name>.py` exporting
  `run(args)`; add to the `_add_*` and dispatch in `cli.py`.
- New keyring backend: handled by the `keyring` library — verify
  in `tests/conftest.py` that the backend is exercised.
- Schema migration: bump `config.SCHEMA_VERSION`, add a `migrate_v1_to_v2`
  in `config.py`, and call it from `read_app` when the read schema
  is older than the current.
