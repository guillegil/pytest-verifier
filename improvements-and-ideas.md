# Improvements and ideas for pytest-verify

> **Status:** [`CHECKLIST.md`](CHECKLIST.md) tracks which of these items shipped (0.4.0 took the
> small improvements that touched the bug fixes) and which wait for 0.5.0 or a discussion.

This list comes out of the same deep review as [`bugs-0.3.1.md`](bugs-0.3.1.md). The bug report
covers behaviour that is wrong today. This file covers everything else: behaviour that is correct
but could be better, refactors that remove whole groups of bugs, new features, and tooling.

Each entry has a rough **effort** (S, M or L), **impact** (low, medium or high), and the
**SemVer** bump it would need.

## Recommended roadmap

**Before anything else: the name `pytest-verify` is already taken on PyPI.** An unrelated
snapshot-testing plugin (github.com/metahris/pytest-verify, version 1.2.0) owns that name and
ships the same import package, `pytest_verify/`. Anyone who installs this project from Git and
later runs `pip install -U pytest-verify` gets the other package silently, in the same directory.
Pick a free name before publishing. `pytest-softverify`, `pytest-soft-verify` and
`pytest-verifier` were all free when checked. See [DX-1](#dx-1-rename-before-publishing-to-pypi).

1. **0.3.2 (patch): stop the crashes and silent passes.** Guard the verdict so it is always a
   `bool` and never raises ([ARCH-2](#arch-2-judge-every-check-in-one-exception-safe-place)), fix
   the teardown and rerun state bugs, and correct the documentation drift. This fixes C-1, C-2,
   H-1, H-7, M-7, M-8 and D-1 to D-7.
2. **0.4.0 (minor): fix the pytest integration and composites.** Raise `ChecksFailedError` from
   the call phase ([ARCH-5](#arch-5-enforce-the-verdict-by-raising-not-by-rewriting-reports)),
   snapshot evidence at record time ([ARCH-3](#arch-3-separate-the-json-evidence-from-the-live-values)),
   and accept lazy composite children ([ARCH-4](#arch-4-lazy-composite-children-instead-of-evaluate-then-discard)).
   Together these fix most of the High and Medium bugs.
3. **0.5.0 and later: features.** Call-site locations, `verify.raises`, collection-aware
   checks, sections, limits tables, and a published schema.
4. **1.0.0: rename, publish to PyPI, and freeze the descriptor schema.**

## Contents

- [Improvements found during the review](#improvements-found-during-the-review)
- [Architecture and refactoring](#architecture-and-refactoring)
- [Feature ideas](#feature-ideas)
- [Developer experience, CI and releases](#developer-experience-ci-and-releases)

---

## Improvements found during the review

These were confirmed with probes but judged not to be bugs: the behaviour matches the spec or
Python's own semantics, yet it surprises users or could be clearer.

### Input validation and footguns

#### IMP-1. Ordering checks compare strings alphabetically

`greater`, `less`, `between` and friends pass their operands straight to Python's comparison
operators. Instrument (SCPI) replies and CSV limit tables are strings, and comparing two strings
compares them character by character. A 100 mV ripple passes a `"20"` mV limit, and a 10.5 MHz
reading fails a `"9.0"` to `"11.0"` window, with no error. Reject `str`, `bytes` and `bytearray`
operands at build time with a message such as "convert instrument replies with `float()`".
*Effort S, impact high, minor.*

#### IMP-2. Validate tolerances and bounds when the check is built

A negative `rel_tol` is ignored when `abs_tol` already passed but raises when it did not, so the
same check passes or crashes depending on the reading. `abs_tol=float("nan")`,
`abs_tol="0.1"` and `between(x, 10, 0)` all build without complaint. Validate in the builders:
tolerances must be finite, non-negative real numbers, and `low <= high`. *Effort S, impact
medium, minor.*

#### IMP-3. Reject a check descriptor used as a guard condition

A failed check returns a non-empty `dict`, which is always truthy. So
`link = verify.is_true(up, ...); if link: ...` and `verify.guard([(link, "link up", ...)])` both
take the "link up" path even when the check failed. Raise `TypeError` in `build_guard` when a
condition looks like a descriptor, and document the `if link["passed"]:` idiom. *Effort S,
impact medium, minor.*

#### IMP-4. Validate composite children and `name`

A factory that forgets `return`, or the natural `lambda v: v > 0`, fails deep in the evaluator
with `'NoneType' object is not subscriptable`. A guard given 2-tuples fails with an unpacking
error. `name=None`, `name=""` and non-string names are accepted although the spec says `name` is
required. Add `_ensure_descriptor(obj, where)` and `_require_name(name)` helpers with messages
that name the argument, for example "all_satisfy factory returned None for item 2; did you forget
`return`?". *Effort S, impact medium, minor.*

#### IMP-5. Helpful errors for `evaluate(list)`

`verify.evaluate(checks)` with a list is a natural mistake and gives
`TypeError: list indices must be integers`. Detect a list or tuple and say
"use `verify.evaluate(*checks)`". Also document that `evaluate()` with no descriptors returns
`True`, and optionally add `require_any=True`. *Effort S, impact low, patch.*

#### IMP-6. Make `evaluate()` and `evaluate_detailed()` total

`evaluate(fail, bad)` returns `False`, but `evaluate(bad, fail)` raises, because `all()`
short-circuits. `evaluate_detailed(ok, bad)` raises and loses the result for `ok`. Evaluate every
descriptor and record an `error` field for the ones that raise. *Effort S, impact low, minor.*

#### IMP-7. Warn when the module-level `verify` is used inside a test by mistake

`from pytest_verify import verify` has the same name as the fixture. If a test imports it and
forgets to request the fixture, every check, including `verify.fail()`, silently does nothing and
the test passes. See [ARCH-9](#arch-9-rename-the-module-level-builder) for the structural fix. A
smaller step is a `PytestWarning` when a module-level builder runs inside a test that did not
request the fixture. *Effort S, impact medium, minor.*

#### IMP-8. Detect use of a stale or forked fixture

A helper object that caches `verify` from one test and reuses it in the next records into the
finished test, and those failures are lost. Checks recorded in a forked worker process are lost
too, and the test passes. Make the fixture a yield fixture that marks itself closed, save the
creating process id, and raise a clear `RuntimeError` in both cases. *Effort S, impact low,
patch.*

### Output quality

#### IMP-9. Show types when values look the same

Values are rendered with `str()`, so a string/int mismatch reads `expected 1, got 1`, and
`'3.3'` and `3.3` look identical. Keep `str()` plus units for numbers so `3.3V` still reads
naturally, and use a safe `repr` for everything else. When the types differ, add them:
`expected 1 (int), got '1' (str)`. *Effort S, impact high, minor.*

#### IMP-10. Show the actual value for `is_true` and `is_false`

The summary prints `bool(actual)`, so an SCPI reply of `"0"` shows as `✓ Output enabled — True`.
Render `'0' (truthy)` or `expected falsy, got '0'`, and consider describing the check as "is
truthy". *Effort S, impact medium, minor.*

#### IMP-11. Say which items or branches failed

`positives — expected all 4 to pass, got 2 failed` does not say which items. Add the first few
failing children, for example `got 1 failed: [1] expected ∈ [3.2V, 3.4V], got 3.55V`, and list the
considered labels when a guard has no match. *Effort S, impact high, minor.*

#### IMP-12. Escape and truncate the summary

Names and values are printed raw. A newline in a value splits a check over several lines and can
even inject a fake `✗ [99]` line. A 5000-character value prints in full, and 5000 passing checks
print 5000 lines. Escape newlines, truncate values with `saferepr` (about 240 characters), and
cap the passed section by default (see [FEAT-12](#feat-12-verbosity-aware-cli-options-and-a-session-summary)).
*Effort S, impact medium, minor.*

#### IMP-13. Keep descriptions small

`equal(big_list, big_list)` produced a 1.5-million-character description, and `length()` stores
the whole container even though only `actual_length` is needed (a 1M-element check serialized to
7.9 MB of JSON). Use a bounded renderer for descriptions and store a preview for `length`.
*Effort S, impact medium, minor.*

#### IMP-14. Readable output on non-UTF-8 terminals

On a terminal that cannot encode `✓ ✗ ∈ ≠` (for example piped output on Windows), pytest escapes
the whole summary, newlines included, into a single line. Emit the summary line by line so only
the problem characters are escaped, and fall back to ASCII (`x`, `ok`, `in`, `!=`) when the
encoding cannot represent them. *Effort S, impact medium, patch.*

#### IMP-15. Tell absolute and relative tolerances apart when units are `%`

With `units="%"`, `abs_tol=2` and `rel_tol=0.02` both render as `± 2%`, so a passing and a
failing check read the same. Add the `(abs)` or `(rel)` label when the unit ends with `%`.
*Effort S, impact low, patch.*

#### IMP-16. Explain NaN comparisons

`not_equal(nan, nan)` passes (IEEE semantics), and `equal(nan, nan)` prints
`expected nan, got nan`. Keep the semantics but add "(NaN never compares equal)" to the message,
and consider `verify.is_nan` or `verify.is_finite`. *Effort S, impact low, minor.*

#### IMP-17. Render compiled regexes properly

`matches(s, re.compile(r"\d+", re.I))` works but renders as
`/re.compile('\\d+', re.IGNORECASE)/`. Either accept only `str`, or accept `re.Pattern` and store
`pattern.pattern` plus its flags. *Effort S, impact low, minor.*

### Data model and reporter contract

#### IMP-18. Keep the verdict out of reach of mutation

The dict a fixture method returns is the same object the verdict hook reads, and
`get_check_results()` copies only the list, not the dicts. Any code that tidies up descriptors,
including a reporter, can turn a failing test into a passing one. Keep verdicts in private state
and return deep copies. *Effort S, impact medium, patch.*

#### IMP-19. Record a pre-built descriptor with the fixture

The README suggests building descriptors in helpers with the module API, but the fixture cannot
record them, and calling `verify.evaluate(d)` on the fixture returns `False` while the test
passes. Add `verify.record(*descriptors)` (see [ARCH-8](#arch-8-one-verify-front-end-with-a-pluggable-sink)).
*Effort S, impact medium, minor.*

#### IMP-20. Consistent per-child verdicts in composites

Whether composite children carry `passed` depends on how they were built: fixture-built children
keep a leftover value, and module-built children have none. Write `passed` on every evaluated
child and mark unmatched branches as not evaluated, so a reporter can always show which items
failed. *Effort S, impact medium, minor.*

#### IMP-21. Document that JSON round-trips change verdicts

JSON turns tuples into lists and non-string keys into strings, so `evaluate()` on a
round-tripped descriptor can flip (`equal((1, 2), [1, 2])`). Document that a serialized
descriptor should carry its result, or add an opt-in mode that stores the verdict. *Effort S,
impact low, patch.*

#### IMP-22. Absorb children consistently when mixing module and fixture checks

Children are absorbed only by identity, one level deep, and only when the parent is also built by
the fixture. A module-built composite over fixture children, a module-built middle level, or a
copied descriptor all leave children behind as standalone checks. Document the rule now; the lazy
children design fixes it. *Effort S, impact low, patch.*

#### IMP-23. Send check data to the xdist controller

Results live only in the worker's `item.stash`, so a reporter running on the xdist controller
gets nothing. Attach a JSON-safe copy of the results to the report, for example in
`user_properties` or a report attribute that xdist serializes. *Effort S, impact medium, minor.*

#### IMP-24. Export the public types and document the reader contract

The README never mentions `get_check_results`, `CheckDescriptor`, or the fact that fixture calls
return evaluated descriptors. `ChecksFailedError` and `GuardBranch` are only importable from
private modules, and `check_results_key` is importable but not in `__all__`. Export the first two,
make the key private, and add a "Reading results" section to the README. *Effort S, impact
medium, minor.*

### Typing and packaging

#### IMP-25. Make the package pass `mypy --strict`

mypy reports 10 errors by default and 24 with `--strict` inside the package itself: `branches` is
built as `list[dict]` but typed `list[GuardBranch]`, `check_results_key` is an untyped
`StashKey[list]`, `_types.py` has an unresolvable name, and there is a stale `type: ignore`.
Fix them and run mypy in CI. *Effort S, impact medium, patch.*

#### IMP-26. Required keys in `CheckDescriptor`

`CheckDescriptor` is `total=False`, so `x: CheckDescriptor = {}` type-checks and then fails with
`KeyError`. Put `check_type`, `name` and `description` in a total base class; this works on
Python 3.9 without `typing_extensions`. *Effort S, impact low, patch.*

#### IMP-27. Narrower parameter types where calls always fail

`length(actual: Any)`, `contains(haystack: Any)` and the `all_satisfy` factory accept calls that
always raise. Use `Sized`, `Container`, and a `TypeVar` that ties `items` to the factory's
argument. *Effort S, impact low, minor.*

#### IMP-28. Remove or use `_types.py`

Nothing imports `Numeric` or `DescriptorFactory`, `DescriptorFactory` refers to a name the module
never imports, and the comment claims a re-export that does not exist. Either delete the module
or import `CheckDescriptor` under `TYPE_CHECKING` and use the aliases. *Effort S, impact low,
patch.*

#### IMP-29. Use a specific plugin name and a public plugin module

The pytest11 entry point is `verify = "pytest_verify._fixture"`, so
`has_plugin("pytest-verify")` is `False`, `-p pytest_verify` does not load the fixture, and
`pytest_plugins = ["pytest_verify._fixture"]` in a conftest crashes startup when autoload is on.
Use a public module such as `pytest_verify.plugin` and match the entry-point name to it.
*Effort S, impact medium, major if the name changes.*

#### IMP-30. Richer package metadata and a tested pytest floor

The built metadata has no classifiers (`Framework :: Pytest`, `Typing :: Typed`, Python
versions), no project URLs and no author. CI only ever runs pytest 8.4 and 9.0, so the declared
`pytest>=7.0` floor is untested. *Effort S, impact medium, patch.*

#### IMP-31. Faster composite recording

Each absorbed child rebuilds both result lists, so an `all_satisfy` built with the fixture is
quadratic: 10,000 items took 2.2 s and 50,000 took 61 s, compared with 0.05 s on the module path.
Remove all children in one pass with an id set. *Effort S, impact medium, patch.*

#### IMP-32. Test the contracts the bugs slipped through

No test covers JSON serialization, xfail, skip, enum keys, reruns, teardown checks,
`is_instance` with a tuple, runtime type hints or the README examples. `verify.fail()` is never
called through the fixture, and the "all 20 methods" smoke class has no `guard` test. The new
`tests/test_regressions_*.py` modules (first added as `tests/test_known_bugs_*.py`) start on this; the harness ideas below finish it.
*Effort M, impact high, patch.*

---

## Architecture and refactoring

#### ARCH-1. A registry of check types instead of parallel if-chains

Each check type is spread over up to six places that must agree: a builder in `_descriptors.py`,
a `Verify` method, a `_FixtureVerify` override, a branch in `_evaluate_single`, a branch in
`_detail`, and `_child_descriptors` for composites. They already disagree (`is_instance` is
judged differently in the fixture and the evaluator; `conditional` is looked up differently by the
evaluator and the renderer). Put one class per check type in a private `_checks/` package with
`build`, `judge`, `detail` and `children` methods, and dispatch through a registry. That also
gives users an extension point ([FEAT-9](#feat-9-custom-check-types)).
*Effort L, impact high, minor.*

```python
class CheckType(Protocol):
    check_type: ClassVar[str]
    def build(self, *args, **kwargs) -> Spec: ...
    def judge(self, spec: Spec, children: Sequence[CheckRecord]) -> Outcome: ...
    def detail(self, record: CheckRecord, passed: bool) -> str: ...
    def children(self, spec: Spec) -> Sequence[Spec]: ...
```

#### ARCH-2. Judge every check in one exception-safe place

Today the verdict is whatever the comparison returned, it is read back from dicts users can
change, and the renderer recomputes it. Make one function the only place that compares user
objects. It coerces the result to `bool` inside a `try`, turns any exception into a failed
outcome with an error note (with a hint such as "elementwise result; use `.all()`" for arrays),
and returns an immutable `Outcome`. Fixes C-1, H-1, L-3 and L-5. *Effort M, impact high, minor.*

#### ARCH-3. Separate the JSON evidence from the live values

One mutable dict is currently the executable spec, the evaluated result, the JSON report and the
user's return value, and it holds live references. Freeze an immutable `CheckRecord` when a check
is recorded: the verdict, an error if any, the phase, the call-site location, a timestamp, and
JSON-safe evidence (`actual_repr`, `actual_type`, and the raw value only when it is JSON-native).
Renderers read only the record and never re-evaluate. Add a `schema` version field. Fixes C-2,
M-3, M-4, M-5 and M-6. *Effort L, impact high, minor.*

#### ARCH-4. Lazy composite children instead of evaluate-then-discard

Accept zero-argument callables wherever a child check goes, and callables as guard conditions,
so only the matched branch runs:

```python
verify.conditional(mode, name="Output", cases={
    Mode.STANDBY: lambda: verify.approx(dut.vout(), 0.0, abs_tol=0.01, name="Standby", units="V"),
    Mode.ACTIVE:  lambda: verify.approx(dut.vout(), 3.3, abs_tol=0.1, name="Active", units="V"),
}, default=lambda: verify.fail(f"Unknown mode {mode}"))
```

Internally, keep a per-thread stack of collectors: checks emitted while a composite runs a thunk
go to that composite instead of the top-level list. Nothing needs to be discarded, so reuse can
no longer delete a check and the quadratic cost disappears. Keep eager children working for
compatibility. Fixes H-2, H-4, L-3 and M-15. *Effort M, impact high, minor.*

#### ARCH-5. Enforce the verdict by raising, not by rewriting reports

Replace the report rewriting with a plugin object whose phase wrappers raise
`ChecksFailedError` (with `__tracebackhide__ = True`):

```python
class VerifyPlugin:
    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_setup(self, item):
        item.stash[_RUN] = ItemRun(item)  # fresh state for every attempt
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        __tracebackhide__ = True
        res = yield  # hard failures propagate unchanged
        if item.stash[_RUN].any_failed("setup", "call"):
            raise ChecksFailedError(item.stash[_RUN].records("setup", "call"))
        return res
```

Handle teardown the same way, and attach the soft summary to the setup error or hard failure when
one already happened. A prototype on pytest 9 showed xfail (strict and `raises=`) reports XFAIL,
`pytest_exception_interact` fires, and junitxml is consistent. Fixes H-7, H-8, M-7 to M-13, L-7,
L-8 and D-1. New-style wrappers need pluggy 1.2 or later; declare it, or keep old-style
hookwrappers. *Effort M, impact high, minor.*

#### ARCH-6. Let pytest-reporter read results without importing pytest-verify

A `StashKey` is matched by identity, so the "shared key by convention" in the spec cannot work.
Offer channels that need no import: hookspecs registered with `pytest_addhooks` (a consumer
implements them with `optionalhook=True`, which is harmless when pytest-verify is absent), and a
JSON-safe report attribute that survives xdist serialization. Keep `get_check_results` for
consumers that do import. *Effort M, impact high, minor.*

```python
def pytest_verify_results(item, when: str, records: list[dict], passed: bool) -> None: ...
```

#### ARCH-7. One thread-safe run object per attempt

State is split between the fixture instance and a separately created stash list, and neither is
reset per attempt. Keep one `ItemRun` per protocol run with an `RLock`, an append-only record
list, the current phase, the creating process id, and a `closed` flag. Fixes M-7, M-8, M-15, L-9
and IMP-8. *Effort M, impact medium, patch.*

#### ARCH-8. One `Verify` front-end with a pluggable sink

`_FixtureVerify` re-declares all 21 signatures only to wrap each call, so every signature change
must be made twice, and the `is_instance` override has already drifted. Give `Verify` a sink:
the module instance builds only, and the fixture's sink judges and records. That removes about
140 lines and makes `verify.record()` (IMP-19) trivial. *Effort S, impact medium, minor.*

#### ARCH-9. Rename the module-level builder

Expose the builder namespace as `checks` (`from pytest_verify import checks`) so it cannot be
confused with the fixture. Keep `verify` for one minor release through a module `__getattr__`
that emits a `DeprecationWarning`. While a test runs, also warn about descriptors built with the
module API that were never recorded, evaluated or used in a composite. *Effort S, impact medium,
minor now, major when the old name is removed.*

#### ARCH-10. A contract test harness driven by the registry

Give each check type a list of examples, including hostile values (an elementwise `__eq__`, a
`__repr__` that raises, a huge `int`, enums, NaN, a same-name class, a value mutated after the
check). Parametrize one test over every type and example, asserting that JSON serialization
works, that the fixture verdict equals `evaluate()` before and after a JSON round-trip, that
rendering never raises, and that mutation does not change the output. *Effort M, impact high,
patch.*

---

## Feature ideas

#### FEAT-1. Record where each check was called

Soft failures have no location, so with checks in loops, helpers or factories you have to search
for the name. Walk the stack outward past `pytest_verify` frames when a check is recorded, and
store the user's `file:line` (plus the test line when a helper made the call). Show it in the
summary, for example `✗ [1] Rail (test_psu.py:17 via :42) — ...`, and optionally emit GitHub
Actions `::error file=...,line=...` annotations. *Effort S, impact high, minor.*

#### FEAT-2. `verify.raises` and error-tolerant checks

Add a soft `verify.raises(ExcType, match=...)`, usable as a context manager, that records a
failed check instead of propagating when the expected exception is missing. Combined with ARCH-2,
add an ini option `verify_errors = record | raise` so teams can choose whether an exception
inside a check is recorded or raised. *Effort M, impact high, minor.*

Shipped in 0.10.0: `with verify.raises(...)`, soft only for what the code under test did
(nothing raised, or the expected type with another message); other exceptions propagate after
the failed check is recorded. `verify_errors` was left out: `verify.require` and fail-fast
already stop at a check that could not be evaluated.

#### FEAT-3. Strictness controls

Add ways to make checks stricter where needed: a marker or ini option for fail-fast (the first
failed check raises immediately), `verify.require(...)` for a single hard check that stops the
test, and a warning level for checks that should be reported but not fail the test. Make the
exception's first line self-contained so `-r` and junit messages carry a reason:
`2 of 5 checks failed: Vout — expected 3.3V ± 0.05V, got 3.8V (+1 more)`. *Effort M, impact
high, minor.*

#### FEAT-4. Export results

Add an ini option `verify_junit_properties = none | failed | all` that writes checks to junit
`<property>` elements, and a `--verify-json PATH` option that writes one JSON Lines record per
check. Both are built from the snapshots in ARCH-3, so they work under xdist. *Effort M, impact
high, minor.*

#### FEAT-5. Collection-aware checks

Hardware tests compare waveforms, sample buffers and register maps. Add
`verify.all_close(actual, expected, *, abs_tol=None, rel_tol=None, name, units=None)` that
accepts sequences or anything with `__array__` (no numpy dependency) and stores only bounded
results: count, number failed, the first failing indices, and the worst sample. It renders as
`3 of 1024 samples outside 3.3V ± 0.05V; worst [517] 3.41V (+0.06V)`. Add `verify.array_equal`,
and structured diffs for `equal` on dicts and sequences ("key `CTRL`: expected 0x3f, got
0x3e"). *Effort L, impact high, minor.*

#### FEAT-6. `pytest.approx` interop

Accept a `pytest.approx` object as the expected value (`verify.approx(v, pytest.approx(3.3,
abs=0.05))` or `verify.equal(v, pytest.approx(...))`) and render it properly. That also gives
sequences, dicts and `timedelta` for free. *Effort S, impact medium, minor.*

#### FEAT-7. Lab-grade number formatting

Add a `fmt=` keyword on numeric checks (`fmt="#04x"` for registers, `fmt="eng"` for SI prefixes
such as `2 nA ± 500 pA`), show small relative tolerances in ppm, format tolerances with the same
formatter as values so `± 0.30000000000000004` disappears, and show how far outside the limit a
failing reading was. Duck-type `pint` quantities (`.magnitude` and `.units`) without depending on
pint. *Effort M, impact medium, minor.*

#### FEAT-8. `verify.section()` for grouping

```python
for rail in RAILS:
    with verify.section(rail.name):  # names become "3V3 › Vout"
        verify.approx(rail.vout(), rail.nominal, rel_tol=0.02, name="Vout", units="V")
        verify.less(rail.ripple_mv(), 20, name="Ripple", units="mV")
```

Each check gets a JSON-safe `section` path, and the summary groups checks under headings with
per-section counts. `verify.section(title, subtest=True)` could map a section onto a pytest
subtest, which also gives correct subtest attribution (M-9). *Effort M, impact medium, minor.*

#### FEAT-9. Custom check types

Labs need domain checks: within ppm of nominal, monotonic sweep, eye-mask margin, CRC OK. On top
of ARCH-1, expose a typed `CheckType` base class and `register_check`, so a `Verify` subclass
can add a method that gets evaluation, rendering, recording and autocompletion for free.
*Effort M, impact medium, minor.*

#### FEAT-10. Limits tables

Production tests are usually driven by limit files per SKU, revision or temperature corner. Add
`verify.limits(measurements, LIMITS, on_missing="fail")`, where each row maps onto an existing
typed builder, and `load_limits("limits.csv")` using only the standard library. Rows are
validated when the table is loaded, and a measurement that was never taken is reported instead of
silently missing. *Effort M, impact high, minor.*

```python
LIMITS = {
    "Vout": {"check": "approx", "expected": 3.3, "abs_tol": 0.05, "units": "V"},
    "Icc":  {"check": "between", "low": 0.1, "high": 0.5, "units": "A"},
}
verify.limits(measurements, LIMITS, on_missing="fail")
```

Shipped in 0.10.0, with strict rows, `load_limits(path, select=, columns=, encoding=)`, cells
typed by the measurement, and `limit_source` on each record.

#### FEAT-11. `verify.eventually` and `verify.stable`

Readings take time to settle after power-up or a setpoint change. `verify.eventually(read,
check_factory, timeout=2.0, interval=0.1, name=...)` records one check that passes as soon as a
sample passes, and stores the number of samples and the settling time. `verify.stable(...)`
requires every sample in a window to pass and stores the worst sample. *Effort M, impact medium,
minor.*

Shipped in 0.10.0 with one `sample` callable that makes the check, try scopes (a try that is
not kept is dropped with its checks), a bounded trace and `settled_at`.

#### FEAT-12. Verbosity-aware CLI options and a session summary

Add `--verify-show-passed=auto|all|none|N` (with `auto` following `-q` and `-v`),
`--verify-ascii` (automatic when the terminal cannot encode the symbols), and
`--verify-summary=off|failed|all|stats`, which adds a terminal section grouping checks by name
across tests and parameters (for example, "Vout: 120 runs, 3 failed, closest margin 4 mV").
*Effort M, impact medium, minor.*

---

## Developer experience, CI and releases

#### DX-1. Rename before publishing to PyPI

As described at the top, `pytest-verify` on PyPI is an unrelated plugin that also ships a
`pytest_verify` import package. Pick a free name and rename the distribution and the import
package in one breaking release, keep the `verify` fixture name (the other plugin does not
define it), and give the entry point a specific name that matches a public module. *Effort M,
impact high, major.*

#### DX-2. Tag-driven release workflow with Trusted Publishing

Add a `release.yml` triggered by `v*` tags that builds with `uv build`, runs `twine check`,
checks that the tag matches the project version and that the CHANGELOG has a dated heading for
it, unpacks the sdist and runs the tests inside it (catches L-12), installs the wheel into fresh
environments with the lowest and latest pytest, publishes to PyPI with Trusted Publishing, and
creates the GitHub release from the CHANGELOG section. Add a `__version__`. *Effort M, impact
high, patch.*

#### DX-3. Turn CI into a quality-gate matrix

CI is a single job on Ubuntu. Split it into lint (ruff check and format), type checking (mypy
`--strict` and pyright, including `pyright --verifytypes` for the public API), and tests across
Ubuntu, Windows and macOS, with a lowest-dependencies job that installs `pytest==7.0.*`. Add a job
with a non-UTF-8 encoding for IMP-14, run on all branches, and add a weekly scheduled run.
*Effort M, impact high, patch.*

#### DX-4. Typing contract tests

Add `tests/typing/` files checked by mypy `--strict` and pyright in CI. Positive cases use
`assert_type`, and negative cases use targeted `type: ignore` comments with
`--warn-unused-ignores`, so a signature that becomes too permissive also fails. This pins M-16
and keeps the "autocompletion is non-negotiable" promise honest. *Effort M, impact high, minor.*

#### DX-5. Property-based tests against an independent oracle

Add `tests/properties/` with Hypothesis as a test-only dependency. Each property compares against
plain Python semantics rather than the code itself: verdicts equal the Python operators, `passed`
is always a `bool`, every descriptor survives `json.dumps`, a composite equals its children, and
mutation after the check does not change the output. *Effort M, impact high, patch.*

#### DX-6. A table-driven outcome matrix for pytest integration

The most severe bugs are in the hook layer. Add one parametrized pytester matrix of scenarios
(soft fail, xfail, strict xfail, skip after a soft failure, teardown checks, setup errors, reruns,
subtests, `--tb` modes, junitxml) with the expected outcomes, and reorganize the tests into
`unit/`, `plugin/`, `properties/`, `typing/` and `docs/`. *Effort M, impact high, patch.*

#### DX-7. Executable documentation

Keep runnable examples in `examples/` with a deterministic fake instrument, include them in the
README with `cog`, and compare the failure output against golden files. The README guard example
(D-2) could not have drifted this way. *Effort M, impact medium, patch.*

#### DX-8. A docs site, the spec in the repo, and a JSON Schema

The authoritative spec is a private Notion page, yet code and tests cite its section numbers.
Move it into `docs/spec.md` with the same numbering, build a docs site with MkDocs Material and
mkdocstrings, and ship a versioned JSON Schema for descriptors that the tests validate against.
*Effort L, impact medium, minor.*

#### DX-9. SemVer and changelog gates on pull requests

Nothing enforces the SemVer and Keep a Changelog rules, and D-5 shows the drift. Add a PR job
that fails when `pytest_verify/` changes without a CHANGELOG `[Unreleased]` entry, validates the
CHANGELOG format, and diffs a snapshot of the public API (`__all__`, signatures, `CheckDescriptor`
keys) so any change needs an explicit minor or major label. *Effort S, impact medium, patch.*

#### DX-10. A contributor on-ramp

The lockfile contains only pytest. Add dependency groups (`test`, `lint`, `docs`, `dev`), ruff
configuration with 100-character lines, a pre-commit config, a `noxfile.py` or justfile,
`CONTRIBUTING.md`, issue and PR templates, and Dependabot for GitHub Actions. *Effort S, impact
medium, patch.*

#### DX-11. An agent skill and a command that installs it

Coding agents (Claude Code, Codex and others that read `.agents/skills/`) write many of the tests
that use the `verify` fixture. Ship an agent skill inside the package that teaches them the
API precisely: every check, its pass rule and its traps, composites and lazy children,
`verify.require` and fail-fast, `checks` with `verify.record()`, and how to read a failure. Add a
`pytest-verifier skill install` command that copies the skill of the installed version into
`.claude/skills/` and `.agents/skills/` (`--claude`, `--agents`, `--global` for the home
folder). Tests keep the skill in step with the code: every public method, export and option must
appear in it, and its version must match the package. *Effort M, impact high, minor.* Asked for
by the owner on 2026-10-02 for 0.9.0.
