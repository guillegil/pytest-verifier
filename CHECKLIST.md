# Checklist

Every item from [`bugs-0.3.1.md`](bugs-0.3.1.md) and [`improvements-and-ideas.md`](improvements-and-ideas.md), with the release it is planned for. Finished items are ticked and struck through, with the version that shipped them.

Order of work: all bugs first (0.4.0), then architecture and refactoring (0.5.0). Improvements are taken only when they are small and touch the same code as a fix. Ideas and items marked *discuss* wait for a conversation.

Since 2026-10-01: the rename to `pytest-verifier` (0.6.0), then every remaining improvement (0.7.0). Ideas (FEAT-*, DX-*) wait for a conversation after that. After 0.7.0 the user chose to publish to PyPI with 0.8.0, which also carries FEAT-1 and FEAT-3 (agreed with the user after 0.7.0). For 0.9.0 the user asked for an agent skill and a command that installs it (DX-11), and for the skill to stay current in every release. For 0.10.0 the user asked for the lab features (FEAT-10, FEAT-2, FEAT-11), then a hunt for bugs and corner cases.

## Release tasks

Every release follows the same steps, written down before any code changes:

1. Write the release's task list below and the plan in `.tasks.md`.
2. Implement on the release's branch, with a test for every change.
3. Test everything: the suite on Python 3.9 to 3.13, pytest 7.0.1 with pluggy 1.2, `mypy --strict`, a wheel and sdist build with the sdist's tests run against the wheel, and the fuzz and performance scripts.
4. Update README, CHANGELOG (Keep a Changelog), CLAUDE.md and this checklist; bump the version (SemVer).
   Update the agent skill (`pytest_verifier/_skill/`) with every user-visible change, and set
   its version to the new one (since 0.9.0; the tests fail when it lags).
5. Independent review of the diff; fix every finding with a test, then re-run step 3.
6. Open the PR, get CI green, merge (squash).
7. Release: run the release workflow on the merge commit, then check the tag, the notes and the wheel and sdist on the release page.
8. Tick the items here, update the shared notes, and report.

### 0.6.0 Rename to pytest-verifier

- [x] ~~Rename the import package to `pytest_verifier` and the distribution to `pytest-verifier` (DX-1)~~
- [x] ~~Public plugin module `pytest_verifier.plugin`, entry point named `pytest_verifier` (IMP-29)~~
- [x] ~~Builder exported as `checks`; `verify` kept as a deprecated alias (ARCH-9)~~
- [x] ~~`pytest_verify` compatibility package~~ dropped after review: the name belongs to another PyPI project; a leftover 0.5 install now stops pytest with a message instead
- [x] ~~Warn about checks built with `checks.*` in a test and never used (IMP-7)~~
- [x] ~~Tests: plugin loading (autoload, `-p`, `-p no:`, conftest), deprecation warnings, unused-check warning~~
- [x] ~~Release workflow titles, README with a migration section, CHANGELOG, CLAUDE.md~~
- [x] ~~Full test matrix, mypy, build, fuzz and performance scripts~~
- [x] ~~Independent review and fixes~~
- [x] ~~PR, CI green, merge, release v0.6.0~~ (PR #4)

### 0.7.0 Clear failures

- [x] ~~String readings in ordering checks fail with a clear error (IMP-1)~~
- [x] ~~Types shown when values look the same; strings quoted (IMP-9)~~
- [x] ~~`is_true`/`is_false` show the value (IMP-10)~~
- [x] ~~Failing items of `all_satisfy`, considered branches of `guard` and `conditional` (IMP-11)~~
- [x] ~~Escaped single-line summary, about 240 characters per value, passed section capped unless `-vv` (IMP-12)~~
- [x] ~~Bounded descriptions, a preview for `length` (IMP-13)~~
- [x] ~~ASCII fallback on terminals that cannot print the symbols (IMP-14)~~
- [x] ~~`(abs)`/`(rel)` with `%` units (IMP-15)~~
- [x] ~~NaN notes (IMP-16)~~
- [x] ~~Compiled regexes in `matches` (IMP-17)~~
- [x] ~~Narrower parameter types (IMP-27)~~
- [x] ~~README, CHANGELOG, CLAUDE.md~~
- [x] ~~Full test matrix, mypy, build, fuzz and performance scripts~~
- [x] ~~Independent review and fixes~~
- [x] ~~PR, CI green, merge, release v0.7.0~~ (PR #5)

### 0.8.0 Locations, strictness and PyPI

- [x] ~~Record where each check was made (`location`, and `called_from` when a helper made it) and show it on failed summary lines~~ (FEAT-1)
- [x] ~~Point the one-line crash entry (`--tb=line`, `-r`) at the test line of the first failed check~~ (FEAT-1)
- [x] ~~A self-contained first line: `N of M checks failed: <first failure> (+k more)`~~ (FEAT-3)
- [x] ~~`verify.require`: a check that stops the test when it fails, as `verify.require(check)` or `verify.require.<check>(...)`~~ (FEAT-3)
- [x] ~~Fail-fast option: `--verify-fail-fast` and the `verify_fail_fast` ini setting~~ (FEAT-3)
- [x] ~~`pytest_verifier.__version__`~~ (DX-2)
- [x] ~~Release workflow: build once, `twine check`, test the wheel with the oldest and newest pytest, publish to PyPI or TestPyPI with Trusted Publishing, then the GitHub release; re-runnable after a partial failure~~ (DX-2)
- [x] ~~PyPI metadata and a README that renders there (absolute links, `pip install pytest-verifier`)~~
- [x] ~~README, CHANGELOG, CLAUDE.md~~
- [x] ~~Full test matrix, mypy, build, fuzz and performance scripts~~
- [x] ~~Independent review and fixes~~ (40 confirmed findings, each fixed with a test)
- [x] ~~Second review of the fixes~~ (11 confirmed findings, each fixed with a test that fails without it)
- [x] ~~Third review of the fixes~~ (13 confirmed findings, 11 distinct, each fixed with a test that fails without it)
- [x] ~~Fourth review of the fixes~~ (4 confirmed findings, 3 distinct, each fixed with a test that fails without it)
- [x] ~~Repository renamed to `pytest-verifier` (by the owner); README, CHANGELOG and package metadata links updated~~
- [x] ~~Trial upload to TestPyPI~~ (release.yml target testpypi from the branch: built, tested, uploaded with Trusted Publishing; metadata and links checked on TestPyPI)
- [x] ~~PR, CI green, merge, release v0.8.0 to PyPI and GitHub~~ (PR #6)

### 0.9.0 Agent skill and reporting

Scope: the agent skill and its installer, asked for by the user, plus the reporting items
(FEAT-8, FEAT-4, FEAT-12), which the user confirmed for 0.9.0.

- [x] ~~Agent skill `pytest-verifier` in the package (`pytest_verifier/_skill/`), written with skill-creator, every claim checked against the code (DX-11)~~ (33 fact-check findings fixed)
- [x] ~~Test prompts run with and without the skill, graded, reviewed, skill revised (DX-11)~~ (2 rounds, 7 tasks)
- [x] ~~`pytest-verifier skill install` with `--claude`, `--agents` (`--generic`), `--global` and `--force`; `python -m pytest_verifier` too (DX-11)~~
- [x] ~~Tests that keep the skill current: every public method, export and option named in it, its version equal to the package's, its examples compile (DX-11)~~
- [x] ~~pytest's header says when a project's installed skill was made for another version (DX-11)~~
- [x] ~~`verify.section()` to group checks: a `section` path on records, `3V3 › Vout` in summaries (FEAT-8)~~
- [x] ~~Export checks to junit properties and a JSON Lines file (FEAT-4)~~
- [x] ~~`--verify-show-passed`, `--verify-ascii` and a session summary (FEAT-12)~~
- [x] ~~The skill covers every new API and option~~
- [x] ~~README, CHANGELOG, CLAUDE.md, roadmap page~~
- [x] ~~Full test matrix, mypy, build (skill files in the wheel and the sdist), sdist tests against the wheel~~
- [x] ~~Independent review and fixes~~ (installer, sections, outputs and exports; each fix with a test)
- [x] ~~PR, CI green, merge, release v0.9.0 to PyPI and GitHub~~ (PR #7)

### 0.10.0 Lab features

Scope: the 0.10.0 row of `next-steps.md` (FEAT-10, FEAT-2, FEAT-11), asked for by the user
after 0.9.0, and the problems the 0.9.0 report found but did not fix. The plan, with the
reasons for each choice, is in `.tasks.md`.

- [x] ~~`verify.limits(measurements, table)` and `load_limits(path)`: limits tables from a dict or a CSV file, rows picked per SKU or corner, a missing measurement fails (FEAT-10)~~ (strict rows, measurement-typed cells, `columns=`, `limit_source`)
- [x] ~~Soft `verify.raises(...)`: a context manager that records whether the block raised the expected exception (FEAT-2)~~ (unexpected exceptions propagate after the check)
- [x] ~~`verify.eventually(...)` and `verify.stable(...)` for readings that settle (FEAT-11)~~ (try scopes, trace, `settled_at`)
- [x] ~~A `str` enum as a name or as units is stored as its text; an empty name is a usage error; a long name is bounded in the description~~
- [x] ~~`all_satisfy` whose factory raised says how many items it checked~~
- [x] ~~`verify.record(check)` of a check a composite took in records a copy~~ (only when it was not selected)
- [x] ~~`abs_tol=-0.0` no longer renders as `± -0.0`~~
- [x] ~~Snapshot strings are capped~~
- [x] ~~Imperative `pytest.xfail()` after a failed check stays XFAIL; its reason names the failed checks~~
- [ ] A subtest whose checks failed is reported as failed: deferred, it needs explicit subtest marks and changes `--maxfail` and junit counts (an opt-in setting for a later release)
- [x] ~~`pytest.exit`, `bdb.BdbQuit` and `unittest.SkipTest` go through lazy children, conditions, factories, sampling and `verify.raises`~~ (found by the design review)
- [x] ~~The skill covers every new API and option; version 0.10.0~~
- [x] ~~README, CHANGELOG, CLAUDE.md, roadmap page~~
- [x] ~~Full test matrix, mypy, build, sdist tests against the wheel~~ (2140 tests on Python 3.9 to 3.13 and pytest 7.0.1)
- [x] ~~Bug and corner-case hunt over the release; every confirmed finding fixed with a test~~ (45 findings, 42 confirmed; limits: exact CSV limits for `Decimal`/`Fraction`, re-readable needles, hints for thousands separators, BOMs and `columns=` targets; raises blocks: dropped with their try, or with a sample, lazy child or factory that raised, reported only when the phase ended without an exception (with phase, section, place, creation order), no teardown crash, a returned block reported once with a fitting hint, required-block stops wait only when the try or composite took the exception, no chained context on a stop, failing `__notes__`; sampling: a try passes only when every check it made passes (`also_failed`), returned thread checks join their try, "never changed" compares whole values and lengths, async samples, margins with units; `verify.record` copies stay on their own; the verbose XFAIL line follows `--verify-ascii`)
- [x] ~~PR, CI green, merge, release v0.10.0 to PyPI and GitHub~~ (PR #8)

## Bugs

### Critical

- [x] ~~**C-1** A non-bool comparison result crashes the whole session~~ · done in 0.4.0
- [x] ~~**C-2** Building the failure summary can crash the whole session~~ · done in 0.4.0

### High

- [x] ~~**H-1** A check that raises stops the test and is never recorded~~ · done in 0.4.0
- [x] ~~**H-2** `guard` and `conditional` evaluate every branch, not just the matched one~~ · done in 0.4.0
- [x] ~~**H-3** Composite checks re-judge their children and can contradict them~~ · done in 0.4.0
- [x] ~~**H-4** Reusing a recorded check inside a composite deletes it~~ · done in 0.4.0 as a documented rule: a check passed to a composite belongs to it; `dict(check)` keeps a copy on its own
- [x] ~~**H-5** Module-level `is_instance` matches by class name only~~ · done in 0.4.0
- [x] ~~**H-6** `conditional` matches cases with `str()`, so results depend on the Python version~~ · done in 0.4.0
- [x] ~~**H-7** Checks recorded during teardown are ignored~~ · done in 0.4.0
- [x] ~~**H-8** Soft failures are misreported under `xfail` and hidden by `skip`~~ · done in 0.4.0

### Medium

- [x] ~~**M-1** `approx` `rel_tol` accepts values outside the advertised band~~ · done in 0.4.0
- [x] ~~**M-2** `is_instance` crashes on tuples and unions of types~~ · done in 0.4.0
- [x] ~~**M-3** Descriptors are not JSON-serializable for common inputs~~ · done in 0.4.0
- [x] ~~**M-4** Reports show values after mutation, not the values that were checked~~ · done in 0.4.0
- [x] ~~**M-5** Every checked value is kept alive until the session ends~~ · done in 0.4.0
- [x] ~~**M-6** `conditional` evaluator and renderer disagree after a JSON round-trip~~ · done in 0.4.0
- [x] ~~**M-7** The results stash accumulates across reruns~~ · done in 0.4.0
- [x] ~~**M-8** A clean rerun is failed with the previous attempt's checks~~ · done in 0.4.0
- [x] ~~**M-9** pytest 9 subtests: one failing check fails every later subtest~~ · done in 0.4.0
- [x] ~~**M-10** Soft failures next to a hard failure are missing from junitxml~~ · done in 0.4.0
- [x] ~~**M-11** Soft failures recorded in setup vanish when setup errors~~ · done in 0.4.0
- [x] ~~**M-12** `--pdb` and `pytest_exception_interact` ignore soft failures~~ · done in 0.4.0
- [x] ~~**M-13** Other `makereport` hooks can see a soft-failed test as passed~~ · done in 0.4.0
- [x] ~~**M-14** The fixture without its hook turns every failure into a pass~~ · done in 0.4.0
- [x] ~~**M-15** Recording checks from several threads loses checks~~ · done in 0.4.0
- [x] ~~**M-16** Type hints reject documented usage~~ · done in 0.4.0

### Low

- [x] ~~**L-1** `conditional` case keys that collide after `str()` are silently overwritten~~ · done in 0.4.0
- [x] ~~**L-2** `approx` loses precision with `Decimal`, `Fraction` and big `int`~~ · done in 0.4.0
- [x] ~~**L-3** A composite that raises leaves its children behind~~ · done in 0.4.0
- [x] ~~**L-4** `conditional` message shows a different key than the one it looked up~~ · done in 0.4.0
- [x] ~~**L-5** A huge `int` crashes a passing check~~ · done in 0.4.0
- [x] ~~**L-6** `ChecksFailedError` cannot be pickled or copied~~ · done in 0.4.0
- [x] ~~**L-7** `--tb=line` prints the summary twice~~ · done in 0.4.0
- [x] ~~**L-8** pytest 8 short summary gives no reason for soft failures~~ · done in 0.4.0
- [x] ~~**L-9** Concurrent first checks race on the stash~~ · done in 0.4.0
- [x] ~~**L-10** `typing.get_type_hints` fails on Python 3.9~~ · done in 0.4.0
- [x] ~~**L-11** Builds fail with setuptools 68 to 76~~ · done in 0.4.0
- [x] ~~**L-12** The sdist cannot run its own tests~~ · done in 0.4.0

### Documentation

- [x] ~~**D-1** `ChecksFailedError` is never raised~~ · done in 0.4.0
- [x] ~~**D-2** The README `guard` example crashes, and `guard` is missing from the catalogs~~ · done in 0.4.0
- [x] ~~**D-3** Reporter-detection and shared-stash-key docs are obsolete~~ · done in 0.4.0
- [x] ~~**D-4** `CLAUDE.md` `conditional` example raises `TypeError`~~ · done in 0.4.0
- [x] ~~**D-5** CHANGELOG and tags disagree~~ · done in 0.4.0
- [x] ~~**D-6** README says CI runs on every push~~ · done in 0.4.0
- [x] ~~**D-7** `CLAUDE.md` package map is out of date~~ · done in 0.4.0

## Improvements

- [x] ~~**IMP-1** Ordering checks compare strings alphabetically~~ · done in 0.7.0
- [x] ~~**IMP-2** Validate tolerances and bounds when the check is built~~ · done in 0.4.0
- [x] ~~**IMP-3** Reject a check descriptor used as a guard condition~~ · done in 0.4.0
- [x] ~~**IMP-4** Validate composite children and `name`~~ · done in 0.4.0
- [x] ~~**IMP-5** Helpful errors for `evaluate(list)`~~ · done in 0.4.0
- [x] ~~**IMP-6** Make `evaluate()` and `evaluate_detailed()` total~~ · done in 0.4.0
- [x] ~~**IMP-7** Warn when the module-level `verify` is used inside a test by mistake~~ · done in 0.6.0
- [x] ~~**IMP-8** Detect use of a stale or forked fixture~~ · done in 0.4.0
- [x] ~~**IMP-9** Show types when values look the same~~ · done in 0.7.0
- [x] ~~**IMP-10** Show the actual value for `is_true` and `is_false`~~ · done in 0.7.0
- [x] ~~**IMP-11** Say which items or branches failed~~ · done in 0.7.0
- [x] ~~**IMP-12** Escape and truncate the summary~~ · done in 0.7.0
- [x] ~~**IMP-13** Keep descriptions small~~ · done in 0.7.0
- [x] ~~**IMP-14** Readable output on non-UTF-8 terminals~~ · done in 0.7.0
- [x] ~~**IMP-15** Tell absolute and relative tolerances apart when units are `%`~~ · done in 0.7.0
- [x] ~~**IMP-16** Explain NaN comparisons~~ · done in 0.7.0
- [x] ~~**IMP-17** Render compiled regexes properly~~ · done in 0.7.0
- [x] ~~**IMP-18** Keep the verdict out of reach of mutation~~ · done in 0.4.0
- [x] ~~**IMP-19** Record a pre-built descriptor with the fixture~~ · done in 0.5.0
- [x] ~~**IMP-20** Consistent per-child verdicts in composites~~ · done in 0.4.0
- [x] ~~**IMP-21** Document that JSON round-trips change verdicts~~ · done in 0.4.0
- [x] ~~**IMP-22** Absorb children consistently when mixing module and fixture checks~~ · done in 0.4.0
- [x] ~~**IMP-23** Send check data to the xdist controller~~ · done in 0.5.0 with ARCH-6 (`report.verify_checks`)
- [x] ~~**IMP-24** Export the public types and document the reader contract~~ · done in 0.4.0 (`check_results_key` stays importable, undocumented)
- [x] ~~**IMP-25** Make the package pass `mypy --strict`~~ · done in 0.4.0
- [x] ~~**IMP-26** Required keys in `CheckDescriptor`~~ · done in 0.5.0
- [x] ~~**IMP-27** Narrower parameter types where calls always fail~~ · done in 0.7.0
- [x] ~~**IMP-28** Remove or use `_types.py`~~ · done in 0.4.0
- [x] ~~**IMP-29** Use a specific plugin name and a public plugin module~~ · done in 0.6.0
- [x] ~~**IMP-30** Richer package metadata and a tested pytest floor~~ · done in 0.4.0
- [x] ~~**IMP-31** Faster composite recording~~ · done in 0.4.0
- [x] ~~**IMP-32** Test the contracts the bugs slipped through~~ · done in 0.4.0 (regression, thread, typing and sdist tests) and 0.5.0 (contract harness)

## Architecture and refactoring

- [x] ~~**ARCH-1** A registry of check types instead of parallel if-chains~~ · done in 0.5.0
- [x] ~~**ARCH-2** Judge every check in one exception-safe place~~ · done in 0.4.0
- [x] ~~**ARCH-3** Separate the JSON evidence from the live values~~ · done in 0.4.0 (JSON-safe snapshots) and 0.5.0 (`phase` on every record, records built only in `_settle`)
- [x] ~~**ARCH-4** Lazy composite children instead of evaluate-then-discard~~ · done in 0.4.0 (linear absorption) and 0.5.0 (lazy callables)
- [x] ~~**ARCH-5** Enforce the verdict by raising, not by rewriting reports~~ · done in 0.4.0
- [x] ~~**ARCH-6** Let pytest-reporter read results without importing pytest-verify~~ · done in 0.5.0
- [x] ~~**ARCH-7** One thread-safe run object per attempt~~ · done in 0.4.0
- [x] ~~**ARCH-8** One `Verify` front-end with a pluggable sink~~ · done in 0.5.0
- [x] ~~**ARCH-9** Rename the module-level builder~~ · done in 0.6.0
- [x] ~~**ARCH-10** A contract test harness driven by the registry~~ · done in 0.5.0

## Ideas: features

Not implemented until we discuss them.

- [x] ~~**FEAT-1** Record where each check was called~~ · done in 0.8.0
- [ ] **FEAT-2** `verify.raises` and error-tolerant checks · partly done in 0.10.0 (soft `verify.raises`); the `verify_errors = record | raise` setting waits
- [ ] **FEAT-3** Strictness controls · partly done in 0.8.0 (fail-fast, `require`, first line); the warning level waits
- [x] ~~**FEAT-4** Export results~~ · done in 0.9.0
- [ ] **FEAT-5** Collection-aware checks · idea, discuss
- [ ] **FEAT-6** `pytest.approx` interop · idea, discuss
- [ ] **FEAT-7** Lab-grade number formatting · idea, discuss
- [x] ~~**FEAT-8** `verify.section()` for grouping~~ · done in 0.9.0
- [ ] **FEAT-9** Custom check types · idea, discuss
- [x] ~~**FEAT-10** Limits tables~~ · done in 0.10.0
- [x] ~~**FEAT-11** `verify.eventually` and `verify.stable`~~ · done in 0.10.0
- [x] ~~**FEAT-12** Verbosity-aware CLI options and a session summary~~ · done in 0.9.0

## Ideas: developer experience, CI and releases

Not implemented until we discuss them.

- [x] ~~**DX-1** Rename before publishing to PyPI~~ · done in 0.6.0 (`pytest-verifier`, chosen by the user)
- [x] ~~**DX-2** Tag-driven release workflow with Trusted Publishing~~ · done in 0.8.0
- [ ] **DX-3** Turn CI into a quality-gate matrix · idea, discuss
- [ ] **DX-4** Typing contract tests · idea, discuss
- [ ] **DX-5** Property-based tests against an independent oracle · idea, discuss
- [ ] **DX-6** A table-driven outcome matrix for pytest integration · idea, discuss
- [ ] **DX-7** Executable documentation · idea, discuss
- [ ] **DX-8** A docs site, the spec in the repo, and a JSON Schema · idea, discuss
- [ ] **DX-9** SemVer and changelog gates on pull requests · idea, discuss
- [ ] **DX-10** A contributor on-ramp · idea, discuss
- [x] ~~**DX-11** An agent skill and a command that installs it~~ · done in 0.9.0
