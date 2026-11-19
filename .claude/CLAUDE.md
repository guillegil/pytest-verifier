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
- **Register formats are data.** `radix=`/`width=`, `verify.bits` and a value's
  `__verify_format__` only change how integers are shown and what the record carries
  (`rendered`, `bit_diff`, ...); they never change a verdict, and a check without a format takes
  the old code path, so its text stays byte-for-byte. The protocol is user code: read once at
  build, never raising, never failing a check (`format_error`).
- **One class per check type.** Everything specific to a `check_type` (build, compare, detail, children) lives in its class in `_checks/`. No other module branches on `check_type`; they dispatch through `REGISTRY`.

## Package Structure

```
pytest_verifier/
├── __init__.py              # Exports: checks, Verify, Require, Raises, CheckDescriptor,
│                            #   GuardBranch, LimitRow, ChecksFailedError, UnusedCheckWarning,
│                            #   VerifyFormat (Protocol), ValueFormat (TypedDict), BitField,
│                            #   get_check_results, load_limits, __version__ (installed metadata);
│                            #   pytest_plugins = ["pytest_verifier.plugin"]
├── py.typed                 # PEP 561 marker — REQUIRED
├── _verify.py               # Verify class: typed public signatures and docstrings; each
│                            #   method builds a descriptor and hands it to a Sink
├── _checks/                 # One CheckType class per check_type, registered in REGISTRY
│   ├── _base.py             #   CheckType, CompositeType, REGISTRY, judge() (never raises),
│   │                        #   render_detail(), call_user() (CALLER: how a sink watches the
│   │                        #   user code a composite calls)
│   ├── _values.py           #   18 leaf check types (build, compare, detail); the seven
│   │                        #   with radix=/width= read a Format at build and in detail
│   ├── _bits.py             #   bits: (actual ^ expected) & mask == 0, always in a radix
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
├── _registers.py            # Register formats: argument checks (require_radix/width/mask/
│                            #   fields), render_int, bit_ranges/bit_diff/difference_clause,
│                            #   the __verify_format__ protocol (_ask, never raises), Format
│                            #   (read() at build, of() from a descriptor or record, stamp()),
│                            #   recorded() (CheckType.evidence), VerifyFormat, ValueFormat
├── _limits.py               # verify.limits (parse_row, Row, limit_checks) and load_limits (CSV)
├── _source.py               # Default names: source text of the value argument at the call
│                            #   (co_positions on 3.11+, else the call on the line), cached;
│                            #   spellings(): the f-string parts of the call's actual_name= /
│                            #   expected_name= (same lookup, `_locate`; own cache)
├── _operands.py             # Operand names: OperandName (str, or Template on 3.14+, guarded
│                            #   by sys.version_info), spell() -> Spelling (concrete and
│                            #   symbolic text), template rendering, the f-string safeguard
├── _location.py             # Where a recorded check was made: location, called_from;
│                            #   FunctionCode (test code by identity, or file and name),
│                            #   ours() (this package's frames, by module name)
├── plugin.py                # The pytest plugin: verify fixture, runtest hook wrappers that
│                            #   raise ChecksFailedError, report.verify_checks
├── _entry.py                # What the entry point names: pytest_plugins, and the fixture
│                            #   imported under TYPE_CHECKING for jedi
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

The pytest11 entry point is `pytest_verifier = "pytest_verifier._entry"`: its name is the
package's, so `-p pytest_verifier`, `-p no:pytest_verifier` and `pytest_plugins =
["pytest_verifier"]` all name the plugin. `_entry` and the package load `pytest_verifier.plugin`
through `pytest_plugins`. `_entry` exists for editors: jedi looks for a plugin's fixtures in the module
its entry point names (a decorated def, or a one-name `from ... import`, here under
`TYPE_CHECKING` so the plugin is not imported before pytest registers it), and in the package
`verify` is the deprecated alias. PyCharm does not read entry points (PY-72784) and Pylance does
not document it; users annotate `verify: Verify` (README, "Editor completion and type checking";
`tests/test_editors.py`).

Adding a check type: a class in `_checks/` with `check_type`, a static `build`, `compare` and
`detail` (a type whose record must not copy a field as built overrides `evidence`; composites also `child_fields`, `children`, `chosen`, `combine`, `map_children`,
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
    name: str                # Always present: given, else actual_name, else the value's
                             #   source text (auto_name in the last two cases)
    note: str                # Only when note= was given (stripped, non-blank)
    auto_name: bool          # True only when name was derived from the source or is actual_name
    actual_name: str         # Only when actual_name= was given: the text with this run's values
    actual_name_symbolic: str    # ... with each interpolation as <expression>; only when known
                                 #   and different from actual_name
    expected_name: str       # Only when expected_name= was given (stored by the check type)
    expected_name_symbolic: str  # As actual_name_symbolic
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
    radix: str               # "hex"/"bin"/"oct"/"dec": the format in force; absent without one
    width: int               # bits, when known
    fields: Dict[str, List[int]]  # {name: [msb, lsb]}, in the given order
    mask: int                # bits only, when given
    rendered: Dict[str, str] # text of each value the format applies to, without units: keys
                             #   among actual, expected, threshold, low, high, mask
    bit_diff: Dict[str, Any] # equal/bits, when the integers differ: bits [[msb, lsb], ...]
                             #   (at most 64, then more_bits), text, fields [{name, msb, lsb,
                             #   actual, expected}] (when fields are known and some differ; at
                             #   most 64, then more_fields); never in a record that passed
    format_error: str        # why a value's __verify_format__ was not used
    format_values: Dict[str, int]  # built checks only: the integer of a register object; the
                             #   record stores it as the operand and drops this key
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
accepted. Actions are pinned to commit SHAs. CI's package job runs the same build and tests, with jedi installed for
`tests/test_editors.py`.
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

## Function Catalog (25 check functions, plus `limits`, `record` and `section`)

Every method that takes `name=` also takes `note=None` (composites, sampling, `fail` and
`raises` included); the tables list it on the value checks, whose `name` is optional.

The 18 value checks also take `actual_name=None`, and the 9 with one compared operand (`equal`,
`not_equal`, `bits`, `approx`, `greater`, `greater_equal`, `less`, `less_equal`, `length`) take
`expected_name=None` (both `Optional[OperandName]`, keyword-only, after `note`; not listed in
the tables). `between`, `contains`, `not_contains`, `matches` and `is_instance` have no single
compared operand to name and take `actual_name=` only; composites, sampling, `raises`, `fail`
and `limits` take neither.

`equal`, `not_equal`, the four ordering checks and `between` also take `radix=None` and
`width=None` (`Optional[Radix]`, `Optional[int]`, keyword-only, last; not listed in the tables).

### Equality & Approximation
| Function | check_type | Key fields |
|----------|-----------|------------|
| `verify.equal(actual, expected, *, name=None, units=None, note=None)` | `"equal"` | actual, expected |
| `verify.not_equal(actual, expected, *, name=None, units=None, note=None)` | `"not_equal"` | actual, expected |
| `verify.approx(actual, expected, *, abs_tol=None, rel_tol=None, name=None, units=None, note=None)` | `"approx"` | actual, expected, abs_tol, rel_tol |
| `verify.bits(actual, expected, *, mask=None, fields=None, width=None, radix=None, name=None, note=None)` | `"bits"` | actual, expected (integers), mask, fields, width, radix; no units |

### Ordering & Range
| Function | check_type | Key fields |
|----------|-----------|------------|
| `verify.greater(actual, threshold, *, name=None, units=None, note=None)` | `"greater"` | actual, threshold |
| `verify.greater_equal(actual, threshold, *, name=None, units=None, note=None)` | `"greater_equal"` | actual, threshold |
| `verify.less(actual, threshold, *, name=None, units=None, note=None)` | `"less"` | actual, threshold |
| `verify.less_equal(actual, threshold, *, name=None, units=None, note=None)` | `"less_equal"` | actual, threshold |
| `verify.between(actual, low, high, *, inclusive=True, name=None, units=None, note=None)` | `"between"` | actual, low, high, inclusive |

### Boolean & Identity
| Function | check_type |
|----------|-----------|
| `verify.is_true(actual, *, name=None, note=None)` | `"true"` |
| `verify.is_false(actual, *, name=None, note=None)` | `"false"` |
| `verify.is_none(actual, *, name=None, note=None)` | `"is_none"` |
| `verify.is_not_none(actual, *, name=None, note=None)` | `"is_not_none"` |

### String & Container
| Function | check_type |
|----------|-----------|
| `verify.contains(haystack: Container \| Iterable, needle, *, name=None, note=None)` | `"contains"` |
| `verify.not_contains(haystack: Container \| Iterable, needle, *, name=None, note=None)` | `"not_contains"` |
| `verify.matches(actual, pattern: str \| re.Pattern, *, name=None, note=None)` | `"matches"` |

### Type / Collection / Conditional
| Function | check_type | Notes |
|----------|-----------|-------|
| `verify.is_instance(actual, expected_type, *, name=None, note=None)` | `"is_instance"` | Store type as string in descriptor |
| `verify.length(actual: Sized, expected, *, name=None, note=None)` | `"length"` | Stores `actual_length` in descriptor; a record keeps a preview of `actual` (100 nodes, text cut at 240) |
| `verify.all_satisfy(items: Iterable[T], descriptor_factory: Callable[[T], ...], *, name)` | `"all_satisfy"` | Factory callable invoked at call time, not stored; `items_name` (source of `items`) and `item_name` (factory parameter) when known |
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

### Registers (`_registers.py`)

- Arguments: `radix` one of `hex`/`bin`/`oct`/`dec` (`ValueError` otherwise, `TypeError` for a
  non-str), `width` an `int` from 1 to 1024 (`MAX_BITS`), `mask` a non-negative `int`, `fields`
  a mapping of non-blank `str` names to a bit index or `(msb, lsb)` (`BitField`; either order,
  normalised to `[msb, lsb]`, a bit below `width` when given). `bool` is never an integer here.
  `width` or `fields` alone means hex. The fields are checked against the width in force after
  the protocol answered: explicit `fields=` beyond a protocol's width raise, protocol fields
  outside an explicit narrower `width=` are left out (`_inside`; `Format._of` does the same for
  a hand-built descriptor).
- `render_int`: `0x00F3` (uppercase, padded to `ceil(width/4)`), `0b1111_0011` (padded to
  `width`), `0o000363` (padded to `ceil(width/3)`, not grouped), dec unchanged; `_` every four
  digits when hex or binary has more than four; a sign for negatives; a wider value in full;
  `None` above 1024 bits (the usual rendering is kept). `display(text)` is the text as a
  description or a detail shows it: whole up to `VALUE_LIMIT`, else head, `...`, tail
  (`Format.shown`, `Format.shown_number`); `rendered` keeps the exact text.
- `Format.read(check, operands, radix, width, fields, default=)` at build: returns `PLAIN` (a
  shared instance that stamps nothing) when there is no argument, no `default` and no operand
  whose type has `__verify_format__`. Otherwise it validates the arguments, asks every operand
  that has the protocol once (`_ask`), takes the format keys from the first that answers
  (arguments win key by key), and collects the integer of each operand: a plain `int`, the
  answer's `value`, or `operator.index()` of a protocol object. A protocol that raises or
  returns junk (not a mapping, a wrong key) sets `Format.error` and is ignored for every
  operand; `None` means no answer. `Format.stamp` writes `radix`, `width`, `fields`, `rendered`,
  `format_error` and `format_values`.
- `Format.of(d, keys, default=)` in `detail`: reads the same keys back from a descriptor or a
  record, ignores anything malformed, never raises. An operand that is an integer is rendered
  from it (a copy of a check with another value shows that value); the stored `rendered` text
  (escaped, bounded by `display`) stands only for an operand that is not an integer any more
  (a record keeps an int enum member as its `repr`). The seven value checks use the format
  branch only when `fmt.texts` is not empty and `_values._in_format` agrees: when an operand
  outside the format reads like the checked value (`5` and `Decimal(5)` in `dec`), the plain
  detail with `value_pair`'s type names is used.
- `CheckType.evidence(d, passed)` (`_registers.recorded` with the type's `format_keys`,
  `format_diff`, `format_default`) is what `_settle._evidence` copies: `format_values` replaces
  the operands and is dropped, so a record of `equal(Reg(0xF0), 0xF3)` has `actual: 240` while
  the built check still compares the object; `rendered` and (with `format_diff`: `equal`,
  `bits`) `bit_diff` are made again from the integers the check has now, and a record that
  passed gets no `bit_diff` (a register object's `==` may accept another integer). `bits`
  stores the integers in `actual`/`expected` at build (`stamp(values=False)`).
- `bit_diff` is computed at build (`Format.difference`) and again for the record: `equal` and
  `bits` only, only when a format is in force, both operands are integers and some bit differs
  (under the mask for `bits`); none without a mask when an operand is negative. `Format.clause(d)`
  computes it from the integers (`difference_clause`: at most 6 fields, then `and N more`;
  `ranges_text`: at most 8 runs, then `and N more differ`), and renders the stored one only
  when an integer is missing; a stored `text` goes through `render_text`.
- With a format, details of two integers never add `value_pair`'s type names. A failed `equal`
  whose integers are equal (a register object whose `==` says no) ends in
  `; the integers are equal, but == is False`.
- `_summary._Group.formats` holds the `(radix, width)` of each measured integer, and
  `(None, None)` for a float: the stats range is printed with `render_number` when there is
  one, left out when there are several. A whole margin beside a hex, binary or octal range is
  shown with `render_int` (`margin 0xC`).
- A `bits` operand that is not an integer names what to do (`_bits._advice`): convert it or
  add `__verify_format__`; for a class that has the method, that it gave no `value` or was
  not used (`format_error`).
- Limits: `ROW_CHECKS` lists `radix`/`width` as optional arguments of the seven checks and the
  `bits` row; CSV: `radix` is text, `width`/`mask` whole numbers (`_WHOLE`), `fields` is
  `EN=0 MODE=3:1` (`_fields`), the `expected` of `bits` a number that must be an `int`.
  `_INTEGER` takes `_` between the digits of a `0x`/`0b`/`0o` number (`0xDEAD_BEEF`), not of
  a decimal one.

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
- `bits`: `(actual ^ expected) & mask == 0` (`mask` absent: every bit); a `TypeError` (so a failed check with an `error`) when an operand is not an `int` (a `bool` is not one)
- `radix`/`width`/`fields` and `__verify_format__` take no part in any verdict: `equal` on a register object is still the object's `==`
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

- `verify.equal(v, 0xF3, radix="hex", width=16, name="R")` → `"Verify 'R' == 0x00F3"`
- `verify.between(v, 1, 3, radix="bin", width=4, name="R")` → `"Verify 'R' ∈ [0b0001, 0b0011]"`
- `verify.bits(v, 0xABF3, mask=0x00FF, width=16, name="CTRL")` → `"Verify 'CTRL' & 0x00FF == 0x00F3"` (the expected bits under the mask; `rendered["expected"]` is the whole `0xABF3`)
- `verify.bits(v, 0xF3, name="CTRL", expected_name="wdata")` → `"Verify 'CTRL' == wdata (0xF3)"`

Details with a format: `0x00F3 == 0x00F3`; `expected 0x00F3, got 0x00F0; bits [1:0] differ`;
with fields `...; bits [1:0] differ: MODE [3:1] is 0b000, expected 0b001` (several fields are
joined with `; `; a field of four bits or fewer is binary, a wider one hex, padded to its
size); `bits`: `0x12F3 & 0x00FF == 0x00F3` and
`expected 0x00F3, got 0x00F0 (mask 0x00FF); bits [1:0] differ: ...`.

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
no margin when other checks failed a kept try whose check passed), `--verify-json`,
`verify_junit_properties` (see Fixture Behavior); `--verify-fail-fast` is checked there too,
but read at each test's setup, so a conftest may set `config.option.verify_fail_fast` later.
Ini values go through `_ini`, which makes a value pytest cannot convert a `UsageError` on every
pytest (9.2 does so itself; before, a bad bool raised `ValueError`, and 9.0/9.1 a `TypeError`
for a native TOML value of the wrong type); `_flag` reads the bool settings, as "`<name>` must
be true or false: …".
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
- `tests/test_registers.py`: rendering in every radix, bit ranges and fields, `bits`, the
  `__verify_format__` protocol (also misbehaving), records and JSON exactness for 64-bit and
  128-bit values, composites, sampling, limits rows and CSV, summary stats, exports, hints
- `tests/test_contracts.py` runs every registered check type through the shared contracts (JSON
  safety, fixture/`evaluate()` parity, rendering, snapshots); a new type needs examples there

## Common Pitfalls

- **Don't forget `py.typed` marker.** Without it, mypy/Pylance won't recognize the package as typed.
- **`name` is optional only on the checks of one value** (the 18 leaf checks with an `actual`/`haystack`): when omitted it is the concrete `actual_name` if one was given, else the source text of that argument at the call (both with `auto_name: True`; `_source.subject_text`; `"value"` without source, or when no call of the method itself is at the site, as for `map(verify.is_true, xs)` or a bound method/`partial` factory; both lookups compare `_called_name` to the method). `fail` defaults it to `msg`; composites, sampling checks and `raises` still raise `TypeError` without it. `name` must be a `str` when given.
- **Operand names are text about the operands, never judged.** `_verify._labels` validates them
  with `_operands.spell` (a `str`, or a `string.templatelib.Template` on 3.14+; stripped; `None`
  or blank is not given; else `TypeError`) only when one is given, so a check without them
  pays nothing. The concrete text is stored as `actual_name`/`expected_name`; the symbolic text
  (`reg<channel>.F`) as `*_symbolic`, only when known and different: from the template's
  `Interpolation.expression`s, or for a `str` from `_source.spellings(method)`, the parts of
  the f-string (`ast.JoinedStr`; format spec and conversion dropped, `{x=}` is `x=<x>`) written
  as that keyword in the call of the method (found like the default name, cached per code
  object and instruction, never raises; nothing for a literal, a variable, `**kwargs`, an
  indirect call or no source). The call read must provably be the one running
  (`_locate(proven=True)`): with instruction positions, a call that is not a call of the
  method gives nothing, also when a call of the method is on its line; on the line path
  `_by_line` gives nothing when another call spanning the line passes an operand name or
  `**kwargs` (`_may_spell`). The default name keeps its line fallback. `symbolic_text` keeps it only when the literal parts occur in
  order (first at the start, last at the end) in the text received. Template interpolations
  are formatted as an f-string would, and an error there (a wrong format spec, a raising
  `__format__`) propagates from the call as from the f-string: a usage error.
  Without `name=`, `actual_name` is the name and sets `auto_name` (the name is how the value
  is spelled); with `name=` it is data only. `expected_name` is passed to the type's `build`,
  which stores it and shows it through `_checks._base.named`: `wdata (243)` wherever the
  expected value is rendered (description and detail; a hand-built field that is not text is
  ignored). Names are stored as given and bounded where shown (`render_text`), like `name`.
- **`note` is free text, never judged.** Every method that takes `name=` takes `note=` (`require_note`: `str` or `None`, stripped, blank dropped; `annotate` stores it). Summaries add ` (note)` after the detail through `note_suffix`.
- **A format must not touch plain checks.** `Format.read` returns `PLAIN` before validating anything when a check has no `radix`/`width` and no operand type with `__verify_format__`; details branch on `fmt.texts`. Hundreds of tests assert the plain text.
- **Records lose the object.** A detail may be rendered from a record (composite children, `evaluate` on records), where a register object is a number and an int enum member its `repr`: read the stored integers, and `rendered`/`bit_diff` where there is none, never the value's type.
- **A register model must read once per check.** `__verify_format__` runs at build and `==` at judgement; the README example returns an `int` subclass from `read()` for that reason.
- **`approx` needs at least one tolerance.** Raise `ValueError` if neither `abs_tol` nor `rel_tol` provided.
- **`all_satisfy` callable is invoked at call time.** The resulting `child_checks` list is stored, not the callable.
- **Lazy children are stored as built.** A `conditional`/`guard` callable that was selected is replaced by the check it returned; one that was not is stored as `None`, and so is a guard condition that was not called.
- **`conditional` keys match by equality.** Enum members compare by value, and an `int` matches its decimal string. Keys are stored as strings; keys that would collide raise `ValueError`.
- **`is_instance` stores type as string.** The descriptor contains `"expected_type": "dict"`, not the actual type object.
- **Fixture vs `checks`:** The fixture evaluates and stores. `checks` just builds descriptors. Don't mix up which does what.
- **Results are always recorded.** Other plugins read them with `get_check_results(item)`.