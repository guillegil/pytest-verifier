# Reading check results from code

Use these instead of parsing the terminal output. Every recorded check is a plain, JSON-safe
dict (`json.dumps` works), with snapshots of the values taken when the check was made. Treat
records as read-only: they are shared, and changing `passed` does not change the outcome.

## Three ways in

| Way | Where it runs | Gets | Use for |
|---|---|---|---|
| `pytest_verifier.get_check_results(item)` | Any hook that has the item, in the process that ran the test | A copy of every check the test recorded so far, in order | Reporters running in-process (`pytest_runtest_makereport`, `pytest_runtest_teardown`) |
| `pytest_verify_results(item, when, checks, passed)` hook | The process that ran the test (an xdist worker) | The checks judged at the end of one phase, and whether all passed | Plugins that must not import pytest-verifier |
| `report.verify_checks` | Wherever reports go, including the xdist controller | The same checks as the hook, on that phase's `TestReport` | Anything that must work under pytest-xdist (`-n`) |

When checks are judged: checks made in setup and in the test body when the test body ends
(`when="call"`), checks made during teardown when teardown ends (`when="teardown"`), and with
`when="setup"` only when setup fails or skips. Each check is handed over once. `when` is the
phase that judged the checks; each check's own `phase` key says where it was made (checks
from fixture setup arrive with `when="call"`). After a rerun (pytest-rerunfailures),
`get_check_results` returns the last attempt's checks only.

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

ROWS = []

def pytest_runtest_logreport(report):
    for check in getattr(report, "verify_checks", None) or []:
        ROWS.append([report.nodeid, check["name"], check["passed"], check["detail"],
                     check.get("location", "")])

def pytest_sessionfinish(session):
    if hasattr(session.config, "workerinput"):
        return
    with open("checks.csv", "w", newline="") as handle:
        csv.writer(handle).writerows([["test", "check", "passed", "detail", "location"], *ROWS])
```

## Keys of a recorded check

| Key | Value |
|---|---|
| `check_type` | `"equal"`, `"approx"`, ..., `"true"`/`"false"` for `is_true`/`is_false` |
| `name`, `description` | The label, and the statement such as `"Verify 'Vout' == 3.3V ± 0.05V"` |
| `passed`, `detail` | The verdict, and `"expected ... got ..."` (or the passing form) |
| `error` | Only when the check could not be evaluated: the exception, e.g. `"TypeError: ..."` |
| `phase` | `"setup"`, `"call"` or `"teardown"`: the test phase that made it |
| `location`, `called_from` | `"tests/test_psu.py:9"`, relative to the rootdir; `called_from` only when a helper made it. Hand-built and `checks` descriptors have neither |
| `section` | Only for a check recorded in `verify.section` blocks: their titles, outermost first (`["3V3", "Load"]`) |
| values | Per type: `actual`, `expected`, `units`, `abs_tol`, `rel_tol`, `threshold`, `low`, `high`, `inclusive`, `haystack`, `needle`, `pattern`, `flags`, `expected_type`, `actual_length`, `msg` ... as JSON-safe snapshots: tuples become lists; sets, bytes, enums, `Decimal`, NaN, dicts with non-text keys and other objects become their repr text (`"Decimal('0.1')"`, `"nan"`) |

Composites nest their children, which carry their own `passed`; a child that was not selected
is `None` and carries no verdict:

- `all_satisfy`: `child_checks`, a list.
- `conditional`: `switch_value`, `switch_label`, `matched_case` (the key as a string, or
  `None`), `cases` (a dict with string keys), `default`.
- `guard`: `matched_index` (or `None`), `branches` (a list of `{"condition", "label",
  "check"}`), `default`.

A nested check is only inside its parent; it is not listed on its own. Read optional keys
with `.get()`: only `check_type`, `name` and `description` are always there, and `passed`,
`detail` and `phase` on top-level records.

## Typing

`CheckDescriptor` is the `TypedDict` of a check, and `GuardBranch` the tuple type of a `guard`
branch; both are exported by `pytest_verifier`, with `Verify`, `Require` and
`ChecksFailedError`. `pytest_verifier.__version__` is the installed version.

## Evaluating built checks yourself

`checks.evaluate(*descriptors)` returns `True` when every check passes, without recording
anything; a check that cannot be evaluated counts as failed. `checks.evaluate_detailed(*descriptors)`
returns one dict per check with `passed`, `details` (the descriptor itself, not text), `seq`
(its argument index), `t` (a timestamp) and `error` when it could not be evaluated. Pass checks as separate arguments, not a list. A recorded check keeps its
recorded verdict. After a JSON round trip a descriptor can judge differently: tuples become
lists and dict keys become strings.
