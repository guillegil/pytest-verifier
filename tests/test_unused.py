"""``UnusedCheckWarning``: checks built with ``checks.*`` in a test body and never used (IMP-7).

The project's pytest configuration ignores the warning, because the suite builds checks to
inspect them; every inner session here turns it back on with ``-W``.
"""
from __future__ import annotations

import threading

import pytest

from pytest_verifier import UnusedCheckWarning, checks
from pytest_verifier import _unused

_ON = ("-W", "default::pytest_verifier.UnusedCheckWarning")
_ERROR = ("-W", "error::pytest_verifier.UnusedCheckWarning")


def _no_warning(result: pytest.RunResult) -> None:
    assert "UnusedCheckWarning" not in result.stdout.str()


def test_a_builder_check_in_a_test_without_the_fixture_warns(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def test_a():
            checks.fail("this was meant to fail the test")
    """)
    result = pytester.runpytest(*_ON)
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines([
        "*test_a_builder_check_in_a_test_without_the_fixture_warns.py:4: UnusedCheckWarning: "
        "1 check was built with pytest_verifier.checks in this test but never recorded or "
        "evaluated, so they cannot fail it: 'this was meant to fail the test' (fail).*",
    ])


def test_the_warning_can_fail_the_test(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def test_a(verify):
            verify.equal(1, 2, name="recorded")
            checks.equal(1, 2, name="lost")
    """)
    result = pytester.runpytest(*_ERROR)
    result.assert_outcomes(failed=1)
    # The soft failure is reported together with the warning turned error.
    result.stdout.fnmatch_lines(["*UnusedCheckWarning*'lost' (equal)*", "*recorded*"])


def test_the_warning_names_the_first_three_checks(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def test_a():
            for n in range(5):
                checks.is_true(n, name=f"c{n}")
    """)
    result = pytester.runpytest(*_ON)
    result.stdout.fnmatch_lines([
        "*5 checks were built*: 'c0' (true), 'c1' (true), 'c2' (true) and 2 more.*"
    ])


def test_checks_that_are_used_do_not_warn(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        import pytest
        from pytest_verifier import checks

        def test_evaluate():
            assert checks.evaluate(checks.equal(1, 1, name="a"))
            assert checks.evaluate_detailed(checks.equal(1, 1, name="b"))[0]["passed"]

        def test_record(verify):
            verify.record(checks.equal(1, 1, name="a"))

        def test_fixture_composite_children(verify):
            verify.conditional(
                1,
                cases={1: checks.equal(1, 1, name="eager"), 2: lambda: checks.fail("never")},
                default=checks.fail("unselected"),
                name="C",
            )
            verify.guard([(True, "on", lambda: checks.is_true(1, name="lazy"))], name="G")
            verify.all_satisfy([1, 2], lambda n: checks.greater(n, 0, name="n"), name="A")

        def test_module_composite_then_evaluate():
            inner = checks.conditional(1, cases={1: checks.equal(1, 1, name="x")}, name="in")
            outer = checks.guard([(True, "t", inner)], name="out")
            assert checks.evaluate(outer)

        def test_hand_built_composite_with_builder_children():
            child = checks.equal(1, 1, name="x")
            hand = {"check_type": "all_satisfy", "name": "h", "description": "h",
                    "child_checks": [child]}
            assert checks.evaluate(hand)

        def test_a_composite_that_raises_on_build():
            with pytest.raises(ValueError):
                checks.conditional(1, cases={1: checks.equal(1, 1, name="a"),
                                             "1": checks.equal(1, 1, name="b")}, name="C")

        def test_module_record_raises():
            with pytest.raises(RuntimeError):
                checks.record(checks.equal(1, 1, name="a"))
    """)
    result = pytester.runpytest(*_ERROR)
    result.assert_outcomes(passed=7)
    _no_warning(result)


def test_only_the_test_body_is_tracked(pytester: pytest.Pytester) -> None:
    """A fixture may build checks that a later test records; module imports build none."""
    pytester.makepyfile("""
        import pytest
        from pytest_verifier import checks

        AT_IMPORT = checks.equal(1, 1, name="built at import")

        @pytest.fixture(scope="module")
        def limits():
            return [checks.between(3.3, 3.2, 3.4, name="rail")]

        def test_one(limits):
            pass

        def test_two(limits, verify):
            verify.record(limits[0])
    """)
    result = pytester.runpytest(*_ERROR)
    result.assert_outcomes(passed=2)
    _no_warning(result)


def test_a_failing_test_body_does_not_warn(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def test_a():
            check = checks.equal(1, 1, name="a")
            raise RuntimeError("before the check is used")
    """)
    result = pytester.runpytest(*_ERROR)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*RuntimeError: before the check is used*"])
    _no_warning(result)


def test_a_check_built_in_a_thread_is_tracked(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        import threading
        from pytest_verifier import checks

        def test_a():
            worker = threading.Thread(target=lambda: checks.equal(1, 2, name="in thread"))
            worker.start()
            worker.join()
    """)
    result = pytester.runpytest(*_ON)
    result.stdout.fnmatch_lines(["*UnusedCheckWarning*'in thread' (equal)*"])


def test_a_copy_does_not_count_as_using_the_check(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def test_a(verify):
            verify.record(dict(checks.equal(1, 1, name="copied")))
    """)
    result = pytester.runpytest(*_ON)
    result.stdout.fnmatch_lines(["*UnusedCheckWarning*'copied' (equal)*"])


def test_trackers_nest_and_stop() -> None:
    outer = _unused.start()
    try:
        inner = _unused.start()
        built = checks.equal(1, 1, name="inner")
        _unused.stop(inner)
        assert [entry[0] for entry in inner.unused()] == [built]
        assert outer.unused() == []
        other = checks.equal(1, 1, name="outer")
        assert [entry[0] for entry in outer.unused()] == [other]
        _unused.used(other)
        assert outer.unused() == []
    finally:
        _unused.stop(outer)
    _unused.stop(outer)  # stopping twice is harmless


def test_the_tracker_is_thread_safe() -> None:
    tracker = _unused.start()
    try:
        def build() -> None:
            for _ in range(500):
                _unused.used(checks.equal(1, 1, name="t"))

        workers = [threading.Thread(target=build) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert tracker.unused() == []
    finally:
        _unused.stop(tracker)


def test_the_warning_class_is_public() -> None:
    assert issubclass(UnusedCheckWarning, pytest.PytestWarning)
    assert UnusedCheckWarning.__module__ == "pytest_verifier"
