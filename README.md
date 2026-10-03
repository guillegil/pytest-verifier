# pytest-verifier

[![PyPI](https://img.shields.io/pypi/v/pytest-verifier)](https://pypi.org/project/pytest-verifier/)
[![CI](https://github.com/guillegil/pytest-verifier/actions/workflows/ci.yml/badge.svg)](https://github.com/guillegil/pytest-verifier/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/guillegil/pytest-verifier)
[![pytest](https://img.shields.io/badge/pytest-7%2B-0a9edc)](https://docs.pytest.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/guillegil/pytest-verifier/blob/main/LICENSE)

A pytest plugin providing **soft assertions** for test verification. Failed checks don't stop
the test unless you ask them to: all checks run to completion, and failures are reported
together at test end.

Until 0.5 it was called pytest-verify. See [Upgrading from pytest-verify](https://github.com/guillegil/pytest-verifier#upgrading-from-pytest-verify).

## Installation

```bash
pip install pytest-verifier
```

This installs the `pytest-verifier` distribution and the `pytest_verifier` package. pytest loads
the plugin automatically. When plugin autoloading is disabled (`PYTEST_DISABLE_PLUGIN_AUTOLOAD`),
load it with `-p pytest_verifier`; to turn it off for one run, use `-p no:pytest_verifier`.

Requires Python 3.9+, pytest 7+ and pluggy 1.2+. Releases up to 0.7.0 were not on PyPI; to
install one of them, or the development version, use Git:

```bash
pip install "git+https://github.com/guillegil/pytest-verifier.git@v0.7.0"
pip install "git+https://github.com/guillegil/pytest-verifier.git"
```

## Quick Start

Use the `verify` fixture in any test — no imports needed:

```python
def test_power_supply(verify):
    verify.approx(measured_voltage, 3.3, abs_tol=0.05, name="Vout", units="V")
    verify.greater(throughput, 100, name="Throughput", units="Mbps")
    verify.between(current, 0.1, 0.5, name="Icc", units="A")
```

If any check fails, the test continues running. When the test body ends, all failures are
reported together in a single `ChecksFailedError`. To stop a test at a check whose failure makes
the rest meaningless, see [Stopping a test at a failed check](https://github.com/guillegil/pytest-verifier#stopping-a-test-at-a-failed-check).

## Agent Skill

pytest-verifier ships an [Agent Skill](https://agentskills.io) that teaches coding agents, such
as Claude Code and Codex, to write tests with it: every `verify` method and when it passes,
composite checks, `verify.require`, helpers, and how to read a failure. Install it into your
project:

```bash
pytest-verifier skill install            # .claude/skills/ and .agents/skills/
pytest-verifier skill install --claude   # only .claude/skills/ (Claude Code)
pytest-verifier skill install --agents   # only .agents/skills/ (Codex and others; --generic works too)
pytest-verifier skill install --global   # in your home folder instead
```

The skill goes into a `pytest-verifier` folder there; commit it to share it with your team. It
describes the installed version of pytest-verifier, so run the command again after upgrading
(from the project's root folder, and with `--global` for the skill in your home folder): it
replaces the skill it installed before and says what it updated. When the skill in a project's
`.claude/skills` or `.agents/skills` is for another version, pytest's header says so, and
whether to update the skill or to upgrade pytest-verifier. A `pytest-verifier` folder that the
command did not install, such as a skill you wrote under that name, is left alone unless you
add `--force`. `python -m pytest_verifier skill install` does the same as the command.

## Failure Output

When one or more checks fail, the test is reported as **failed** with a summary. Its first line
names the first failed check, so `-r` summaries and junit reports say what failed. Then it lists
the failed checks (`✗`) before the passed ones (`✓`), each with its index, name, where a failed
check was made, and an `expected … got …` detail:

```text
1 of 3 checks failed: Vout — expected 3.3V ± 0.05V, got 3.8V

  ✗ [1] Vout (tests/test_psu.py:3) — expected 3.3V ± 0.05V, got 3.8V

  ✓ [0] PSU stable — True
  ✓ [2] Throughput — 120Mbps > 100Mbps
```

A failed check shows the file and line that made it, relative to the rootdir. When a helper
made it, the line of the test that called the helper follows:
`(lib/rails.py:8, called from tests/test_psu.py:22)`, or `called from line 22` when both are in
the same file. With `--tb=line`, pytest points at that line of the test.

How values read in the summary:

- Numbers read naturally, with their units: `3.3V`. Strings are quoted, so `expected 1, got '1'`
  shows that a reply was never converted. Enum members show as `Mode.ACTIVE`, and numeric ones
  add their value: `Gain.LOW (10dB)`.
- When two values still look the same, their types are added:
  `expected 0.1 (float), got 0.1 (Decimal)`.
- A long value is shortened to about 240 characters. When a failed `equal` shows the same text
  for both values, it says where they first differ:
  `first difference at [25]: expected 3.3V, got 3.9V`.
- `is_true` and `is_false` show the value and how it tests, e.g. `'0' (truthy)`.
- A composite says what went wrong inside it: the first three failing items of an
  `all_satisfy`, or the cases and branches it considered when none matched.
- A NaN gets a note, since it never compares equal and fails every ordering:
  `expected 1.0, got nan (NaN never compares equal)`.
- Each check stays on one line. Line breaks and other control characters in names and values
  are escaped (`\n`).
- At most 10 passed checks are listed, followed by `✓ … N more passed checks`. Run pytest with
  `-vv` to list them all, or set how many with `--verify-show-passed=N` (`all`, or `none`).
  Every check is still recorded (see
  [Reading Results from Another Plugin](https://github.com/guillegil/pytest-verifier#reading-results-from-another-plugin)).
- On a terminal that cannot show `✗` and `✓`, such as a Windows CI log, they print as `x` and
  `ok`, and any other character the terminal cannot show is escaped (`\u2014`). Only the
  terminal output changes: reports such as junitxml keep the summary as it is. For a log that
  shows Unicode wrongly although the terminal claims to support it, `--verify-ascii` forces
  this form.

### Output options

| Option | ini setting | Default | What it does |
|---|---|---|---|
| `--verify-show-passed=N\|all\|none` | `verify_show_passed` | `10` | How many passed checks a failure summary lists; `-vv` lists them all |
| `--verify-ascii` | `verify_ascii` | `false` | ASCII markers and escapes in the terminal, as on a terminal that cannot show Unicode |
| `--verify-summary=off\|failed\|all\|stats` | `verify_summary` | `off` | A section that counts each check name across the whole run |
| `--verify-json=PATH` | | | Every check to a JSON Lines file (see [Exporting Results](https://github.com/guillegil/pytest-verifier#exporting-results)) |
| | `verify_junit_properties` | `none` | Checks as junit `<property>` elements: `none`, `failed` or `all` |
| `--verify-fail-fast` | `verify_fail_fast` | `false` | Stop each test at its first failed check (see below) |

An option on the command line wins over its ini setting. `--verify-summary` adds a section
after the failures that groups the checks of every test by name, with their section titles,
including the checks inside composites. Names with a failed check come first; `failed` lists
only those, and `stats` adds the range of numeric values and the smallest margin, how close the
nearest value came to its limit (negative when it was past it; `0` at the limit itself, which
fails `greater`, `less` and an exclusive `between`):

```text
======================= pytest-verifier: checks by name ========================
  ✗ 3V3 › Vout: 1 of 2 failed (first: tests/test_rails.py::test_rail[hot]); 3.31V to 3.36V, margin -0.01V
  ✓ Current: 2 passed; 0.2A to 0.45A, margin 0.05A
```

Margins come from `approx` (the tolerance left), `between` (the distance to the nearer bound)
and the ordering checks (the distance to the threshold), for values that are plain numbers. A
test that pytest-rerunfailures runs again counts once, with its last attempt.

### When failures are raised

`ChecksFailedError` is an `AssertionError`, so pytest treats a soft failure like a failed
`assert`, and `xfail(raises=AssertionError)` catches it.

- Checks made in fixtures' setup and in the test body are raised after the test body.
- Checks made while fixtures are torn down are raised after teardown, as a teardown error.
  Their indices follow those of the test body.
- If the test already failed with another exception, that exception is reported and the
  failed checks are added to its report under **Soft assertion failures**. This works in
  unittest `TestCase`s too.
- A skip after a failed check does not hide the failure: `pytest.skip()`, `unittest.SkipTest`
  and `TestCase.skipTest()` alike.
- `--pdb` opens the debugger when the failure is raised. For soft checks that is after the
  test body has finished, so the test's local variables are gone. To inspect them, check the
  returned result, e.g. `if not check["passed"]: breakpoint()`, or make the check required
  (`verify.require`, or `--verify-fail-fast` for every check): then `--pdb` opens in the test,
  at the line of the failed check.
- With pytest-rerunfailures, rerun failed checks with `--only-rerun "checks failed"`.
  `--only-rerun ChecksFailedError` needs pytest-rerunfailures 15.1: older versions match the
  failure's message, which for failed checks does not name the exception.

### Stopping a test at a failed check

Some failures make the rest of a test meaningless, such as a link that could not be opened.
Make that check required: `verify.require` has the same methods as `verify`, and a check made
with it that fails stops the test at once with `ChecksFailedError`, listing every check made so
far.

```python
def test_link(verify):
    verify.equal(read_firmware(), "1.2.0", name="Firmware")
    link = open_link()
    verify.require.is_not_none(link, name="Link")   # stops here when there is no link
    verify.equal(link.status(), "ready", name="Status")
```

The first line of the summary then names the check that stopped the test, and `--tb=line`
points at its line:

```text
2 of 2 checks failed, stopped at [1]: Link — expected not None, got None (+1 more)

  ✗ [0] Firmware (tests/test_link.py:2) — expected '1.2.0', got '1.1.0'
  ✗ [1] Link (tests/test_link.py:4) — expected not None, got None
```

`verify.require(check)` does the same for a check made earlier or built with `checks`, for
example `verify.require(verify.equal(reply, "OK", name="Reply"))`. The check stays recorded, so
a test that catches the error still fails. A required check stops the test from inside a lazy
child or an `all_satisfy` factory too. In a fixture, a required check that fails is an error in
the test's setup or teardown, like a failed `assert` there. To pass `verify.require` to a
helper, annotate the parameter as `pytest_verifier.Require`.

To stop every test at its first failed check, for example while bringing up new hardware, run
pytest with `--verify-fail-fast`, or turn it on in the configuration:

```toml
[tool.pytest.ini_options]
verify_fail_fast = true
```

Fail-fast leaves checks made while fixtures are torn down soft, so a fixture's cleanup after a
failed check still runs, and so are the checks of a unittest `TestCase`'s `tearDown`,
`asyncTearDown` and cleanups; use `verify.require` to stop there. In a fixture's setup, a failed
check stops like an `assert`: if that happens before `yield`, the fixture's teardown does not
run either, so put cleanup that must run in `try`/`finally` or `request.addfinalizer`.

Every check is judged when it is made. With fail-fast, a check passed directly as a
`conditional` case or default, or a `guard` branch or default, is judged before the composite
chooses, so a failed one stops the test even when it would not be selected. That includes
`default=verify.fail(...)`, which always fails, and the checks an `all_satisfy` factory makes.
Pass cases, branches and defaults as functions (see [Lazy children](https://github.com/guillegil/pytest-verifier#lazy-children--build-only-the-selected-branch))
so that only the selected one is judged.

### Checks that cannot be evaluated

A comparison that raises never stops the test by itself: the check fails and the error is
shown (a required or fail-fast check then stops the test like any failed check), for example
comparing `None` with a number:

```text
  ✗ [1] Reading (tests/test_power.py:18) — expected > 100, got None (TypeError: '>' not supported between instances of 'NoneType' and 'int')
```

Text is never compared as a number. `"100" < "20"` is true for Python, which compares strings
letter by letter, so the ordering checks (`greater`, `less`, their `_equal` forms and `between`)
fail when the value and a limit are both a `str`, `bytes` or `bytearray`. Convert instrument
replies and values read from files first, e.g. `float(reply)`:

```text
  ✗ [2] Ripple (tests/test_power.py:19) — expected < '20', got '100' (TypeError: str values are compared as text, not as numbers; convert readings with float() first)
```

Text against a number fails with Python's own error, plus the same advice. Values that compare
with text on their own terms, such as a `semver.Version` against `"1.9.0"`, work as usual.

The same happens when a comparison returns something whose truth value is ambiguous, such as a
numpy array. Reduce it first: `verify.is_true((a == b).all(), name="Arrays equal")`.

Mistakes in how a check is called raise right away, like any Python error: a missing or
non-string `name`, a negative tolerance, `between` with `low` above `high`, or a malformed
`guard` branch.

## Check Functions

### Equality & Approximation

| Function | Description |
|----------|-------------|
| `verify.equal(actual, expected, *, name, units=None)` | `actual == expected` |
| `verify.not_equal(actual, expected, *, name, units=None)` | `actual != expected` |
| `verify.approx(actual, expected, *, abs_tol=None, rel_tol=None, name, units=None)` | Approximate equality (at least one tolerance required) |

With `units="%"`, the tolerance says whether it is absolute or relative: `50% ± 1% (abs)`.

### Ordering & Range

| Function | Description |
|----------|-------------|
| `verify.greater(actual, threshold, *, name, units=None)` | `actual > threshold` |
| `verify.greater_equal(actual, threshold, *, name, units=None)` | `actual >= threshold` |
| `verify.less(actual, threshold, *, name, units=None)` | `actual < threshold` |
| `verify.less_equal(actual, threshold, *, name, units=None)` | `actual <= threshold` |
| `verify.between(actual, low, high, *, inclusive=True, name, units=None)` | Value within range |

These compare numbers, or other values that order themselves such as version tuples and dates.
Text compared with text fails (see
[Checks that cannot be evaluated](https://github.com/guillegil/pytest-verifier#checks-that-cannot-be-evaluated)).

### Boolean & Identity

| Function | Description |
|----------|-------------|
| `verify.is_true(actual, *, name)` | `bool(actual) is True` |
| `verify.is_false(actual, *, name)` | `bool(actual) is False` |
| `verify.is_none(actual, *, name)` | `actual is None` |
| `verify.is_not_none(actual, *, name)` | `actual is not None` |

### String & Container

| Function | Description |
|----------|-------------|
| `verify.contains(haystack, needle, *, name)` | `needle in haystack` |
| `verify.not_contains(haystack, needle, *, name)` | `needle not in haystack` |
| `verify.matches(actual, pattern, *, name)` | Regex search matches; `pattern` can be a string or compiled with `re.compile` |

### Type, Collection & Conditional

| Function | Description |
|----------|-------------|
| `verify.is_instance(actual, expected_type, *, name)` | `isinstance(actual, expected_type)` |
| `verify.length(actual, expected, *, name)` | `len(actual) == expected` |
| `verify.all_satisfy(items, descriptor_factory, *, name)` | All items pass factory check |
| `verify.conditional(switch_value, *, cases, default=None, name)` | Check the case selected by a switch value |
| `verify.guard(branches, *, default=None, name)` | Check the first branch whose condition is true |
| `verify.fail(msg, *, name=None)` | Unconditional failure |

### Expected errors, settling values and limit tables

| Function | Description |
|----------|-------------|
| `with verify.raises(expected_exception, *, match=None, name):` | The block raises the expected exception (soft) |
| `verify.eventually(sample, *, timeout, interval=0.1, name)` | A check made by `sample` passes within `timeout` seconds |
| `verify.stable(sample, *, duration, interval=0.1, name)` | Every check made by `sample` for `duration` seconds passes |
| `verify.limits(measurements, table, *, on_missing="fail")` | One check per row of a limits table |
| `pytest_verifier.load_limits(path, *, select=None, columns=None, encoding="utf-8-sig")` | Reads a limits table from a CSV file |

The fixture also has `verify.record(check)`, which records a check built elsewhere (see
[Recording checks built by helpers](https://github.com/guillegil/pytest-verifier#recording-checks-built-by-helpers)), and `verify.require`,
whose checks stop the test when they fail (see
[Stopping a test at a failed check](https://github.com/guillegil/pytest-verifier#stopping-a-test-at-a-failed-check)).

## Usage Examples

Most checks read like their table entry — `verify.equal(status, 200, name="Status")`.
The ones below take a little more setup.

### `section` — group checks under a title

When the same checks run for several rails, channels or units, put each group in a section.
Every check recorded in the `with` block is named after the section in reports:

```python
RAILS = {"3V3": 3.3, "5V0": 5.0}

def test_rails(verify, dut):
    for rail, nominal in RAILS.items():
        with verify.section(rail):
            verify.approx(dut.vout(rail), nominal, rel_tol=0.02, name="Vout", units="V")
            verify.less(dut.ripple_mv(rail), 20, name="Ripple", units="mV")
```

```text
1 of 4 checks failed: 5V0 › Ripple — expected < 20mV, got 27.0mV

  ✗ [3] 5V0 › Ripple (tests/test_rails.py:7) — expected < 20mV, got 27.0mV

  ✓ [0] 3V3 › Vout — 3.31V == 3.3V ± 2%
  ✓ [1] 3V3 › Ripple — 12.0mV < 20mV
  ✓ [2] 5V0 › Vout — 4.98V == 5.0V ± 2%
```

Sections nest, and each recorded check keeps their titles, outermost first, in its `section`
field (`["5V0"]`). A check is in the sections of the code that records it:
`verify.record(check)` gives a check built elsewhere the section of that call. Sections follow
`contextvars`: an asyncio task created in a section is in it, and so is a thread that runs in
a copy of the context (`asyncio.to_thread`), but not a plain `threading.Thread` (except on
free-threaded Python 3.14, where threads inherit the context). A section opened around a
fixture's `yield` also covers the test body, except for an async fixture whose plugin runs its
setup and the test in different tasks (pytest-asyncio before 0.25).

### `conditional` — pick one branch by a switch value

Only the case whose key matches `switch_value` counts. Use `default` for the no-match case;
without one, no match is a failure. A key matches when it equals the switch value. Enum
members match by their value, and an int matches its decimal string, so
`cases={0: ..., 1: ...}` and `cases={"0": ..., "1": ...}` behave the same. Keys that would
match the same values, such as `1` and `"1"`, raise `ValueError`. When nothing matched and there
is no default, the summary lists the keys it tried: `[mode=7 → no case matched: 0, 1, 2]`.

```python
def test_output_by_mode(verify):
    verify.conditional(
        mode,                       # e.g. 0, 1, or 2
        name="Output voltage",
        cases={
            0: verify.approx(output, 0.0, abs_tol=0.01, name="Standby", units="V"),
            1: verify.approx(output, 3.3, abs_tol=0.1, name="Active", units="V"),
            2: verify.approx(output, 5.0, abs_tol=0.1, name="Boost", units="V"),
        },
        default=lambda: verify.fail(f"Unknown mode: {mode}"),
    )
```

The default is a function, so it fails only when it is selected (see
[Lazy children](https://github.com/guillegil/pytest-verifier#lazy-children--build-only-the-selected-branch)).
With `--verify-fail-fast`, make the cases functions too.

### `guard` — if / elif / else with arbitrary conditions

When the expected check depends on a chain of conditions (not a single switch value),
list them as ordered `(condition, label, check)` branches. The first branch whose
condition is true is evaluated; if none match, `default` is used. The label identifies
the chosen branch in the failure summary.

```python
def test_sensor_output(verify):
    verify.guard(
        branches=[
            (shutter_closed,     "shutter closed", verify.equal(reading, 0, name="Dark")),
            (level < floor - 5,  "below floor",    verify.equal(reading, 0, name="Dark")),
            (not sensor_enabled, "disabled",       verify.equal(reading, 0, name="Dark")),
        ],
        default=verify.approx(reading, expected_dn, abs_tol=2, name="Lit", units="DN"),
        name="Sensor output",
    )
```

Conditions can be any truthy or falsy value, and a condition that is a function is called
(see [Lazy children](https://github.com/guillegil/pytest-verifier#lazy-children--build-only-the-selected-branch)).
A failed guard reports the branch it took, e.g.
`✗ [0] Sensor output (tests/test_sensor.py:2) [→ below floor] — expected 0, got 7`, or the
labels it tried when none matched: `[→ no branch matched: shutter closed, below floor]`. When a
condition raises, the guard fails with `[→ no branch chosen]` and the error. Passing a
check as a condition raises `TypeError`, because a check is always truthy. Use its result
instead, e.g. `check["passed"]`.

### `all_satisfy` — apply one check to every item

The factory is called once per item to build a child check; the parent passes only if
**all** children pass. `items` can be any iterable. If the factory raises or returns something
that is not a check (for example, a forgotten `return`), the check fails with that error.

```python
def test_all_channels(verify):
    verify.all_satisfy(
        channel_voltages,                                  # e.g. [3.31, 3.29, 3.30]
        lambda v: verify.between(v, 3.2, 3.4, name="Channel", units="V"),
        name="All channels within spec",
    )
```

A failure names the first three failing items by their index:

```text
  ✗ [0] All channels within spec (tests/test_channels.py:2) — expected all 4 to pass, got 2 failed: [1] expected [3.2V, 3.4V], got 3.55V; [3] expected [3.2V, 3.4V], got 3.1V
```

### How child checks are counted

With the fixture, every check you pass to a composite (in `cases`, `branches`, `default`, or
built by the `all_satisfy` factory) belongs to the composite. It is not reported on its own,
and only the selected ones count toward the composite's verdict. This holds whether you build
the checks inline or earlier, for example in a `cases` dict:

```python
cases = {
    0: verify.approx(output, 0.0, abs_tol=0.01, name="Standby", units="V"),
    1: verify.approx(output, 3.3, abs_tol=0.1, name="Active", units="V"),
}
verify.conditional(mode, cases=cases, name="Output voltage")  # only the selected case counts
```

A child built this way is evaluated when it is built, because it is an argument of the call,
so it should not depend on values that only its own branch can use. A child that raises is
just a failed child. When that matters, build the children lazily (see below).

To also keep a check on its own, pass a copy, `verify.guard([(cond, "label", dict(check))], ...)`,
or record it with `verify.record(check)` before the composite. `verify.record()` of a check that a
composite took but did not select records a copy of it on its own.
A check that stopped the test (see [Stopping a test at a failed check](https://github.com/guillegil/pytest-verifier#stopping-a-test-at-a-failed-check))
always stays on its own as well, so the summary can name it and a test that catches the error
still fails; a composite made afterwards that selects it counts it once more.
A composite built with `checks` (see [Building checks without the fixture](https://github.com/guillegil/pytest-verifier#building-checks-without-the-fixture))
is never recorded, so fixture checks passed to it stay separate checks.

### Lazy children — build only the selected branch

A `conditional` case or default, and a `guard` check or default, can be a function with no
arguments that returns the check, such as a `lambda`. Only the selected one is called, so the
other branches never touch values they cannot use. A `guard` condition can be a function too.
Conditions are called in order until one is true, and the ones after it are not called. A
condition function that returns a check instead of a truth value makes the guard fail with an
error, because a check is always truthy.

```python
def test_sensor_output(verify):
    verify.guard(
        branches=[
            (lambda: sensor.shutter_closed(), "shutter closed",
             lambda: verify.equal(sensor.read(), 0, name="Dark")),
            (lambda: sensor.enabled(), "enabled",
             lambda: verify.approx(sensor.read(), 512, abs_tol=2, name="Lit", units="DN")),
        ],
        default=lambda: verify.fail("sensor disabled"),
        name="Sensor output",
    )
```

In the recorded check, a lazy child that was not called is `None`, and so is a condition that
was not called. If the selected function raises, or returns something that is not a check
(for example, a forgotten `return`), the composite fails and its `error` says why. Any other
check the function records while it runs stays a check of its own.

### `raises` — an error the code under test must raise

`with verify.raises(...)` checks that the block raises an exception, without stopping the
test. The check is recorded when the block ends:

```python
def test_rejects_overvoltage(verify, psu):
    with verify.raises(ValueError, match="out of range", name="Reject 7 V") as raised:
        psu.set_voltage(7)
    verify.approx(psu.voltage_setpoint(), 3.3, abs_tol=0.01, name="Setpoint unchanged", units="V")
```

- It passes when the block raised an instance of the class (or of a class in a tuple), and
  `match`, a regular expression, finds the exception's message (`str(exc)` and its notes).
- If nothing was raised, or the expected type was raised with another message, the check fails
  and the test goes on. Any other exception records the failed check, then goes on with its
  traceback, as with `pytest.raises`: it is a bug or a broken bench, not the behaviour under
  test. `pytest.skip`, `pytest.exit` and `KeyboardInterrupt` go on untouched.
- `raised.value` is the expected exception (else `None`), `raised.type` its class, and
  `raised.check` the recorded check, once the block has ended. The check's location is the
  `with` line, and `raised_at` in the record is the line that raised.
- `Exception` and `BaseException` need `match=`: on their own, a typo in the block would raise
  one and pass. A `verify.raises()` that is never used in a `with` statement becomes a failed
  check. `verify.require.raises` stops the test at the end of a block that failed.
  `checks.raises` raises `RuntimeError`: only the fixture can record a block's check.

### `eventually` and `stable` — values that settle, values that must hold

Readings take time to settle after power-up or a setpoint change. `verify.eventually` tries a
check again until it passes; `verify.stable` tries it again for a while, and every try must
pass. Both take a *sample*: a function with no arguments that reads the value and returns the
check.

```python
def test_power_up(verify, dut, psu):
    dut.power_on()
    verify.eventually(lambda: verify.equal(dut.state(), "READY", name="State"),
                      timeout=5, name="Boots")
    verify.stable(lambda: verify.approx(psu.vout(), 3.3, abs_tol=0.05, name="Vout", units="V"),
                  duration=2, interval=0.2, name="Vout steady")
```

- The sample is called at once, then each try starts `interval` seconds after the last one
  started. `eventually` stops at the first passing try, or when `timeout` has passed (the last
  try starts at it). `stable` stops at the first failed try, or once a try has started at
  `duration`, so it tries at least twice. Times are seconds or a `timedelta`.
- The record keeps one try as its child (the passing one or the last; the failed one or the
  passing one closest to its limit), and `tries`, `elapsed`, `settled_at` (`eventually`) and a
  `trace` of `[seconds, value, passed]` for the first and the last 50 tries.
- Failed tries never fail or stop the test, even with `verify.require` or `--verify-fail-fast`:
  a try that is not kept is dropped with every check it recorded. A sample that raises fails
  its try, and the tries go on. A usage error, such as a check without a tolerance, a sample
  without `return`, or the same check returned again (the value was read only once), fails the
  check at once.
- Both wait with `time.sleep`, in the thread that calls them. In an `async` test this blocks
  the event loop, and a `RuntimeWarning` says so.

### `limits` and `load_limits` — check measurements against a limits table

Production tests are often driven by a table of limits per product or corner. `verify.limits`
makes one ordinary check per row of the table, named by the row, of the measurement with the
same name, and returns the checks by name:

```python
LIMITS = {
    "3V3": {"low": 3.2, "high": 3.4, "units": "V"},
    "Vref": {"expected": 1.25, "abs_tol": 0.01, "units": "V"},
    "Ripple": {"high": 20, "units": "mV"},
    "FW": {"check": "matches", "pattern": r"\A2\.\d+\Z"},
}

def test_rails(verify, dut):
    verify.limits({"3V3": dut.v3v3(), "Vref": dut.vref(), "Ripple": dut.ripple_mv(),
                   "FW": dut.firmware()}, LIMITS)
```

A row holds the arguments of a check after the measured value. `"check"` names the check;
without it, the limits say which: `low` and `high` make a `between`, `low` alone a
`greater_equal` and `high` alone a `less_equal` (`greater`/`less` with `"inclusive": False`),
`expected` with `abs_tol` or `rel_tol` an `approx`, and `expected` alone an `equal` (a float
needs a tolerance, or `"check": "equal"`).

- Every row is checked before anything is recorded: limits must be finite numbers (not text,
  NaN or infinity), `rel_tol` a fraction below 1, and `inclusive` a `bool`. A wrong row raises
  and names the row.
- A row with no measurement fails with an error, `not measured: ...`, that names a similar
  measurement key when there is one. `on_missing="ignore"` makes no check for it instead.
  Measurements that have no row are not checked.
- `verify.require.limits` and `--verify-fail-fast` stop the test only after the whole table is
  recorded, so the report shows every row.
- `LimitRow` is the `TypedDict` of a row, for type checkers.

`load_limits` reads the table from a CSV file, such as one exported from Excel:

```text
# limits.csv
name;corner;Min;Max;units;Notes
3V3;;3,2;3,4;V;spec 4.2
3V3;hot|warm;3,1;3,5;V;derated
Ripple;;;20;mV;
```

```python
from pathlib import Path
from pytest_verifier import load_limits

LIMITS = load_limits(Path(__file__).with_name("limits.csv"), select={"corner": "hot"},
                     columns={"Min": "low", "Max": "high", "Notes": None})
```

- The file needs a `name` column; `check` and the argument columns are optional, and an empty
  cell is not given. Columns are separated by commas, semicolons or tabs, whichever gives a
  `name` column; with semicolons a decimal comma is a number. Blank lines and lines that start
  with `#` are skipped. The file is UTF-8 (Excel's "CSV UTF-8"); pass `encoding="cp1252"` for
  Excel's plain "CSV" on Windows.
- Column names match in any case. `columns` renames columns or skips them (`None`). Any other
  column is an error, so a misspelt limit is never ignored.
- Selector columns choose lines. With `select={"corner": "hot"}`, a line whose `corner` is
  `hot`, or lists it (`hot|warm`), beats one whose `corner` is empty, so a default line can be
  overridden per corner. A selected value that no line names is an error.
- Numeric columns must hold numbers (`3.3`, `-5`, `0x1F`). The `expected` of `equal` and
  `not_equal`, and `needle`, are compared as the measurement is: `1.10` stays text against a
  `str` reply and becomes a number against a number (a `Decimal` against a `Decimal`).
- Every line is checked when the file is read, and errors name the file and line. Each row gets
  `source`, such as `"limits.csv:3"`, which its record keeps as `limit_source`.

### `is_instance` — type check

Takes what `isinstance` takes: a class, a tuple of classes, or a union.

```python
verify.is_instance(response, dict, name="Response is a dict")
verify.is_instance(reading, (int, float), name="Reading is a number")
```

### `fail` — force a failure

Useful as the `default` branch of a `conditional`, or to mark an unreachable path.
`name` defaults to the message. As a default, pass it as a function,
`default=lambda: verify.fail("...")`: a check made directly is recorded as failed at once, and
with `--verify-fail-fast` it stops the test even when a case matches.

```python
verify.fail(f"Unexpected state: {state}")
```

## Building checks without the fixture

`checks` has the same methods as the fixture, but it only builds checks: each call returns an
unevaluated descriptor, and nothing is recorded. Use it in helper functions, or to evaluate
checks yourself:

```python
from pytest_verifier import checks

descriptor = checks.approx(3.28, 3.3, abs_tol=0.05, name="Vout")
result = checks.evaluate(descriptor)       # True / False
details = checks.evaluate_detailed(descriptor)  # [{passed, details, seq, t}]
```

Pass several checks as separate arguments: `checks.evaluate(*descriptors)`. Each result of
`evaluate_detailed` also has an `error` key when its check could not be evaluated.

A check built with `checks` cannot fail a test by itself. If the body of a test builds one and
it is not recorded, evaluated or passed to a composite by the end of the test's teardown, pytest
shows an `UnusedCheckWarning` that points at the line that built it. A fixture can therefore
collect checks from the test and record them when it is torn down. Checks built in fixtures, at
import time or in a unittest `TestCase` are not tracked, so a fixture can prepare checks for
later tests. Where building checks without using them is intended, filter the warning, for the
whole project or for one module:

```toml
[tool.pytest.ini_options]
filterwarnings = ["ignore::pytest_verifier.UnusedCheckWarning"]
# or only in tests/test_limits.py:
# filterwarnings = ["ignore::pytest_verifier.UnusedCheckWarning:tests.test_limits"]
```

With `-W error::pytest_verifier.UnusedCheckWarning`, the warning fails the test's teardown.

A descriptor sent through JSON loses Python types: tuples become lists and dict keys become
strings. `equal((1, 2), [1, 2])` fails, but the same descriptor passes after a JSON round-trip.
Results recorded by the fixture carry their `passed` verdict, and `evaluate()` keeps it.

### Recording checks built by helpers

A helper that builds checks with `checks` does not need the fixture. In the test, pass what it
returns to the fixture's `verify.record()`. The check is then judged and reported like any
other, and a composite absorbs the fixture checks passed to it.

```python
# helpers.py
from pytest_verifier import checks

def rail_ok(voltage):
    return checks.between(voltage, 3.2, 3.4, name="3V3 rail", units="V")

# test_power.py
def test_rails(verify):
    verify.record(rail_ok(measure("3V3")))
```

Calling `checks.record()` raises `RuntimeError`, because only the fixture records checks.

## Exporting Results

Two options write the checks to files, also under pytest-xdist.

`--verify-json PATH` writes every check as one line of JSON (JSON Lines), with the test it
belongs to:

```bash
pytest --verify-json results/checks.jsonl
```

Each line is one object; this is the failed `Ripple` check of the [`section`
example](https://github.com/guillegil/pytest-verifier#section--group-checks-under-a-title),
formatted:

```json
{
    "nodeid": "tests/test_rails.py::test_rails",
    "attempt": 1,
    "when": "call",
    "outcome": "failed",
    "index": 3,
    "check": {
        "check_type": "less",
        "name": "Ripple",
        "description": "Verify 'Ripple' < 20mV",
        "actual": 27.0,
        "threshold": 20,
        "units": "mV",
        "passed": false,
        "detail": "expected < 20mV, got 27.0mV",
        "phase": "call",
        "location": "tests/test_rails.py:7",
        "section": ["5V0"]
    }
}
```

`attempt` counts the runs of the test from 1: pytest-rerunfailures repeats a failed test in a
new attempt, so keep the lines of each test's last attempt. `when` is the test phase that judged
the check, `outcome` that phase's outcome (`"rerun"` for the report that made
pytest-rerunfailures repeat the test), `index` the check's `[k]` in the summary, and `check` the
recorded check (see
[Reading Results from Another Plugin](https://github.com/guillegil/pytest-verifier#reading-results-from-another-plugin)).
The path works like `--junitxml`'s: relative to where pytest runs, with `~` and environment
variables expanded, and folders created. The file is replaced when the tests start.

`verify_junit_properties` adds checks to the junit XML report (`--junitxml`) as properties of
their test case: `none` (the default), `failed` or `all`. With pytest-rerunfailures, only the
last attempt's checks are added.

```toml
[tool.pytest.ini_options]
verify_junit_properties = "failed"
junit_family = "xunit1"
```

```xml
<property name="verify[3] 5V0 › Ripple" value="failed: expected &lt; 20mV, got 27.0mV"/>
```

pytest's default junit family, `xunit2`, does not allow properties in its schema, so
pytest-verifier warns when it is used; tools that validate the report need `xunit1`, as with
pytest's own `record_property`.

## Reading Results from Another Plugin

Reporters and other plugins read a test's checks with `get_check_results(item)`:

```python
from pytest_verifier import get_check_results

def pytest_runtest_makereport(item, call):
    for check in get_check_results(item):
        print(check["name"], check["passed"], check["detail"])
```

It returns a new list of the checks the test recorded, in order. The checks are the recorded
ones, not copies, so treat them as read-only. Each one is a plain dict with
`passed`, `detail`, `phase` (`"setup"`, `"call"` or `"teardown"`, the test phase that made
it) and `location` (`"tests/test_psu.py:3"`, where it was made, relative to the rootdir). When
a helper made the check, `called_from` holds the line of the test function that led to it, and
a check made in a `verify.section` has the titles in `section` (`["3V3", "Load"]`). Each
check holds JSON-safe copies of the checked values taken when the check was made, so
`json.dumps` works on it. A check nested in `all_satisfy`, `conditional` or `guard` is inside
its parent. After a rerun, only the last attempt's checks are returned. `pytest-reporter` uses
this to render verification cards.

A plugin that should not import pytest-verifier can implement the `pytest_verify_results` hook
instead. It is called when a phase ends with checks to judge: when the test body ends, for the
checks made in setup and in the body, and when teardown ends, for the checks made in teardown.
If setup fails or skips, it is called with `when="setup"` for the checks made so far.
Mark it optional, so it also loads where pytest-verifier is not installed:

```python
import pytest

@pytest.hookimpl(optionalhook=True)
def pytest_verify_results(item, when, checks, passed):
    print(item.nodeid, when, passed, [check["name"] for check in checks])
```

The same checks are on that phase's test report as `report.verify_checks`. They are JSON-safe,
so they also reach the main process under pytest-xdist. Unlike `get_check_results`, the hook
and `report.verify_checks` also deliver the checks of attempts that pytest-rerunfailures
repeats (their report's outcome is `"rerun"`).

## Upgrading from 0.9

0.10.0 adds checks and fixes a few behaviours that could affect existing tests:

- `verify.record(check)` of a check that a composite took but did not select now records a
  copy of it on its own; before, it did nothing.
- `pytest.exit`, `unittest.SkipTest` and `bdb.BdbQuit` (quitting the debugger) raised in a lazy
  child, a guard condition or an `all_satisfy` factory now go on, instead of failing the
  composite.
- `verify.fail(msg)` with an empty or blank message no longer raises: the check is named `fail`.
- A `str` enum member used as a name is stored as its text.
- The reason of an xfailed test whose checks failed ends with `[N of M checks failed: ...]`.

## Upgrading from 0.7

- pytest-verifier is on PyPI: `pip install pytest-verifier`.
- The first line of a failure now names the first failed check:
  `2 of 5 checks failed: Vout — expected 3.3V ± 0.05V, got 3.8V (+1 more)`. A tool that
  matched the whole line `N of M checks failed` should match its start instead.
- Failed checks show where they were made, and recorded checks have the new keys `location`
  and `called_from`.
- With `--tb=line`, the line shown is the test's line that made the check the first line names
  instead of the `def` line.

## Upgrading from 0.6

Most of 0.7 changes how failures read. A few changes can affect existing tests:

- An ordering check between two texts, such as `verify.greater("100", "20", name=...)`, now
  fails with an error instead of comparing letter by letter. Convert readings with `float()`.
- Type checkers see narrower types: `length()` needs a sized value and `contains()` a
  container, both reject an `Optional` until it is narrowed, and the body of an `all_satisfy`
  `lambda` is checked against the type of the items.
- The summary text has new formats (quoted strings, type hints, at most 10 passed checks
  unless `-vv`). Code that needs the results should read them with
  [`get_check_results()` or `report.verify_checks`](https://github.com/guillegil/pytest-verifier#reading-results-from-another-plugin)
  rather than parse the text.

Every change is listed in the
[CHANGELOG](https://github.com/guillegil/pytest-verifier/blob/main/CHANGELOG.md).

## Upgrading from pytest-verify

Version 0.6.0 renamed the project, because another plugin on PyPI already uses the name
`pytest-verify` and its `pytest_verify` package. Tests that only use the `verify` fixture need
no change. The hook `pytest_verify_results` and `report.verify_checks` keep their names too.

First uninstall the old distribution, then install the new one:

```bash
pip uninstall pytest-verify
pip install pytest-verifier
```

If pytest-verify is still installed, for example after `pip install -U` from the same git URL,
pytest stops with a message that says so, because the two cannot be loaded together. Then update the imports and options that use the old name:

| 0.5 | 0.6 |
|-----|-----|
| `from pytest_verify import verify` | `from pytest_verifier import checks` |
| `from pytest_verify import get_check_results` (and the other names) | `from pytest_verifier import get_check_results` |
| `-p pytest_verify._fixture`, `pytest_plugins = ["pytest_verify._fixture"]` | `-p pytest_verifier`, `pytest_plugins = ["pytest_verifier"]` |
| `from pytest_verify._fixture import verify` in a conftest | `pytest_plugins = ["pytest_verifier"]` |
| `-p no:verify` | `-p no:pytest_verifier` |

`pytest_verifier.verify` still works as an alias of `checks` and shows a `DeprecationWarning`.
It will be removed in a future release.

## Development

The project uses [uv](https://docs.astral.sh/uv/). Clone and run the test suite:

```bash
git clone https://github.com/guillegil/pytest-verifier.git
cd pytest-verifier
uv run pytest
```

Type-check with `uv run --with mypy mypy` (strict mode, configured in `pyproject.toml`).

CI runs on every push to `main` and every pull request targeting `main`
(see [`.github/workflows/ci.yml`](https://github.com/guillegil/pytest-verifier/blob/main/.github/workflows/ci.yml)). It runs the test suite on
Python 3.9–3.13 and on the oldest supported pytest (7.0) and pluggy (1.2). It also runs
`mypy --strict` and the tests of the built sdist.

To release, bump `version` in `pyproject.toml`, move the `[Unreleased]` CHANGELOG entries under
a dated heading for the new version, merge, then push a `vX.Y.Z` tag or run the **Release**
workflow
([`.github/workflows/release.yml`](https://github.com/guillegil/pytest-verifier/blob/main/.github/workflows/release.yml)).
It builds the sdist and wheel once, runs the tests against the wheel with the oldest and the
newest pytest, tags the commit, uploads to PyPI with Trusted Publishing, and publishes a GitHub
release with the CHANGELOG notes. It only releases commits that are on `main`. Run it with the
`testpypi` target, from any branch, to try an upload on TestPyPI first. If a run fails half way,
use **Re-run failed jobs**, which reuses the files it built: an index never accepts other files
for a version it already has.

Publishing needs a one-time setup: on pypi.org and test.pypi.org, add this repository's
`release.yml` as a trusted publisher with the environment `pypi` (or `testpypi`), and in the
repository's Settings > Environments, limit `pypi` to the `main` branch and `v*` tags.

## Known Issues and Roadmap

Version 0.4.0 fixed every bug found by the review of 0.3.1. The report is in
[`bugs-0.3.1.md`](https://github.com/guillegil/pytest-verifier/blob/main/bugs-0.3.1.md), and `tests/test_regressions_*.py` keeps a regression test
for each bug.

Two behaviours are known and kept for now:

- With pytest subtests, failed checks belong to the whole test: the test fails with them, and
  the subtest that made them shows as passed. Group each iteration's checks with
  `verify.section(...)`, or parametrize the test. A per-subtest mode is planned.
- A unittest `@expectedFailure` test whose only failures are soft checks is reported as an
  unexpected success, because unittest saw no exception.

Planned improvements and feature ideas are collected in
[`improvements-and-ideas.md`](https://github.com/guillegil/pytest-verifier/blob/main/improvements-and-ideas.md).
[`CHECKLIST.md`](https://github.com/guillegil/pytest-verifier/blob/main/CHECKLIST.md) tracks every
item and the release that handles it. See
[`CHANGELOG.md`](https://github.com/guillegil/pytest-verifier/blob/main/CHANGELOG.md) for what
each release changed.

## License

MIT
