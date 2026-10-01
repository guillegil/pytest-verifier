# Known bugs in pytest-verify 0.3.1

> **Status: all 45 bugs are fixed in 0.4.0.** See [`CHANGELOG.md`](CHANGELOG.md) for what
> changed and [`CHECKLIST.md`](CHECKLIST.md) for the status of every review item. The entries
> below describe 0.3.1 as it was reviewed; line numbers refer to that version.

This report comes from a deep review of `pytest-verify` 0.3.1 (commit `5bf4adf`, tag `v0.3.1`).
Every entry was reproduced with a probe script, then reproduced again by an independent
reviewer who wrote their own probe, and judged against the spec (`.claude/CLAUDE.md`), the
README and the docstrings. Nothing here is speculative.

Probes ran on Python 3.9 (pytest 8.4), 3.10 to 3.13 (pytest 9.0) and, for plugin interop,
Python 3.11 with pytest 9.1, pytest-xdist 3.8, pytest-rerunfailures 16.7 and mypy.

Every bug except L-11 (a build requirement) and the documentation entries has a regression
test that failed on 0.3.1 and passes since 0.4.0. The **Pinned by** line at the end of each
entry names it. Most live in `tests/test_regressions_*.py` (first added as strict `xfail` tests
in `tests/test_known_bugs_*.py`). The thread races are covered by `tests/test_threads.py`, the
typing bug by the `mypy --strict` CI job over `tests/typing_usage.py`, and L-12 by the CI job
that builds the sdist and runs its tests.

Non-bug findings (design improvements, API ideas, tooling) are in
[`improvements-and-ideas.md`](improvements-and-ideas.md).

## Severity scale

| Severity | Meaning |
|----------|---------|
| **Critical** | Crashes the whole pytest run, or gives a wrong verdict silently in common usage. |
| **High** | Breaks a core promise: a soft check stops the test, a verdict is wrong in plausible usage, or a common pytest feature (xfail, skip, teardown) is misreported. |
| **Medium** | Wrong behaviour in less common but legitimate usage, misleading output, differences between Python versions, or typing that contradicts the docs. |
| **Low** | Cosmetic issues, rare edge cases, packaging details, minor performance. |
| **Docs** | The code is fine but the documentation says something false. |

## Summary

| ID | Severity | Bug | Fixed in |
|----|----------|-----|----------|
| [C-1](#c-1-a-non-bool-comparison-result-crashes-the-whole-session) | Critical | A non-bool comparison result (numpy/pandas style) crashes the whole session | 0.4.0 |
| [C-2](#c-2-building-the-failure-summary-can-crash-the-whole-session) | Critical | Building the failure summary can crash the whole session | 0.4.0 |
| [H-1](#h-1-a-check-that-raises-stops-the-test-and-is-never-recorded) | High | A check that raises stops the test and is never recorded | 0.4.0 |
| [H-2](#h-2-guard-and-conditional-evaluate-every-branch-not-just-the-matched-one) | High | `guard` and `conditional` evaluate every branch, not just the matched one | 0.4.0 |
| [H-3](#h-3-composite-checks-re-judge-their-children-and-can-contradict-them) | High | Composite checks re-judge their children and can contradict them | 0.4.0 |
| [H-4](#h-4-reusing-a-recorded-check-inside-a-composite-deletes-it) | High | Reusing a recorded check inside a composite deletes it, so the test passes | 0.4.0 |
| [H-5](#h-5-module-level-is_instance-matches-by-class-name-only) | High | Module-level `is_instance` matches by class name only | 0.4.0 |
| [H-6](#h-6-conditional-matches-cases-with-str-so-results-depend-on-the-python-version) | High | `conditional` matches cases with `str()`, so results depend on the Python version | 0.4.0 |
| [H-7](#h-7-checks-recorded-during-teardown-are-ignored) | High | Checks recorded during teardown are ignored and the test passes | 0.4.0 |
| [H-8](#h-8-soft-failures-are-misreported-under-xfail-and-hidden-by-skip) | High | Soft failures are misreported under `xfail` and hidden by `skip` | 0.4.0 |
| [M-1](#m-1-approx-rel_tol-accepts-values-outside-the-advertised-band) | Medium | `approx` `rel_tol` accepts values outside the advertised band | 0.4.0 |
| [M-2](#m-2-is_instance-crashes-on-tuples-and-unions-of-types) | Medium | `is_instance` crashes on tuples and unions of types | 0.4.0 |
| [M-3](#m-3-descriptors-are-not-json-serializable-for-common-inputs) | Medium | Descriptors are not JSON-serializable for common inputs | 0.4.0 |
| [M-4](#m-4-reports-show-values-after-mutation-not-the-values-that-were-checked) | Medium | Reports show values after mutation, not the values that were checked | 0.4.0 |
| [M-5](#m-5-every-checked-value-is-kept-alive-until-the-session-ends) | Medium | Every checked value is kept alive until the session ends | 0.4.0 |
| [M-6](#m-6-conditional-evaluator-and-renderer-disagree-after-a-json-round-trip) | Medium | `conditional` evaluator and renderer disagree after a JSON round-trip | 0.4.0 |
| [M-7](#m-7-the-results-stash-accumulates-across-reruns) | Medium | The results stash accumulates across reruns | 0.4.0 |
| [M-8](#m-8-a-clean-rerun-is-failed-with-the-previous-attempts-checks) | Medium | A clean rerun is failed with the previous attempt's checks | 0.4.0 |
| [M-9](#m-9-pytest-9-subtests-one-failing-check-fails-every-later-subtest) | Medium | pytest 9 subtests: one failing check fails every later subtest | 0.4.0 |
| [M-10](#m-10-soft-failures-next-to-a-hard-failure-are-missing-from-junitxml) | Medium | Soft failures next to a hard failure are missing from junitxml | 0.4.0 |
| [M-11](#m-11-soft-failures-recorded-in-setup-vanish-when-setup-errors) | Medium | Soft failures recorded in setup vanish when setup errors | 0.4.0 |
| [M-12](#m-12---pdb-and-pytest_exception_interact-ignore-soft-failures) | Medium | `--pdb` and `pytest_exception_interact` ignore soft failures | 0.4.0 |
| [M-13](#m-13-other-makereport-hooks-can-see-a-soft-failed-test-as-passed) | Medium | Other `makereport` hooks can see a soft-failed test as passed | 0.4.0 |
| [M-14](#m-14-the-fixture-without-its-hook-turns-every-failure-into-a-pass) | Medium | The fixture without its hook turns every failure into a pass | 0.4.0 |
| [M-15](#m-15-recording-checks-from-several-threads-loses-checks) | Medium | Recording checks from several threads loses checks | 0.4.0 |
| [M-16](#m-16-type-hints-reject-documented-usage) | Medium | Type hints reject documented usage | 0.4.0 |
| [L-1](#l-1-conditional-case-keys-that-collide-after-str-are-silently-overwritten) | Low | `conditional` case keys that collide after `str()` are silently overwritten | 0.4.0 |
| [L-2](#l-2-approx-loses-precision-with-decimal-fraction-and-big-int) | Low | `approx` loses precision with `Decimal`, `Fraction` and big `int` | 0.4.0 |
| [L-3](#l-3-a-composite-that-raises-leaves-its-children-behind) | Low | A composite that raises leaves its children behind as standalone checks | 0.4.0 |
| [L-4](#l-4-conditional-message-shows-a-different-key-than-the-one-it-looked-up) | Low | `conditional` message shows a different key than the one it looked up | 0.4.0 |
| [L-5](#l-5-a-huge-int-crashes-a-passing-check) | Low | A huge `int` crashes a passing check | 0.4.0 |
| [L-6](#l-6-checksfailederror-cannot-be-pickled-or-copied) | Low | `ChecksFailedError` cannot be pickled or copied | 0.4.0 |
| [L-7](#l-7---tbline-prints-the-summary-twice) | Low | `--tb=line` prints the summary twice | 0.4.0 |
| [L-8](#l-8-pytest-8-short-summary-gives-no-reason-for-soft-failures) | Low | pytest 8 short summary gives no reason for soft failures | 0.4.0 |
| [L-9](#l-9-concurrent-first-checks-race-on-the-stash) | Low | Concurrent first checks race on the stash | 0.4.0 |
| [L-10](#l-10-typingget_type_hints-fails-on-python-39) | Low | `typing.get_type_hints` fails on Python 3.9 | 0.4.0 |
| [L-11](#l-11-builds-fail-with-setuptools-68-to-76) | Low | Builds fail with setuptools 68 to 76 | 0.4.0 |
| [L-12](#l-12-the-sdist-cannot-run-its-own-tests) | Low | The sdist cannot run its own tests | 0.4.0 |
| [D-1](#d-1-checksfailederror-is-never-raised) | Docs | `ChecksFailedError` is documented as raised but never is | 0.4.0 |
| [D-2](#d-2-the-readme-guard-example-crashes-and-guard-is-missing-from-the-catalogs) | Docs | The README `guard` example crashes, and `guard` is missing from the catalogs | 0.4.0 |
| [D-3](#d-3-reporter-detection-and-shared-stash-key-docs-are-obsolete) | Docs | Reporter-detection and shared-stash-key docs are obsolete | 0.4.0 |
| [D-4](#d-4-claudemd-conditional-example-raises-typeerror) | Docs | `CLAUDE.md` `conditional` example raises `TypeError` | 0.4.0 |
| [D-5](#d-5-changelog-and-tags-disagree) | Docs | CHANGELOG and tags disagree | 0.4.0 |
| [D-6](#d-6-readme-says-ci-runs-on-every-push) | Docs | README says CI runs on every push | 0.4.0 |
| [D-7](#d-7-claudemd-package-map-is-out-of-date) | Docs | `CLAUDE.md` package map is out of date | 0.4.0 |

## Root causes at a glance

Most of the 45 entries come from five design decisions. Fixing these removes whole groups of
bugs at once, which is the better path than patching each entry separately.

1. **The verdict is applied by rewriting the call report instead of raising.**
   `pytest_runtest_makereport` (`_fixture.py:186-207`) sets `report.outcome = "failed"` and a
   plain-string `longrepr`, and only looks at the call phase. That causes H-7, H-8, M-9, M-10,
   M-11, M-12, M-13, L-7, L-8 and D-1. Raising `ChecksFailedError` at the end of the call phase
   (a `pytest_runtest_call` wrapper), plus handling setup and teardown, fixes all of them.
2. **The verdict is whatever the comparison returned, computed with no error handling.**
   `_record` stores `_evaluate_single(...)` as-is (`_fixture.py:59`). That causes C-1, H-1 and
   L-3. One guarded `_judge()` that coerces to `bool` and turns exceptions into failed checks
   fixes them.
3. **Fixture children are evaluated eagerly and then discarded by identity.** Branches and
   cases are function arguments, so they are recorded before the composite exists
   (`_fixture.py:50-71`). That causes H-2, H-4, L-3, M-15 and the quadratic slowdown.
   Accepting lazy children (callables) and claiming children instead of discarding them fixes
   these.
4. **Composites and the renderer re-evaluate instead of reading stored verdicts.**
   `_record` re-judges the whole tree and `_detail` re-runs `_evaluate_single`
   (`_exceptions.py:108`). That causes C-2, H-3, M-4 and M-6.
5. **Descriptors hold live user objects.** That causes C-2, M-3, M-4 and M-5. Storing a
   JSON-safe snapshot at record time fixes them.

---

## Critical

### C-1. A non-bool comparison result crashes the whole session

The evaluator returns the raw result of `==`, `>` and the other comparisons, and `_record` stores
it as `passed` without converting it to `bool`. For array-like values (numpy arrays, pandas
Series), `==` returns an elementwise object whose truth value raises. The makereport hook then
calls `not r.get("passed")` on it, with no error handling, and pytest aborts with
`INTERNALERROR`. Every later test, including real failures, is never run or reported. The same
happens under xdist and on Python 3.9. A truthy non-bool result, such as `'yes'`, passes and
leaves a non-bool `passed` in the descriptor.

```python
class Elementwise(list):
    def __bool__(self):
        raise ValueError("The truth value of an array with more than one element is ambiguous.")

class Arr(list):
    def __eq__(self, other):
        return Elementwise(a == b for a, b in zip(self, other))

def test_waveform(verify):
    verify.equal(Arr([1, 2]), Arr([1, 3]), name="waveform")

def test_unrelated():
    assert False  # never reported
```

- **Observed:** `INTERNALERROR ... _fixture.py:196 ... ValueError: The truth value of an array
  ... is ambiguous`. Exit code 3. Later tests never run.
- **Expected:** `passed` is always a real `bool`. An ambiguous result is recorded as a failed
  check with an error note, the test fails normally, and every other test still runs.
- **Root cause:** `_evaluator.py:16-50` returns raw comparison results. `_fixture.py:59` stores
  them unconverted. `_fixture.py:196` truth-tests them inside the hook with no guard.
- **Fix:** Coerce the verdict to `bool` inside a `try` in one place. On an exception, record
  `passed=False` plus an `error` string. In the hook, test `r.get("passed") is not True`, and
  guard the hook body so it can never raise.

**Pinned by:** `tests/test_regressions_evaluation.py` (`test_c1_elementwise_comparison_fails_test_without_internalerror`, `test_c1_fixture_stores_non_bool_comparison_result_as_bool`, `test_c1_module_evaluate_detailed_passed_is_bool_for_truthy_result`, `test_c1_module_ambiguous_comparison_evaluates_as_failed`)

### C-2. Building the failure summary can crash the whole session

When a test has a failed soft check, the hook builds `str(ChecksFailedError(fv._results))` with
no guard. `_detail` calls `bool()` again on the live `actual` for `is_true` and `is_false`,
calls `str()` and `repr()` on live values, and re-runs `_evaluate_single` on every `all_satisfy`
child. Any of these can raise at report time. Common triggers are an object whose `__bool__`
fails after cleanup (a closed connection), an object whose `__repr__` raises (a detached ORM
row), and an `all_satisfy` whose record-time `all()` short-circuited past a child that cannot be
evaluated. pytest then exits with code 3 and never reports later tests. A plain `assert` uses
`saferepr` and survives the same objects.

```python
class Conn:
    open = True
    def __bool__(self):
        if not self.open:
            raise RuntimeError("I/O operation on closed connection")
        return False

def test_link(verify):
    c = Conn()
    verify.is_true(c, name="link up")  # fails
    c.open = False                     # normal cleanup
```

- **Observed:** `INTERNALERROR ... _exceptions.py:64, in _detail ... RuntimeError: I/O operation
  on closed connection`. Exit code 3.
- **Expected:** The summary is built from values captured when the check ran, rendered with a
  safe repr. The hook never raises.
- **Root cause:** `_fixture.py:198` (unguarded), and `_exceptions.py:17, 64, 67, 70-89, 108`
  (live `str`/`bool`/`repr` and re-evaluation).
- **Fix:** Snapshot a safe repr and the child verdicts at record time, render only from the
  snapshot, and wrap the summary in a `try` that falls back to a minimal message.

**Pinned by:** `tests/test_regressions_evaluation.py` (`test_c2_failure_summary_never_crashes_session`)

---

## High

### H-1. A check that raises stops the test and is never recorded

The soft-assertion contract is that checks never stop the test. But `_record` evaluates with no
error handling, and some builders do work that can raise. Realistic inputs therefore abort the
test: a `None` reading from an instrument timeout, a string reading, `None` passed to `matches`
or `contains`, an invalid regex, a negative tolerance, a generator or `None` passed to `length`,
or a non-iterable passed to `all_satisfy`. Every later check is skipped, and the offending check
never reaches the results or the stash, so reporters lose it.

```python
def test_psu(verify):
    verify.equal(1, 2, name="first")               # fails softly
    verify.greater(None, 100, name="throughput")   # instrument timed out
    verify.equal(3, 3, name="never runs")
```

- **Observed:** `TypeError: '>' not supported between instances of 'NoneType' and 'int'`. The
  test stops, and only `first` is recorded.
- **Expected:** `throughput` is recorded as failed with the error text, `never runs` runs, and
  the summary shows all three checks.
- **Root cause:** `_fixture.py:59` (no `try`), `_fixture.py:141-148` (`is_instance`), and the
  builders at `_descriptors.py:291`, `:309` and `:320`.
- **Fix:** Wrap evaluation, and the builder work on user data (`len()`, iterating `items`), so
  that an exception becomes `passed=False` plus a JSON-safe `error`. Still append the check and
  keep going.

**Pinned by:** `tests/test_regressions_evaluation.py` (`test_h1_odd_input_is_recorded_and_later_checks_still_run`)

### H-2. `guard` and `conditional` evaluate every branch, not just the matched one

With the fixture, every branch, case and default passed to `guard` or `conditional` is itself a
fixture call, so it is evaluated as a function argument before the composite decides which
branch applies. That defeats the main use case. For example, a reading is `None` when a sensor is
disabled, and a branch checks its value only when it is enabled. The unmatched branch raises,
the test stops, and the already-recorded branch stays behind as a standalone result. Unmatched
children of a passing composite also keep their own `passed`, often `False`, in the nested data,
so a reporter would draw red cards for branches that never ran. The README, the docstrings and
the 0.3.1 CHANGELOG all say only the matched branch is evaluated.

```python
def test_sensor(verify):
    reading = None  # sensor disabled
    verify.guard(
        [(reading is None, "disabled", verify.is_none(reading, name="no reading"))],
        default=verify.greater(reading, 5, name="reading"),
        name="sensor",
    )
    verify.equal(1, 1, name="later check")
```

- **Observed:** `TypeError: '>' not supported between instances of 'NoneType' and 'int'`. The
  test stops. The same code works with the module-level `verify`.
- **Expected:** Only the matched branch is evaluated, the guard passes, and later checks run.
- **Root cause:** `_fixture.py:59` runs for every argument before the composite exists. The
  discard at `_fixture.py:60-61, 66-71` happens afterwards and leaves nested `passed` values.
- **Fix:** Accept lazy children (zero-argument callables, or descriptors from the module
  `verify`) and evaluate only the matched one. At minimum, apply the H-1 fix so an unmatched
  branch that raises becomes a discarded failed child, and strip `passed` from unmatched
  children.

**Pinned by:** `tests/test_regressions_composites.py` (`test_h2_guard_unmatched_default_not_evaluated`, `test_h2_guard_unmatched_branch_not_evaluated`, `test_h2_conditional_unmatched_case_not_evaluated`, `test_h2_module_path_length_in_unmatched_default_not_evaluated`, `test_h2_unmatched_children_carry_no_failed_verdict`)

### H-3. Composite checks re-judge their children and can contradict them

The fixture evaluates `is_instance` with real `isinstance()` and stores `passed` on the child.
The `all_satisfy`, `conditional` and `guard` parents ignore that value and re-evaluate the whole
tree with `_evaluate_single`, which matches the class name along the MRO and re-runs every
comparison on the live objects. With ABCs and virtual subclasses (`numbers.Number`, `Sequence`,
`Mapping`), every child passes but the parent fails. With an unrelated class that shares the
name, every child fails but the parent passes. The 0.3.1 discard then removes the failing
children, so the test goes green. If an object changes between the child check and the
composite, the parent reaches the opposite verdict from its child.

```python
import numbers

def test_numeric(verify):
    verify.all_satisfy(
        [1, 2.5, 3],
        lambda v: verify.is_instance(v, numbers.Number, name="num"),
        name="all numeric",
    )
```

- **Observed:** children `[True, True, True]`, parent `False`: `✗ [0] all numeric — expected all
  3 to pass, got 3 failed`.
- **Expected:** A composite's verdict equals its matched child's recorded verdict (`guard`,
  `conditional`), or all of its children's recorded verdicts (`all_satisfy`).
- **Root cause:** `_fixture.py:59` re-evaluates composites through `_evaluator.py:87-108`, which
  reaches the name-based match at `_evaluator.py:82`. `_exceptions.py:108` recounts the same way.
- **Fix:** In the fixture, derive a composite's verdict from the stored `passed` of its children
  and fall back to `_evaluate_single` only for module-built children.

**Pinned by:** `tests/test_regressions_composites.py` (`test_h3_passing_abc_is_instance_children_pass_parent`, `test_h3_failing_same_name_children_fail_parent`, `test_h3_mutation_after_child_check_does_not_flip_parent`, `test_h3_failure_summary_counts_recorded_child_verdicts`)

### H-4. Reusing a recorded check inside a composite deletes it

Since 0.3.1, `_record` removes every descriptor that appears as a child of a composite from the
results and the stash, by identity, whenever it was recorded. If a test records a standalone
check that fails, and later passes the same descriptor to a composite branch that is not taken,
the standalone failure disappears and the test passes.

```python
def test_power(verify):
    power = verify.approx(4.1, 3.3, abs_tol=0.05, name="Power check")  # fails
    verify.guard([(False, "never", power)], default=verify.equal(1, 1, name="ok"), name="G")
```

- **Observed:** recorded checks are `[('G', True)]`, and the test PASSES.
- **Expected:** A failed top-level check always fails the test. Only children created for the
  composite call are absorbed, or the reuse is rejected with a clear error.
- **Root cause:** `_fixture.py:60-61` and `_fixture.py:66-71` discard any child by identity,
  with no record of where it came from.
- **Fix:** The same lazy-children design as H-2 removes the need for discarding. As a stopgap,
  track which checks were recorded as top-level and refuse (or warn about) reusing them as
  children.

**Pinned by:** `tests/test_regressions_composites.py` (`test_h4_failing_check_reused_in_unmatched_guard_branch_still_fails`, `test_h4_failing_check_reused_as_unmatched_conditional_case_still_fails`)

### H-5. Module-level `is_instance` matches by class name only

`build_is_instance` stores only `expected_type.__name__`, and `evaluate()` passes if any class in
`type(actual).__mro__` has that name. Unrelated classes with the same name therefore pass, for
example `Config` from two different modules, `decimal.Decimal` against `_pydecimal.Decimal`, and
`concurrent.futures.CancelledError` against `asyncio.CancelledError`. ABCs, runtime-checkable
Protocols and typing aliases give false negatives. The README documents the check as
`isinstance(actual, expected_type)`. The fixture itself uses real `isinstance`, but composites
reach this path through H-3.

```python
import types
from pytest_verify import verify

A = types.new_class("Config"); B = types.new_class("Config")
d = verify.is_instance(A(), B, name="cfg")
assert verify.evaluate(d) is False  # returns True today
```

- **Observed:** `isinstance=False`, `evaluate=True`.
- **Expected:** The module verdict equals `isinstance(actual, expected_type)`, or at least a
  non-instance never passes.
- **Root cause:** `_descriptors.py:291` and `_evaluator.py:82`.
- **Fix:** Compute `isinstance` while the type object is still available and store the result as
  a JSON-safe bool. Store a qualified display name (`module.qualname`). Use the name match only
  as a fallback for hand-built descriptors, and compare qualified names there.

**Pinned by:** `tests/test_regressions_data.py` (`test_h5_unrelated_same_name_class_does_not_pass`, `test_h5_runtime_checkable_protocol_instance_passes`, `test_h5_fixture_composite_with_same_name_module_child_fails`)

### H-6. `conditional` matches cases with `str()`, so results depend on the Python version

Cases are looked up with `str(switch_value)` against `{str(k): ...}`. `str()` of an `IntEnum`
member changed in Python 3.11, from `'Mode.ACTIVE'` to `'1'`. An `IntEnum` switch with `int`
keys, or the reverse, matches on 3.11 to 3.13 and silently falls through to the default on 3.9
and 3.10. The same test passes on one CI Python and fails on another. On every version, a switch
of `True` or `1.0` does not match key `1`, a `(str, Enum)` member does not match its string
value, and `None` matches a literal `'None'` key. The README says keys "can be ints, strings, or
enums — they are normalized internally".

```python
import enum
from pytest_verify import verify

class Mode(enum.IntEnum):
    ACTIVE = 1

d = verify.conditional(Mode.ACTIVE, cases={1: verify.equal(1, 1, name="a")}, name="M")
print(d["matched_case"])  # '1' on 3.11+, None on 3.9 and 3.10
```

- **Observed:** Python 3.9 and 3.10: no match, check fails. Python 3.11 to 3.13: match, check
  passes.
- **Expected:** Case selection follows Python equality (`switch_value in cases`) and gives the
  same result on every supported version.
- **Root cause:** `_descriptors.py:340-341` and `_evaluator.py:92`.
- **Fix:** Pick the case once at build time with a dict lookup on the original keys, normalising
  `Enum` members through `.value`. Store a version-stable string key for JSON and use the stored
  `matched_case` everywhere (see M-6).

**Pinned by:** `tests/test_regressions_composites.py` (`test_h6_intenum_switch_matches_int_case_key`, `test_h6_int_switch_matches_intenum_case_key`, `test_h6_python_equal_switch_matches_case_key`, `test_h6_none_switch_does_not_match_string_none_key`)

### H-7. Checks recorded during teardown are ignored

The verdict is computed only while the call-phase report is built. A failed check after that
point is ignored and the test is reported PASSED with no output. That covers code after `yield`
in a fixture that uses `verify`, `request.addfinalizer` callbacks and autouse teardown.
`get_check_results(item)` still returns the failed checks, so a reporter would draw red cards on
a green test. CLAUDE.md and the docstrings describe evaluation "at teardown".

```python
import pytest

@pytest.fixture
def dut(verify):
    yield
    verify.equal("busy", "idle", name="DUT idle after test")

def test_run(dut):
    pass
```

- **Observed:** `PASSED`. The stash holds the failed check.
- **Expected:** The failure is reported, for example as a teardown ERROR carrying the summary,
  and the verdict never contradicts `get_check_results(item)`.
- **Root cause:** `_fixture.py:193` returns early for every phase except `call`.
- **Fix:** Remember how many checks the call phase judged, and in the teardown phase fail the
  report if any later check failed.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_h7_failed_check_in_teardown_fails_the_run`, `test_h7_verdict_never_contradicts_recorded_results`)

### H-8. Soft failures are misreported under `xfail` and hidden by `skip`

Soft failures never raise, so pytest's skipping plugin classifies an `xfail` test with a failed
soft check as XPASS. The verify hook then flips the outcome to failed but leaves `wasxfail` set.
The terminal says FAILED and the exit code is 1, while junitxml writes
`<skipped message="xfail-marked test passes unexpectedly">`, so junit-based CI gating misses it.
With `strict=True` the message is `[XPASS(strict)]`, and `xfail(raises=AssertionError)` never
matches. Separately, a soft failure followed by `pytest.skip()` is reported only as SKIPPED: the
summary goes into a section that pytest never displays.

```python
import pytest

@pytest.mark.xfail(reason="known")
def test_known(verify):
    verify.equal(1, 2, name="a")          # expected: XFAIL, observed: FAILED

def test_skip(verify):
    verify.equal(1, 2, name="a")
    pytest.skip("env not ready")          # the failure above is never shown
```

- **Expected:** A failed soft check under `xfail` reports XFAIL consistently in the terminal, the
  exit code and junitxml. A later skip never hides a soft failure.
- **Root cause:** `_fixture.py:193` and `_fixture.py:199-207` rewrite a report that pytest has
  already classified.
- **Fix:** Raise `ChecksFailedError` from a `pytest_runtest_call` wrapper after the test body
  returns (with `__tracebackhide__ = True`), so pytest's own xfail, strict and `raises=`
  handling applies. A prototype showed this makes every xfail case report XFAIL.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_h8_soft_failure_under_xfail_is_xfailed`, `test_h8_terminal_exit_code_and_junitxml_agree_for_xfail_soft_failure`, `test_h8_later_skip_does_not_hide_soft_failure`)

---

## Medium

### M-1. `approx` `rel_tol` accepts values outside the advertised band

The description says `Verify 'Gain' == 100 ± 50%`, but evaluation uses `math.isclose`, which
scales `rel_tol` by `max(|actual|, |expected|)`. The accepted band is therefore wider than
`expected × (1 ± rel)` and lopsided. At 50%, an actual of 200 passes. At 9.5%, 110 passes while
90 fails. With `rel_tol >= 1`, almost any value of the same sign passes, including `1e300`.
`pytest.approx` rejects all of these. The passing summary then prints the false statement
`200dB == 100dB ± 50%`.

```python
from pytest_verify import verify
assert not verify.evaluate(verify.approx(200, 100, rel_tol=0.5, name="Gain"))  # passes today
```

- **Root cause:** `_evaluator.py:28`.
- **Fix:** Use `abs(actual - expected) <= rel_tol * abs(expected)`, like `pytest.approx`.
  Verdicts can change at the edges, so ship it as a documented behaviour change.

**Pinned by:** `tests/test_regressions_evaluation.py` (`test_m1_rel_tol_verdict_matches_advertised_band`)

### M-2. `is_instance` crashes on tuples and unions of types

The README describes the check as `isinstance(actual, expected_type)`, which accepts tuples and
unions. The builder reads `expected_type.__name__`, so `(int, float)`, `int | str`, and on 3.9
`typing.List` and `Optional[int]`, raise `AttributeError` when the descriptor is built. With the
fixture the test stops and the check is never recorded.

```python
def test_num(verify):
    verify.is_instance(1, (int, float), name="numeric")  # AttributeError
```

- **Root cause:** `_descriptors.py:291`.
- **Fix:** Normalise tuples and unions into a list of classes and store their names. Reject
  anything else with a clear `TypeError`, the same way on every Python version.

**Pinned by:** `tests/test_regressions_data.py` (`test_m2_tuple_of_types_builds_and_matches_isinstance`, `test_m2_pep604_union_builds_and_matches_isinstance`, `test_m2_typing_optional_is_rejected_or_matches_isinstance`, `test_m2_fixture_tuple_of_types_check_passes`)

### M-3. Descriptors are not JSON-serializable for common inputs

CLAUDE.md and the CHANGELOG promise JSON-serializable descriptors. Every builder stores the
caller's raw objects, so `json.dumps` fails for enums, sets, bytes, `Decimal`, `datetime`,
compiled regexes and custom objects. That affects fixture results a reporter reads through
`get_check_results`. Case keys are stringified but `switch_value` is not, so the README's own
enum-keyed `conditional` fails. NaN and infinity serialize as the non-standard tokens `NaN` and
`Infinity`.

```python
import enum, json
from pytest_verify import verify

class Mode(enum.Enum):
    ACTIVE = "active"

d = verify.conditional(Mode.ACTIVE, cases={Mode.ACTIVE: verify.equal(1, 1, name="x")}, name="M")
json.dumps(d)  # TypeError: Object of type Mode is not JSON serializable
```

- **Root cause:** `_descriptors.py:347` for `switch_value`, and raw storage in every builder.
- **Fix:** Store a JSON-safe `switch_value` now. For the general case, store a JSON-safe snapshot
  of each value (see M-4), or narrow the documented guarantee.

**Pinned by:** `tests/test_regressions_data.py` (`test_m3_module_conditional_with_enum_switch_value_is_json_serializable`, `test_m3_fixture_conditional_with_enum_switch_value_is_json_serializable`, `test_m3_fixture_results_are_json_serializable_for_common_inputs`)

### M-4. Reports show values after mutation, not the values that were checked

The fixture computes `passed` when the check runs but keeps references to `actual`, `expected`
and `haystack`, and renders them at report time. If the test changes the object afterwards, the
output contradicts itself.

```python
def test_state(verify):
    state = {"mode": "idle"}
    verify.equal(state, {"mode": "run"}, name="state")
    state["mode"] = "run"
```

- **Observed:** `✗ [0] state — expected {'mode': 'run'}, got {'mode': 'run'}`.
- **Root cause:** `_fixture.py:59` keeps live references. `_exceptions.py:38-108` renders them
  late and recounts `all_satisfy` children on the mutated values.
- **Fix:** Snapshot a safe repr (and a JSON-safe copy where possible) when the check is
  recorded, and render only from the snapshot.

**Pinned by:** `tests/test_regressions_data.py` (`test_m4_failed_equal_reports_the_value_that_was_compared`, `test_m4_failed_contains_reports_the_haystack_that_was_searched`, `test_m4_passed_equal_reports_the_value_that_was_compared`, `test_m4_all_satisfy_failed_count_uses_recorded_child_verdicts`)

### M-5. Every checked value is kept alive until the session ends

Descriptors keep references to `actual` and `expected`. The descriptor list and the fixture
object both live in `item.stash`, which lasts for the whole session. Large captures, waveforms
or frames passed to any check are never freed. In a probe, 15 of 16 large objects were still
alive at the end of the session (351 MB peak), while the same tests without `verify` freed them.

- **Root cause:** `_fixture.py:63`, `:147` and `:215`.
- **Fix:** Remove `_verify_key` from the stash once the verdict is known, and replace live
  values in stored descriptors with bounded snapshots.

**Pinned by:** `tests/test_regressions_data.py` (`test_m5_checked_object_is_collectable_after_its_test`)

### M-6. `conditional` evaluator and renderer disagree after a JSON round-trip

`guard` uses its stored `matched_index` everywhere. `conditional`'s evaluator ignores the stored
`matched_case` and recomputes `str(switch_value)`, while `_detail` renders from `matched_case`.
After a JSON round-trip, a tuple switch becomes a list, `str()` changes, and the check fails with
the message `expected 1, got 1`.

```python
import json
from pytest_verify import verify

d = verify.conditional((1, 2), cases={(1, 2): verify.equal(1, 1, name="ok")}, name="pair")
rt = json.loads(json.dumps(d))
assert verify.evaluate(d) and verify.evaluate(rt)  # the second is False today
```

- **Root cause:** `_evaluator.py:92`.
- **Fix:** Treat the stored `matched_case` as authoritative, and recompute only when the field is
  missing.

**Pinned by:** `tests/test_regressions_composites.py` (`test_m6_conditional_verdict_survives_json_round_trip`, `test_m6_rendered_case_agrees_with_verdict_after_round_trip`)

### M-7. The results stash accumulates across reruns

Each attempt gets a fresh fixture object, so the verdict is correct. But the stash list is
created with `setdefault` on the same item, which pytest-rerunfailures reuses, and it is never
reset. For a flaky test that passes on its third attempt, `get_check_results()` returns the
failed checks from attempts one and two plus the passing one. A reporter would draw red cards on
a PASSED test.

- **Root cause:** `_fixture.py:63` and `:147` (`setdefault`), and `_fixture.py:209-216`.
- **Fix:** Reset the stash list when each attempt starts, for example in a `tryfirst`
  `pytest_runtest_setup` hook.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_m7_stash_holds_only_the_last_rerun_attempt`)

### M-8. A clean rerun is failed with the previous attempt's checks

`item.stash[_verify_key]` is set only when the fixture is created and is never cleared. If a
rerun attempt does not create the fixture (for example, it is requested with
`request.getfixturevalue` on only some paths), the hook reuses the earlier attempt's object and
fails the clean attempt with stale results.

- **Observed:** `FAILED ... 1 of 1 checks failed` / `1 failed, 2 rerun`, although the last attempt
  recorded no checks.
- **Root cause:** `_fixture.py:215` (never cleared), read at `_fixture.py:195`.
- **Fix:** Clear both stash keys at the start of each attempt (same hook as M-7).

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_m8_clean_rerun_attempt_is_not_failed_by_previous_attempt`)

### M-9. pytest 9 subtests: one failing check fails every later subtest

pytest 9's built-in `subtests` fixture builds each subtest report through
`pytest_runtest_makereport` with `when="call"`. The verify hook runs for each one and judges the
whole cumulative result list. After the first failing check, every later subtest is SUBFAILED,
including subtests with only passing checks or no checks at all. One failing check produced
`4 failed`.

- **Root cause:** `_fixture.py:193-199`.
- **Fix:** Skip subtest reports (they carry a `context` attribute) and judge only the parent, or
  keep a cursor and judge each subtest on the checks it recorded.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_m9_passing_subtests_after_a_failing_one_are_not_failed`)

### M-10. Soft failures next to a hard failure are missing from junitxml

If the test body also raises, the soft summary is stored only as a report section. pytest treats
sections as captured output: the terminal hides them with `--show-capture=no`, `stdout` or
`log`, and junitxml never writes them, under any `junit_logging` mode. The 0.2.0 CHANGELOG says
soft failures are "no longer silently dropped" in this case.

- **Root cause:** `_fixture.py:207`.
- **Fix:** Add the summary to the failure representation itself (`longrepr.addsection(...)`), not
  to `report.sections`.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_m10_soft_summary_shown_whatever_show_capture`, `test_m10_soft_summary_in_junitxml_failure`)

### M-11. Soft failures recorded in setup vanish when setup errors

A fixture can record failing soft checks and then raise, or a later fixture can raise, for
example "DUT did not answer". The test is reported as ERROR with only the setup traceback. The
soft failures appear nowhere, although they are often the root cause (a wrong rail voltage that
stops the DUT from answering).

- **Root cause:** `_fixture.py:193` ignores setup reports.
- **Fix:** On a setup error, append the soft summary to the setup report, as the call-phase path
  already does.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_m11_setup_error_report_shows_soft_failures`)

### M-12. `--pdb` and `pytest_exception_interact` ignore soft failures

Soft failures are applied by rewriting the report, so `call.excinfo` stays `None` and pytest
never calls `pytest_exception_interact`. `--pdb` does not stop on them, and plugins that capture
screenshots, logs or instrument dumps on failure through that hook miss these tests.

- **Root cause:** `_fixture.py:198-202`.
- **Fix:** Same as H-8: raise `ChecksFailedError` in the call phase.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_m12_exception_interact_fires_for_soft_failure`)

### M-13. Other `makereport` hooks can see a soft-failed test as passed

The verify hook is an unordered old-style hookwrapper that changes the outcome after `yield`.
Any `makereport` implementation that runs inside it sees `outcome='passed'` and
`call.excinfo=None` for a soft-failed test. Whether that happens depends on plugin registration
order, which can differ between environments. That includes pytest-reporter (which CLAUDE.md
says observes the outcome this way), in-house upload plugins, and pytest-rerunfailures'
`--only-rerun` and `--rerun-except` filters, which never match a soft failure.

- **Observed:** `-p fake_reporter -p verify` prints `outcome=passed failed_checks=1`, then
  `FAILED`. The reverse order prints `outcome=failed`.
- **Root cause:** `_fixture.py:186-205`.
- **Fix:** Same as H-8: raise `ChecksFailedError` in the call phase, so every hook sees a real
  failure regardless of order.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_m13_makereport_wrapper_registered_first_sees_failed`, `test_m13_soft_failed_call_phase_has_assertion_excinfo`)

### M-14. The fixture without its hook turns every failure into a pass

Only the module-level makereport hook turns recorded failures into a failed test. If the fixture
is available but the hook is not registered, checks are recorded and ignored, and failing tests
pass. That happens with `from pytest_verify._fixture import verify` in a conftest (a common way
to vendor fixtures under `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`), or with that re-export plus
`-p no:verify`.

- **Observed:** A test that calls `verify.fail("x")` reports `1 passed`.
- **Root cause:** `_fixture.py:209-216`.
- **Fix:** Have the fixture detect that the hook is not active (a flag set in
  `pytest_configure`) and fail loudly with a usage error.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_m14_reexported_fixture_without_hook_never_passes`)

### M-15. Recording checks from several threads loses checks

`_discard` builds a new list from a snapshot, rebinds `self._results`, and slice-assigns the stash
list, with no lock. When one test records checks from several threads and any thread builds a
composite, a check appended between the snapshot and the store is dropped, and a concurrent
discard can bring back another composite's removed children. In probes on Python 3.9 and 3.11 to
3.13, about 7 to 13% of tests that called `verify.fail()` or ran one failing guard PASSED. This
is timing-dependent, so it is not pinned by a test.

- **Root cause:** `_fixture.py:66-71`, reached from `_record`.
- **Fix:** Hold a `threading.RLock` in `_record`, `is_instance` and `_discard`, and delete in
  place by index instead of rebinding.

**Pinned by:** `tests/test_threads.py` (`test_m15_every_check_from_every_thread_is_recorded_once`, `test_m15_one_failing_guard_among_threads_always_fails_the_test`)

### M-16. Type hints reject documented usage

The signatures are narrower than what the README documents and the runtime accepts, so correct
code fails type checking. `cases: dict[str, CheckDescriptor]` rejects the README's own
`cases={0: ..., 1: ...}` example and enum keys. `branches: list[tuple[bool, ...]]` rejects
truthy non-bool conditions, although the docstring says "truthy". `expected_type: type` rejects
tuples. CLAUDE.md calls typing and IDE support non-negotiable. This needs mypy to observe, so it
is not pinned by a runtime test.

```text
error: Dict entry 0 has incompatible type "int": "CheckDescriptor"; expected "str": "CheckDescriptor"
```

- **Root cause:** `_verify.py:340` and `:363`, mirrored in `_fixture.py:162, 170` and
  `_descriptors.py:333, 356`.
- **Fix:** Type `cases` as `Mapping[Any, CheckDescriptor]` and `branches` as
  `Sequence[tuple[object, str, CheckDescriptor]]`, and add a typing test to CI.

**Pinned by:** the `typing` CI job (`mypy --strict` over `tests/typing_usage.py`)

---

## Low

### L-1. `conditional` case keys that collide after `str()` are silently overwritten

`{1: a, '1': b}`, `{True: a, 'True': b}` and `{Color.RED: a, 'Color.RED': b}` each collapse to
one key, and the later value wins with no error. With the fixture, the overwritten descriptor is
no longer a child of the composite, so it is never discarded. It stays as a standalone check and
a case that was never selected fails the test.

- **Root cause:** `_descriptors.py:340`.
- **Fix:** Raise `ValueError` at build time when two keys normalise to the same string.

**Pinned by:** `tests/test_regressions_composites.py` (`test_l1_colliding_case_keys_not_silently_overwritten`, `test_l1_overwritten_case_not_left_as_standalone_result`)

### L-2. `approx` loses precision with `Decimal`, `Fraction` and big `int`

`math.isclose` converts operands and tolerances to floats.
`approx(Decimal('1.1'), Decimal('1.0'), abs_tol=Decimal('0.1'))` fails although the difference
equals the tolerance exactly. `abs_tol=0` accepts a tiny non-zero `Decimal` difference, and
`10**400` raises `OverflowError` and stops the test.

- **Root cause:** `_evaluator.py:26` and `:28`.
- **Fix:** Compare in the native type (`abs(actual - expected) <= tol`) when neither operand is a
  float, and keep `math.isclose` for floats.

**Pinned by:** `tests/test_regressions_evaluation.py` (`test_l2_approx_compares_exact_numeric_types_exactly`, `test_l2_approx_huge_int_does_not_overflow`)

### L-3. A composite that raises leaves its children behind

`_record` evaluates the parent before it discards the children. If that evaluation raises, or an
`all_satisfy` factory raises partway through, the children recorded so far stay behind as
independent checks. An unmatched branch then fails the test even when the user catches the
exception.

- **Root cause:** `_fixture.py:59-61`, and `_descriptors.py:320` (the factory runs before
  `_record`).
- **Fix:** Discard children first, then evaluate inside a `try` (see H-1).

**Pinned by:** `tests/test_regressions_composites.py` (`test_l3_composite_evaluation_error_leaves_no_orphan_children`, `test_l3_all_satisfy_factory_error_leaves_no_orphan_children`)

### L-4. `conditional` message shows a different key than the one it looked up

The lookup uses `str(switch_value)`, while the description and summary use `format()`. For
`IntEnum` and `(str, Enum)` switch values on Python 3.9 and 3.10 the two differ, so the output
contradicts itself: `mode=1 → no match` while case `'1'` exists.

- **Root cause:** `_descriptors.py:346` and `_exceptions.py:143-144`.
- **Fix:** Render the same key that was used for the lookup.

**Pinned by:** `tests/test_regressions_composites.py` (`test_l4_conditional_displays_the_switch_it_looked_up`)

### L-5. A huge `int` crashes a passing check

Descriptions interpolate raw values. Python refuses to convert an `int` with more than 4300
digits to a string, so `verify.equal(n, n)` with a big factorial raises `ValueError` while its
description is built, and the test stops.

- **Root cause:** `_descriptors.py:91` and the other description f-strings; `_exceptions.py:17`.
- **Fix:** Render values through one safe formatter that catches the error and returns a
  placeholder such as `<int with 5736 digits>`.

**Pinned by:** `tests/test_regressions_evaluation.py` (`test_l5_passing_check_on_huge_int_builds_and_passes`, `test_l5_fixture_passing_checks_on_huge_int_let_test_continue`, `test_l5_failing_check_on_huge_int_fails_test_without_internalerror`)

### L-6. `ChecksFailedError` cannot be pickled or copied

`ChecksFailedError.__init__(self, results)` passes only the formatted message to
`AssertionError`. Unpickling calls `ChecksFailedError(<message>)`, which iterates the characters
and fails with `AttributeError: 'str' object has no attribute 'get'`. `copy.copy`, `deepcopy` and
raising it in a `ProcessPoolExecutor` worker fail the same way.

- **Root cause:** `_exceptions.py:156-174`.
- **Fix:** Add `__reduce__` returning `(type(self), (self.results,), self.__dict__)`.

**Pinned by:** `tests/test_regressions_data.py` (`test_l6_checks_failed_error_round_trips`)

### L-7. `--tb=line` prints the summary twice

The soft-failure `longrepr` is a plain string with no `reprcrash`. With `--tb=line`, pytest prints
the whole multi-line block and then `str(longrepr)[:50]` as the "crash line", cut mid-value and
with no file or line.

- **Root cause:** `_fixture.py:202`.
- **Fix:** Same as H-8 (raise in the call phase), or set a longrepr object that has a
  `reprcrash`.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_l7_tb_line_crash_line_is_a_location_line`)

### L-8. pytest 8 short summary gives no reason for soft failures

pytest 8 builds the `-r` short-summary reason from `longrepr.reprcrash.message`, and silently
drops it when that is missing. On pytest 8.4 (the only line available on Python 3.9, and within
the declared `pytest>=7`), a soft failure is listed as a bare `FAILED test_x.py::test`, while hard
failures show a reason. pytest 9 happens to print the first line of the string.

- **Root cause:** `_fixture.py:202`.
- **Fix:** Same as L-7.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_l8_short_summary_gives_reason_for_soft_failure`)

### L-9. Concurrent first checks race on the stash

The stash list is created lazily with `item.stash.setdefault(...)`, which is not atomic. When the
first checks of a test come from several threads at once, each thread can install its own list,
and all but one are overwritten. `get_check_results()` then returns fewer checks than the verdict
used (about 2% of trials in a probe). This is timing-dependent, so it is not pinned by a test.

- **Root cause:** `_fixture.py:63` and `:147`.
- **Fix:** Create the list once when the fixture is set up, and append under the lock from M-15.

**Pinned by:** `tests/test_threads.py` (`test_l9_concurrent_first_checks_all_reach_the_results`)

### L-10. `typing.get_type_hints` fails on Python 3.9

With `from __future__ import annotations`, `X | None` annotations are fine for static checkers
but raise `TypeError` when evaluated at runtime on 3.9. `typing.get_type_hints(CheckDescriptor)`
and the hints of 11 public `Verify` methods fail there, which breaks tools such as pydantic,
typeguard, beartype and sphinx-autodoc-typehints.

- **Root cause:** `_descriptors.py:25-27, 40-43` and the public method signatures.
- **Fix:** Use `Optional[...]` in the TypedDict and public signatures, or drop Python 3.9 (it is
  end-of-life).

**Pinned by:** `tests/test_regressions_data.py` (`test_l10_check_descriptor_type_hints_resolve`, `test_l10_public_verify_method_type_hints_resolve`)

### L-11. Builds fail with setuptools 68 to 76

`build-system.requires` allows `setuptools>=68.0`, but the PEP 639 string form
`license = "MIT"` needs setuptools 77 or later. setuptools 68.1.2 and 75.3.4 reject the
`pyproject.toml`. This only affects builds without isolation (distro packaging, offline CI).

- **Root cause:** `pyproject.toml:2` together with `pyproject.toml:10`.
- **Fix:** Require `setuptools>=77` and drop `wheel`.

**Fixed by:** `build-system.requires` asks for `setuptools>=77`. There is no test; a build with setuptools 77 succeeds and one with 76 is refused.

### L-12. The sdist cannot run its own tests

The default sdist rules include `tests/test*.py` but not `tests/conftest.py`, which enables the
`pytester` plugin. Running the tests from the sdist, as distro packagers do, gives 21 errors
(`fixture 'pytester' not found`).

- **Fix:** Add a `MANIFEST.in` with `graft tests`, or enable `pytester` through `addopts`.

**Pinned by:** the `package` CI job, which runs the tests of the built sdist

---

## Documentation drift

### D-1. `ChecksFailedError` is never raised

The README says failures are "reported together in a single `ChecksFailedError`", the class
docstring says "Raised at fixture teardown", and `pytest --fixtures` says the fixture "fails at
teardown". In fact the class is only used to format text (`_fixture.py:198`), nothing raises it,
and it is not exported from `pytest_verify`. So `pytest.raises(ChecksFailedError)`,
`xfail(raises=...)` and `--only-rerun ChecksFailedError` can never match.

**Fix:** Either raise and export it (the H-8 fix), or correct `README.md:38-39`,
`_exceptions.py:148`, `_fixture.py:212` and CLAUDE.md.

**Pinned by:** `tests/test_regressions_lifecycle.py` (`test_d1_checks_failed_error_raised_and_exported_or_not_documented_as_raised`)

### D-2. The README `guard` example crashes, and `guard` is missing from the catalogs

The README's only `guard` example calls `verify.equal(reading, 0)` and `verify.approx(...)`
without the required `name=`, so it raises `TypeError` when copied. `verify.guard` (added in
0.3.0) is missing from the README "Check Functions" tables and from the CLAUDE.md catalog, which
still says "20 functions" (there are 21). Test docstrings also say "all 20".

**Fix:** Add `name=` to the example, and add a `guard` row to both catalogs.

### D-3. Reporter-detection and shared-stash-key docs are obsolete

CLAUDE.md and the README describe detecting pytest-reporter in `pytest_configure` and writing to
the stash only when it is present. There is no `pytest_configure`, and writes have been
unconditional since 0.2.0. CLAUDE.md also says the two plugins share a `StashKey` "by convention"
without importing each other. `StashKey` works by identity, so that cannot work: a reporter that
creates its own key sees nothing. The only working contract is importing
`pytest_verify.get_check_results`.

**Fix:** Document `get_check_results(item)` as the contract and remove the detection text.

### D-4. `CLAUDE.md` `conditional` example raises `TypeError`

`verify.conditional(mode, name="M")` in the description-format section leaves out the required
`cases` argument.

### D-5. CHANGELOG and tags disagree

Tag `v0.3.1` points at `5bf4adf` (2026-07-06), whose CHANGELOG still lists the CI workflow,
badges and install fix under `[Unreleased]`, while 0.3.1 is dated 2026-06-23. Versions 0.1.0,
0.2.1 and 0.3.0 have no tags, so the README's `@vX.Y.Z` pin pattern fails for them, and there are
no Keep a Changelog link definitions.

**Fix:** Do not move the pushed tag. Release the `[Unreleased]` entries as 0.3.2, tag the
historical releases, and add compare links.

### D-6. README says CI runs on every push

`README.md:207` and the CHANGELOG say CI runs "on every push and pull request", but
`.github/workflows/ci.yml` limits both triggers to `main`.

### D-7. `CLAUDE.md` package map is out of date

CLAUDE.md says `_stash.py` does reporter detection (it only defines the key), `_fixture.py` holds
teardown logic (the verdict comes from a makereport hook), and `_types.py` holds shared aliases
(nothing imports it). It lists the `__init__` exports without `get_check_results`.

---

## What was checked and behaves correctly

Around 150 corner cases behaved as specified. Some worth noting:

- Descriptions match every spec example except D-4, including units, `± x% (rel)` labels and
  inclusive or exclusive `between` brackets.
- No state leaks between parametrized tests or between tests in a class.
- A soft failure together with a hard failure keeps the original traceback in the terminal
  (with the default `--show-capture`).
- `-x`, `--maxfail`, `--lf`, `--ff` and `--sw` all treat soft failures as failures.
- Under pytest-xdist, verdicts and failure text survive worker-to-controller serialization.
- The plugin emits no warnings under `-W error`, `--strict-markers` or `--strict-config`.
- Module- and class-scoped fixtures that request `verify` get pytest's usual `ScopeMismatch`.
- `all_satisfy` on an empty list passes vacuously and reports `all 0 items pass`, which is
  consistent with Python's `all()`.
