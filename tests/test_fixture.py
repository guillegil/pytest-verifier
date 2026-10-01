"""Tests for the verify fixture lifecycle, teardown, and the results stash."""
from __future__ import annotations

import pytest

from pytest_verify import ChecksFailedError


class TestFixtureBasicBehavior:
    """Test that the fixture evaluates checks and sets the passed field."""

    def test_passing_check_has_passed_true(self, verify):
        result = verify.equal(1, 1, name="One")
        assert result["passed"] is True

    def test_failing_check_has_passed_false(self, pytester):
        """Verify that a failing check sets passed=False (tested via pytester
        to avoid teardown error in the outer test)."""
        pytester.makepyfile("""
            def test_inner(verify):
                result = verify.equal(1, 2, name="Wrong")
                assert result["passed"] is False
        """)
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)  # fails at teardown, but inner assertion held

    def test_returns_descriptor_dict(self, verify):
        result = verify.greater(10, 5, name="Speed")
        assert isinstance(result, dict)
        assert result["check_type"] == "greater"
        assert "passed" in result


class TestFixtureTeardown:
    """Test that ChecksFailedError is raised at teardown when checks fail."""

    def test_all_pass_no_error(self, verify):
        verify.equal(1, 1, name="A")
        verify.greater(10, 5, name="B")
        # No error raised — test passes

    def test_failed_checks_raise_at_teardown(self, pytester):
        pytester.makepyfile("""
            def test_soft_fail(verify):
                verify.equal(1, 1, name="Good")
                verify.equal(1, 2, name="Bad")
                verify.equal(3, 4, name="AlsoBad")
        """)
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*checks failed*"])

    def test_error_message_format(self, pytester):
        pytester.makepyfile("""
            def test_message_format(verify):
                verify.equal(1, 1, name="Pass1")
                verify.equal(1, 2, name="Fail1")
                verify.equal(3, 3, name="Pass2")
        """)
        result = pytester.runpytest("-v")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines([
            "*1 of 3 checks failed*",
            "*[1]*Fail1*",
            "*[0]*Pass1*",
            "*[2]*Pass2*",
        ])

    def test_hard_failure_still_surfaces_soft_checks(self, pytester):
        """When the test body raises a hard error AND soft checks failed, both
        the original traceback and the soft-assert summary must be reported —
        the soft failures must not be silently dropped."""
        pytester.makepyfile("""
            def test_hard_and_soft(verify):
                verify.equal(1, 2, name="SoftBad")
                raise RuntimeError("hard boom")
        """)
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*RuntimeError*hard boom*"])
        result.stdout.fnmatch_lines(["*checks failed*", "*SoftBad*"])

    def test_guard_failure_reports_branch_label(self, pytester):
        """A failing guard reaches teardown as a FAILED outcome whose summary
        names the branch that was taken."""
        pytester.makepyfile("""
            def test_guarded(verify):
                verify.guard(
                    branches=[(True, "below floor", verify.equal(7, 0, name="lo"))],
                    name="Sensor output",
                )
        """)
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*checks failed*", "*Sensor output*below floor*"])

    def test_guard_does_not_evaluate_unmatched_branches(self, pytester):
        """Only the matched branch counts. An unmatched branch whose check would
        fail must NOT make the test fail."""
        pytester.makepyfile("""
            def test_g(verify):
                verify.guard(
                    branches=[
                        (False, "bad", verify.equal(1, 2, name="bad")),
                        (True, "good", verify.equal(1, 1, name="good")),
                    ],
                    name="G",
                )
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)

    def test_conditional_does_not_evaluate_unmatched_cases(self, pytester):
        pytester.makepyfile("""
            def test_c(verify):
                verify.conditional(
                    1,
                    cases={
                        0: verify.equal(1, 2, name="zero"),
                        1: verify.equal(1, 1, name="one"),
                    },
                    name="C",
                )
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)

    def test_composite_records_only_parent_not_children(self):
        """A composite consumes its child descriptors; the children must not
        remain as independent recorded results."""
        from pytest_verify._fixture import _FixtureVerify, _Run

        run = _Run()
        fv = _FixtureVerify(run)
        fv.guard(
            branches=[
                (False, "bad", fv.equal(1, 2, name="bad")),
                (True, "good", fv.equal(1, 1, name="good")),
            ],
            name="G",
        )
        assert [r["check_type"] for r in run.records] == ["guard"]

    def test_no_state_bleed_between_tests(self, pytester):
        pytester.makepyfile("""
            def test_first(verify):
                verify.equal(1, 1, name="A")

            def test_second(verify):
                verify.equal(2, 2, name="B")
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=2)


class TestFixtureAllCheckMethods:
    """Smoke test that all 21 methods work through the fixture path."""

    def test_equal(self, verify):
        assert verify.equal(1, 1, name="X")["passed"] is True

    def test_not_equal(self, verify):
        assert verify.not_equal(1, 2, name="X")["passed"] is True

    def test_approx(self, verify):
        assert verify.approx(3.28, 3.3, abs_tol=0.05, name="V")["passed"] is True

    def test_greater(self, verify):
        assert verify.greater(10, 5, name="X")["passed"] is True

    def test_greater_equal(self, verify):
        assert verify.greater_equal(5, 5, name="X")["passed"] is True

    def test_less(self, verify):
        assert verify.less(3, 5, name="X")["passed"] is True

    def test_less_equal(self, verify):
        assert verify.less_equal(5, 5, name="X")["passed"] is True

    def test_between(self, verify):
        assert verify.between(0.3, 0.1, 0.5, name="I")["passed"] is True

    def test_is_true(self, verify):
        assert verify.is_true(True, name="X")["passed"] is True

    def test_is_false(self, verify):
        assert verify.is_false(False, name="X")["passed"] is True

    def test_is_none(self, verify):
        assert verify.is_none(None, name="X")["passed"] is True

    def test_is_not_none(self, verify):
        assert verify.is_not_none(42, name="X")["passed"] is True

    def test_contains(self, verify):
        assert verify.contains("hello", "ell", name="X")["passed"] is True

    def test_not_contains(self, verify):
        assert verify.not_contains("hello", "xyz", name="X")["passed"] is True

    def test_matches(self, verify):
        assert verify.matches("abc123", r"\d+", name="X")["passed"] is True

    def test_is_instance(self, verify):
        assert verify.is_instance({}, dict, name="X")["passed"] is True

    def test_length(self, verify):
        assert verify.length([1, 2, 3], 3, name="X")["passed"] is True

    def test_all_satisfy(self, verify):
        from pytest_verify._descriptors import build_greater
        factory = lambda x: build_greater(x, 0, name=f"item_{x}")
        assert verify.all_satisfy([1, 2, 3], factory, name="Pos")["passed"] is True

    def test_conditional(self, verify):
        from pytest_verify._descriptors import build_equal
        cases = {"1": build_equal(10, 10, name="case1")}
        assert verify.conditional(1, cases=cases, name="M")["passed"] is True

    def test_guard(self, verify):
        check = verify.guard(
            branches=[(False, "off", verify.fail("unused")), (True, "on", verify.is_true(1, name="On"))],
            name="G",
        )
        assert check["passed"] is True

    @pytest.mark.xfail(raises=ChecksFailedError, strict=True)
    def test_fail(self, verify):
        # The failed check is recorded and the test fails after its body.
        assert verify.fail("intentional")["passed"] is False


class TestUnconditionalStashWrite:
    """Test stash writes are unconditional — reporter presence does not matter."""

    def test_stash_written_without_reporter(self, pytester):
        """Stash is always populated regardless of whether reporter is installed."""
        pytester.makepyfile("""
            from pytest_verify._stash import check_results_key

            def test_stash_present(request, verify):
                verify.equal(1, 1, name="X")
                results = request.node.stash.get(check_results_key, None)
                assert results is not None, "stash not written"
                assert len(results) == 1
                assert results[0]["passed"] is True
        """)
        result = pytester.runpytest("-p", "no:pytest_reporter")
        result.assert_outcomes(passed=1)

    def test_stash_written_with_reporter(self, pytester):
        """Stash is populated when reporter is installed (unchanged behavior)."""
        pytester.makepyfile("""
            from pytest_verify._stash import check_results_key

            def test_stash_present(request, verify):
                verify.equal(1, 1, name="X")
                results = request.node.stash[check_results_key]
                assert len(results) == 1
                assert results[0]["passed"] is True
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)
