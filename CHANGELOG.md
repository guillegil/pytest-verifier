# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-10-01

Fixes all 45 bugs listed in [`bugs-0.3.1.md`](bugs-0.3.1.md). Some fixes change behaviour that
existing tests may rely on. Those are listed under **Changed**.

### Added

- `ChecksFailedError` is exported from `pytest_verify` and is now actually raised. Checks made in setup and in the test body are raised after the test body. Checks made while fixtures are torn down are raised after teardown. It subclasses `AssertionError`, so `xfail(raises=AssertionError)`, `--pdb`, `pytest_exception_interact` and rerun filters treat a soft failure like a failed `assert`.
- `GuardBranch` TypedDict, exported for typing the branches of a recorded `guard`.
- Recorded results carry `detail` (the rendered `expected … got …` clause). When a check could not be evaluated, they also carry `error`. Every evaluated child of a composite carries its own `passed`.
- `evaluate_detailed()` results include `error` when a check could not be evaluated.
- Package metadata: classifiers, project URLs and author. `py.typed` is declared explicitly, and a `MANIFEST.in` puts the whole test suite in the sdist.
- CI jobs for `mypy --strict`, for the oldest supported pytest (7.0) and pluggy (1.2), and for building the sdist and running its tests against the wheel.
- `bugs-0.3.1.md` (the severity-ranked bug report behind this release) and `improvements-and-ideas.md` (improvements, refactors and feature ideas).
- `CHECKLIST.md`, which tracks every item of both documents and the release that handles it.

### Changed

- A check whose comparison raises, or returns a value with an ambiguous truth value (a numpy array, a pandas series), no longer stops the test or crashes the session. It is recorded as a failed check, and the error is shown in its detail.
- Usage errors raise `TypeError` or `ValueError` as soon as the check is built. They cover:
  - a `name` that is not a `str`;
  - a negative, NaN or non-numeric tolerance;
  - `between` with `low` above `high`;
  - a malformed `guard` branch, including a check descriptor used as a condition (it is always truthy);
  - `conditional` cases or defaults that are not checks, and case keys that collide (`{1: …, "1": …}`);
  - an `is_instance` type that `isinstance` cannot check (`list[int]`).
- Problems with the data inside composites never raise. An `all_satisfy` whose items cannot be iterated, or whose factory raises or returns something other than a check, is a failed check with an error note. A forgotten `return` is one example.
- `conditional` matches case keys by equality, comparing enum members by their value. An `int` and its decimal string are the same key, so `1` selects `"1"`. Results no longer depend on the Python version, and `None` no longer matches `"None"`. Case keys are still stored as strings.
- `approx` with `rel_tol` uses the band it prints, `expected ± rel_tol × |expected|`. `math.isclose` also accepted `rel_tol × |actual|`. `Decimal`, `Fraction` and `int` values are compared exactly unless a `float` is involved.
- `is_instance` behaves like `isinstance` in both APIs. Tuples and unions of types work, ABCs and runtime-checkable protocols work, and the module-level API no longer matches unrelated classes that share a name.
- Recorded results, including those returned by `get_check_results()`, hold JSON-safe snapshots of the checked values, taken when the check is made. A later mutation cannot change a report, and checked objects are no longer kept alive. NaN and infinities are stored as `"nan"`, `"inf"` and `"-inf"`. Values JSON cannot represent are stored as their `repr`.
- `evaluate()` and `evaluate_detailed()` raise a helpful `TypeError` for a list argument (use `verify.evaluate(*checks)`) or for an argument that is not a check. An unknown check type fails with an error instead of raising.
- The `verify` fixture refuses checks with `RuntimeError` once its test has finished, and in a forked child process, instead of losing them.
- Using the `verify` fixture without the plugin's hooks is an error, instead of every test passing. This happens, for example, when the fixture is imported into a `conftest.py` while the plugin is disabled.
- The `-r` short summary and `--tb=line` show `N of M checks failed` at the test's location.
- Requires `pluggy>=1.2`, which pytest already depends on. Building from source requires `setuptools>=77`.

### Fixed

- Building the failure summary can no longer crash the session, and a huge `int` no longer stops a passing check.
- `guard` and `conditional` evaluate only the selected branch. Unselected branches carry no verdict.
- Composites use the verdicts their children were recorded with.
- Reusing a recorded check inside a composite no longer removes it.
- A composite that raises leaves no stray child checks behind.
- Checks made during teardown fail the run.
- Soft failures are reported correctly under `xfail` and are no longer hidden by a later `skip`.
- With pytest-rerunfailures, each attempt starts with no checks, so a clean rerun passes.
- With pytest 9 subtests, a failing check fails only its own subtest.
- Soft failures next to a hard failure appear in junitxml and are shown whatever `--show-capture` is set to.
- A setup error keeps the soft failures recorded during setup.
- Recording checks from several threads no longer loses checks.
- Type hints accept the documented usage: `int` and enum `conditional` keys, truthy `guard` conditions, tuples for `is_instance`, and any iterable for `all_satisfy`. The package passes `mypy --strict`, and `typing.get_type_hints` works on Python 3.9.
- `ChecksFailedError` can be pickled and copied.
- Recording composite checks takes linear time in the number of children instead of quadratic time.
- Documentation: the README `guard` example works and `guard` is in the catalogs. `get_check_results()` is documented as the way for other plugins to read results. The CI triggers are described correctly. The CHANGELOG matches the tags.

### Removed

- The unused `pytest_verify._types` module.

## [0.3.1] - 2026-07-06

### Added

- GitHub Actions CI workflow running the test suite on pushes to `main` and on pull requests targeting `main`, across Python 3.9–3.13.
- README status badges (CI, Python versions, pytest, license) and a Development section.

### Fixed

- Child checks nested inside `verify.guard`, `verify.conditional`, and `verify.all_satisfy` are no longer recorded as independent results when built via the `verify` fixture. Previously every branch/case check was evaluated and reported on its own, so an **unmatched** branch whose check failed would fail the whole test — defeating the purpose of only evaluating the matched branch. Now only the composite check is reported; its verdict still reflects the chosen child.
- Corrected the README install instructions: the package is not published on PyPI, so `pip install pytest-verify` never resolved. Documented the Git install (`pip install "git+https://github.com/guillegil/pytest_verify.git"`).

## [0.3.0] - 2026-06-23

### Added

- `verify.guard(branches, *, default=None, name)` — an ordered if/elif/else check. Each branch is a `(condition, label, check)` tuple; the first branch whose condition is truthy is evaluated, falling back to `default` (or failing if none match and no default is given). Complements `verify.conditional` (which switches on a single value) for cases where the expected check depends on a chain of arbitrary boolean conditions. The chosen branch's `label` appears in the `ChecksFailedError` summary.

## [0.2.1] - 2026-06-23

### Fixed

- `verify.conditional` now matches cases whose keys are passed as the same type as `switch_value` (e.g. `cases={0: ..., 1: ...}`). Case keys are normalized to strings when the descriptor is built, so int and enum keys no longer silently fall through to the `default` branch. Descriptors remain JSON-serializable.

## [0.2.0] - 2026-06-20

### Added

- Public `get_check_results(item)` helper, importable from `pytest_verify`. Returns a copy of the check-result descriptors recorded for a pytest item (`[]` when none), reading from the shared stash. This is the read contract consumed by `pytest-reporter` to render verification cards.

### Changed

- Stash writes are now unconditional: every check records its descriptor to `item.stash` regardless of pass/fail and regardless of whether `pytest-reporter` is installed, so observers see passed checks (green cards), not only failures.
- `ChecksFailedError` message now follows the spec §7 format: an `N of M checks failed` header, then failed checks (`✗`) before passed checks (`✓`), each with its `[seq]` index, name, and a per-type detail clause — `expected … got …` for failures and a compact restatement for passes (see spec §7.1) — replacing the previous `FAILED checks:` / `PASSED checks:` lists.
- Check `description` strings now match the authoritative spec (§5): `approx` with both tolerances renders `== 3.3V ± 0.05V (abs) ± 1% (rel)` (labels only when both are present) and percentages drop a trailing `.0` (`1%`, not `1.0%`); `length` renders `Verify 'name' has length N` instead of `Verify len('name') == N`; `all_satisfy` appends the item count, e.g. `… satisfy condition (4 items)`.

### Fixed

- Standalone `verify.evaluate()` of an `is_instance` check now honours concrete subclasses (e.g. `is_instance(True, int)` passes), matching the expected type name against the actual object's MRO instead of only its exact type name. The `verify` fixture already evaluated this correctly via real `isinstance`.
- Soft-assert failures are no longer silently dropped when the test body also fails with a hard error (exception or `assert`). The original traceback is preserved and the failed-checks summary is appended as a report section. Previously the verdict hook only ran when the call phase passed, hiding soft failures behind any hard failure.

## [0.1.0] - 2026-04-04

### Added

- `verify` pytest fixture providing soft assertions that collect failures and report them at test end.
- 20 check functions: `equal`, `not_equal`, `approx`, `greater`, `greater_equal`, `less`, `less_equal`, `between`, `is_true`, `is_false`, `is_none`, `is_not_none`, `contains`, `not_contains`, `matches`, `is_instance`, `length`, `all_satisfy`, `conditional`, `fail`.
- Module-level `verify` instance (`from pytest_verify import verify`) for building unevaluated descriptors.
- `verify.evaluate()` and `verify.evaluate_detailed()` for standalone descriptor evaluation.
- `CheckDescriptor` TypedDict for fully typed, JSON-serializable check results.
- `ChecksFailedError` with formatted message listing failed and passed checks.
- Optional `pytest-reporter` integration via `item.stash` (auto-detected at session start).
- Full type annotations and `py.typed` marker for IDE autocompletion (PEP 561).

[Unreleased]: https://github.com/guillegil/pytest_verify/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/guillegil/pytest_verify/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/guillegil/pytest_verify/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/guillegil/pytest_verify/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/guillegil/pytest_verify/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/guillegil/pytest_verify/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/guillegil/pytest_verify/releases/tag/v0.1.0
