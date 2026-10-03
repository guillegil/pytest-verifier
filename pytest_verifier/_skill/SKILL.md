---
name: pytest-verifier
description: Write, fix and review pytest tests that use pytest-verifier soft assertions - the verify fixture (verify.equal, approx, between, greater, less, is_true, is_none, contains, matches, is_instance, length, all_satisfy, conditional, guard, fail, raises, eventually, stable, limits, record, require, section), load_limits for CSV limit files, the checks builder, --verify-fail-fast, get_check_results and ChecksFailedError output. Use it whenever a test takes a verify argument, code imports pytest_verifier, the project depends on pytest-verifier, a run fails with ChecksFailedError or "N of M checks failed", or the user wants several checks in one test to all run and be reported together (soft assertions; measurements with limits, units and tolerances; limit tables; values that must settle or stay stable; expected errors; hardware, lab, bench or production tests), even if the plugin is not named.
metadata:
  version: "0.10.0"
---

# pytest-verifier

Soft assertions for pytest. A test requests the `verify` fixture (no import) and calls
`verify.<check>(..., name=...)`. Each call judges its check at once, records it and returns
it; a failed check does not stop the test. When the test body ends, its failed checks are
raised together as one `ChecksFailedError` (an `AssertionError`), so the test fails and its
report lists every failed check (checks made in fixture teardown are judged after teardown).

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
  `pytest.raises`: nothing is raised until the test body ends (to check that code raises, use
  `with verify.raises(...)`, below). To branch on a verdict, read
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
  tolerance, a negative or NaN tolerance, `between` with `low > high`, or an `is_instance` type
  that is not a class, tuple or union (`list[int]`, `None`) raises at the call and stops the
  test; inside an `all_satisfy` factory or a lazy child it fails the composite instead, with
  the error in its detail. A check whose comparison raises (`None > 3`, an ambiguous numpy
  truth value, a regex on an `int`, `isinstance` with a plain `Protocol`) does not raise: it
  fails, and its detail ends with the error, e.g. `(TypeError: ...)`.
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
| `approx(actual, expected, *, abs_tol=None, rel_tol=None, name, units=None)` | equal, or `abs(a - e) <= abs_tol`, or `<= rel_tol * abs(e)` | Needs a tolerance. Numbers only, no lists. Exact only when the values and the tolerances are all `int`/`Decimal`/`Fraction` (not `Decimal` with `Fraction`): a `float` anywhere, `abs_tol=0.05` included, compares in floats |
| `greater(actual, threshold, *, name, units=None)` | `actual > threshold` | Also `greater_equal` (`>=`), `less` (`<`), `less_equal` (`<=`) |
| `between(actual, low, high, *, inclusive=True, name, units=None)` | `low <= actual <= high` | `inclusive=False` excludes both bounds |
| `is_true(actual, *, name)`, `is_false(...)` | `bool(actual)` is `True` / `False` | Truthiness: `is_true("0")`, `is_true("False")` and an uncalled method pass |
| `is_none(actual, *, name)`, `is_not_none(...)` | `actual is None` / `is not None` | `0`, `""` and `False` are not `None` |
| `contains(haystack, needle, *, name)`, `not_contains(...)` | `needle in haystack` | Haystack first. Text: substring; dict: keys only; list: whole items, so `not_contains(lines, "error")` misses `"error: x"` (join the lines). A generator is consumed |
| `matches(actual, pattern, *, name)` | `re.search(pattern, actual)` finds a match | Matches anywhere: anchor with `\A...\Z` for the whole string. No `flags=`: use `re.compile(p, re.I)` or start the pattern with `(?i)`. `actual` must be text |
| `is_instance(actual, expected_type, *, name)` | `isinstance(actual, expected_type)` | Class, tuple or union (`Optional[int]`, or `int \| None` from Python 3.10). `list[int]`, `Literal[...]` and `None` raise `TypeError`: use `list`, `type(None)` |
| `length(actual, expected, *, name)` | `len(actual) == expected` | Pass a sized value (`list(gen)`) and an `int` |
| `fail(msg, *, name=None)` | never | `name` defaults to `msg` |

## Composite checks

`all_satisfy`, `conditional` and `guard` give one verdict from child checks. Every check a
composite takes as a child (a case, a branch's check, a default, or what an `all_satisfy`
factory returns) belongs to it: it is reported inside the composite and not on its own, and
only the selected children count (an unselected child that failed does not fail the test).
Pass `dict(check)`, or record the check with `verify.record(check)` before the composite, to
keep it on its own as well; `verify.record` of a child that was not selected records a copy.

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
`"1"`); otherwise it is plain `==`: `1.0` and `True` select `1` but not `"1"`, and `"1\n"` and
`"01"` match neither, so turn a text reply into the key type first. An unmatched value
silently takes `default`, so make the default a failure (`lambda: verify.fail(...)`) unless
other values are valid. Keys that would collide (`1` and `"1"`) raise `ValueError`.

**`guard(branches, *, default=None, name)`** is if/elif/else: branches are
`(condition, label, check)` tuples in that order, and the first branch whose condition is
truthy is checked, else `default`. A swapped tuple is not detected (a label is always truthy).
The label names the chosen branch in the report (`[→ boost]`). A condition must not be a check
(`TypeError`, a check is always truthy): use `check["passed"]`. A callable condition that
raises (or whose truth test raises) fails the guard, and `default` is not used; a condition
written inline runs before `guard` does, so it raises in the test: make it a lambda.

**Lazy children.** A case, a branch's check or a default can be a zero-argument callable that
returns the check; only the selected one is called. An eager child is an ordinary argument:
it is built and judged before the composite chooses, so a reading only valid in its own
branch raises there, and `default=verify.fail(...)` is judged failed at once (with fail-fast it
stops the test even when a case matches). Use lambdas for those, and for every child under
`--verify-fail-fast`. Guard conditions can be callables too, called in order until one is
true. In a loop, bind the variable: `lambda m=m: ...`.

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

## Expected errors

`with verify.raises(expected_exception, *, match=None, name) as raised:` records a `raises`
check when the block ends, and the test goes on. It passes when the block raised an instance
of the class (or of a class in a tuple) whose message `match` (a regex, searched in `str(exc)`
and its notes) finds. Nothing raised, or the expected type with another message: a failed
check, and the test goes on. Any other exception records the failed check, then goes on with
its traceback, as with `pytest.raises`; `pytest.skip`, `pytest.exit` and `KeyboardInterrupt`
go on untouched.

```python
with verify.raises(ValueError, match="out of range", name="Reject 7 V") as raised:
    psu.set_voltage(7)
if raised.value is not None:   # the ValueError, else None; raised.type is its class
    verify.equal(raised.value.args[0], "out of range", name="Reject message")
```

- Put only the call that must raise in the block: the lines after it do not run.
- `Exception` and `BaseException` need `match=` (`TypeError` otherwise: a typo in the block
  would raise one and pass). `match` is a regex: `re.escape()` text with `(`, `.` or `[`.
- `raised` is a `Raises`; `raised.check` is the record once the block has ended
  (`RuntimeError` before). A `verify.raises()` never used in a `with` fails at the end of the
  phase. `verify.require.raises` stops the test at the end of a failed block.
  `checks.raises` raises `RuntimeError`.

## Values that settle or must hold

`verify.eventually(sample, *, timeout, interval=0.1, name)` passes once a check made by
`sample` passes within `timeout` seconds. `verify.stable(sample, *, duration, interval=0.1,
name)` passes when every check made for `duration` seconds passes, and stops at the first
that fails. `sample` is a zero-argument callable that reads the value and returns the check:
a value read before the call never changes (the failure then says so).

```python
verify.eventually(lambda: verify.less(dut.temperature(), 40, name="Temp", units="C"),
                  timeout=30, interval=1, name="Cools down")
verify.stable(lambda: verify.approx(psu.vout(), 3.3, abs_tol=0.05, name="Vout", units="V"),
              duration=2, interval=0.2, name="Vout steady")
```

- Each try starts `interval` seconds after the last one started; times are seconds or a
  `timedelta`. `eventually` tries until the timeout (the last try starts at it); `stable` at
  least twice when `duration > 0`.
- The record keeps one try as its child (the passing one or the last; the failing one or the
  passing one closest to its limit), plus `tries`, `elapsed`, `settled_at` (`eventually`) and
  a `trace` of `[seconds, value, passed]` for the first and last 50 tries.
- Failed tries never fail or stop the test: a try that is not kept is dropped with every
  check recorded in it. A sample that raises fails its try, and the tries go on; a usage error
  (no tolerance, no `return`, a comparison instead of a check) fails the check at once.
- It waits with `time.sleep`: in an `async` test it blocks the event loop (`RuntimeWarning`).

## Limits tables

`verify.limits(measurements, table, *, on_missing="fail")` makes one ordinary check per row
of `table` (a dict of `LimitRow`s), named by the row, of the measurement with the same name,
and returns the checks by name. A row holds the check's arguments after the value and,
under `"check"`, the method; without it the limits say: `low`+`high` is `between`, `low` is
`greater_equal`, `high` is `less_equal` (`"inclusive": False` gives `greater`/`less`),
`expected` with `abs_tol`/`rel_tol` is `approx`, and `expected` alone (not a float) `equal`.

```python
LIMITS = {
    "3V3": {"low": 3.2, "high": 3.4, "units": "V"},
    "Ripple": {"high": 20, "units": "mV"},
    "FW": {"check": "matches", "pattern": r"\A2\.\d+\Z"},
}
verify.limits({"3V3": vout, "Ripple": ripple_mv, "FW": fw}, LIMITS)
```

- Rows are strict and checked before anything is recorded: limits are finite numbers (not
  text), `rel_tol` is below 1, and a float `expected` needs a tolerance or `"check": "equal"`.
- A row with no measurement fails with an `error` that starts `not measured` and names a
  similar key (`on_missing="ignore"` skips it). Measurements without a row are not checked:
  keys must match row names exactly (an `int` key matches its decimal string).
- `verify.require.limits` and fail-fast stop only after the whole table is recorded.

`load_limits(path, *, select=None, columns=None, encoding="utf-8-sig")` reads such a table
from a CSV file: a `name` column, an optional `check` column and a column per argument; an
empty cell is not given, and `#` lines are skipped.

```python
LIMITS = load_limits(Path(__file__).with_name("limits.csv"), select={"corner": "hot"},
                     columns={"Min": "low", "Max": "high", "Notes": None})
```

- Column names match in any case; `columns` renames a column or skips it (`None`). Any other
  column is an error, so a misspelt limit is never ignored.
- A selector column chooses lines: with `select={"corner": "hot"}`, a line whose `corner` is
  `hot` (or lists it: `hot|cold`) beats one whose `corner` is empty. A selected value that no
  line names is an error.
- Numeric cells must be numbers (`3,3` only in a `;` file). The `expected` of `equal`/
  `not_equal` and `needle` take the measurement's type when checked: `1.10` stays text against
  a `str` reply and is a number against a number (a `Decimal` against a `Decimal`).
- Every line is checked when the file is read (errors name `path:line`), and each record gets
  `limit_source` (`"limits.csv:12"`). Save Excel files as "CSV UTF-8", or pass `encoding`.

## Grouping checks

`with verify.section(title):` puts every check recorded in the block under `title`: the
report names them `3V3 › Vout`, and each record gets `section` (`["3V3"]`). Sections nest
(`["3V3", "Load"]`). A check gets the section of the code that records it, so
`verify.record(check)` gives the section of that call. Sections follow `contextvars`: an
asyncio task or `asyncio.to_thread` call made in the block is in it, a `threading.Thread` is
not (except on free-threaded Python 3.14). A section opened around a sync fixture's `yield` also covers the test body.
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
check stop the test at its first failure. `-o verify_fail_fast=false` turns the ini setting off
for one run; nothing turns the command-line option off, so set fail-fast with the ini setting,
not in `addopts`. Fail-fast leaves checks soft while fixtures are torn down and in a unittest
`TestCase`'s `tearDown` and cleanups; `verify.require` still stops there, cutting the rest of
that teardown short.

## Fixtures, helpers and checks built elsewhere

- `verify` is function-scoped: a class-, module- or session-scoped fixture that requests it
  fails with `ScopeMismatch`, and a `verify` kept for a later test raises `RuntimeError`.
- In a function-scoped fixture, a failed check before `yield` does not stop setup: it is
  reported with the test body's checks. A failed check after `yield` is a teardown error, so
  the test shows as passed plus one error. A required check (or, with fail-fast, any check)
  that fails before `yield` is a setup error, and that fixture's teardown does not run: put
  cleanup in `try`/`finally`.
- A unittest `TestCase` reaches the fixture through an autouse fixture that stores it:
  `@pytest.fixture(autouse=True)` on `def _verify(self, verify): self.verify = verify`.
- Threads may make checks; join them before the test returns, or their checks are lost. A
  required (or fail-fast) check that fails in a thread stops only that thread: the test goes
  on and fails at its end. A child process cannot make checks: return its values and check
  them in the test.
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
  verdict away without any warning, so assert it or, better, record the checks.
  `assert checks.equal(...)` always passes (a dict is truthy); a `checks` check built in a
  test body and never used gives `UnusedCheckWarning`, but not in a unittest `TestCase`.
  `checks.record()`, `checks.require(...)` and `checks.require.<check>(...)` raise
  `RuntimeError`. `pytest_verifier.verify` is a deprecated alias of `checks`.

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

- The first line counts the failures and repeats the first one. When a required check (or
  fail-fast) stopped the test, it reads `N of M checks failed, stopped at [k]: <that check>`
  instead, even if an earlier check failed.
- Failed checks (`✗`) come before passed ones (`✓`); `[k]` numbers the test's checks in the
  order they were made. A composite's children get no `[k]`: its line names up to three
  failed children, numbered by their position in the composite.
  A failed check shows where it was made (`path:line`, plus `called from` the test line when a
  helper made it), then `expected ... got ...`. Strings are quoted, so `expected 1, got '1'`
  means a conversion is missing; types are added when two values print alike.
- A note that names an exception, `(TypeError: ...)`, means the check could not be evaluated
  (its record has `error`); `(truthy)`, `(NaN ...)` or type names only explain the verdict.
- At most 10 passed checks are listed (`--verify-show-passed=N`, `all` or `none` changes
  that); `-vv` lists all. A terminal that cannot print `✗`/`✓` shows `x`/`ok`, and
  `--verify-ascii` forces that form.
- `--verify-summary=failed` (or `all`, or `stats` with value ranges and the smallest margin
  to a limit) adds a section after the failures that counts each check name across every
  test: `✗ 3V3 › Vout: 3 of 12 failed (first: <nodeid>)`. These three options are also ini
  settings (`verify_show_passed`, `verify_ascii`, `verify_summary`); the option wins.
- Read the FAILURES and ERRORS sections, not the short `FAILED nodeid - ...` lines: those are
  cut to the terminal width (80 columns without a terminal; whole when `CI` is set, or with
  `-vv` from pytest 8.2). Search for `checks failed`: the name `ChecksFailedError` shows only
  in a traceback chain.
- When the test raised another exception, its traceback comes first and the failed checks
  follow under "Soft assertion failures" (not shown with `--tb=no`). Checks that failed after
  a fixture's `yield` appear under `ERROR at teardown of <test>`. A skip after a failed check
  does not hide it: the test fails (an error, when a fixture skipped in setup).
- A passing test prints none of its checks. To see them, print what the `verify` calls return
  (run with `-s`), or use the results hook below.
- A test that xfails (a marker, or `pytest.xfail()` after a failed check) stays XFAIL, and its
  reason ends with the first line, `[N of M checks failed: ...]`.
- `xfail(raises=AssertionError)` catches `ChecksFailedError`; with pytest-rerunfailures use
  `--only-rerun "checks failed"` (`--only-rerun ChecksFailedError` needs version 15.1).

## Results for reports and plugins

Do not parse the terminal text. For a file of every check, run with
`--verify-json checks.jsonl`: one JSON object per check (`nodeid`, `attempt`, `when`,
`outcome`, `index`, and the recorded `check`), also under pytest-xdist. `verify_junit_properties = failed`
(or `all`) in the pytest configuration adds checks to the `--junitxml` report as
`<property name="verify[1] 3V3 › Vout" value="failed: expected ...">`; set
`junit_family = xunit1` if a tool validates the report. To read results from code (a
conftest, a reporter), read [references/results.md](references/results.md):
`get_check_results(item)`, the `pytest_verify_results` hook (`optionalhook=True`),
`report.verify_checks` (works under pytest-xdist) and the keys of a recorded check.

## This skill's version

This skill describes pytest-verifier 0.10.0; the `plugins:` line of pytest's header shows the
project's version (`verifier-X.Y.Z`). For another version from 0.9.0 on, run
`pytest-verifier skill install` in the project root (with `--global` when this skill is in
`~/.claude/skills` or `~/.agents/skills`). Versions before 0.9.0 have no skill and no
`pytest-verifier` command.
