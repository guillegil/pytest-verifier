# Checklist

Every item from [`bugs-0.3.1.md`](bugs-0.3.1.md) and [`improvements-and-ideas.md`](improvements-and-ideas.md), with the release it is planned for. Finished items are ticked and struck through, with the version that shipped them.

Order of work: all bugs first (0.4.0), then architecture and refactoring (0.5.0). Improvements are taken only when they are small and touch the same code as a fix. Ideas and items marked *discuss* wait for a conversation.

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

- [ ] **IMP-1** Ordering checks compare strings alphabetically · discuss (would reject string readings the soft fixture must record)
- [x] ~~**IMP-2** Validate tolerances and bounds when the check is built~~ · done in 0.4.0
- [x] ~~**IMP-3** Reject a check descriptor used as a guard condition~~ · done in 0.4.0
- [x] ~~**IMP-4** Validate composite children and `name`~~ · done in 0.4.0
- [x] ~~**IMP-5** Helpful errors for `evaluate(list)`~~ · done in 0.4.0
- [x] ~~**IMP-6** Make `evaluate()` and `evaluate_detailed()` total~~ · done in 0.4.0
- [ ] **IMP-7** Warn when the module-level `verify` is used inside a test by mistake · discuss
- [x] ~~**IMP-8** Detect use of a stale or forked fixture~~ · done in 0.4.0
- [ ] **IMP-9** Show types when values look the same · discuss (changes output)
- [ ] **IMP-10** Show the actual value for `is_true` and `is_false` · discuss (changes output)
- [ ] **IMP-11** Say which items or branches failed · discuss (changes output)
- [ ] **IMP-12** Escape and truncate the summary · 0.4.0 part done (a detail shows at most 100 items and 1000 characters per value, found by the PR review); escaping, a tighter limit and capping the passed section: discuss (changes output)
- [ ] **IMP-13** Keep descriptions small · discuss (changes output)
- [ ] **IMP-14** Readable output on non-UTF-8 terminals · discuss (changes output)
- [ ] **IMP-15** Tell absolute and relative tolerances apart when units are `%` · discuss (changes output)
- [ ] **IMP-16** Explain NaN comparisons · discuss (changes output)
- [ ] **IMP-17** Render compiled regexes properly · discuss (changes output)
- [x] ~~**IMP-18** Keep the verdict out of reach of mutation~~ · done in 0.4.0
- [x] ~~**IMP-19** Record a pre-built descriptor with the fixture~~ · done in 0.5.0
- [x] ~~**IMP-20** Consistent per-child verdicts in composites~~ · done in 0.4.0
- [x] ~~**IMP-21** Document that JSON round-trips change verdicts~~ · done in 0.4.0
- [x] ~~**IMP-22** Absorb children consistently when mixing module and fixture checks~~ · done in 0.4.0
- [x] ~~**IMP-23** Send check data to the xdist controller~~ · done in 0.5.0 with ARCH-6 (`report.verify_checks`)
- [x] ~~**IMP-24** Export the public types and document the reader contract~~ · done in 0.4.0 (`check_results_key` stays importable, undocumented)
- [x] ~~**IMP-25** Make the package pass `mypy --strict`~~ · done in 0.4.0
- [x] ~~**IMP-26** Required keys in `CheckDescriptor`~~ · done in 0.5.0
- [ ] **IMP-27** Narrower parameter types where calls always fail · discuss
- [x] ~~**IMP-28** Remove or use `_types.py`~~ · done in 0.4.0
- [ ] **IMP-29** Use a specific plugin name and a public plugin module · discuss (renames the plugin)
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
- [ ] **ARCH-9** Rename the module-level builder · discuss (renames public API)
- [x] ~~**ARCH-10** A contract test harness driven by the registry~~ · done in 0.5.0

## Ideas: features

Not implemented until we discuss them.

- [ ] **FEAT-1** Record where each check was called · idea, discuss
- [ ] **FEAT-2** `verify.raises` and error-tolerant checks · idea, discuss
- [ ] **FEAT-3** Strictness controls · idea, discuss
- [ ] **FEAT-4** Export results · idea, discuss
- [ ] **FEAT-5** Collection-aware checks · idea, discuss
- [ ] **FEAT-6** `pytest.approx` interop · idea, discuss
- [ ] **FEAT-7** Lab-grade number formatting · idea, discuss
- [ ] **FEAT-8** `verify.section()` for grouping · idea, discuss
- [ ] **FEAT-9** Custom check types · idea, discuss
- [ ] **FEAT-10** Limits tables · idea, discuss
- [ ] **FEAT-11** `verify.eventually` and `verify.stable` · idea, discuss
- [ ] **FEAT-12** Verbosity-aware CLI options and a session summary · idea, discuss

## Ideas: developer experience, CI and releases

Not implemented until we discuss them.

- [ ] **DX-1** Rename before publishing to PyPI · idea, discuss
- [ ] **DX-2** Tag-driven release workflow with Trusted Publishing · idea, discuss
- [ ] **DX-3** Turn CI into a quality-gate matrix · idea, discuss
- [ ] **DX-4** Typing contract tests · idea, discuss
- [ ] **DX-5** Property-based tests against an independent oracle · idea, discuss
- [ ] **DX-6** A table-driven outcome matrix for pytest integration · idea, discuss
- [ ] **DX-7** Executable documentation · idea, discuss
- [ ] **DX-8** A docs site, the spec in the repo, and a JSON Schema · idea, discuss
- [ ] **DX-9** SemVer and changelog gates on pull requests · idea, discuss
- [ ] **DX-10** A contributor on-ramp · idea, discuss
