# CLAUDE.md — pytest-verify

## Project Overview

`pytest-verify` is a pytest plugin that provides soft assertions for test verification. It is the **judge** — the only plugin that determines pass/fail for check-based assertions. It works standalone and optionally integrates with `pytest-reporter` for rich HTML rendering.

**Specification:** The authoritative spec lives in Notion under "Pytest Verify" (child of "Pytest Reporter"). Always consult the spec for schema details, edge cases, and design decisions.

## Architecture

```
pytest-verify (this plugin)          pytest-reporter (separate plugin)
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
         │  pytest-verify never imports reporter. │
         │  Reporter reads results through        │
         │  pytest_verify.get_check_results(item),│
         │  the pytest_verify_results hook, or    │
         │  report.verify_checks.                 │
         └────────────────────────────────────────┘
```

## Core Principles

- **verify fixture is the primary API.** It evaluates checks immediately, records results, returns descriptor dicts, and raises `ChecksFailedError` once the phase that recorded a failed check ends (after the test body; after teardown for checks made in teardown). No imports needed — it's a pytest fixture.
- **verify module is the secondary API.** `from pytest_verify import verify` provides the same functions but returns unevaluated descriptors. Used standalone or for building descriptors to pass to `verify.evaluate()`.
- **Soft assertions.** Failed checks never stop the test, and neither does a check whose comparison raises (it fails with an `error` note). All checks run to completion. Failures are collected and raised as a single `ChecksFailedError` (an `AssertionError`) when the test body ends.
- **Pure data descriptors.** All check functions return plain dicts matching the CheckDescriptor schema. Results recorded by the fixture hold JSON-safe snapshots of the checked values.
- **No dependency on pytest-reporter.** The plugin works standalone and always records results on the item. Other plugins read them with `pytest_verify.get_check_results(item)`, the `pytest_verify_results` hook (declare it `optionalhook=True`) or `report.verify_checks`; there is no reporter detection.
- **One class per check type.** Everything specific to a `check_type` (build, compare, detail, children) lives in its class in `_checks/`. No other module branches on `check_type`; they dispatch through `REGISTRY`.

## Package Structure

```
pytest_verify/
├── __init__.py              # Exports: verify, Verify, CheckDescriptor, GuardBranch,
│                            #   ChecksFailedError, get_check_results
├── py.typed                 # PEP 561 marker — REQUIRED
├── _verify.py               # Verify class: typed public signatures and docstrings; each
│                            #   method builds a descriptor and hands it to a Sink
├── _checks/                 # One CheckType class per check_type, registered in REGISTRY
│   ├── _base.py             #   CheckType, CompositeType, REGISTRY, judge() (never raises),
│   │                        #   render_detail()
│   ├── _values.py           #   The 18 leaf check types (build, compare, detail)
│   └── _composites.py       #   all_satisfy, conditional, guard (children, lazy children)
├── _descriptors.py          # CheckDescriptor/GuardBranch TypedDicts, argument validation,
│                            #   conditional case matching, shared formatting
├── _evaluator.py            # evaluate(), evaluate_detailed()
├── _render.py               # Exception-safe str/repr/format, JSON-safe snapshots
├── _settle.py               # Turns a descriptor into a recorded result (verdict, detail,
│                            #   snapshots, child verdicts)
├── _run.py                  # Run (per-attempt, thread-safe state) and Recorder (the
│                            #   fixture's Sink: judges, records, absorbs children)
├── _fixture.py              # Plugin entry point: verify fixture, runtest hook wrappers that
│                            #   raise ChecksFailedError, report.verify_checks
├── _hookspecs.py            # pytest_verify_results hookspec
├── _stash.py                # check_results_key (read through get_check_results)
└── _exceptions.py           # ChecksFailedError, failure summary rendering
```

Adding a check type: a class in `_checks/` with `check_type`, a static `build`, `compare` and
`detail` (composites also `child_fields`, `children`, `chosen`, `combine`, `map_children`),
registered with `register()`; a `Verify` method that passes its descriptor to the sink; and
examples in `tests/test_contracts.py`, which fails until every registered type has them.

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
   - Record a copy with `passed`, `detail`, `phase` and JSON-safe snapshots of the values
   - A composite absorbs every recorded check passed to it as a child, by identity, whenever
     it was built (`dict(check)` keeps a copy standalone); each evaluated child carries its
     own `passed`, unselected children carry none
   - A lazy child (a callable) is just called: the checks it records go to the top level, and
     the composite then absorbs the one it returned like an eager child. If building the
     composite raises (a usage error, or `pytest.skip` in a lazy child), the checks passed
     to the call are absorbed; the ones a factory or lazy child made so far stay standalone
   - Return the recorded dict

2. **At the end of each phase (runtest hook wrappers):**
   - Checks recorded in setup and in the test body are judged after the test body; checks
     recorded in teardown after teardown. Any failure → raise `ChecksFailedError`
   - If the phase already raised, keep that error and add the summary to its report as a
     "Soft assertion failures" section (a skip, including `unittest.SkipTest`, never hides a
     failed check). A unittest `TestCase` records its failures and skips in `item._excinfo`
     instead of raising them, so the call phase reads that list too
   - `ChecksFailedError` message format: failed checks first, then passed, with `[seq]` indices
     (their index among all the test's records, so teardown checks continue the numbering)
   - The judged checks go to the `pytest_verify_results` hook and to that phase's report as
     `report.verify_checks`

3. **Reset:** Fresh run state per test attempt (reruns included). No state bleeds between tests.

### Results contract (shared with pytest-reporter §15.1)

Other plugins call `pytest_verify.get_check_results(item)`. A `StashKey` works by identity, so a
key created by another plugin can never see these results; the key in `_stash.py` is private.
Plugins that must not import pytest-verify implement `pytest_verify_results(item, when, checks,
passed)` with `optionalhook=True`, or read `report.verify_checks` (JSON-safe, survives xdist).

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

## Function Catalog (21 check functions, plus `record`)

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
| `verify.contains(haystack, needle, *, name)` | `"contains"` |
| `verify.not_contains(haystack, needle, *, name)` | `"not_contains"` |
| `verify.matches(actual, pattern, *, name)` | `"matches"` |

### Type / Collection / Conditional
| Function | check_type | Notes |
|----------|-----------|-------|
| `verify.is_instance(actual, expected_type, *, name)` | `"is_instance"` | Store type as string in descriptor |
| `verify.length(actual, expected, *, name)` | `"length"` | Stores `actual_length` in descriptor |
| `verify.all_satisfy(items, descriptor_factory, *, name)` | `"all_satisfy"` | Factory callable invoked at call time, not stored |
| `verify.conditional(switch_value, *, cases, default=None, name)` | `"conditional"` | Only matched branch evaluated; a case/default may be a callable, called only if selected |
| `verify.guard(branches, *, default=None, name)` | `"guard"` | First truthy `(condition, label, check)` branch evaluated; checks, default and conditions may be callables |
| `verify.fail(msg, *, name=None)` | `"fail"` | Always fails. name defaults to msg |

`verify.record(check)` (fixture only) judges and records a check built elsewhere, typically by
the module-level `verify`; on the module-level instance it raises `RuntimeError`.

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
- `true`: `bool(actual) is True`
- `false`: `bool(actual) is False`
- `is_none`: `actual is None`
- `is_not_none`: `actual is not None`
- `contains`: `needle in haystack`
- `not_contains`: `needle not in haystack`
- `matches`: `re.search(pattern, actual) is not None`
- `is_instance`: `isinstance(actual, expected_type)`, computed when the check is built (`instance_check`)
- `length`: `len(actual) == expected`
- `all_satisfy`: all child checks pass
- `conditional`: resolve matched case → evaluate child → parent passes if child passes
- `guard`: first branch with a truthy condition (else `default`) → parent passes if that child passes
- `fail`: always False

## Description Formatting

The `description` field is generated by the assertion engine. Format conventions:

- `verify.approx(v, 3.3, abs_tol=0.05, name="Vout", units="V")` → `"Verify 'Vout' == 3.3V ± 0.05V"`
- `verify.between(v, 0.1, 0.5, name="I", units="A")` → `"Verify 'I' ∈ [0.1A, 0.5A]"`
- `verify.between(v, 0.1, 0.5, inclusive=False, name="I")` → `"Verify 'I' ∈ (0.1, 0.5)"`
- `verify.greater(v, 100, name="T", units="Mbps")` → `"Verify 'T' > 100Mbps"`
- `verify.is_true(v, name="Alive")` → `"Verify 'Alive' is True"`
- `verify.conditional(1, cases={1: ...}, name="M")` → `"Verify 'M' [mode=1]"`
- `verify.fail("msg")` → `"FAIL: msg"`

When `units` is `None`, values appear without suffix: `"Verify 'Vout' == 3.3 ± 0.05"`

## Coding Standards

- Python 3.9+ (public hints use `Optional`/`Union`; see IDE Autocompletion above)
- PEP 8 with 100 character lines
- Type hints on all public APIs — this is non-negotiable for IDE autocompletion
- Docstrings (Google style) on all public methods
- pytest for testing (test the plugin itself with pytest)
- No external dependencies beyond pytest
- All descriptors must be JSON-serializable

## File Boundaries

- Safe to edit: `pytest_verify/`, `tests/`
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
- **Fixture vs module:** The fixture evaluates and stores. The module just builds descriptors. Don't mix up which does what.
- **Results are always recorded.** Other plugins read them with `get_check_results(item)`.