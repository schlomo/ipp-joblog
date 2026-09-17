# ipp-joblog — orientation for AI sessions

Per-user page accounting for AirPrint/IPP printers, read **from the printer**
over IPP. Python 3.11+, zero runtime dependencies, ships as a Docker image and a
git-installed CLI. Read-only: it only ever issues IPP `Get-*` operations.

Startup is meant to be cheap: skim this page, then open the one topic doc that
matches your task. Each is short and saves a wrong turn.

| Working on… | Read first |
| --- | --- |
| Module layout, data model, how a poll flows | [docs/ai/architecture.md](docs/ai/architecture.md) |
| Design rules, git/CI, releases, code style | [docs/ai/conventions.md](docs/ai/conventions.md) |
| Traps that already bit us (printers, clocks, TZ, LAN, migrations) | [docs/ai/pitfalls.md](docs/ai/pitfalls.md) |
| Running tests and verifying against a real printer | [docs/ai/testing.md](docs/ai/testing.md) |

## Three things that waste a session if you don't know them

1. **This Mac's LAN is blocked to most interpreters.** macOS Local Network
   privacy: the uv/Homebrew Python cannot reach the printer (`OSError: [Errno 65]
   No route to host`), while `/usr/bin/python3`, `curl`, and the user's own shell
   can. It is not a routing or sandbox bug. To test against the printer, ask the
   user to run it, or use the system-Python shim — see testing.md.

2. **Verify before you claim.** Real printers keep surfacing bugs the fixtures
   miss, and commit messages in this history have described fixes that were never
   applied. Run the code (live, or ask the user) before writing "it works" — in a
   reply, a comment, or a commit message.

3. **Never depend on the wall clock or the machine's time zone in a test.** CI is
   UTC; a hardcoded local time passes here and fails there. `tests/conftest.py`
   pins the zone — keep new time assertions runner-independent.

## Everyday commands

```bash
uv run pytest                              # 218 tests, no printer needed
uvx ruff check . && uvx ruff format --check .
uv run ipp-joblog --host <printer> probe   # run probe first on any new printer
```

CLI verbs: `probe` (run first), `poll`, `watch`, `serve`, `report`, `jobs`,
`diagnose`. Releases are automatic: every green push to `main` tags
`1.0.<commit-count>` and publishes a multi-arch image to ghcr.
