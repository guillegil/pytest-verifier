---
name: pytest-verifier
description: Write, fix and review pytest tests that use pytest-verifier soft assertions - the verify fixture (verify.equal, approx, between, greater, less, is_true, is_none, contains, matches, is_instance, length, all_satisfy, conditional, guard, fail, record, require, section), the checks builder, --verify-fail-fast, get_check_results and ChecksFailedError output. Use it whenever a test takes a verify argument, code imports pytest_verifier, the project depends on pytest-verifier, a run fails with ChecksFailedError or "N of M checks failed", or the user wants several checks in one test to all run and be reported together (soft assertions; measurements with limits, units and tolerances; hardware, lab, bench or production tests), even if the plugin is not named.
metadata:
  version: "0.9.0"
---

# pytest-verifier

Soft assertions for pytest. A test requests the `verify` fixture (no import) and calls
`verify.<check>(..., name=...)`. Each call judges its check at once, records it and returns
it; a failed check does not stop the test. When the test body ends, every failed check is
raised together as one `ChecksFailedError` (an `AssertionError`), so the test fails and its
report lists all the checks.

```python
def test_3v3_rail(verify, psu):
    vout = float(psu.read_voltage("3V3"))    # instrument replies are text: convert first
    verify.approx(vout, 3.3, rel_tol=0.02, name="3V3 Vout", units="V")
    verify.less(float(psu.read_ripple_mv("3V3")), 20, name="3V3 ripple", units="mV")
    verify.between(float(psu.read_current("3V3")), 0.1, 0.5, name="3V3 current", units="A")
```

## Rules that prevent most mistakes

- **Request the fixture.** Write `def test_x(verify):`; nothing is imported. `from
  pytest_verifier import verify` is a deprecated name for the `checks` builder, whose checks
  are never judged: a test written with it passes whatever the values.
- **Measured value first, then keywords.** Pass the measured value, then the expected value
  or limit, positionally (`equal(measured, expected)`, `greater(measured, limit)`): the report
  reads `expected <second>, got <first>`. Everything else is keyword-only: `name=`, `units=`,
  `abs_tol=`, `rel_tol=`, `inclusive=`. `name` is required on every check except `fail` and
  must be a `str`. Make names specific and unique within the test (`f"{rail} ripple"`, or
  `"Ripple"` in a `verify.section(rail)`, not a bare `"ripple"` in a loop): the report
  identifies checks by name.
- **Soft checks never raise.** Do not `assert verify.equal(...)` or write `if verify.equal(...)`
  (the returned dict is always truthy), and do not wrap checks in `try`/`except` or
  `pytest.raises`: nothing is raised until the test body ends. To branch on a verdict, read
  `check["passed"]`; to stop at a failure, use `verify.require` (below). A check returns its
  record (a dict of JSON-safe snapshots), never the value: keep your own variable.
- **Convert text replies first** (`float(reply)`). Ordering checks and `between` fail with
  an error when both sides are text; `approx` fails on text; `equal("3.3", 3.3)` fails. Never
  test a text reply for truth: `is_true("0")` passes. Compare it: `equal(reply, "1")`. Turn a
  `timedelta` into seconds (`elapsed.total_seconds()`).
- **Floats go through `approx`**, never `equal` (`0.1 + 0.2 != 0.3`). Give `abs_tol` or
  `rel_tol`; with both, either one passing is enough (make two checks if both limits must
  hold). `rel_tol` is a fraction of `abs(expected)`: 0.02 is 2%, and `rel_tol=2` silently
  accepts ±200%. When `expected` can be 0 use `abs_tol`. Leave headroom:
  `approx(3.35, 3.3, abs_tol=0.05)` fails by float rounding.
- **Call mistakes raise; bad values fail.** A missing or positional `name`, `approx` without a
  tolerance, a negative tolerance, `between` with `low > high` or a type `isinstance` rejects
  raises at the call and stops the test. A value that cannot be compared (`None > 3`, an
  ambiguous numpy truth value, a regex on an `int`) never raises: the check fails and its
  detail ends with the error, e.g. `(TypeError: ...)`.
- **Use the most specific check.** Its failure says what was expected and what was got:
  `between` beats `is_true(lo <= x <= hi)`, `length` beats `equal(len(x), n)`.
- **`units` is only a label**, appended to numbers as written (`"V"` gives `3.3V`, `" ms"`
  gives `3 ms`), and only the numeric checks below take it. Nothing is converted.

## Checks

The same methods exist on `verify`, on `verify.require` and on the `checks` builder.

| Call | Passes when | Watch out |
|---|---|---|
| `equal(actual, expected, *, name, units=None)` | `actual == expected` | Python equality: `1 == 1.0 == True`. Not for floats |
| `not_equal(actual, expected, *, name, units=None)` | `actual != expected` | Passes for NaN |
| `approx(actual, expected, *, abs_tol=None, rel_tol=None, name, units=None)` | equal, or `abs(a - e) <= abs_tol`, or `<= rel_tol * abs(e)` | Needs a tolerance. Numbers only, no lists. Exact for `int`/`Decimal`/`Fraction` unless any operand is a `float` |
| `greater(actual, threshold, *, name, units=None)` | `actual > threshold` | Also `greater_equal` (`>=`), `less` (`<`), `less_equal` (`<=`) |
| `between(actual, low, high, *, inclusive=True, name, units=None)` | `low <= actual <= high` | `inclusive=False` excludes both bounds |
| `is_true(actual, *, name)`, `is_false(...)` | `bool(actual)` is `True` / `False` | Truthiness: `is_true("0")`, `is_true("False")` and an uncalled method pass |
| `is_none(actual, *, name)`, `is_not_none(...)` | `actual is None` / `is not None` | `0`, `""` and `False` are not `None` |
| `contains(haystack, needle, *, name)`, `not_contains(...)` | `needle in haystack` | Haystack first. Text: substring; dict: keys only; list: whole items, so `not_contains(lines, "error")` misses `"error: x"` (join the lines). A generator is consumed |
| `matches(actual, pattern, *, name)` | `re.search(pattern, actual)` finds a match | Matches anywhere: anchor with `\A...\Z` for the whole string. No `flags=`: use `re.compile(p, re.I)` or `(?i)`. `actual` must be text |
| `is_instance(actual, expected_type, *, name)` | `isinstance(actual, expected_type)` | Class, tuple or union (`int \| None`). `list[int]`, `Literal[...]` and `None` raise `TypeError`: use `list`, `type(None)` |
| `length(actual, expected, *, name)` | `len(actual) == expected` | Pass a sized value (`list(gen)`) and an `int` |
| `fail(msg, *, name=None)` | never | `name` defaults to `msg` |

## Composite checks

`all_satisfy`, `conditional` and `guard` give one verdict from child checks. Every check passed
to a composite belongs to it: it is reported inside the composite and not on its own, and only
the selected children count (an unselected child that failed does not fail the test). Pass
`dict(check)` to keep a copy on its own as well.

**`all_satisfy(items, descriptor_factory, *, name)`** applies a check to every item. The
factory is called once per item, at the call, and must return one check (not a bool). An
empty iterable passes, so check the count when it matters. A factory that raises or returns
no check (a `def` without `return`) fails the composite at that item, and later items are not
checked. The failure names the first
three failing items by index: `expected all 8 to pass, got 1 failed: [2] expected [3.2V,
3.4V], got 3.47V`.

```python
voltages = dut.channel_voltages()
verify.length(voltages, 8, name="Channel count")
verify.all_satisfy(
    voltages,
    lambda v: verify.between(v, 3.2, 3.4, name="Channel", units="V"),
    name="Channels in range",
)
```

**`conditional(switch_value, *, cases, default=None, name)`** checks the case whose key equals
`switch_value`, else `default`; with no match and no default it fails, listing the keys it
tried. Enum members match their value, and an `int` matches its decimal string (`1` and
`"1"`); nothing else is converted (`"1\n"`, `"01"`, `1.0` do not match), so turn a text reply
into the key type first. An unmatched value silently takes `default`, so make the default a
failure (`lambda: verify.fail(...)`) unless other values are valid. Keys that would collide
(`1` and `"1"`) raise `ValueError`.

**`guard(branches, *, default=None, name)`** is if/elif/else: branches are
`(condition, label, check)` tuples in that order, and the first branch whose condition is
truthy is checked, else `default`. A swapped tuple is not detected (a label is always truthy).
The label names the chosen branch in the report (`[→ boost]`). A condition must not be a check
(`TypeError`, a check is always truthy): use `check["passed"]`. A condition that raises fails
the guard, and `default` is not used.

**Lazy children.** A case, a branch's check or a default can be a zero-argument callable that
returns the check; only the selected one is called. An eager child is an ordinary argument:
it is built and judged before the composite chooses, so a reading only valid in its own
branch raises there, and `default=verify.fail(...)` is recorded as failed at once. Use lambdas
for those, and for every child under `--verify-fail-fast`. Guard conditions can be callables
too, called in order until one is true. In a loop, bind the variable: `lambda m=m: ...`.

```python
mode = dut.mode()
verify.conditional(
    mode,
    cases={
        0: lambda: verify.approx(dut.output_voltage(), 0.0, abs_tol=0.01, name="Standby", units="V"),
        1: lambda: verify.approx(dut.output_voltage(), 3.3, abs_tol=0.1, name="Active", units="V"),
        2: lambda: verify.less(dut.boost_current(), 2.0, name="Boost current", units="A"),
    },
    default=lambda: verify.fail(f"Unknown mode {mode}", name="Mode"),
    name="Output by mode",
)
```

A lazy child that raises, or returns something that is not a check, fails the composite with
that error. Checks a lazy child records besides the one it returns stay separate checks. To
check several things in one case, return a group:
`lambda: verify.all_satisfy([verify.equal(...), verify.less(...)], lambda c: c, name="Boost")`.

## Grouping checks

`with verify.section(title):` puts every check recorded in the block under `title`: the
report names them `3V3 › Vout`, and each record gets `section` (`["3V3"]`). Sections nest
(`["3V3", "Load"]`). A check gets the section of the code that records it, so
`verify.record(check)` gives the section of that call, and a thread started in the block is
outside it. A section opened around a fixture's `yield` also covers the test body.
`checks.section` raises `RuntimeError`.

```python
for rail, nominal in {"3V3": 3.3, "5V0": 5.0}.items():
    with verify.section(rail):
        verify.approx(float(dut.vout(rail)), nominal, rel_tol=0.02, name="Vout", units="V")
        verify.less(float(dut.ripple_mv(rail)), 20, name="Ripple", units="mV")
```

## Stopping a test at a failed check

Use `verify.require` when a failure makes the rest of the test meaningless (no connection, no
reply). It has every check method, and `verify.require(check)` takes a check made earlier or
built with `checks`. A failed required check raises `ChecksFailedError` at once, listing the
checks made so far. It stays recorded, so catching the error does not make the test pass.
`verify.require.all_satisfy`/`conditional`/`guard` make only the composite required.

```python
idn = psu.identify()
verify.require.is_not_none(idn, name="PSU answers")   # stops here when it does not answer
verify.require(verify.matches(idn, r"^ACME,", name="PSU model"))
```

`--verify-fail-fast`, or `verify_fail_fast = true` in the pytest configuration, makes every
check stop the test at its first failure (`-o verify_fail_fast=false` turns the setting off
for one run). Checks made while fixtures are torn down stay soft, and so do a unittest
`TestCase`'s `tearDown` and cleanups.

## Fixtures, helpers and checks built elsewhere

- `verify` is function-scoped: a class-, module- or session-scoped fixture that requests it
  fails with `ScopeMismatch`, and a `verify` kept for a later test raises `RuntimeError`.
- In a function-scoped fixture, a failed check before `yield` does not stop setup: it is
  reported with the test body's checks. A failed check after `yield` is a teardown error, so
  the test shows as passed plus one error. A required check that fails before `yield` is a
  setup error, and that fixture's teardown does not run: put cleanup in `try`/`finally`.
- A unittest `TestCase` reaches the fixture through an autouse fixture that stores it:
  `@pytest.fixture(autouse=True)` on `def _verify(self, verify): self.verify = verify`.
- Threads may make checks; join them before the test returns, or their checks are lost. A
  child process cannot make checks: return its values and check them in the test.
- Soft failures belong to the whole test, not to a pytest subtest: group each iteration's
  checks with `verify.section(f"ch{i}")`, or parametrize the test.
- `fixture 'verify' not found` means the plugin is not loaded (for example with
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD`): add `-p pytest_verifier`.
- A helper can take the fixture as a parameter; annotate it `Verify` (or `Require` for
  `verify.require`) from `pytest_verifier`. A failed check a helper made shows the helper's
  line and `called from` the test's line.
- `from pytest_verifier import checks` builds the same checks without judging or recording
  them. Record one with `verify.record(check)` (or `verify.require(check)`) right after
  building it, or pass it to a composite. `checks.evaluate(*descs)` (also `verify.evaluate`)
  only returns a bool (`True` when all pass) and records nothing: a bare call throws the
  verdict away without any warning, so assert it or, better, record the checks. `assert checks.equal(...)` is a silent pass: a `checks`
  check built in a test and never used only triggers `UnusedCheckWarning`.
  `checks.record()` and `checks.require` raise `RuntimeError`.
  `pytest_verifier.verify` is a deprecated alias of `checks`.

```python
from pytest_verifier import checks

def rail_ok(voltage: float):
    return checks.between(voltage, 3.2, 3.4, name="3V3 rail", units="V")

def test_rails(verify, psu):
    verify.record(rail_ok(float(psu.read_voltage("3V3"))))
```

## Reading a failure

```text
2 of 3 checks failed: 5V0 Vout — expected 5.0V ± 2%, got 4.71V (+1 more)

  ✗ [1] 5V0 Vout (tests/test_psu.py:9) — expected 5.0V ± 2%, got 4.71V
  ✗ [2] 3V3 ripple (tests/test_psu.py:10) — expected < 20mV, got 27.0mV

  ✓ [0] 1V8 Vout — 1.803V == 1.8V ± 2%
```

- The first line counts the failures and repeats the first one. After a required check it
  reads `N of M checks failed, stopped at [k]: ...`.
- Failed checks (`✗`) come before passed ones (`✓`); `[k]` numbers the test's checks in the
  order they were made (a check passed to a composite is listed inside it, not on its own).
  A failed check shows where it was made (`path:line`, plus `called from` the test line when a
  helper made it), then `expected ... got ...`. Strings are quoted, so `expected 1, got '1'`
  means a conversion is missing; types are added when two values print alike.
- A note in parentheses, `(TypeError: ...)`, means the check could not be evaluated.
- At most 10 passed checks are listed; `-vv` lists all. A terminal that cannot print `✗`/`✓`
  shows `x`/`ok`.
- Read the FAILURES and ERRORS sections, not the short `FAILED nodeid - ...` lines: without a
  terminal those are cut at 80 columns (`-vv` prints them whole). `ChecksFailedError` itself
  is not printed: search for `checks failed`.
- When the test raised another exception, its traceback comes first and the failed checks
  follow under "Soft assertion failures" (not shown with `--tb=no`). Checks that failed after
  a fixture's `yield` appear under `ERROR at teardown of <test>`. A skip after a failed check
  does not hide it: the test fails.
- A passing test prints none of its checks. To see them, print what the `verify` calls return
  (run with `-s`), or use the results hook below.
- `xfail(raises=AssertionError)` catches `ChecksFailedError`; with pytest-rerunfailures use
  `--only-rerun ChecksFailedError`.

## Results for reports and plugins

To read results from code (a conftest, a reporter, a CSV export), read
[references/results.md](references/results.md): `get_check_results(item)`, the
`pytest_verify_results` hook (`optionalhook=True`), `report.verify_checks` (works under
pytest-xdist) and the keys of a recorded check. Do not parse the terminal text.

## This skill's version

This skill describes pytest-verifier 0.9.0. If the project uses another version
(`pip show pytest-verifier`), run `pytest-verifier skill install` to install the matching
skill.
