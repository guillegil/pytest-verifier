# pytest-verify

[![CI](https://github.com/guillegil/pytest_verify/actions/workflows/ci.yml/badge.svg)](https://github.com/guillegil/pytest_verify/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/guillegil/pytest_verify)
[![pytest](https://img.shields.io/badge/pytest-7%2B-0a9edc)](https://docs.pytest.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A pytest plugin providing **soft assertions** for test verification. Failed checks never stop
the test — all checks run to completion, and failures are reported together at test end.

## Installation

Not published on PyPI — install from Git:

```bash
pip install "git+https://github.com/guillegil/pytest_verify.git"
```

Or, to pin a release:

```bash
pip install "git+https://github.com/guillegil/pytest_verify.git@v0.4.0"
```

Requires Python 3.9+, pytest 7+ and pluggy 1.2+.

## Quick Start

Use the `verify` fixture in any test — no imports needed:

```python
def test_power_supply(verify):
    verify.approx(measured_voltage, 3.3, abs_tol=0.05, name="Vout", units="V")
    verify.greater(throughput, 100, name="Throughput", units="Mbps")
    verify.between(current, 0.1, 0.5, name="Icc", units="A")
```

If any check fails, the test continues running. When the test body ends, all failures are
reported together in a single `ChecksFailedError`.

## Failure Output

When one or more checks fail, the test is reported as **failed** with a summary
listing the failed checks (`✗`) before the passed ones (`✓`), each with its index,
name, and an `expected … got …` detail:

```text
1 of 3 checks failed

  ✗ [1] Vout — expected 3.3V ± 0.05V, got 3.8V

  ✓ [0] PSU stable — True
  ✓ [2] Throughput — 120Mbps > 100Mbps
```

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
- `--pdb` opens the debugger when the failure is raised, after the test body has finished, so
  the test's local variables are gone. To inspect them, check the returned result,
  e.g. `if not check["passed"]: breakpoint()`.
- Rerun filters match exceptions by name, so use `--only-rerun ChecksFailedError` with
  pytest-rerunfailures.

### Checks that cannot be evaluated

Problems with the checked data never stop the test. If a comparison raises, the check fails and
the error is shown, for example comparing `None` with a number:

```text
  ✗ [1] Reading — expected > 100, got None (TypeError: '>' not supported between instances of 'NoneType' and 'int')
```

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

### Ordering & Range

| Function | Description |
|----------|-------------|
| `verify.greater(actual, threshold, *, name, units=None)` | `actual > threshold` |
| `verify.greater_equal(actual, threshold, *, name, units=None)` | `actual >= threshold` |
| `verify.less(actual, threshold, *, name, units=None)` | `actual < threshold` |
| `verify.less_equal(actual, threshold, *, name, units=None)` | `actual <= threshold` |
| `verify.between(actual, low, high, *, inclusive=True, name, units=None)` | Value within range |

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
| `verify.matches(actual, pattern, *, name)` | Regex search matches |

### Type, Collection & Conditional

| Function | Description |
|----------|-------------|
| `verify.is_instance(actual, expected_type, *, name)` | `isinstance(actual, expected_type)` |
| `verify.length(actual, expected, *, name)` | `len(actual) == expected` |
| `verify.all_satisfy(items, descriptor_factory, *, name)` | All items pass factory check |
| `verify.conditional(switch_value, *, cases, default=None, name)` | Check the case selected by a switch value |
| `verify.guard(branches, *, default=None, name)` | Check the first branch whose condition is true |
| `verify.fail(msg, *, name=None)` | Unconditional failure |

## Usage Examples

Most checks read like their table entry — `verify.equal(status, 200, name="Status")`.
The ones below take a little more setup.

### `conditional` — pick one branch by a switch value

Only the case whose key matches `switch_value` counts. Use `default` for the no-match case;
without one, no match is a failure. A key matches when it equals the switch value. Enum
members match by their value, and an int matches its decimal string, so
`cases={0: ..., 1: ...}` and `cases={"0": ..., "1": ...}` behave the same. Keys that would
match the same values, such as `1` and `"1"`, raise `ValueError`.

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
        default=verify.fail(f"Unknown mode: {mode}"),
    )
```

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

Conditions can be any truthy or falsy value. A failed guard reports the branch it took, e.g.
`✗ [0] Sensor output [→ below floor] — expected 0, got 7`. Passing a check as a condition
raises `TypeError`, because a check is always truthy. Use its result instead, e.g.
`check["passed"]`.

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

Each child is still evaluated when it is built, because it is an argument of the call, so an
unselected branch should not depend on values that only the selected branch can use. A child
that raises is just a failed child.

To also keep a check on its own, pass a copy: `verify.guard([(cond, "label", dict(check))], ...)`.
A composite built with the module-level `verify` is never recorded, so fixture checks passed to
it stay separate checks.

### `is_instance` — type check

Takes what `isinstance` takes: a class, a tuple of classes, or a union.

```python
verify.is_instance(response, dict, name="Response is a dict")
verify.is_instance(reading, (int, float), name="Reading is a number")
```

### `fail` — force a failure

Useful as the `default` branch of a `conditional`, or to mark an unreachable path.
`name` defaults to the message.

```python
verify.fail(f"Unexpected state: {state}")
```

## Module-Level API

For building descriptors outside of tests (e.g., in helper functions):

```python
from pytest_verify import verify

descriptor = verify.approx(3.28, 3.3, abs_tol=0.05, name="Vout")
result = verify.evaluate(descriptor)       # True / False
details = verify.evaluate_detailed(descriptor)  # [{passed, details, seq, t}]
```

Pass several checks as separate arguments: `verify.evaluate(*checks)`. Each result of
`evaluate_detailed` also has an `error` key when its check could not be evaluated.

A descriptor sent through JSON loses Python types: tuples become lists and dict keys become
strings. `equal((1, 2), [1, 2])` fails, but the same descriptor passes after a JSON round-trip.
Results recorded by the fixture carry their `passed` verdict, and `evaluate()` keeps it.

## Reading Results from Another Plugin

Reporters and other plugins read a test's checks with `get_check_results(item)`:

```python
from pytest_verify import get_check_results

def pytest_runtest_makereport(item, call):
    for check in get_check_results(item):
        print(check["name"], check["passed"], check["detail"])
```

It returns a copy of the checks the test recorded, in order. Each one is a plain dict with
`passed` and `detail`, and holds JSON-safe copies of the checked values taken when the check
was made, so `json.dumps` works on it. A check nested in `all_satisfy`, `conditional` or
`guard` is inside its parent. After a rerun, only the last attempt's checks are returned.
`pytest-reporter` uses this to render verification cards.

## Development

The project uses [uv](https://docs.astral.sh/uv/). Clone and run the test suite:

```bash
git clone https://github.com/guillegil/pytest_verify.git
cd pytest_verify
uv run pytest
```

Type-check with `uv run --with mypy mypy` (strict mode, configured in `pyproject.toml`).

CI runs on every push to `main` and every pull request targeting `main`
(see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)). It runs the test suite on
Python 3.9–3.13 and on the oldest supported pytest (7.0) and pluggy (1.2). It also runs
`mypy --strict` and the tests of the built sdist.

To release, bump `version` in `pyproject.toml`, move the `[Unreleased]` CHANGELOG entries under
the new version, merge, then push a `vX.Y.Z` tag or run the **Release** workflow
([`.github/workflows/release.yml`](.github/workflows/release.yml)). It publishes a GitHub
release with the CHANGELOG notes and the built sdist and wheel.

## Known Issues and Roadmap

Version 0.4.0 fixes every bug found by the review of 0.3.1. The report is in
[`bugs-0.3.1.md`](bugs-0.3.1.md), and `tests/test_regressions_*.py` keeps a regression test
for each bug.

Planned improvements and feature ideas are collected in
[`improvements-and-ideas.md`](improvements-and-ideas.md). [`CHECKLIST.md`](CHECKLIST.md) tracks
every item and the release that handles it. See [`CHANGELOG.md`](CHANGELOG.md) for what each
release changed.

## License

MIT
