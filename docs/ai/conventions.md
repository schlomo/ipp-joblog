# Conventions — how we work here

## Design rules that earned their place

- **Measure, don't assume.** The clock offset is read from the printer's own
  clock, not guessed from the local DST rules — because "add our DST" would break
  a correctly-configured printer, and we can't tell one from the other a priori.
  Same instinct everywhere: detect from evidence, not from what is usually true.
- **Store the true value; keep the raw.** Corrected timestamps go in the primary
  columns so the dashboard needs no clock logic; the printer's literal value is
  kept in `*_raw` so a bad reading is never destructive. Mirrors storing
  `job_uuid`/`time_at_creation` so a future key change can recompute, not strand.
- **Correct at ingest, not at display.** A correction applied at display is
  date-dependent and breaks at a daylight-saving boundary. Correcting each job
  when it is stored binds the correction to when it happened.
- **Degrade, don't crash.** Every printer attribute is optional on some device.
  Missing values become empty strings / zeroes / `None` (and `None` ≠ 0 for
  sheets). A non-IPP HTTP answer is rejected before decoding, never parsed off a
  cliff.
- **Only positive evidence counts.** A *refused* port proves nothing is there; a
  silent one might be a sleeping printer — so discovery retries, and never
  excludes a port on silence alone.
- **stdout = data, stderr = logs.** See architecture.md. Never print progress to
  stdout.
- **One word per concept.** e.g. "correction", "skew", "endpoint", "state dir"
  are used verbatim throughout; don't introduce synonyms.

## Migrations (`store.py`)

- `MIGRATIONS` is an ordered tuple; `user_version` records how many have run.
  **Append only — never edit or reorder an existing migration**; databases in the
  wild have already run it.
- `CREATE_JOBS` is the **v1** schema and is frozen history. A later table rebuild
  must spell out the *current* shape, not reuse `CREATE_JOBS` (it predates columns
  added by v2+). This has bitten us.
- A database from a newer schema than the code is refused, not written to.
- The schema-drift test compares the live table's column *set* to the expected
  one; keep it honest when adding columns.

## Git, versioning, releases

- Versions are `1.0.<commit-count>`, derived from git by `hatch-vcs`. There is no
  number to bump by hand.
- **Every green push to `main` releases**: CI tags `v1.0.<n>`, publishes a
  multi-arch image to `ghcr.io/schlomo/ipp-joblog`, and cuts a GitHub release.
  Tags pushed with `GITHUB_TOKEN` don't retrigger CI, so it can't loop.
- Installing a commit between tags yields a `…devN+g<sha>` version — expected, not
  a fault.
- No PyPI, no Homebrew — GitHub is the only source. Docker and `pip/uv install
  git+…` are the supported installs.

## Code style

- Ruff for lint and format (`uvx ruff check .`, `uvx ruff format .`). Target
  py311; keep `from __future__ import annotations`.
- Frozen `slots=True` dataclasses for records. Type hints on functions.
- Tests live beside behaviour; add them in the same change, not after.
- Commit messages state what changed and *why*, in the project's plain register.
  Do not describe a fix you have not verified (see pitfalls).
