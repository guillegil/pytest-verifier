"""FEAT-3: required checks, fail-fast, and a first line that says what failed."""
from __future__ import annotations

import gc
import json
import sys
from typing import Any, Dict, List

import pytest

from pytest_verifier import ChecksFailedError, checks
from pytest_verifier._exceptions import format_summary
from pytest_verifier._run import Run, recording_verify


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest("-p", "no:cacheprovider", *args)


#: Lines that would show that a stop error was swallowed, repeated or never raised.
_NEVER = ("*never reached*", "*Soft assertion failures*", "*raised ChecksFailedError*")


def _never(result: pytest.RunResult) -> None:
    for pattern in _NEVER:
        result.stdout.no_fnmatch_line(pattern)


class TestRequire:
    def test_a_failed_required_check_stops_the_test(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_link(verify):
                verify.equal(1, 2, name="soft")
                verify.require.is_not_none(None, name="link")
                raise AssertionError("never reached")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            [
                "2 of 2 checks failed, stopped at ?1?: link — expected not None, got None"
                " (+1 more)",
                "",
                "  ✗ ?0? soft (*.py:2) — expected 2, got 1",
                "  ✗ ?1? link (*.py:3) — expected not None, got None",
            ]
        )
        _never(result)

    def test_a_passed_required_check_goes_on(self) -> None:
        verify = recording_verify(Run())
        record = verify.require.greater(5, 1, name="ok")
        assert record["passed"] is True
        assert verify.require(verify.equal(1, 1, name="done"))["passed"] is True

    def test_require_with_a_check(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest
            from pytest_verifier import ChecksFailedError, checks

            def test_built(verify):
                with pytest.raises(ChecksFailedError) as caught:
                    verify.require(checks.equal(1, 2, name="built"))
                    raise AssertionError("never reached")
                assert [r["name"] for r in caught.value.results] == ["built"]

            def test_recorded(verify):
                check = verify.equal(1, 2, name="recorded")
                with pytest.raises(ChecksFailedError):
                    verify.require(check)
                    raise AssertionError("never reached")
            """
        )
        # The checks stay recorded: catching the error does not make the tests pass.
        result = _run(pytester, "-rf")
        result.assert_outcomes(failed=2)
        result.stdout.fnmatch_lines(
            [
                "FAILED *::test_built - 1 of 1 checks failed: built — expected 2, got 1",
                "FAILED *::test_recorded - 1 of 1 checks failed: recorded — expected 2, got 1",
            ]
        )
        _never(result)

    def test_a_test_that_catches_the_error_still_fails(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            from pytest_verifier import ChecksFailedError

            def test_swallowed(verify):
                stopped = False
                try:
                    verify.require.equal(1, 2, name="first")
                except ChecksFailedError:
                    stopped = True
                assert stopped, "require did not stop"
                verify.equal(3, 4, name="later")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        # The test went on, so nothing stopped it in the end.
        result.stdout.fnmatch_lines(
            [
                "2 of 2 checks failed: first — expected 2, got 1 (+1 more)",
                "",
                "  ✗ ?0? first *",
                "  ✗ ?1? later *",
            ]
        )
        result.stdout.no_fnmatch_line("*require did not stop*")

    def test_checks_made_before_reraising_join_the_error(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_reraised(verify):
                try:
                    verify.require.equal(1, 2, name="first")
                except Exception:
                    verify.equal(3, 4, name="cleanup")
                    raise
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["2 of 2 checks failed, stopped at ?0?: first *(+1 more)"])
        # The first stop error is replaced, not printed as the cause of the second.
        result.stdout.no_fnmatch_line("1 of 1 checks failed*")
        _never(result)

    def test_a_stop_in_finally_after_a_stop(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_finally(verify):
                try:
                    verify.equal(1, 2, name="first")
                finally:
                    verify.equal(3, 4, name="cleanup")
                    raise AssertionError("never reached")
            """
        )
        result = _run(pytester, "--verify-fail-fast")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            ["2 of 2 checks failed, stopped at ?1?: cleanup — expected 4, got 3 (+1 more)"]
        )
        result.stdout.no_fnmatch_line("1 of 1 checks failed*")
        result.stdout.no_fnmatch_line("*During handling*")
        _never(result)

    def test_the_error_a_stop_was_raised_from_stays_visible(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            """
            def test_context(verify):
                try:
                    try:
                        raise OSError("port busy")
                    except OSError:
                        verify.require.equal(1, 2, name="link")
                finally:
                    verify.equal(3, 4, name="cleanup")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            ["*port busy*", "*direct cause*", "2 of 2 checks failed, stopped at ?0?: link *"]
        )
        result.stdout.no_fnmatch_line("1 of 1 checks failed*")

    def test_a_stop_inside_another_error(self, pytester: pytest.Pytester) -> None:
        # A skip gives way to the failure, which takes the stop's place in the skip's chain.
        # Another error keeps its chain and gets the section, which names the stop too.
        pytester.makepyfile(
            """
            import pytest
            from pytest_verifier import ChecksFailedError

            def test_skip(verify):
                try:
                    verify.require.equal(1, 2, name="Req")
                finally:
                    pytest.skip("hardware missing")

            def test_cleanup_error(verify):
                try:
                    verify.require.equal(1, 2, name="Req")
                finally:
                    raise OSError("port busy")

            def test_wrapped(verify):
                try:
                    verify.require.equal(1, 2, name="Req")
                except ChecksFailedError as exc:
                    raise RuntimeError("could not connect") from exc
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=3)
        headline = "1 of 1 checks failed, stopped at ?0?: Req — expected 2, got 1"
        result.stdout.fnmatch_lines(
            [
                "*_ test_skip _*",
                "*hardware missing*",
                headline,
                "*_ test_cleanup_error _*",
                "*OSError: port busy",
                "*Soft assertion failures*",
                headline,
                "*_ test_wrapped _*",
                "*RuntimeError: could not connect",
                "*Soft assertion failures*",
                headline,
            ]
        )
        # The skip's report shows the summary once.
        skip_report = "\n".join(result.outlines).split("_ test_cleanup_error _")[0]
        assert skip_report.count("checks failed") == 1, skip_report

    def test_a_stop_in_an_exception_group(self, pytester: pytest.Pytester) -> None:
        # pytest < 7.2 does not show a group's members, and newer ones show 15 at most: the
        # section lists every check.
        if sys.version_info < (3, 11):
            pytest.importorskip("exceptiongroup")
        pytester.makepyfile(
            """
            import sys

            from pytest_verifier import ChecksFailedError

            if sys.version_info < (3, 11):
                from exceptiongroup import ExceptionGroup

            def test_group(verify):
                errors = []
                for channel in range(20):
                    try:
                        verify.require.equal(channel, -1, name=f"ch{channel}")
                    except ChecksFailedError as exc:
                        errors.append(exc)
                raise ExceptionGroup("channels failed", errors)
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            [
                "*Soft assertion failures*",
                "20 of 20 checks failed, stopped at ?0?: ch0 — expected -1, got 0 (+19 more)",
                "*✗ ?19? ch19 *",
            ]
        )

    def test_results_and_report_carry_the_checks_once(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            calls = []

            def pytest_verify_results(item, when, checks, passed):
                calls.append((when, [c["name"] for c in checks], passed))

            def pytest_sessionfinish(session):
                assert calls == [("call", ["a", "b"], False)], calls
            """
        )
        pytester.makepyfile(
            """
            def test_it(verify):
                verify.equal(1, 1, name="a")
                verify.require.equal(1, 2, name="b")
            """
        )
        reprec = pytester.inline_run("-p", "no:cacheprovider")
        reprec.assertoutcome(failed=1)
        [call] = [r for r in reprec.getreports("pytest_runtest_logreport") if r.when == "call"]
        assert [c["name"] for c in call.verify_checks] == ["a", "b"]

    def test_in_fixture_setup_and_teardown(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import pytest

            @pytest.fixture
            def broken_setup(verify):
                verify.require.equal(1, 2, name="supply on")
                yield

            @pytest.fixture
            def broken_teardown(verify):
                yield
                verify.require.equal(1, 2, name="supply off")
                raise AssertionError("never reached")
            """
        )
        pytester.makepyfile(
            """
            def test_setup(broken_setup):
                raise AssertionError("never reached")

            def test_teardown(broken_teardown):
                pass
            """
        )
        result = _run(pytester)
        result.assert_outcomes(passed=1, errors=2)
        result.stdout.fnmatch_lines(
            [
                "*ERROR at setup of test_setup*",
                "1 of 1 checks failed, stopped at ?0?: supply on — expected 2, got 1",
                "*ERROR at teardown of test_teardown*",
                "1 of 1 checks failed, stopped at ?0?: supply off — expected 2, got 1",
            ]
        )
        _never(result)

    def test_a_required_composite(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_items(verify):
                verify.require.all_satisfy(
                    [1, 2, 3], lambda x: verify.less(x, 2, name=f"x{x}"), name="items"
                )
                raise AssertionError("never reached")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["1 of 1 checks failed, stopped at ?0?: items *"])
        _never(result)

    _INSIDE_COMPOSITES = """
        def test_factory(verify):
            verify.all_satisfy(
                [1, 5, 2], lambda x: verify.require.less(x, 3, name=f"x{x}"), name="items"
            )
            raise AssertionError("never reached")

        def test_lazy_case(verify):
            verify.conditional(
                1, cases={1: lambda: verify.require.equal(3.0, 3.3, name="Active")}, name="mode"
            )
            raise AssertionError("never reached")

        def test_lazy_default(verify):
            verify.conditional(
                9,
                cases={1: lambda: verify.equal(1, 1, name="one")},
                default=lambda: verify.require.fail("Unknown mode"),
                name="mode",
            )
            raise AssertionError("never reached")

        def test_lazy_branch(verify):
            verify.guard(
                [(True, "on", lambda: verify.require.equal(1, 2, name="lit"))], name="sensor"
            )
            raise AssertionError("never reached")

        def test_lazy_condition(verify):
            verify.guard(
                [
                    (
                        lambda: verify.require.is_true(False, name="powered")["passed"],
                        "on",
                        lambda: verify.equal(1, 1, name="x"),
                    )
                ],
                name="sensor",
            )
            raise AssertionError("never reached")
        """

    def test_inside_lazy_children_factories_and_conditions(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(self._INSIDE_COMPOSITES)
        result = _run(pytester, "-rf")
        result.assert_outcomes(failed=5)
        result.stdout.fnmatch_lines(
            [
                "FAILED *::test_factory - 1 of 2 checks failed, stopped at ?1?: x5 *",
                "FAILED *::test_lazy_case - 1 of 1 checks failed, stopped at ?0?: Active"
                " — expected 3.3, got 3.0",
                "FAILED *::test_lazy_default - 1 of 1 checks failed, stopped at ?0?: Unknown mode*",
                "FAILED *::test_lazy_branch - 1 of 1 checks failed, stopped at ?0?: lit *",
                "FAILED *::test_lazy_condition - 1 of 1 checks failed, stopped at ?0?: powered *",
            ]
        )
        _never(result)

    def test_xfail_raises_assertion_error(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(raises=AssertionError, strict=True)
            def test_known(verify):
                verify.require.is_true(False, name="known issue")
            """
        )
        _run(pytester).assert_outcomes(xfailed=1)

    def test_a_thread_raises_in_the_thread_and_the_test_fails(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            """
            import threading

            from pytest_verifier import ChecksFailedError

            def test_thread(verify):
                errors = []

                def work():
                    try:
                        verify.require.equal(1, 2, name="in thread")
                    except Exception as exc:
                        errors.append(exc)

                worker = threading.Thread(target=work)
                worker.start()
                worker.join()
                assert [type(e) for e in errors] == [ChecksFailedError], errors
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["1 of 1 checks failed: in thread *"])
        _never(result)

    def test_a_check_a_composite_absorbed(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest
            from pytest_verifier import ChecksFailedError

            def test_unselected(verify):
                standby = verify.equal(0.0, 3.3, name="standby")
                verify.conditional(
                    1, cases={1: verify.equal(1, 1, name="active"), 2: standby}, name="mode"
                )
                with pytest.raises(ChecksFailedError) as caught:
                    verify.require(standby)
                assert [r["name"] for r in caught.value.results] == ["mode", "standby"]
                assert str(caught.value).startswith(
                    "1 of 2 checks failed, stopped at [1]: standby — "
                )
            """
        )
        # A copy is recorded, so catching the error does not make the test pass.
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            ["1 of 2 checks failed: standby — expected 3.3, got 0.0", "", "  ✗ ?1? standby *"]
        )

    def test_a_check_an_earlier_phase_judged(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import pytest

            @pytest.fixture
            def link(verify):
                state = {}
                yield state
                verify.require(state["link"])
            """
        )
        pytester.makepyfile(
            """
            def test_it(link, verify):
                link["link"] = verify.is_not_none(None, name="link up")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1, errors=1)
        result.stdout.fnmatch_lines(
            [
                "*ERROR at teardown of test_it*",
                "1 of 1 checks failed, stopped at ?1?: link up — expected not None, got None",
            ]
        )
        result.stdout.no_fnmatch_line("*0 of * checks failed*")

    def test_a_later_composite_absorbing_checks_of_the_stop(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            """
            def test_it(verify):
                a = verify.equal(1, 2, name="a")
                try:
                    verify.require.equal(1, 2, name="b")
                finally:
                    verify.all_satisfy([a], lambda c: c, name="group")
            """
        )
        # As many checks as the stop error listed, but not the same ones: "a" now belongs to
        # "group", and the stopping check moved to index 0.
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            [
                "2 of 2 checks failed, stopped at ?0?: b — expected 2, got 1 (+1 more)",
                "",
                "  ✗ ?0? b *",
                "  ✗ ?1? group *",
            ]
        )
        result.stdout.no_fnmatch_line("  ✗ ?1? a *")

    def test_the_check_that_stopped_the_test_is_never_absorbed(
        self, pytester: pytest.Pytester
    ) -> None:
        # The stopping check is also a child of the composite whose lazy child stopped, or of a
        # composite made later: it stays at the top level, so the results, the hook and a
        # swallowed stop still see it.
        pytester.makeconftest(
            """
            import json

            def pytest_verify_results(item, when, checks, passed):
                with open("results.jsonl", "a", encoding="utf-8") as out:
                    names = [check["name"] for check in checks]
                    out.write(json.dumps([item.name, names, passed]) + "\\n")
            """
        )
        pytester.makepyfile(
            """
            import pytest
            from pytest_verifier import ChecksFailedError

            def test_raised(verify):
                standby = verify.equal(0.0, 3.3, name="standby")
                verify.conditional(
                    1, cases={1: lambda: verify.require(standby), 2: standby}, name="mode"
                )

            def test_swallowed(verify):
                standby = verify.equal(0.0, 3.3, name="standby")
                with pytest.raises(ChecksFailedError):
                    verify.guard([(lambda: verify.require(standby), "on", standby)], name="on")

            def test_later(verify):
                standby = verify.equal(0.0, 3.3, name="standby")
                try:
                    verify.require(standby)
                finally:
                    verify.conditional(
                        1, cases={1: verify.equal(1, 1, name="active"), 2: standby}, name="mode"
                    )

            def test_selected_later(verify):
                standby = verify.equal(0.0, 3.3, name="standby")
                try:
                    verify.require(standby)
                finally:
                    verify.conditional(2, cases={2: standby}, name="mode")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=4)
        result.stdout.fnmatch_lines(
            [
                "*_ test_raised _*",
                "1 of 1 checks failed, stopped at ?0?: standby — expected 3.3, got 0.0",
                "*_ test_swallowed _*",
                "1 of 1 checks failed: standby — expected 3.3, got 0.0",
                "*_ test_later _*",
                "1 of 2 checks failed, stopped at ?0?: standby — expected 3.3, got 0.0",
                "*_ test_selected_later _*",
                # Counted on its own and in the composite that selected it.
                "2 of 2 checks failed, stopped at ?0?: standby — expected 3.3, got 0.0 (+1 more)",
            ]
        )
        reported = (pytester.path / "results.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line) for line in reported] == [
            ["test_raised", ["standby"], False],
            ["test_swallowed", ["standby"], False],
            ["test_later", ["standby", "mode"], False],
            ["test_selected_later", ["standby", "mode"], False],
        ]

    def test_a_stop_lists_at_most_ten_passed_checks(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_many(verify):
                for i in range(12):
                    verify.equal(i, i, name=f"ok{i}")
                verify.require.equal(1, 2, name="bad")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["  ✓ … 2 more passed checks (-vv shows them)"])
        result.stdout.no_fnmatch_line("*ok11*")

    def test_a_stop_in_teardown_continues_the_numbering(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import json

            import pytest
            from pytest_verifier import ChecksFailedError

            @pytest.fixture
            def supply(verify):
                yield
                try:
                    verify.require.equal(1, 2, name="off")
                except ChecksFailedError as error:
                    seen = [error.start, error.stopped_at, str(error).splitlines()[0]]
                    with open("seen.json", "w", encoding="utf-8") as out:
                        json.dump(seen, out)
                    raise
            """
        )
        pytester.makepyfile(
            """
            def test_it(supply, verify):
                verify.equal(1, 1, name="a")
                verify.equal(2, 2, name="b")
            """
        )
        _run(pytester).assert_outcomes(passed=1, errors=1)
        seen = json.loads((pytester.path / "seen.json").read_text(encoding="utf-8"))
        assert seen == [2, 2, "1 of 1 checks failed, stopped at [2]: off — expected 2, got 1"]

    def test_a_users_checks_failed_error_is_not_a_stop(self, pytester: pytest.Pytester) -> None:
        # Only the errors verify.require raised are stops; any other ChecksFailedError keeps
        # the soft failures in their own section.
        pytester.makepyfile(
            """
            from pytest_verifier import ChecksFailedError, checks

            def test_it(verify):
                verify.equal(1, 2, name="soft")
                raise ChecksFailedError([dict(checks.equal(3, 4, name="batch"), passed=False)])
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            [
                "1 of 1 checks failed: batch *",
                "*Soft assertion failures*",
                "1 of 1 checks failed: soft *",
                "",
                "  ✗ ?0? soft (*.py:4) *",
            ]
        )

    def test_raised_matches_by_identity(self) -> None:
        run = Run()
        verify = recording_verify(run)
        with pytest.raises(ChecksFailedError) as caught:
            verify.require.equal(1, 2, name="x")
        assert run.raised(caught.value)
        assert not run.raised(ChecksFailedError(caught.value.results))
        assert not run.raised(None)

    _TESTCASE = """
        import unittest

        import pytest

        class TestStop(unittest.TestCase):
            @pytest.fixture(autouse=True)
            def _verify(self, verify):
                self.verify = verify

            def test_required(self):
                self.verify.equal(1, 1, name="Fine")
                self.verify.require.equal(1, 2, name="Req")
                raise RuntimeError("never reached")
        """

    @pytest.mark.parametrize("fail_fast", [False, True], ids=["require", "fail-fast"])
    def test_a_testcase_stop_is_reported_once(
        self, pytester: pytest.Pytester, fail_fast: bool
    ) -> None:
        source = self._TESTCASE
        args = []
        if fail_fast:
            source = source.replace("self.verify.require.equal", "self.verify.equal")
            args.append("--verify-fail-fast")
        pytester.makepyfile(source)
        result = _run(pytester, *args)
        result.assert_outcomes(failed=1)
        summaries = [line for line in result.outlines if line.startswith("1 of 2 checks failed")]
        assert len(summaries) == 1, result.outlines
        result.stdout.fnmatch_lines(["1 of 2 checks failed, stopped at ?1?: Req *"])
        _never(result)

    def test_two_testcase_stops_are_reported_once(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            self._TESTCASE
            + """
            def tearDown(self):
                self.verify.require.equal(3, 4, name="Off")
        """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["2 of 3 checks failed, stopped at ?1?: Req * (+1 more)"])
        assert sum(line.startswith("2 of 3 checks failed") for line in result.outlines) == 1
        _never(result)

    def test_a_testcase_stop_after_a_skip_or_an_error(self, pytester: pytest.Pytester) -> None:
        # tearDown's stop is reported once, in the call phase, and still named.
        pytester.makepyfile(
            """
            import unittest

            import pytest

            class Base(unittest.TestCase):
                @pytest.fixture(autouse=True)
                def _verify(self, verify):
                    self.verify = verify

                def tearDown(self):
                    self.verify.require.equal(3, 4, name="Off")

            class TestSkip(Base):
                def test_it(self):
                    self.verify.equal(1, 1, name="Fine")
                    self.skipTest("no hardware")

            class TestError(Base):
                def test_it(self):
                    self.verify.equal(1, 1, name="Fine")
                    raise OSError("port busy")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=2)
        result.stdout.fnmatch_lines(
            [
                "*_ TestSkip.test_it _*",
                "1 of 2 checks failed, stopped at ?1?: Off — expected 4, got 3",
                "*_ TestError.test_it _*",
                "*OSError: port busy",
                "*Soft assertion failures*",
                "1 of 2 checks failed, stopped at ?1?: Off — expected 4, got 3",
            ]
        )
        result.stdout.no_fnmatch_line("*at teardown*")

    def test_pdb_opens_in_the_test(self, pytester: pytest.Pytester) -> None:
        # pytest's debugger starts at the last frame whose locals have no true
        # ``__tracebackhide__``; record that frame for every failure.
        pytester.makeconftest(
            """
            def pytest_exception_interact(node, call, report):
                frames = []
                tb = call.excinfo._excinfo[2]
                while tb is not None:
                    frames.append(tb.tb_frame)
                    tb = tb.tb_next
                i = len(frames) - 1
                while i and frames[i].f_locals.get("__tracebackhide__", False):
                    i -= 1
                with open("frames.txt", "a", encoding="utf-8") as out:
                    out.write(frames[i].f_code.co_name + "\\n")
            """
        )
        pytester.makepyfile(
            """
            def test_required(verify):
                verify.require.equal(1, 2, name="a")

            def test_record(verify):
                verify.require(verify.equal(1, 2, name="b"))

            def test_fail_fast(verify, request):
                if request.config.getoption("verify_fail_fast"):
                    verify.all_satisfy([1], lambda x: x, name="c")

            # The phase end replaces these stops: a check came after, or a sibling was absorbed.
            def test_cleanup(verify):
                try:
                    verify.require.equal(1, 2, name="d")
                finally:
                    verify.equal(1, 1, name="cleaned up")

            def test_lazy(verify):
                sibling = verify.equal(1, 1, name="f")
                required = lambda: verify.require.equal(1, 2, name="e")
                verify.conditional(1, cases={1: required, 2: sibling}, name="g")
            """
        )
        pytester.makepyfile(
            test_unit="""
            import unittest

            import pytest

            class TestUnit(unittest.TestCase):
                @pytest.fixture(autouse=True)
                def _verify(self, verify):
                    self.verify = verify

                def test_unit(self):
                    try:
                        self.verify.require.equal(1, 2, name="h")
                    finally:
                        self.verify.equal(1, 1, name="cleaned up")

                # The TestCase reports the skip first (Python < 3.11 reports every skip before
                # the errors); the phase end replaces it with the stop.
                def test_skipped_then_stopped(self):
                    self.addCleanup(self.stop)
                    self.skipTest("not today")

                def stop(self):
                    self.verify.require.equal(1, 2, name="i")

                def test_stopped_then_skipped(self):
                    self.addCleanup(self.skipTest, "not today")
                    self.verify.require.equal(1, 2, name="j")
            """
        )
        # Python 3.11+ reports the cleanup's skip after the stop: pytest reports it in teardown.
        _run(pytester).assert_outcomes(failed=7, passed=1, skipped=sys.version_info >= (3, 11))
        _run(pytester, "--verify-fail-fast", "-k", "fail_fast").assert_outcomes(failed=1)
        frames = (pytester.path / "frames.txt").read_text(encoding="utf-8").split()
        expected = ["test_required", "test_record", "test_cleanup", "<lambda>"]
        expected += ["stop", "test_stopped_then_skipped", "test_unit"]  # in name order
        assert frames == expected + ["test_fail_fast"]

    def test_require_is_cached_and_idempotent(self) -> None:
        verify = recording_verify(Run())
        assert verify.require is verify.require
        assert verify.require.require is verify.require

    def test_checks_require_needs_the_fixture(self) -> None:
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require.equal(1, 1, name="x")
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require(checks.equal(1, 1, name="x"))
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require.all_satisfy([1], lambda x: checks.equal(x, 1, name="x"), name="all")

    def test_checks_require_marks_its_check_used(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest
            from pytest_verifier import checks

            def test_misuse():
                check = checks.equal(1, 1, name="x")
                with pytest.raises(RuntimeError):
                    checks.require(check)
            """
        )
        result = _run(pytester, "-W", "error::pytest_verifier.UnusedCheckWarning")
        result.assert_outcomes(passed=1)

    def test_a_non_descriptor_is_a_type_error(self) -> None:
        verify = recording_verify(Run())
        with pytest.raises(TypeError, match="^require\\(\\) argument must be a check descriptor"):
            verify.require(42)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="^record\\(\\) argument must be a check descriptor"):
            verify.require.record(42)  # type: ignore[arg-type]


class TestFailFast:
    _TEST = """
        def test_first(verify):
            verify.equal(1, 1, name="fine")
            verify.equal(1, 2, name="first")
            verify.equal(3, 4, name="second")
        """

    def test_off_by_default(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(self._TEST)
        _run(pytester).stdout.fnmatch_lines(["2 of 3 checks failed: first *(+1 more)"])

    def test_the_option_stops_at_the_first_failed_check(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(self._TEST)
        result = _run(pytester, "--verify-fail-fast")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            ["1 of 2 checks failed, stopped at ?1?: first — expected 2, got 1"]
        )
        result.stdout.no_fnmatch_line("*second*")

    def test_the_ini_setting(self, pytester: pytest.Pytester) -> None:
        pytester.makeini("[pytest]\nverify_fail_fast = true\n")
        pytester.makepyfile(self._TEST)
        _run(pytester).stdout.fnmatch_lines(["1 of 2 checks failed, stopped at ?1?: first *"])

    def test_the_option_is_listed_in_help(self, pytester: pytest.Pytester) -> None:
        result = _run(pytester, "--help")
        result.stdout.fnmatch_lines(["*--verify-fail-fast*", "*verify_fail_fast*"])

    def test_eager_and_lazy_children(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_eager(verify):
                verify.conditional(
                    1, cases={1: verify.equal(1, 1, name="one"), 2: verify.equal(1, 2, name="two")},
                    name="mode",
                )

            def test_lazy(verify):
                verify.conditional(
                    1,
                    cases={
                        1: lambda: verify.equal(1, 1, name="one"),
                        2: lambda: verify.equal(1, 2, name="two"),
                    },
                    name="mode",
                )

            def test_eager_default(verify):
                verify.conditional(
                    1,
                    cases={1: lambda: verify.equal(1, 1, name="one")},
                    default=verify.fail("Unknown mode"),
                    name="mode",
                )

            def test_lazy_default(verify):
                verify.conditional(
                    1,
                    cases={1: lambda: verify.equal(1, 1, name="one")},
                    default=lambda: verify.fail("Unknown mode"),
                    name="mode",
                )
            """
        )
        result = _run(pytester, "--verify-fail-fast")
        # A child made eagerly, a default too, is judged when it is made, before the
        # conditional chooses.
        result.assert_outcomes(failed=2, passed=2)
        result.stdout.fnmatch_lines(
            [
                "*_ test_eager _*",
                "1 of 2 checks failed, stopped at ?1?: two — *",
                "*_ test_eager_default _*",
                "1 of 1 checks failed, stopped at ?0?: Unknown mode*",
            ]
        )

    def test_a_failed_lazy_child_stops_the_test(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_lazy(verify):
                verify.conditional(
                    1,
                    cases={
                        1: lambda: verify.equal(3.0, 3.3, name="Active"),
                        2: lambda: verify.equal(0, 1, name="Standby"),
                    },
                    name="mode",
                )
                raise AssertionError("never reached")
            """
        )
        result = _run(pytester, "--verify-fail-fast")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            ["1 of 1 checks failed, stopped at ?0?: Active — expected 3.3, got 3.0"]
        )
        _never(result)

    def test_fixture_teardown_stays_soft(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import pytest

            @pytest.fixture
            def supply(verify):
                yield
                verify.equal(1, 2, name="off")
                open("cleaned.txt", "w").close()
                verify.equal(3, 4, name="discharged")

            @pytest.fixture
            def strict_supply(verify):
                yield
                verify.require.equal(1, 2, name="must be off")
                open("not-cleaned.txt", "w").close()

            @pytest.fixture
            def broken_setup(verify):
                verify.equal(1, 2, name="on")
                open("set-up.txt", "w").close()
                yield
            """
        )
        pytester.makepyfile(
            """
            def test_soft_teardown(supply):
                pass

            def test_required_teardown(strict_supply):
                pass

            def test_setup(broken_setup):
                open("body.txt", "w").close()
            """
        )
        result = _run(pytester, "--verify-fail-fast")
        result.assert_outcomes(passed=2, errors=3)
        result.stdout.fnmatch_lines(
            [
                "*ERROR at teardown of test_soft_teardown*",
                "2 of 2 checks failed: off — expected 2, got 1 (+1 more)",
                "*ERROR at teardown of test_required_teardown*",
                "1 of 1 checks failed, stopped at ?0?: must be off *",
                "*ERROR at setup of test_setup*",
                "1 of 1 checks failed, stopped at ?0?: on *",
            ]
        )
        made = {path.name for path in pytester.path.iterdir() if path.suffix == ".txt"}
        assert made == {"cleaned.txt"}


    def test_testcase_cleanup_stays_soft(self, pytester: pytest.Pytester) -> None:
        # unittest runs tearDown and cleanups in the call phase: fail-fast leaves them soft,
        # as it does fixture teardown; verify.require still stops there.
        pytester.makepyfile(
            """
            import unittest

            import pytest

            class Base:
                @pytest.fixture(autouse=True)
                def _verify(self, verify):
                    self.verify = verify

            class TestSync(Base, unittest.TestCase):
                def setUp(self):
                    self.addCleanup(self.disconnect)

                def disconnect(self):
                    self.verify.equal(1, 2, name="link down")
                    open("disconnected.txt", "w").close()

                def tearDown(self):
                    self.verify.equal(1, 2, name="off")
                    open("off.txt", "w").close()

                def test_it(self):
                    self.verify.equal(1, 1, name="body")

            class TestAsync(Base, unittest.IsolatedAsyncioTestCase):
                async def asyncTearDown(self):
                    self.verify.equal(1, 2, name="async off")
                    open("async-off.txt", "w").close()

                async def test_it(self):
                    self.verify.equal(1, 1, name="body")

            class TestRequired(Base, unittest.TestCase):
                def tearDown(self):
                    self.verify.require.equal(1, 2, name="must be off")
                    open("not-off.txt", "w").close()

                def test_it(self):
                    pass

            VERIFY = []

            class TestOddTearDowns(Base, unittest.TestCase):
                @pytest.fixture(autouse=True)
                def _keep(self, verify):
                    VERIFY[:] = [verify]

                @property
                def tearDown(self):
                    return self._tear_down

                def _tear_down(self):
                    self.verify.equal(1, 2, name="property")
                    open("property.txt", "w").close()

                def test_it(self):
                    pass

            class TestStaticTearDown(TestOddTearDowns):
                @staticmethod
                def tearDown():
                    VERIFY[0].equal(1, 2, name="static")
                    open("static.txt", "w").close()
            """
        )
        result = _run(pytester, "--verify-fail-fast")
        result.assert_outcomes(failed=5)
        result.stdout.fnmatch_lines(
            [
                "2 of 3 checks failed: off — expected 2, got 1 (+1 more)",
                "1 of 2 checks failed: async off — expected 2, got 1",
                "1 of 1 checks failed, stopped at ?0?: must be off *",
                "1 of 1 checks failed: property — expected 2, got 1",
                "1 of 1 checks failed: static — expected 2, got 1",
            ]
        )
        made = {path.name for path in pytester.path.iterdir() if path.suffix == ".txt"}
        assert made == {
            "disconnected.txt", "off.txt", "async-off.txt", "property.txt", "static.txt"
        }

    def test_a_testcase_with_its_own_run_stays_soft_in_teardown(
        self, pytester: pytest.Pytester
    ) -> None:
        # twisted.trial and testtools have their own run(), which calls tearDown directly.
        pytester.makepyfile(
            """
            import sys
            import unittest

            import pytest

            class OwnRun(unittest.TestCase):
                def run(self, result=None):
                    result.startTest(self)
                    try:
                        self.setUp()
                        try:
                            getattr(self, self._testMethodName)()
                        finally:
                            self.tearDown()
                    except Exception:
                        result.addError(self, sys.exc_info())
                    finally:
                        result.stopTest(self)

            class TestOwnRun(OwnRun):
                @pytest.fixture(autouse=True)
                def _verify(self, verify):
                    self.verify = verify

                def tearDown(self):
                    self.verify.equal(1, 2, name="off")
                    open("off.txt", "w").close()

                def test_it(self):
                    self.verify.equal(1, 1, name="body")
            """
        )
        result = _run(pytester, "--verify-fail-fast")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["1 of 2 checks failed: off — expected 2, got 1"])
        assert (pytester.path / "off.txt").exists()

    def test_a_testcase_that_cleans_up_itself_stays_strict(
        self, pytester: pytest.Pytester
    ) -> None:
        # Only while unittest cleans up: the test body may call doCleanups() or tearDown().
        pytester.makepyfile(
            """
            import unittest

            import pytest

            class TestEarlyCleanup(unittest.TestCase):
                @pytest.fixture(autouse=True)
                def _verify(self, verify):
                    self.verify = verify

                def test_it(self):
                    self.addCleanup(lambda: None)
                    self.doCleanups()
                    self.tearDown()
                    self.verify.equal(1, 2, name="first")
                    open("went-on.txt", "w").close()
            """
        )
        result = _run(pytester, "--verify-fail-fast")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["1 of 1 checks failed, stopped at ?0?: first *"])
        assert not (pytester.path / "went-on.txt").exists()

    def test_a_testcase_is_freed_after_its_test(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import gc
            import unittest
            import weakref

            gc.disable()
            INSTANCES = []

            class TestFreed(unittest.TestCase):
                def test_1(self):
                    INSTANCES.append(weakref.ref(self))

                def test_2(self):
                    assert INSTANCES[0]() is None
            """
        )
        enabled = gc.isenabled()  # the inner module turns it off in this process
        try:
            if _run(pytester, "-p", "no:randomly").ret != 0:
                pytest.skip("this pytest keeps TestCase instances alive itself")
            _run(pytester, "--verify-fail-fast", "-p", "no:randomly").assert_outcomes(passed=2)
        finally:
            if enabled:
                gc.enable()


class TestFirstLine:
    def _records(self, *passed: bool) -> List[Dict[str, Any]]:
        return [
            dict(checks.equal(i, i if ok else -1, name=f"c{i}"), passed=ok, detail=f"d{i}")
            for i, ok in enumerate(passed)
        ]

    @pytest.mark.parametrize(
        "passed, first",
        [
            ((True, True), "0 of 2 checks failed"),
            ((True, False), "1 of 2 checks failed: c1 — d1"),
            ((False, True, False, False), "3 of 4 checks failed: c0 — d0 (+2 more)"),
            ((), "0 of 0 checks failed"),
        ],
    )
    def test_the_first_failure_is_repeated(self, passed: Any, first: str) -> None:
        assert format_summary(self._records(*passed)).splitlines()[0] == first

    @pytest.mark.parametrize(
        "stopped_at, start, first",
        [
            (2, 0, "2 of 3 checks failed, stopped at [2]: c2 — d2 (+1 more)"),
            (7, 5, "2 of 3 checks failed, stopped at [7]: c2 — d2 (+1 more)"),
            (1, 0, "2 of 3 checks failed: c0 — d0 (+1 more)"),  # a passed check
            (9, 0, "2 of 3 checks failed: c0 — d0 (+1 more)"),  # not in the list
        ],
    )
    def test_the_check_that_stopped_the_test(self, stopped_at: int, start: int, first: str) -> None:
        records = self._records(False, True, False)
        summary = format_summary(records, start=start, stopped_at=stopped_at)
        assert summary.splitlines()[0] == first

    def test_the_first_line_is_bounded(self) -> None:
        [record] = self._records(False)
        record["detail"] = "x" * 5000
        first = format_summary([record]).splitlines()[0]
        assert first.startswith("1 of 1 checks failed: c0 — xxx")
        assert len(first) < 340

    def test_a_record_that_cannot_be_rendered(self) -> None:
        class Weird(dict):  # type: ignore[type-arg]
            def get(self, key: Any, default: Any = None) -> Any:
                if key == "name":
                    raise RuntimeError("boom")
                return super().get(key, default)

        summary = format_summary([Weird(check_type="equal", passed=False)])
        assert summary.splitlines()[0] == "1 of 1 checks failed"

    def test_junit_and_short_summary(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_rail(verify):
                verify.between(3.6, 3.2, 3.4, name="rail", units="V")
                verify.equal(1, 2, name="other")
            """
        )
        result = _run(pytester, "-rf", "--junitxml=out.xml")
        result.stdout.fnmatch_lines(
            [
                "FAILED *::test_rail - 2 of 2 checks failed: rail — expected ?3.2V, 3.4V?, "
                "got 3.6V (+1 more)"
            ]
        )
        xml = (pytester.path / "out.xml").read_text(encoding="utf-8")
        assert (
            'message="2 of 2 checks failed: rail — expected [3.2V, 3.4V], got 3.6V (+1 more)'
            in xml
        )

    def test_pickled_errors_keep_the_first_line(self) -> None:
        import pickle

        error = ChecksFailedError(self._records(False, True, False), start=3, stopped_at=5)
        copy = pickle.loads(pickle.dumps(error))
        assert str(copy).splitlines()[0] == str(error).splitlines()[0]
        assert str(copy).startswith("2 of 3 checks failed, stopped at [5]: c2 — d2")
        assert (copy.start, copy.stopped_at) == (3, 5)
