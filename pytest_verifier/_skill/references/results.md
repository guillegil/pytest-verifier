# Reading check results from code

Use these instead of parsing the terminal output. Every recorded check is a plain, JSON-safe
dict (`json.dumps` works), with snapshots of the values taken when the check was made. Treat
records as read-only: they are shared, and changing `passed` does not change the outcome.

## Without code

- `--verify-json PATH` writes one JSON object per check and line: `{"nodeid": ...,
  "attempt": 1, "when": "call", "outcome": "failed", "index": 1, "check": {...}}`. `attempt`
  counts the test's runs from 1 (pytest-rerunfailures repeats a test in a new attempt: keep
  each test's last), `when` is the phase that judged the check, `outcome` that phase's report
  outcome, `index` the `[k]` of the summary, `check` the record (keys below). The path works
  like `--junitxml`'s (`~` and `$VARS` expanded); the file is replaced when the tests start.
  Works under pytest-xdist.
- `verify_junit_properties = failed` or `all` (ini, or `-o`) adds a junit `<property>` per
  check to its test case (the last attempt's, after a rerun): name `verify[k] <section ›
  name>`, value `passed: <detail>` or `failed: <detail>`. The default is `none`. pytest's
  default `junit_family` (xunit2) does not allow properties, so pytest-verifier warns; set
  `junit_family = xunit1`.

## Three ways in

| Way | Where it runs | Gets | Use for |
|---|---|---|---|
| `pytest_verifier.get_check_results(item)` | Any hook that has the item, in the process that ran the test | A new list of every check the test recorded so far, in order (the records themselves, not copies) | Reporters running in-process: `pytest_runtest_makereport` (every check once `call.when == "teardown"`) or after the `yield` of a `pytest_runtest_teardown` wrapper. A plain `pytest_runtest_teardown` runs before fixtures are torn down and misses their checks |
| `pytest_verify_results(item, when, checks, passed)` hook | The process that ran the test (an xdist worker) | The checks judged at the end of one phase, and whether all passed | Plugins that must not import pytest-verifier |
| `report.verify_checks` | Wherever reports go, including the xdist controller | The same checks as the hook, on that phase's `TestReport` | Anything that must work under pytest-xdist (`-n`) |

When checks are judged: checks made in setup and in the test body when the test body ends
(`when="call"`), checks made during teardown when teardown ends (`when="teardown"`), and with
`when="setup"` only when setup fails or skips. Each check is handed over once. `when` is the
phase that judged the checks; each check's own `phase` key says where it was made (checks
from fixture setup arrive with `when="call"`). After a rerun (pytest-rerunfailures),
`get_check_results` returns the last attempt's checks only, but the hook and
`report.verify_checks` deliver every attempt's checks: start a test's rows over at its
`setup` report, as below.

```python
# conftest.py: a hook that loads even where pytest-verifier is not installed
import pytest

@pytest.hookimpl(optionalhook=True)
def pytest_verify_results(item, when, checks, passed):
    print(item.nodeid, when, passed, [check["name"] for check in checks])
```

Collecting under xdist: read `getattr(report, "verify_checks", None) or []` in
`pytest_runtest_logreport` (it runs on the controller for every report; reports of phases
without checks have no such attribute) and write files in `pytest_sessionfinish`, skipping
workers (`hasattr(session.config, "workerinput")`). A `pytest_runtest_makereport` wrapper in a
conftest runs before the attribute is set and does not see it.

```python
import csv

ROWS = {}  # rows by test, so a rerun replaces the attempt before it

def pytest_runtest_logreport(report):
    if report.when == "setup":
        ROWS[report.nodeid] = []
    for check in getattr(report, "verify_checks", None) or []:
        ROWS[report.nodeid].append([report.nodeid, check.get("name", ""), check["passed"],
                                    check["detail"], check.get("location", "")])

def pytest_sessionfinish(session):
    if hasattr(session.config, "workerinput"):
        return
    rows = [row for test in ROWS.values() for row in test]
    with open("checks.csv", "w", newline="") as handle:
        csv.writer(handle).writerows([["test", "check", "passed", "detail", "location"], *rows])
```

## Keys of a recorded check

| Key | Value |
|---|---|
| `check_type` | `"equal"`, `"approx"`, ..., `"true"`/`"false"` for `is_true`/`is_false` |
| `name`, `description` | The label, and the statement such as `"Verify 'Vout' == 3.3V ± 0.05V"` |
| `passed`, `detail` | The verdict, and `"expected ... got ..."` (or the passing form) |
| `error` | Only when the check could not be evaluated: the exception, e.g. `"TypeError: ..."` |
| `phase` | `"setup"`, `"call"` or `"teardown"`: the test phase that made it |
| `location`, `called_from` | `"tests/test_psu.py:9"`, relative to the rootdir; `called_from` only when a helper made it. A check that was never recorded (a `checks` or hand-built child of a composite) has neither; `verify.record` gives it a `location` |
| `section` | Only for a check recorded in `verify.section` blocks: their titles, outermost first (`["3V3", "Load"]`) |
| values | Per type: `actual`, `expected`, `units`, `abs_tol`, `rel_tol`, `threshold`, `low`, `high`, `inclusive`, `haystack`, `needle`, `pattern`, `flags`, `expected_type`, `actual_length`, `msg` ... as JSON-safe snapshots: tuples become lists; sets, bytes, enums, `Decimal`, NaN, dicts with non-text keys and other objects become their repr text (`"Decimal('0.1')"`, `"nan"`). `units` given as a `str` enum member is stored as its text. A value of more than 10,000 items becomes shortened repr text, and `length` keeps only a preview of `actual` (repr text past 100 items, text cut at 240 characters): read `actual_length` |

Keys of the 0.10.0 checks:

- `raises`: `expected_type` (`"ValueError | KeyError"`), `expected_types`, `match`, `flags`,
  `raised_type` and `raised_message` (`None` when nothing was raised), `raised_at` (the
  `path:line` that raised), `type_check`, `match_check`. A block never used in a `with` is
  recorded with `error` (`"verify.raises() was never used in a with statement: ..."`), and
  keeps the `phase`, `section` and `location` where it was made, in the order it was made.
- `eventually`/`stable`: `timeout` or `duration`, `interval`, `tries`, `elapsed` (seconds),
  `settled_at` (`eventually`: the start of the passing try, else `None`), `trace` (a list of
  `[seconds, value, passed]`. `value` is the try's recorded `actual` when that is a number, text,
  a bool or `None`, else `None`; it is the snapshot, so a NaN reading is `"nan"` and a `Decimal`
  its repr text, and text is cut to 240 characters. `passed` is the try's verdict: its check and
  every other check it recorded passed), `value_changed` (whether that recorded value, before
  cutting, differed between any two tries; for `length`, whose record keeps only a preview,
  whether the preview or `actual_length` did), `child_checks` (the kept try), `also_failed` (only
  when there are some: the names, as text, of the other checks of the kept try that failed, which
  failed the try and the check), and `sample_error` when the kept try's sample raised. A failed
  check whose kept try's check passed (it failed through `also_failed`) has no margin in
  `--verify-summary=stats`.
- A check made by `verify.limits` from a row with a `source` has `limit_source`. Against a
  `Decimal` or `Fraction` measurement, its CSV limits written with a decimal point (or comma) or
  an exponent are that type too (repr text in the record, such as `"Decimal('3.2')"`); whole
  numbers stay `int`.

Composites nest their children. Each child that was evaluated carries its own `passed`; one
that was not selected carries no `passed` or `detail` (an eager child is still a dict, a lazy
one is `None`), so test `"passed" in child`, not `child is None`:

- `all_satisfy`: `child_checks`, a list.
- `conditional`: `switch_value`, `switch_label`, `matched_case` (the key as a string, or
  `None`), `cases` (a dict with string keys), `default`.
- `guard`: `matched_index` (or `None`), `branches` (a list of `{"condition", "label",
  "check"}`; a callable condition that was not called is `None`), `default`.

A nested check is only inside its parent; it is not listed on its own. Read optional keys
with `.get()`: `check_type` is always there; `name` and `description` on every check a
`verify` or `checks` method built (a hand-built descriptor keeps only its own keys); `passed`,
`detail` and `phase` on top-level records.

## Typing

`CheckDescriptor` is the `TypedDict` of a check, and `GuardBranch` the `TypedDict` of one entry
of a guard record's `branches` (`guard()` itself takes `(condition, label, check)` tuples);
both are exported by `pytest_verifier`, with `Verify`, `Require`, `ChecksFailedError`,
`LimitRow` (a row of a `verify.limits` table) and `Raises` (the `verify.raises` block,
generic in its exception type).
`pytest_verifier.__version__` is the installed version.

## Evaluating built checks yourself

`checks.evaluate(*descriptors)` returns `True` when every check passes, without recording
anything; a check that cannot be evaluated counts as failed.
`checks.evaluate_detailed(*descriptors)` returns one dict per check with `passed`, `details`
(the descriptor itself, not text), `seq` (its argument index), `t` (a timestamp) and `error`
when it could not be evaluated. Pass checks as separate arguments, not a list. A recorded check
keeps its recorded verdict. After a JSON round trip a descriptor can judge differently: tuples
become lists and dict keys become strings.
