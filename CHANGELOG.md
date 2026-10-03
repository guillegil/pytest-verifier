# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] - 2026-10-03

### Added

- `verify.limits(measurements, table, *, on_missing="fail")` checks measurements against a table
  of limits: one ordinary check per row, named by the row, returned by name. A row holds the
  arguments of a check method (and `"check"`, its name); without it the limits say which check
  (`low`+`high` is `between`, `low` or `high` alone an ordering check, `expected` with a
  tolerance `approx`, `expected` alone `equal`). Rows are validated before anything is
  recorded: limits must be finite numbers, `rel_tol` below 1, `inclusive` a bool, and a float
  `expected` needs a tolerance. A row with no measurement fails with `not measured: ...`,
  naming a similar measurement key (`on_missing="ignore"` skips it). `verify.require.limits`
  and fail-fast stop only after the whole table is recorded. `LimitRow` is the row's
  `TypedDict`.
- `pytest_verifier.load_limits(path, *, select=None, columns=None, encoding="utf-8-sig")` reads
  a limits table from a CSV file: comma, semicolon (with decimal commas) or tab separated,
  headers in any case, `#` comment lines, `columns=` to rename or skip columns, and selector
  columns (`select={"corner": "hot"}`) where a line naming the value beats a default line and
  a cell may list values (`hot|warm`). Every line is validated when the file is read, with
  `path:line` in the error, and each row's `source` reaches its record as `limit_source`. The
  `expected` of `equal`/`not_equal` and `needle` are compared as the measurement is (text
  against text, a number against a number, a `Decimal` against a `Decimal`).
- `with verify.raises(expected_exception, *, match=None, name) as raised:`, a soft
  `pytest.raises`: it records a `raises` check when the block ends. Nothing raised, or the
  expected type with a message `match` does not find, is a failed check and the test goes on;
  any other exception records the failed check and then propagates. `raised.value`,
  `raised.type` and `raised.check` give the exception and the check; the record has
  `raised_type`, `raised_message` and `raised_at` (the line that raised). `Exception` and
  `BaseException` need `match=`. A `verify.raises()` never used in a `with` statement becomes
  a failed check. `Raises` is exported, generic in the exception type.
- `verify.eventually(sample, *, timeout, interval=0.1, name)` and `verify.stable(sample, *,
  duration, interval=0.1, name)` try a check made by `sample` again until it passes, or for a
  duration in which every try must pass. Each try starts one interval after the last one
  started; `stable` tries at least twice. The record keeps the try that decided, `tries`,
  `elapsed`, `settled_at` and a `trace` of `[seconds, value, passed]` (first and last 50
  tries). Failed tries never fail or stop the test: a try that is not kept is dropped with
  every check it recorded, and a skip in a sample stays a skip. A usage error in the sample
  fails the check at once, and a value that never changed is pointed out. Times take a
  `timedelta` too; a running event loop gets a `RuntimeWarning`.
- The reason of an xfailed test whose checks failed ends with the summary's first line
  (`[1 of 2 checks failed: ...]`), so `-rx` and junitxml show the failures.

### Changed

- `verify.record(check)` of a check that a composite took but did not select records a copy of
  it on its own (it did nothing). Recording a check before passing it to a composite keeps it
  on its own as well.
- `pytest.exit` (also quitting the debugger), `bdb.BdbQuit` and `unittest.SkipTest` raised in a
  lazy child, a guard condition or an `all_satisfy` factory go on instead of failing the
  composite.
- `verify.fail(msg)` with an empty or blank message is named `fail` and described `FAIL: (no message)`
  instead of raising; an explicit empty `name` still raises.
- A `str` enum member (or another `str` subclass) used as a name, a `fail` message or a guard
  label is stored as its text, not as its repr. A blank name raises `ValueError`.

### Fixed

- `all_satisfy` whose factory raised said "expected all 0 to pass"; it now says how many items
  were checked before the error.
- A negative zero tolerance (`abs_tol=-0.0`) is stored and shown as `0.0`.
- Very long strings in records are cut at 10,000 characters and count toward the snapshot
  size limit.
- A long name no longer makes a description of unbounded length.

## [0.9.0] - 2026-10-03

### Added

- An agent skill that teaches coding agents (Claude Code, Codex and others) to write tests with
  pytest-verifier, shipped in the package, and the `pytest-verifier` command that installs it.
  `pytest-verifier skill install` writes it to `.claude/skills/pytest-verifier/` and
  `.agents/skills/pytest-verifier/` in the current folder: `--claude` or `--agents` (also
  `--generic`) picks one of them, `--global` uses the home folder, and `--force` replaces a
  folder there that the command did not install. Running it again updates the skill and says
  from which version. `python -m pytest_verifier` runs the same command.
- pytest's header says when the skill in the project's `.claude/skills` or `.agents/skills` is
  for another version of pytest-verifier, and whether to update the skill or to upgrade
  pytest-verifier.
- `verify.section(title)`, a context manager that groups the checks recorded in its block.
  Summaries name them after their sections (`✗ [3] 5V0 › Ripple …`), also in the first line,
  and each record has their titles in a new `section` field (`["5V0"]`, outermost first).
  Sections nest and follow `contextvars`: an asyncio task created in a section is in it, a
  plain `threading.Thread` started in it is not. `checks.section()` raises `RuntimeError`.
- `--verify-json PATH` writes every check to a JSON Lines file, one object per check with the
  test's node ID, its attempt (more than one when pytest-rerunfailures repeats the test), the
  phase that judged it, the report's outcome, its index in the summary and the recorded check.
  It is written from the reports, so it works under pytest-xdist.
- The `verify_junit_properties` setting (`none`, `failed` or `all`; default `none`) adds checks
  to the junit XML report as `<property name="verify[3] 5V0 › Ripple" value="failed: …"/>`. A
  rerun keeps only the last attempt's. With a junit family that does not allow properties
  (pytest's default `xunit2`), a warning says to use `xunit1`.
- `--verify-show-passed=N|all|none` (ini `verify_show_passed`, default 10) sets how many passed
  checks a failure summary lists. `-vv` still lists them all.
- `--verify-ascii` (ini `verify_ascii`) prints summaries in the terminal with ASCII markers and
  escapes, as pytest-verifier already does on a terminal that cannot show Unicode. Reports
  such as junitxml keep the Unicode text.
- `--verify-summary=off|failed|all|stats` (ini `verify_summary`, default `off`) adds a terminal
  section that counts each check name across the run, failed names first, with the first test
  that failed it: `✗ 3V3 › Vout: 3 of 12 failed (first: tests/test_rails.py::test_rail[hot])`.
  `stats` adds the range of numeric values and the smallest margin to a limit. It is built from
  the reports, so it works under pytest-xdist, and a rerun test counts once.

### Fixed

- `verify.require(value)` with something that is not a check named `record()` in its error; it
  now names `require()`.
- A composite made in teardown no longer takes in checks that the test body made: they were
  judged and numbered when the body ended, so they stay listed on their own. Before, the
  teardown summary could give two checks the same `[k]`.
- Recorded checks can always be written as UTF-8: text with lone surrogates, such as a file name
  `os.listdir` could not decode, is kept as `\udce9` escapes. Such text stopped pytest-xdist
  workers. A hand-built check whose key is not text gets the key's `repr`, so records stay
  JSON-safe.
- Summaries show lone surrogates and the noncharacters U+FFFE and U+FFFF as escapes, so they
  print on any terminal and junit reports stay well-formed.

## [0.8.0] - 2026-10-02

The first release on PyPI: `pip install pytest-verifier`. Failed checks now say where they were
made, a check can stop the test when it fails, and the first line of a failure says what
failed.

### Added

- Published on PyPI as `pytest-verifier`. Releases up to 0.7.0 stay installable from Git tags.
- Every check the `verify` fixture records has a `location`, the file and line that made it,
  relative to the rootdir (`"tests/test_psu.py:17"`). When a helper or a lazy child made it,
  `called_from` holds the line of the test function that led to it. Failed checks show both in
  the summary: `✗ [1] Rail (lib/rails.py:8, called from tests/test_psu.py:22) — …`, or
  `called from line 22` in the same file. Passed checks stay as they were. The test function is
  found behind decorators, also ones without `functools.wraps` such as Hypothesis's `@given`,
  and a comprehension in the test body counts as the test on every Python version. Installed
  code that runs the test, such as pytest-bdd's generated test, is never shown as the caller.
- `verify.require`, with the same methods as `verify`: a check made with it that fails stops
  the test at once with `ChecksFailedError`, listing every check made so far, also from inside
  a lazy child or an `all_satisfy` factory. The first line names the check that stopped the
  test: `2 of 2 checks failed, stopped at [1]: Link — …`. `verify.require(check)` does the same
  for a check made earlier or built with `checks`; a check that a composite took or an earlier
  phase judged is recorded again, so the error names it, and a composite never takes the check
  that stopped the test. The checks stay recorded, so a test that catches the error still fails
  at the end of the phase. `--pdb` opens in the test, at the failed check, also when checks are
  made after it. A skip in a `finally` after a stop does not hide it, and the summary keeps
  "stopped at"; so does the "Soft assertion failures" section of another error raised there.
  The methods of `checks.require`, and calling it, raise `RuntimeError`.
- `pytest_verifier.Require`, the type of `verify.require`, for annotating helpers.
- `--verify-fail-fast` and the `verify_fail_fast` ini setting stop each test at its first
  failed check. Checks made while fixtures are torn down, or in a unittest `TestCase`'s
  `tearDown` and cleanups, stay soft, so their cleanup runs.
- `pytest_verifier.__version__`.
- The release workflow builds once, checks the metadata with `twine check`, runs the sdist's
  tests against the wheel with pytest 7.0.1 and with the newest pytest, and uploads to PyPI with
  Trusted Publishing before publishing the GitHub release. A `testpypi` target uploads to
  TestPyPI only. Only commits on `main` are released, the actions are pinned to commits, and
  after a partial failure "Re-run failed jobs" finishes the release with the same files. CI
  runs the same build and tests on every pull request.

### Changed

- The first line of a failure names the first failed check, so `-r` summaries and junit
  messages say what failed: `2 of 5 checks failed: Vout — expected 3.3V ± 0.05V, got 3.8V
  (+1 more)`. It was `2 of 5 checks failed`. The first failure is cut at about 300 characters.
- With `--tb=line`, the line shown for a soft failure is the line of the test that made the
  check the first line names (or called the helper that made it), instead of the test's `def`
  line. It is paired with the file of the test function, also for a test inherited from a class
  in another file or hidden by a decorator from another module.
- `-r` summaries and `--tb=line` print the first line in the terminal's encoding, like the
  rest of the summary. Other plugins' terminal summaries still read the summary as it is.
- The README renders on PyPI: its links, section links included, point at GitHub.
- The repository moved to https://github.com/guillegil/pytest-verifier, the name the plugin
  is installed by. The old address redirects.

## [0.7.0] - 2026-10-01

Makes failures easier to read: values show their type when it matters, long values say where
they differ, composites say what failed inside them, and terminals that cannot show the
summary's symbols still get one line per check. Ordering checks now refuse to compare text
with text, which Python does letter by letter.

### Added

- `matches()` accepts a pattern compiled with `re.compile`. The check stores its source as `pattern` and its flags as `flags`, and shows them as `/^v\d/i`. Every `matches` check now has a `flags` key, an `int` (`0` for a string pattern).
- The summary lists at most 10 passed checks, then `✓ … N more passed checks (-vv shows them)`. With `-vv` it lists all of them. Every check is still recorded and passed to other plugins. `ChecksFailedError` takes `max_passed` to set the limit.
- A failed `equal` whose values look the same once shortened says where they first differ: `first difference at [25]: expected 3.3V, got 3.9V`, a missing or unexpected key, a different length, or a window of the text or digits around the first difference.
- On a terminal whose encoding cannot show every character of a summary, such as a Windows CI log in cp1252, the summary is printed with `x` and `ok` in place of `✗` and `✓`, and any other character the terminal cannot show is escaped (`\u2014`), line by line. This works with pytest-xdist too. Only what the terminal prints changes: the error message, `report.longrepr`, junitxml and other reports keep the summary as it is. Before, pytest escaped each character it could not print, and wrote the "Soft assertion failures" section on one line with `\n` escapes.

### Changed

- `greater`, `greater_equal`, `less`, `less_equal` and `between` fail when the value and a limit are both text (`str`, `bytes` or `bytearray`), with an error saying to convert them with `float()`. Before, `"100" > "20"` was compared letter by letter and gave the wrong answer without any error. Text against a number already failed with Python's `TypeError`; that error now also says to convert with `float()`. Types that compare with text on their own terms, such as `semver.Version`, work as before.
- Values that are not numbers are shown with a `repr`, so strings are quoted: `expected 1, got '1'`. Numbers keep their units (`3.3V`). Enum members show as `Mode.ACTIVE`, and numeric ones add their value: `Gain.LOW (10dB)`. When two values still look the same, `equal`, `not_equal`, the ordering checks and `between` add their types: `expected 0.1 (float), got 0.1 (Decimal)`. Descriptions follow the same rules, e.g. `Verify 'Reply' == 'OK'`.
- `is_true` and `is_false` show the value and how it tests, e.g. `'0' (truthy)`, instead of `True` or `False`. The value's truth is read once, when the check is judged.
- A failed `all_satisfy` lists its first three failing items by index, with their details: `got 2 failed: [1] expected [3.2V, 3.4V], got 3.55V; [3] …`. Item names are shown when they differ.
- A `conditional` with no matching case and no default lists the keys it tried, `[mode=7 → no case matched: 0, 1, 2]`, and adds the switch value's type when it reads like one of them: `[mode=1.5 (str) → no case matched: 1.5, 2.5]`. A `guard` lists the branch labels it tried, `[→ no branch matched: shutter closed, below floor]`, or says `[→ no branch chosen]` when a condition raised.
- With `units` ending in `%`, an `approx` tolerance says whether it is absolute or relative: `± 2% (abs)` or `± 2% (rel)`.
- A NaN in a comparison adds a note: `(NaN never compares equal)` for `equal`, `not_equal` and `approx`, also for a NaN inside a list, and `(NaN fails every comparison)` for the ordering checks. Verdicts are unchanged.
- Line breaks and other control characters in names, labels, messages, units and values are escaped (`\n`), so a value can no longer add lines to the summary. A value is shortened to about 240 characters, a unit label to 40 and a `fail()` message to 1000; a summary line cuts its detail at 2000.
- Descriptions use the same bounded rendering, so `equal(big_list, big_list)` no longer stores a description as long as the list. A recorded `length` check keeps a preview of the value (at most 100 items, text cut at about 240 characters) instead of all of it; `actual_length` has the length.
- Narrower parameter types for type checkers. `length()` takes a `Sized`, and `contains()` and `not_contains()` a container or iterable, so `mypy` rejects a number there, and also an `Optional` value: narrow it first, e.g. `assert names is not None`. In `all_satisfy()` the factory's argument has the type of the items, so IDEs complete it in a `lambda` and type checkers check the `lambda`'s body; for loosely typed items such as `Dict[str, object]` rows, use `typing.cast`.

## [0.6.0] - 2026-10-01

Renames the project to pytest-verifier, because the name `pytest-verify` belongs to another
plugin on PyPI that also ships a `pytest_verify` package. Tests that only use the `verify`
fixture need no change; imports and options that name `pytest_verify` must be updated. See
"Upgrading from pytest-verify" in the README.

### Added

- `UnusedCheckWarning`, a `PytestWarning` shown when the body of a test builds a check with `checks.*` and, by the end of the test's teardown, has not recorded it, evaluated it or passed it to a composite. Such a check cannot fail the test, which is what happened when a test imported the builder instead of requesting the `verify` fixture. The warning names where each check was built and points at the line that built the first one. Checks built in fixtures (including ones the body requests with `request.getfixturevalue()`), at import time, during setup and teardown, and in unittest `TestCase`s are not tracked, and neither is a session that `pytester` runs inside a test. Filter it with `ignore::pytest_verifier.UnusedCheckWarning`, optionally for one module, where building checks without using them is intended.
- `-p pytest_verifier` loads the plugin when plugin autoloading is disabled, and `-p no:pytest_verifier` turns it off.

### Changed

- The distribution is now `pytest-verifier` and the import package `pytest_verifier`. Uninstall `pytest-verify` before installing it: pytest cannot load both, and stops with a message that says what to uninstall.
- The module-level builder is now `checks` (`from pytest_verifier import checks`), so it cannot be confused with the `verify` fixture. `pytest_verifier.verify` still works and shows a `DeprecationWarning`.
- The plugin module is public: `pytest_verifier.plugin`, loaded through an entry point named `pytest_verifier`. A conftest can list `pytest_plugins = ["pytest_verifier"]` even when autoloading is on; with 0.5, listing the plugin module there crashed pytest at startup with "Plugin already registered under a different name".
- `ChecksFailedError` reports its module as `pytest_verifier`.
- Unchanged on purpose: the `verify` fixture, the `pytest_verify_results` hook and `report.verify_checks` keep their names, so pytest-reporter and other readers keep working.

### Deprecated

- `pytest_verifier.verify`, an alias of `checks`.

### Removed

- The `pytest_verify` import package, with every name it had, including `pytest_verify._fixture`. Import from `pytest_verifier` instead, and list `pytest_verifier` in a conftest's `pytest_plugins`; the old name belongs to the other project.
- The `verify` entry point name: `-p no:verify` no longer turns the plugin off; use `-p no:pytest_verifier`.

## [0.5.0] - 2026-10-01

Reorganizes the internals around one class per check type, and adds lazy composite children,
`verify.record()` and a way for other plugins to read results without importing pytest-verify.
Apart from callable `guard` conditions (see **Changed**), checks made the 0.4.0 way behave as
before.

### Added

- Lazy children. A `conditional` case or default, and a `guard` check or default, can be a function with no arguments that returns the check, such as a `lambda`. Only the selected one is called, so the other branches never touch values they cannot use. In the recorded check, a lazy child that was not called is `None`. If the selected function raises, or returns something that is not a check, the composite fails with an `error` note.
- `guard` conditions can be functions too. They are called in order until one is true; the conditions after it are not called and are recorded as `None`. A condition function that returns a check, which is always truthy, makes the guard fail with an error.
- `verify.record(check)` on the fixture records a check built elsewhere, for example by a helper that uses the module-level `verify`. A check the fixture already recorded is returned as is, and a composite absorbs the fixture checks passed to it. On the module-level `verify` it raises `RuntimeError`.
- Every recorded check has a `phase` key: `"setup"`, `"call"` or `"teardown"`, the test phase that made it.
- A `pytest_verify_results(item, when, checks, passed)` hook, called when a test phase ends with checks to judge. Plugins implement it with `@pytest.hookimpl(optionalhook=True)` and need not import pytest-verify.
- Test reports carry the checks judged in their phase as `report.verify_checks`. They are JSON-safe, so they survive pytest-xdist.
- A contract test suite (`tests/test_contracts.py`) driven by the check-type registry. Every check type needs passing, failing and hostile examples, and each one must record JSON-safe results, get the same verdict from the fixture and from `evaluate()`, render without raising and stay unchanged when the checked values change later.

### Changed

- A `guard` condition that is callable is now called, and its result picks the branch. In 0.4.0 a function, class or mock used as a condition counted as true without being called.
- `CheckDescriptor` marks `check_type`, `name` and `description` as required keys, so type checkers know every check has them.
- For type checkers, `CheckDescriptor["cases"]` values, `GuardBranch["check"]` and `GuardBranch["condition"]` may be `None` (a lazy child or condition that was not called).
- The error for a `conditional` case, `guard` check or default that is not a check now says it may also be a function that returns one.
- Internals: each check type is one class in `pytest_verify/_checks/` that builds, judges and renders it; the module-level and fixture `verify` are the same `Verify` class with a different sink; per-attempt state lives in `pytest_verify/_run.py`. Private helpers such as `pytest_verify._descriptors.build_equal` are gone; build checks with the module-level `verify`.

## [0.4.0] - 2026-10-01

Fixes all 45 bugs listed in [`bugs-0.3.1.md`](bugs-0.3.1.md). Some fixes change behaviour that
existing tests may rely on. Those are listed under **Changed**.

### Added

- `ChecksFailedError` is exported from `pytest_verify` and is now actually raised. Checks made in setup and in the test body are raised after the test body. Checks made while fixtures are torn down are raised after teardown, numbered after the checks of the test body. It subclasses `AssertionError`, so `pytest.raises(AssertionError)` and `xfail(raises=AssertionError)` catch it, and `--pdb` and `pytest_exception_interact` see it once the test body has finished. Rerun filters match it by name: `--only-rerun ChecksFailedError`.
- `GuardBranch` TypedDict, exported for typing the branches of a recorded `guard`.
- Recorded results carry `detail` (the rendered `expected … got …` clause). When a check could not be evaluated, they also carry `error`. Every evaluated child of a composite carries its own `passed`.
- `evaluate_detailed()` results include `error` when a check could not be evaluated.
- Package metadata: classifiers, project URLs and author. `py.typed` is declared explicitly, and a `MANIFEST.in` puts the whole test suite in the sdist.
- CI jobs for `mypy --strict`, for the oldest supported pytest (7.0) and pluggy (1.2), and for building the sdist and running its tests against the wheel.
- A release workflow. Pushing a `vX.Y.Z` tag, or running the workflow by hand, publishes a GitHub release with the CHANGELOG notes and the built sdist and wheel.
- `bugs-0.3.1.md` (the severity-ranked bug report behind this release) and `improvements-and-ideas.md` (improvements, refactors and feature ideas).
- `CHECKLIST.md`, which tracks every item of both documents and the release that handles it.

### Changed

- A check whose comparison raises, or returns a value with an ambiguous truth value (a numpy array, a pandas series), no longer stops the test or crashes the session. It is recorded as a failed check, and the error is shown in its detail.
- Usage errors raise `TypeError` or `ValueError` as soon as the check is built. They cover:
  - a `name` that is not a `str`;
  - a negative, NaN or non-numeric tolerance;
  - `between` with `low` above `high`;
  - a malformed `guard` branch, including a check descriptor used as a condition (it is always truthy);
  - `conditional` cases or defaults that are not checks, and case keys that collide (`{1: …, "1": …}`);
  - an `is_instance` type that `isinstance` cannot check (`list[int]`).
- Problems with the data inside composites never raise. An `all_satisfy` whose items cannot be iterated, or whose factory raises or returns something other than a check, is a failed check with an error note. A forgotten `return` is one example.
- `conditional` matches case keys by equality, comparing enum members by their value. An `int` and its decimal string are the same key, so `1` selects `"1"`. Results no longer depend on the Python version, and `None` no longer matches `"None"`. Case keys are still stored as strings.
- `approx` with `rel_tol` uses the band it prints, `expected ± rel_tol × |expected|`. `math.isclose` also accepted `rel_tol × |actual|`. `Decimal`, `Fraction` and `int` values are compared exactly unless a `float` is involved. Values that are neither real numbers nor convertible with `float()`, such as `None`, strings or lists, fail the check with a `TypeError` note, as with `math.isclose`.
- `is_instance` behaves like `isinstance` in both APIs. Tuples and unions of types work, ABCs and runtime-checkable protocols work, and the module-level API no longer matches unrelated classes that share a name.
- Recorded results, including those returned by `get_check_results()`, hold JSON-safe snapshots of the checked values, taken when the check is made. A later mutation cannot change a report, and checked objects are no longer kept alive. NaN and infinities are stored as `"nan"`, `"inf"` and `"-inf"`. Values JSON cannot represent are stored as their `repr`.
- `evaluate()` and `evaluate_detailed()` raise a helpful `TypeError` for a list argument (use `verify.evaluate(*checks)`) or for an argument that is not a check. An unknown check type fails with an error instead of raising.
- The `verify` fixture refuses checks with `RuntimeError` once its test has finished, and in a forked child process, instead of losing them.
- Using the `verify` fixture without the plugin's hooks is an error, instead of every test passing. This happens, for example, when the fixture is imported into a `conftest.py` while the plugin is disabled.
- The `-r` short summary and `--tb=line` show `N of M checks failed` at the test's location.
- The `detail` of a check shows at most 100 items of a container and 1000 characters of any other value, so one huge value no longer slows down every check or bloats every report.
- Requires `pluggy>=1.2`, which pytest already depends on. Building from source requires `setuptools>=77`.

### Fixed

- Building the failure summary can no longer crash the session, and a huge `int` no longer stops a passing check.
- `guard` and `conditional` count only the selected branch. An unselected branch that fails or raises no longer stops or fails the test, and it carries no verdict in the record. With the fixture, each branch is still evaluated when it is built, because it is an argument of the call.
- Composites use the verdicts their children were recorded with.
- A check passed to a composite belongs to it, whether it was built inline or earlier in a variable, so building `cases` or `branches` before the call works. To keep a check on its own as well, pass a copy, `dict(check)` (see the README).
- A composite that raises leaves no stray child checks behind.
- Checks made during teardown fail the run.
- Soft failures are reported correctly under `xfail` and are no longer hidden by a later skip, including unittest's `SkipTest` and `TestCase.skipTest()`.
- In a unittest `TestCase`, a failed assertion no longer hides the soft failures: they are added to its report.
- With pytest-rerunfailures, each attempt starts with no checks, so a clean rerun passes.
- With pytest 9 subtests, a failed check no longer fails every later subtest. The test fails once, after its body, with all of its failed checks.
- Soft failures next to a hard failure appear in junitxml and are shown whatever `--show-capture` is set to.
- A setup error keeps the soft failures recorded during setup.
- Recording checks from several threads no longer loses checks.
- Type hints accept the documented usage: `int` and enum `conditional` keys, truthy `guard` conditions, tuples for `is_instance`, and any iterable for `all_satisfy`. The package passes `mypy --strict`, and `typing.get_type_hints` works on Python 3.9.
- `ChecksFailedError` can be pickled and copied.
- Recording composite checks takes linear time in the number of children instead of quadratic time.
- Documentation: the README `guard` example works and `guard` is in the catalogs. `get_check_results()` is documented as the way for other plugins to read results. The CI triggers are described correctly. The CHANGELOG matches the tags.

### Removed

- The unused `pytest_verify._types` module.

## [0.3.1] - 2026-07-06

### Added

- GitHub Actions CI workflow running the test suite on pushes to `main` and on pull requests targeting `main`, across Python 3.9–3.13.
- README status badges (CI, Python versions, pytest, license) and a Development section.

### Fixed

- Child checks nested inside `verify.guard`, `verify.conditional`, and `verify.all_satisfy` are no longer recorded as independent results when built via the `verify` fixture. Previously every branch/case check was evaluated and reported on its own, so an **unmatched** branch whose check failed would fail the whole test — defeating the purpose of only evaluating the matched branch. Now only the composite check is reported; its verdict still reflects the chosen child.
- Corrected the README install instructions: the package is not published on PyPI, so `pip install pytest-verify` never resolved. Documented the Git install (`pip install "git+https://github.com/guillegil/pytest_verify.git"`).

## [0.3.0] - 2026-06-23

### Added

- `verify.guard(branches, *, default=None, name)` — an ordered if/elif/else check. Each branch is a `(condition, label, check)` tuple; the first branch whose condition is truthy is evaluated, falling back to `default` (or failing if none match and no default is given). Complements `verify.conditional` (which switches on a single value) for cases where the expected check depends on a chain of arbitrary boolean conditions. The chosen branch's `label` appears in the `ChecksFailedError` summary.

## [0.2.1] - 2026-06-23

### Fixed

- `verify.conditional` now matches cases whose keys are passed as the same type as `switch_value` (e.g. `cases={0: ..., 1: ...}`). Case keys are normalized to strings when the descriptor is built, so int and enum keys no longer silently fall through to the `default` branch. Descriptors remain JSON-serializable.

## [0.2.0] - 2026-06-20

### Added

- Public `get_check_results(item)` helper, importable from `pytest_verify`. Returns a copy of the check-result descriptors recorded for a pytest item (`[]` when none), reading from the shared stash. This is the read contract consumed by `pytest-reporter` to render verification cards.

### Changed

- Stash writes are now unconditional: every check records its descriptor to `item.stash` regardless of pass/fail and regardless of whether `pytest-reporter` is installed, so observers see passed checks (green cards), not only failures.
- `ChecksFailedError` message now follows the spec §7 format: an `N of M checks failed` header, then failed checks (`✗`) before passed checks (`✓`), each with its `[seq]` index, name, and a per-type detail clause — `expected … got …` for failures and a compact restatement for passes (see spec §7.1) — replacing the previous `FAILED checks:` / `PASSED checks:` lists.
- Check `description` strings now match the authoritative spec (§5): `approx` with both tolerances renders `== 3.3V ± 0.05V (abs) ± 1% (rel)` (labels only when both are present) and percentages drop a trailing `.0` (`1%`, not `1.0%`); `length` renders `Verify 'name' has length N` instead of `Verify len('name') == N`; `all_satisfy` appends the item count, e.g. `… satisfy condition (4 items)`.

### Fixed

- Standalone `verify.evaluate()` of an `is_instance` check now honours concrete subclasses (e.g. `is_instance(True, int)` passes), matching the expected type name against the actual object's MRO instead of only its exact type name. The `verify` fixture already evaluated this correctly via real `isinstance`.
- Soft-assert failures are no longer silently dropped when the test body also fails with a hard error (exception or `assert`). The original traceback is preserved and the failed-checks summary is appended as a report section. Previously the verdict hook only ran when the call phase passed, hiding soft failures behind any hard failure.

## [0.1.0] - 2026-04-04

### Added

- `verify` pytest fixture providing soft assertions that collect failures and report them at test end.
- 20 check functions: `equal`, `not_equal`, `approx`, `greater`, `greater_equal`, `less`, `less_equal`, `between`, `is_true`, `is_false`, `is_none`, `is_not_none`, `contains`, `not_contains`, `matches`, `is_instance`, `length`, `all_satisfy`, `conditional`, `fail`.
- Module-level `verify` instance (`from pytest_verify import verify`) for building unevaluated descriptors.
- `verify.evaluate()` and `verify.evaluate_detailed()` for standalone descriptor evaluation.
- `CheckDescriptor` TypedDict for fully typed, JSON-serializable check results.
- `ChecksFailedError` with formatted message listing failed and passed checks.
- Optional `pytest-reporter` integration via `item.stash` (auto-detected at session start).
- Full type annotations and `py.typed` marker for IDE autocompletion (PEP 561).

[Unreleased]: https://github.com/guillegil/pytest-verifier/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/guillegil/pytest-verifier/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/guillegil/pytest-verifier/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/guillegil/pytest-verifier/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/guillegil/pytest-verifier/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/guillegil/pytest-verifier/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/guillegil/pytest-verifier/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/guillegil/pytest-verifier/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/guillegil/pytest-verifier/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/guillegil/pytest-verifier/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/guillegil/pytest-verifier/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/guillegil/pytest-verifier/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/guillegil/pytest-verifier/releases/tag/v0.1.0
