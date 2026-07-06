# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-06-23

### Fixed

- Child checks nested inside `verify.guard`, `verify.conditional`, and `verify.all_satisfy` are no longer recorded as independent results when built via the `verify` fixture. Previously every branch/case check was evaluated and reported on its own, so an **unmatched** branch whose check failed would fail the whole test — defeating the purpose of only evaluating the matched branch. Now only the composite check is reported; its verdict still reflects the chosen child.

## [0.3.0] - 2026-06-23

### Added

- `verify.guard(branches, *, default=None, name)` — an ordered if/elif/else check. Each branch is a `(condition, label, check)` tuple; the first branch whose condition is truthy is evaluated, falling back to `default` (or failing if none match and no default is given). Complements `verify.conditional` (which switches on a single value) for cases where the expected check depends on a chain of arbitrary boolean conditions. The chosen branch's `label` appears in the `ChecksFailedError` summary.

## [0.2.1] - 2026-06-20

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
