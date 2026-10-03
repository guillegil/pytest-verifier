# CLAUDE.md — pytest-verifier

## Project Overview

`pytest-verifier` (import package `pytest_verifier`; called `pytest-verify` until 0.5) is a pytest plugin that provides soft assertions for test verification. It is the **judge** — the only plugin that determines pass/fail for check-based assertions. It works standalone and optionally integrates with `pytest-reporter` for rich HTML rendering.

**Specification:** The authoritative spec lives in Notion under "Pytest Verify" (child of "Pytest Reporter"). Always consult the spec for schema details, edge cases, and design decisions.

## Architecture

```
pytest-verifier (this plugin)        pytest-reporter (separate plugin)
┌──────────────────────────┐         ┌────────────────────────────────┐
│ verify fixture (scope:   │         │ step(check=descriptor)         │
│   test)                  │         │   → reads descriptor           │
│   .approx()              │         │   → creates procedure substep  │
│   .between()             │ get_    │   → NO evaluation              │
│   .greater()             │ check_  │                                │
│   ...                    │ results │ Reads get_check_results(item)  │
│                          │ ────►   │ for verification card rendering│
│ Evaluates immediately    │         │                                │
│ Records JSON-safe results│         │ Observes outcome via           │
│ Raises ChecksFailedError │         │ pytest_runtest_makereport      │
│   after the test body    │         │                                │
└──────────────────────────┘         └────────────────────────────────┘
         │                                        │
         │  pytest-verifier never imports the     │
         │  reporter.                             │
         │  Reporter reads results through        │
         │  pytest_verifier.get_check_results(),  │
         │  the pytest_verify_results hook, or    │
         │  report.verify_checks.                 │
         └────────────────────────────────────────┘
```

## Core Principles

- **verify fixture is the primary API.** It evaluates checks immediately, records results, returns descriptor dicts, and raises `ChecksFailedError` once the phase that recorded a failed check ends (after the test body; after teardown for checks made in teardown). No imports needed — it's a pytest fixture.
- **`verify.require` is the one exception to soft.** A `Require` (a `Verify` subclass, also callable with a check) whose failed checks stop the test at once; `--verify-fail-fast`/`verify_fail_fast` makes every check behave so, except in teardown and while a unittest `TestCase`'s `_callTearDown`, `tearDown`, `asyncTearDown` or `doCleanups` run (`run.cleaning`, saved and restored, so nesting is harmless; frameworks with their own `run()` call `tearDown` directly; the instance wrappers are removed when the call phase ends). `Run.stop()` raises without judging, so the phase end still judges (a swallowed stop still fails the test) and keeps the stop error (if it lists the same records) instead of adding a duplicate section; an error that replaces it (or a skip chained to it, or a skip a `TestCase` reported before it) gets its traceback and takes its place in the chain; another error chained to a stop keeps its chain and gets the section, with "stopped at". A stop error is marked `stops_test`; composites re-raise it instead of treating it as an error of a lazy child, condition or factory. A check that stopped the test is never absorbed by a composite.
- **`checks` is the secondary API.** `from pytest_verifier import checks` provides the same functions but returns unevaluated descriptors. Used standalone or for building descriptors to pass to `checks.evaluate()` or the fixture's `verify.record()`. A check built with `checks` in a test body and still unused when the test's teardown ends gives an `UnusedCheckWarning` (`_unused.py`; tracking pauses in fixtures, setup, teardown, unittest tests and nested sessions, and keeps only labels, never the checks). `pytest_verifier.verify` is a deprecated alias of `checks`.
- **Soft assertions.** Failed checks never stop the test, and neither does a check whose comparison raises (it fails with an `error` note). All checks run to completion. Failures are collected and raised as a single `ChecksFailedError` (an `AssertionError`) when the test body ends.
- **Some exceptions always go on.** Code that turns an exception into a failed check (lazy children, guard conditions, `all_satisfy` factories, sampling tries, `verify.raises`) lets `_checks._base.passes_through(exc)` exceptions through: stop errors (also inside exception groups), non-`Exception`s, and `pytest.exit`, `bdb.BdbQuit`, `unittest.SkipTest`; `verify.raises` takes them only when an expected class names them.
- **Pure data descriptors.** All check functions return plain dicts matching the CheckDescriptor schema. Results recorded by the fixture hold JSON-safe snapshots of the checked values.
- **No dependency on pytest-reporter.** The plugin works standalone and always records results on the item. Other plugins read them with `pytest_verifier.get_check_results(item)`, the `pytest_verify_results` hook (declare it `optionalhook=True`) or `report.verify_checks`; there is no reporter detection.
- **One class per check type.** Everything specific to a `check_type` (build, compare, detail, children) lives in its class in `_checks/`. No other module branches on `check_type`; they dispatch through `REGISTRY`.

## Package Structure

```
pytest_verifier/
├── __init__.py              # Exports: checks, Verify, Require, Raises, CheckDescriptor,
│                            #   GuardBranch, LimitRow, ChecksFailedError, UnusedCheckWarning,
│                            #   get_check_results, load_limits, __version__ (installed metadata);
│                            #   pytest_plugins = ["pytest_verifier.plugin"]
├── py.typed                 # PEP 561 marker — REQUIRED
├── _verify.py               # Verify class: typed public signatures and docstrings; each
│                            #   method builds a descriptor and hands it to a Sink
├── _checks/                 # One CheckType class per check_type, registered in REGISTRY
│   ├── _base.py             #   CheckType, CompositeType, REGISTRY, judge() (never raises),
│   │                        #   render_detail(), call_user() (CALLER: how a sink watches the
│   │                        #   user code a composite calls)
│   ├── _values.py           #   The 18 leaf check types (build, compare, detail)
│   ├── _raises.py           #   raises: the check of a verify.raises block, decided at exit
│   ├── _sampling.py         #   eventually, stable: Sampler takes tries (Try), trace, schedule
│   └── _composites.py       #   all_satisfy, conditional, guard (children, lazy children)
├── _descriptors.py          # CheckDescriptor/GuardBranch TypedDicts, argument validation,
│                            #   conditional case matching, shared formatting
├── _evaluator.py            # evaluate(), evaluate_detailed()
├── _render.py               # Exception-safe str/repr/format, bounded one-line value
│                            #   rendering (render_value, escape), JSON-safe snapshots
├── _settle.py               # Turns a descriptor into a recorded result (verdict, detail,
│                            #   snapshots, child verdicts)
├── _run.py                  # Run (per-attempt, thread-safe state, stop()) and Recorder (the
│                            #   fixture's Sink: judges, records, absorbs children, stops
│                            #   when hard or fail-fast; try scopes for sampling; raises blocks
│                            #   (`_Block`), calls of user code (`_Call`))
├── _limits.py               # verify.limits (parse_row, Row, limit_checks) and load_limits (CSV)
├── _location.py             # Where a recorded check was made: location, called_from;
│                            #   FunctionCode (test code by identity, or file and name),
│                            #   ours() (this package's frames, by module name)
├── plugin.py                # The pytest plugin: verify fixture, runtest hook wrappers that
│                            #   raise ChecksFailedError, report.verify_checks
├── _unused.py               # UnusedCheckWarning: checks built in a test body, never used
├── _hookspecs.py            # pytest_verify_results hookspec
├── _stash.py                # check_results_key (read through get_check_results)
├── _exceptions.py           # ChecksFailedError, failure summary (passed cap), for_terminal,
│                            #   label() (section › name), junit_property()
├── _summary.py              # SessionSummary: --verify-summary lines, checks grouped by label
├── _skill/                  # The agent skill (SKILL.md, package data) that the CLI installs
├── _installer.py            # Shipped skill files, frontmatter, target folders, plan and
│                            #   swap-in write; stale_skills() for the pytest header line
├── _cli.py                  # `pytest-verifier` command (console script): skill install
└── __main__.py              # `python -m pytest_verifier`
```

The pytest11 entry point is `pytest_verifier = "pytest_verifier"`: its name is an importable
module, so `-p pytest_verifier` and `-p no:pytest_verifier` work, and the package loads
`pytest_verifier.plugin` through `pytest_plugins`.

Adding a check type: a class in `_checks/` with `check_type`, a static `build`, `compare` and
`detail` (composites also `child_fields`, `children`, `chosen`, `combine`, `map_children`,
optionally `vetoed`; a leaf type whose record keeps only a preview of `actual`
(`snapshot_limits`) may implement `reading`, which sampling compares to tell whether the value
changed: `Length` adds `actual_length`), registered with `register()`; a `Verify` method that
passes its descriptor to the sink; and examples in `tests/test_contracts.py`, which fails until
every registered type has them.

## Key Types

```python
# CheckDescriptor — returned by every verify.* call
class CheckDescriptor(TypedDict, total=False):
    check_type: str          # Required: "approx", "equal", "between", etc.
    name: str                # Required: human-readable label
    description: str         # Required: pre-formatted verification statement
    passed: bool             # Present only when returned from fixture (evaluated)
    detail: str              # Fixture only: rendered "expected … got …" clause
    phase: str               # Fixture only: "setup", "call" or "teardown"
    location: str            # Fixture only: "path:line" that made it, rootdir-relative, "/"
    called_from: str         # Fixture only: test function line that led to it, if another
    section: list[str]       # Fixture only: titles of the verify.section blocks, if any
    error: str               # Why the check could not be evaluated (it then fails)
    actual: Any              # Check-type-specific
    expected: Any            # Check-type-specific
    abs_tol: float | None    # approx only
    rel_tol: float | None    # approx only
    units: str | None        # Optional unit label for numeric checks
    threshold: float         # greater/less only
    low: float               # between only
    high: float              # between only
    inclusive: bool           # between only
    # ... other check-type-specific fields
```

## Fixture Behavior

### `verify` fixture (scope: test)

1. **On each `verify.*()` call:**
   - Build the descriptor dict from the arguments (usage errors raise here)
   - Judge it once; a raising comparison is a failed check with an `error` note
   - Record a copy with `passed`, `detail`, `phase`, `location` (first frame outside the
     package; `called_from` when that is not the test function and the test function is on the
     stack, unless the frame found is installed or outside code that runs the test, such as
     pytest-bdd's generated test, not code in the test module), `section` (the titles of the
     `verify.section` blocks the recording code is in: a `ContextVar` keyed by the run, so
     asyncio tasks inherit it and threads start outside it) and JSON-safe snapshots of the
     values
   - With `verify.require` or fail-fast (not in teardown or `TestCase` cleanup), a failed check raises
     `ChecksFailedError` right there; `verify.require(check)` on a failed check that is not
     pending at the top level (absorbed, or judged earlier) records a copy first. Frames of
     this package set `__tracebackhide__ = hide_stop_frames`, so `--pdb` opens in the test
   - A composite absorbs every recorded check passed to it as a child, by identity, whenever
     it was built (`dict(check)` keeps a copy standalone), except a check that stopped the
     test or that an earlier phase judged (it was reported and numbered there); each
     evaluated child carries its own `passed`, unselected children carry none
   - A lazy child (a callable) is just called: the checks it records go to the top level, and
     the composite then absorbs the one it returned like an eager child. If building the
     composite raises (a usage error, or `pytest.skip` in a lazy child), the checks passed
     to the call are absorbed; the ones a factory or lazy child made so far stay standalone
   - Return the recorded dict
   - Sampling (`eventually`/`stable`): each try runs in a try scope (`_TRYING` ContextVar keyed
     by the run); a try that is not kept is discarded with every check recorded in it, failures
     in a try never stop the test, and after the composite is recorded require/fail-fast apply
     to the kept try's other failed checks. A `BaseException` through sampling drops every try.
     A try passes only when its check and every other check recorded in it that is still at the
     top level pass (`Sampler.failed_beside`, `Run.failed`, `Try.others`). A kept try that
     failed this way lists them in `also_failed` (text), and `CompositeType.vetoed` fails the
     composite (in settle and evaluate); a vetoed sampling check whose kept check passed has no
     margin (`_Sampling.margin`). A returned check that another thread recorded during the try
     joins it (`Run.adopt`: made after `_TryScope.opened`, by `_Entry.order`); other thread checks
     stay outside. Dropped tries leave `_RecordingSampler.tries` and outer try scopes at once.
     "Never changed" compares `CheckType.reading` over every try, and ignores a reading whose
     snapshot is a non-finite float, Decimal or numpy number (`_NOT_FINITE`)
   - Composites call their lazy children, guard conditions and factories through `call_user`,
     and samplers call samples, each as a `_Call` (`_CALLING`, a ContextVar keyed by run). A
     `raises` block whose unexpected exception goes on does not stop at once: `Recorder._wait`
     puts its stop in the innermost `_Call`. When that call raises, its blocks are dropped and
     its stops go to the composite (enforced after it is recorded, while still pending) or to
     the try's deferred stops. When it returns, they stop nothing

2. **At the end of each phase (runtest hook wrappers):**
   - Checks recorded in setup and in the test body are judged after the test body; checks
     recorded in teardown after teardown. Any failure → raise `ChecksFailedError`
   - If the phase already raised, keep that error and add the summary to its report as a
     "Soft assertion failures" section (a skip, including `unittest.SkipTest`, never hides a
     failed check). A unittest `TestCase` records its failures and skips in `item._excinfo`
     instead of raising them, so the call phase reads that list too, and takes the later stop
     errors out of it (the call summary lists their checks and names the stop)
   - `ChecksFailedError` message format: a first line `N of M checks failed: <first failure>
     (+k more)`, or `N of M checks failed, stopped at [k]: <stopping check> …` after a stop,
     then failed checks (with their location) before passed, with `[seq]` indices (their index
     among all the test's records, so teardown checks continue the numbering)
   - The crash line (`--tb=line`) points at the line, in the test function's file (reportinfo's,
     else the test module), of the check the first line names; `-r` and `--tb=line` lines go
     through `for_terminal` while TerminalReporter prints them
   - The judged checks go to the `pytest_verify_results` hook and to that phase's report as
     `report.verify_checks`; with `verify_junit_properties` (`none`/`failed`/`all`) also to
     `item.user_properties` (`verify[k] label`, `passed: detail`), dropped at the next
     attempt's setup; on the controller `_JunitAttempts` strips them from the reports of an
     attempt pytest-rerunfailures repeats (16.6.1+ logs its teardown report) and warns about
     an xunit2 report at session start. `--verify-json PATH` registers `_JsonLines` on the
     controller only (path checked at configure, file emptied at session start), which writes
     a line per check from `report.verify_checks` with its `attempt` (`_Attempts`, by node ID
     and worker) and index (counted per attempt)

3. **Reset:** Fresh run state per test attempt (reruns included). No state bleeds between tests.

### Results contract (shared with pytest-reporter §15.1)

Other plugins call `pytest_verifier.get_check_results(item)`. A `StashKey` works by identity, so a
key created by another plugin can never see these results; the key in `_stash.py` is private.
Plugins that must not import pytest-verifier implement `pytest_verify_results(item, when, checks,
passed)` with `optionalhook=True`, or read `report.verify_checks` (JSON-safe, survives xdist).
Records carry `location`/`called_from` strings; readers must not require them (hand-built and
`checks` descriptors have none).

## Releases

`.github/workflows/release.yml` builds once, runs `twine check` and the sdist's tests against the
wheel (pytest 7.0.1 and newest), then tags, uploads to PyPI (Trusted Publishing, environment
`pypi`; `testpypi` for the trial target) and publishes the GitHub release from CHANGELOG. The
CHANGELOG needs a dated `## [X.Y.Z] - YYYY-MM-DD` heading; the release and github targets need a
commit on main. No `skip-existing`: after a partial failure use "Re-run failed jobs" (same built
files); an existing tag on the same commit and an existing release with the same files are
accepted. Actions are pinned to commit SHAs. CI's package job runs the same build and tests.
Before a release, bump `version` in pyproject.toml and `metadata.version` in the skill together.

## Agent skill — keep it current

`pytest_verifier/_skill/SKILL.md` teaches coding agents the public API; `pytest-verifier skill
install` copies it into `.claude/skills/pytest-verifier/` and `.agents/skills/pytest-verifier/`
(`--claude`, `--agents`/`--generic`, `--global` for the home folder, `--force`). Reinstalling
replaces a pytest-verifier skill of any version (found by `name: pytest-verifier` in its
frontmatter) and refuses anything else. Every change to user-visible behaviour updates the
skill in the same PR, and every release sets its `metadata.version` to the new version.
`tests/test_skill.py` fails when a public `Verify` method, a name in `__all__` or a
`--verify-*` option or `verify_*` ini setting is missing from the skill, when its version is
not the package's, or when an example does not parse. Keep it precise and lean: only what an
agent needs to use the API correctly, each claim checked against the code.

## IDE Autocompletion — CRITICAL REQUIREMENT

**Every public interface must be fully typed.** Users must get autocompletion on `verify.` in PyCharm/VS Code.

Implementation requirements:
- `Verify` class with explicit method signatures (not `**kwargs`)
- All parameters typed with `float`, `str`, `Any`, `int`, etc.
- Return type `CheckDescriptor` on all check methods
- `CheckDescriptor` as a TypedDict (not a plain dict)
- `py.typed` marker in package root
- Docstrings on all public methods
- Use `Optional[...]` and `Union[...]` in public signatures and TypedDicts: tools evaluate them with `typing.get_type_hints`, and `X | None` fails there on Python 3.9 (`X | None` is fine in private code with `from __future__ import annotations`)

## Function Catalog (24 check functions, plus `limits`, `record` and `section`)

### Equality & Approximation
| Function | check_type | Key fields |
|----------|-----------|------------|
| `verify.equal(actual, expected, *, name, units=None)` | `"equal"` | actual, expected |
| `verify.not_equal(actual, expected, *, name, units=None)` | `"not_equal"` | actual, expected |
| `verify.approx(actual, expected, *, abs_tol=None, rel_tol=None, name, units=None)` | `"approx"` | actual, expected, abs_tol, rel_tol |

### Ordering & Range
| Function | check_type | Key fields |
|----------|-----------|------------|
| `verify.greater(actual, threshold, *, name, units=None)` | `"greater"` | actual, threshold |
| `verify.greater_equal(actual, threshold, *, name, units=None)` | `"greater_equal"` | actual, threshold |
| `verify.less(actual, threshold, *, name, units=None)` | `"less"` | actual, threshold |
| `verify.less_equal(actual, threshold, *, name, units=None)` | `"less_equal"` | actual, threshold |
| `verify.between(actual, low, high, *, inclusive=True, name, units=None)` | `"between"` | actual, low, high, inclusive |

### Boolean & Identity
| Function | check_type |
|----------|-----------|
| `verify.is_true(actual, *, name)` | `"true"` |
| `verify.is_false(actual, *, name)` | `"false"` |
| `verify.is_none(actual, *, name)` | `"is_none"` |
| `verify.is_not_none(actual, *, name)` | `"is_not_none"` |

### String & Container
| Function | check_type |
|----------|-----------|
| `verify.contains(haystack: Container \| Iterable, needle, *, name)` | `"contains"` |
| `verify.not_contains(haystack: Container \| Iterable, needle, *, name)` | `"not_contains"` |
| `verify.matches(actual, pattern: str \| re.Pattern, *, name)` | `"matches"` |

### Type / Collection / Conditional
| Function | check_type | Notes |
|----------|-----------|-------|
| `verify.is_instance(actual, expected_type, *, name)` | `"is_instance"` | Store type as string in descriptor |
| `verify.length(actual: Sized, expected, *, name)` | `"length"` | Stores `actual_length` in descriptor; a record keeps a preview of `actual` (100 nodes, text cut at 240) |
| `verify.all_satisfy(items: Iterable[T], descriptor_factory: Callable[[T], ...], *, name)` | `"all_satisfy"` | Factory callable invoked at call time, not stored |
| `verify.conditional(switch_value, *, cases, default=None, name)` | `"conditional"` | Only matched branch evaluated; a case/default may be a callable, called only if selected |
| `verify.guard(branches, *, default=None, name)` | `"guard"` | First truthy `(condition, label, check)` branch evaluated; checks, default and conditions may be callables |
| `verify.fail(msg, *, name=None)` | `"fail"` | Always fails. name defaults to msg (`fail` when blank) |

### Errors and time
| Function | check_type | Notes |
|----------|-----------|-------|
| `with verify.raises(expected_exception, *, match=None, name) as raised:` | `"raises"` | Returns `Raises[E]`; the check is built at `__exit__` (`RAISES.build(raised, ...)`), the site taken at the call. Soft only for nothing raised or a non-matching message; other exceptions record the check, then propagate. A stop at `__exit__` after the expected type takes that exception's context in place of it. Never entered: failed at `take_unjudged(exc)` only when the phase ended without an exception, with the phase/section/site/order kept in `_Block` (inserted in creation order, after judged records, without `ensure_open`). Blocks made in a dropped try (`_TryScope.blocks`), or by a sample, lazy child, guard condition or factory that raised (`_Call.blocks`), are dropped. A block returned in place of a check is forgotten. `checks.raises` raises `RuntimeError` |
| `verify.eventually(sample, *, timeout, interval=0.1, name)` | `"eventually"` | Composite of one kept try; `Sink.sampling` runs it with a `Sampler` (settling for `checks`, try scopes for the fixture) |
| `verify.stable(sample, *, duration, interval=0.1, name)` | `"stable"` | Same; stops at the first failed try, keeps the closest passing one by `margin` |

`verify.limits(measurements, table, *, on_missing="fail")` builds one leaf check per row
(`_limits.parse_row` validates, `Row.build` builds; CSV `CellText` cells take the measurement's
type there, and `CellNumber` cells, floats that keep their digits, become a `Decimal` or
`Fraction` against one) and records them with `Sink.batch`, which applies require/fail-fast
after the whole table.
`load_limits(path, *, select, columns, encoding)` validates every line with `parse_row`.

`verify.record(check)` (fixture only) judges and records a check built elsewhere, typically by
`checks`; of a check a composite took without selecting it, it records a pinned copy (`Run.pin`;
`Run.keep_copy` stores it and `Run.copy_of` gives the same copy again). `checks.record()`
raises `RuntimeError`.
`verify.section(title)` (fixture only) is a context manager whose recorded checks get its title
in `section`; summaries show `3V3 › Vout`; `checks.section()` raises `RuntimeError`.
`verify.require` has every check method and is callable like `record`; its failed checks stop
the test. The methods of `checks.require`, and calling it, raise `RuntimeError`.

## Evaluation Logic

### `verify.evaluate(*descriptors) -> bool`
- Pure function, no side effects
- Returns `True` only if ALL descriptors pass
- Each descriptor evaluated independently

### `verify.evaluate_detailed(*descriptors) -> list[dict]`
- Returns list of evaluated result dicts with `passed`, `details`, `seq`, `t` fields
- Matches schema from pytest-reporter §15.3

### Evaluation rules by check_type:
- `equal`: `actual == expected`
- `not_equal`: `actual != expected`
- `approx`: `|actual - expected| <= abs_tol` or `<= rel_tol * |expected|` — at least one tolerance required, passes if either satisfied; exact for `int`/`Decimal`/`Fraction` unless a `float` is involved
- `greater`: `actual > threshold`
- `greater_equal`: `actual >= threshold`
- `less`: `actual < threshold`
- `less_equal`: `actual <= threshold`
- `between` (inclusive): `low <= actual <= high`
- `between` (exclusive): `low < actual < high`
- The ordering checks and `between` fail with an `error` when the value and a limit are both `str`/`bytes`/`bytearray` (text compares letter by letter); a `TypeError` with a text operand gets a hint to use `float()`
- `true`: `bool(actual) is True`
- `false`: `bool(actual) is False`
- `is_none`: `actual is None`
- `is_not_none`: `actual is not None`
- `contains`: `needle in haystack`
- `not_contains`: `needle not in haystack`
- `matches`: `re.search(pattern, actual, flags) is not None`; a compiled pattern is stored as its source plus `flags`, a plain `int` (`re.UNICODE` dropped for `str`), `flags` is `0` for a string; a hand-built descriptor holding a compiled pattern uses `pattern.search`
- `is_instance`: `isinstance(actual, expected_type)`, computed when the check is built (`instance_check`)
- `length`: `len(actual) == expected`
- `all_satisfy`: all child checks pass
- `conditional`: resolve matched case → evaluate child → parent passes if child passes
- `guard`: first branch with a truthy condition (else `default`) → parent passes if that child passes
- `fail`: always False
- `raises`: `type_check` and `match_check`, decided when the block ended
- `eventually`: any kept child passed; `stable`: the kept child passed; both fail when the kept try made another check that failed (`also_failed`)

## Description Formatting

The `description` field is generated by the assertion engine. Format conventions:

- `verify.approx(v, 3.3, abs_tol=0.05, name="Vout", units="V")` → `"Verify 'Vout' == 3.3V ± 0.05V"`
- `verify.between(v, 0.1, 0.5, name="I", units="A")` → `"Verify 'I' ∈ [0.1A, 0.5A]"`
- `verify.between(v, 0.1, 0.5, inclusive=False, name="I")` → `"Verify 'I' ∈ (0.1, 0.5)"`
- `verify.greater(v, 100, name="T", units="Mbps")` → `"Verify 'T' > 100Mbps"`
- `verify.is_true(v, name="Alive")` → `"Verify 'Alive' is True"`
- `verify.conditional(1, cases={1: ...}, name="M")` → `"Verify 'M' [mode=1]"`
- `verify.fail("msg")` → `"FAIL: msg"`
- `verify.equal(r, "OK", name="Reply")` → `"Verify 'Reply' == 'OK'"`
- `verify.approx(v, 50, abs_tol=2, name="Duty", units="%")` → `"Verify 'Duty' == 50% ± 2% (abs)"`
- `verify.matches(s, re.compile(r"^v\d", re.I), name="FW")` → `"Verify 'FW' matches /^v\d/i"`

When `units` is `None`, values appear without suffix: `"Verify 'Vout' == 3.3 ± 0.05"`

Values go through `render_value` in descriptions and details: numbers (not `bool`, not enums)
with `format()` plus units, enum members as `Class.NAME` (numeric ones add `(value units)`),
everything else as a bounded `repr` without units. It never raises, even when a type check
does. Every rendering is one line (control characters escaped) and about 240 characters at
most; names, labels, messages and units go through `render_text`/`units_text`/`escape` (units
that are a `str` subclass, such as a `str` enum member, are stored as their text by
`plain_units`, also in a hand-built descriptor's record (`_settle._evidence`), and shown as it
by `units_text`).
`equal`, `not_equal`, the ordering checks and `between` add the types when two values render
the same (`value_pair`); a failed `equal` whose values still render the same adds where they
first differ; NaN operands get a note. Truth labels (`is_true`/`is_false`) come from the
verdict: a value is never tested twice. `render_detail` passes a judging error to the detail
as `d["error"]`.

Options read at configure (each also an ini setting of the same name; the option wins):
`--verify-show-passed` (stash, default 10; `-vv` lists all), `--verify-ascii` (`_ASCII_TERMINAL`
ContextVar, reset by a config cleanup, read when a report prints), `--verify-summary`
(`_Summary` plugin on the controller, from `report.verify_checks`, counting an attempt at its
teardown report unless one of its reports was a rerun; margins from `CheckType.margin`, which
ordering checks, `between` and `approx` implement, computed as the verdict is: exact unless the
check compares in floats; units from `CheckType.units` (a sampling check: its kept child's), and
no margin when other checks failed a kept try whose check passed), `--verify-json` and
`verify_junit_properties` (see Fixture Behavior).
Text in records goes through `utf8_safe` (lone surrogates escaped), and `escape` also escapes
surrogates and U+FFFE/U+FFFF, so records encode as UTF-8 and junit reports stay well-formed.

The summary text (`ChecksFailedError`'s message, the "Soft assertion failures" section) is
always Unicode. Only the terminal gets another form: `plugin.pytest_runtest_logreport` makes a
failed report's `longrepr.toterminal` pass the summary lines through `for_terminal` when the
stream's encoding cannot show them (ASCII markers, escapes for the rest), so junitxml and
`longreprtext` keep the Unicode text, and xdist controllers adapt it too. The reason of an
xfailed report with checks (`_name_failures_in_xfail` adds the summary's first line) goes the
same way on the `-rx` line (`_crash_lines_for_terminal`) and the `-v` line
(`_verbose_reason_for_terminal` swaps `_pytest.terminal._get_raw_skip_reason` for that report
while it is logged).

## Coding Standards

- Python 3.9+ (public hints use `Optional`/`Union`; see IDE Autocompletion above)
- PEP 8 with 100 character lines
- Type hints on all public APIs — this is non-negotiable for IDE autocompletion
- Docstrings (Google style) on all public methods
- pytest for testing (test the plugin itself with pytest)
- No external dependencies beyond pytest
- All descriptors must be JSON-serializable

## File Boundaries

- Safe to edit: `pytest_verifier/`, `tests/`
- Never ship a `pytest_verify` package: that import name belongs to another project on PyPI
- Never touch: `venv/`, `__pycache__/`, `.pytest_cache/`, `dist/`, `*.egg-info/`

## Testing Strategy

- Test each check function: correct descriptor shape, correct `check_type`, correct `description` format
- Test evaluation logic: each check_type passes when it should, fails when it should
- Test `evaluate(*args)`: multiple descriptors, all-pass, any-fail
- Test `evaluate_detailed`: correct result schema
- Test fixture lifecycle: results collected, `ChecksFailedError` raised after the test body (and after teardown for teardown checks), message format
- Test `get_check_results`: every recorded check, JSON-serializable, last rerun attempt only
- Test `units` parameter: present in description, absent when None
- Test `conditional`: case matching, default fallback, no-match-no-default
- Test `all_satisfy`: all pass, some fail, empty list
- Test `fail()`: always fails, name defaults to msg
- `tests/test_contracts.py` runs every registered check type through the shared contracts (JSON
  safety, fixture/`evaluate()` parity, rendering, snapshots); a new type needs examples there

## Common Pitfalls

- **Don't forget `py.typed` marker.** Without it, mypy/Pylance won't recognize the package as typed.
- **`name` is required on all checks** (except `fail` where it defaults to `msg`). Raise `TypeError` if missing.
- **`approx` needs at least one tolerance.** Raise `ValueError` if neither `abs_tol` nor `rel_tol` provided.
- **`all_satisfy` callable is invoked at call time.** The resulting `child_checks` list is stored, not the callable.
- **Lazy children are stored as built.** A `conditional`/`guard` callable that was selected is replaced by the check it returned; one that was not is stored as `None`, and so is a guard condition that was not called.
- **`conditional` keys match by equality.** Enum members compare by value, and an `int` matches its decimal string. Keys are stored as strings; keys that would collide raise `ValueError`.
- **`is_instance` stores type as string.** The descriptor contains `"expected_type": "dict"`, not the actual type object.
- **Fixture vs `checks`:** The fixture evaluates and stores. `checks` just builds descriptors. Don't mix up which does what.
- **Results are always recorded.** Other plugins read them with `get_check_results(item)`.