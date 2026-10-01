"""``UnusedCheckWarning``: checks built with ``checks.*`` in a test body and never used (IMP-7).

The project's pytest configuration ignores the warning, because the suite builds checks to
inspect them; every inner session here turns it back on with ``-W``.
"""
from __future__ import annotations

import gc
import threading
import weakref

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
        "evaluated, so it cannot fail the test: 'this was meant to fail the test' (fail) at "
        "test_a_builder_check_in_a_test_without_the_fixture_warns.py:4. Use the 'verify' "
        "fixture*",
    ])


def test_the_warning_can_fail_the_test(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def test_a(verify):
            verify.equal(1, 2, name="recorded")
            checks.equal(1, 2, name="lost")
    """)
    result = pytester.runpytest(*_ERROR)
    # The soft failure fails the test body; the warning, turned into an error, its teardown.
    result.assert_outcomes(failed=1, errors=1)
    result.stdout.fnmatch_lines([
        "*recorded*",
        "*UnusedCheckWarning: 1 check was built*'lost' (equal) at "
        "test_the_warning_can_fail_the_test.py:5.*",
    ])


def test_the_warning_names_the_first_three_checks(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def test_a():
            for n in range(5):
                checks.is_true(n, name=f"c{n}")
    """)
    result = pytester.runpytest(*_ON)
    result.stdout.fnmatch_lines([
        "*5 checks were built*so they cannot fail the test: 'c0' (true) at "
        "test_the_warning_names_the_first_three_checks.py:5, 'c1' (true) at *, 'c2' (true) at * "
        "and 2 more.*"
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


def test_pauses_and_trackers_nest() -> None:
    outer = _unused.start()
    try:
        paused = _unused.pause()
        checks.equal(1, 1, name="paused")
        inner = _unused.start()
        built = checks.equal(1, 1, name="inner")
        _unused.stop_building(inner)
        _unused.resume(paused)
        other = checks.equal(1, 1, name="outer")
        assert [entry.label for entry in outer.unused()] == ["'outer' (equal)"]
        assert [entry.label for entry in inner.unused()] == ["'inner' (equal)"]
        _unused.used(built, other)  # every tracker still listening hears about a use
        assert _unused.finish(inner) == []
        assert outer.unused() == []
    finally:
        _unused.finish(outer)
    _unused.finish(outer)  # finishing twice is harmless
    _unused.resume(paused)
    checks.equal(1, 1, name="untracked")
    assert outer.unused() == []


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
        _unused.finish(tracker)


def test_the_warning_class_is_public() -> None:
    assert issubclass(UnusedCheckWarning, pytest.PytestWarning)
    assert UnusedCheckWarning.__module__ == "pytest_verifier"


# ---------------------------------------------------------------------------
# Review of 0.6.0
# ---------------------------------------------------------------------------


def test_evaluate_does_not_use_up_an_iterator_of_children(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def rows():
            return {"check_type": "all_satisfy", "name": "rows", "description": "rows",
                    "child_checks": (checks.greater_equal(r, 0, name="row") for r in [1, -3])}

        OUTSIDE = checks.evaluate(rows())

        def test_a():
            assert OUTSIDE is False
            assert checks.evaluate(rows()) is False
            assert checks.evaluate_detailed(rows())[0]["passed"] is False
    """)
    pytester.runpytest(*_ON).assert_outcomes(passed=1)


@pytest.mark.filterwarnings("error::pytest_verifier.UnusedCheckWarning")
def test_an_inner_session_is_not_counted_against_the_test_running_it(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest("""
        from pytest_verifier import checks

        IN_CONFTEST = checks.equal(1, 1, name="conftest")
    """)
    pytester.makepyfile("""
        import pytest
        from pytest_verifier import checks

        AT_IMPORT = checks.equal(1, 1, name="built at import")

        @pytest.fixture
        def limit():
            return checks.between(3.3, 3.2, 3.4, name="rail")

        def test_inner(verify, limit):
            verify.record(limit)
    """)
    result = pytester.runpytest(*_ERROR)
    result.assert_outcomes(passed=1)
    _no_warning(result)


def test_a_large_composite_counts_every_child(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def test_a(verify):
            rows = checks.all_satisfy(range(100_500), lambda n: checks.greater_equal(n, 0, name="r"),
                                      name="rows")
            assert checks.evaluate(rows)
    """)
    result = pytester.runpytest(*_ERROR)
    result.assert_outcomes(passed=1)
    _no_warning(result)


def test_checks_a_fixture_records_in_teardown_count(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        import pytest
        from pytest_verifier import checks

        @pytest.fixture
        def expect(verify, request):
            pending = []
            yield pending.append
            for check in pending:
                verify.record(check)

        @pytest.fixture
        def later(request):
            pending = []
            request.addfinalizer(lambda: checks.evaluate(*pending))
            return pending.append

        def test_deferred_pass(expect, later):
            expect(checks.equal(1, 1, name="a"))
            later(checks.equal(1, 1, name="b"))

        def test_deferred_fail(expect):
            expect(checks.equal(1, 2, name="c"))

        @pytest.fixture
        def builds_in_teardown():
            yield
            checks.equal(1, 1, name="built in teardown")

        def test_teardown_is_not_tracked(builds_in_teardown):
            pass
    """)
    result = pytester.runpytest(*_ERROR)
    result.assert_outcomes(passed=3, errors=1)
    result.stdout.fnmatch_lines(["*✗ ?0? c — expected 2, got 1*"])
    _no_warning(result)


def test_fixtures_requested_by_the_body_are_not_tracked(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        import pytest
        from pytest_verifier import checks

        @pytest.fixture(scope="module")
        def rail_limits():
            return {"3v3": checks.between(3.3, 3.2, 3.4, name="3v3"),
                    "5v0": checks.between(5.0, 4.8, 5.2, name="5v0")}

        @pytest.mark.parametrize("rail", ["3v3", "5v0"])
        def test_rail(request, verify, rail):
            verify.record(request.getfixturevalue("rail_limits")[rail])
    """)
    result = pytester.runpytest(*_ERROR)
    result.assert_outcomes(passed=2)
    _no_warning(result)


def test_unittest_test_cases_are_not_tracked(pytester: pytest.Pytester) -> None:
    """``setUp`` runs with the test method, so checks it prepares cannot be told apart."""
    pytester.makepyfile("""
        import unittest
        from pytest_verifier import checks

        class T(unittest.TestCase):
            def setUp(self):
                self.limits = {"3v3": checks.between(3.3, 3.2, 3.4, name="3v3"),
                               "5v0": checks.between(5.0, 4.8, 5.2, name="5v0")}

            def test_3v3(self):
                self.assertTrue(checks.evaluate(self.limits["3v3"]))
    """)
    result = pytester.runpytest(*_ERROR)
    result.assert_outcomes(passed=1)
    _no_warning(result)


def test_the_tracker_keeps_no_check_alive() -> None:
    class Big:
        pass

    tracker = _unused.start()
    try:
        value = Big()
        alive = weakref.ref(value)
        checks.equal(value, 1, name="dropped")
        del value
        gc.collect()
        assert alive() is None
        assert [entry.label for entry in tracker.unused()] == ["'dropped' (equal)"]
    finally:
        _unused.finish(tracker)


def test_a_reused_id_does_not_hide_a_dropped_check(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        from pytest_verifier import checks

        def test_a():
            for _ in range(20):
                checks.equal(1, 1, name="dropped")
            kept = checks.equal(1, 1, name="kept")
            assert checks.evaluate(kept)
    """)
    result = pytester.runpytest(*_ON)
    result.stdout.fnmatch_lines(["*20 checks were built*'dropped' (equal)*and 17 more.*"])


def test_a_module_filter_applies(pytester: pytest.Pytester) -> None:
    pytester.makeini("""
        [pytest]
        filterwarnings =
            default::pytest_verifier.UnusedCheckWarning
            ignore::pytest_verifier.UnusedCheckWarning:test_quiet
    """)
    lost = """
        from pytest_verifier import checks

        def test_a():
            checks.equal(1, 2, name="lost")
    """
    pytester.makepyfile(test_quiet=lost, test_loud=lost)
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*test_loud.py:4: UnusedCheckWarning*"])
    result.stdout.no_fnmatch_line("*test_quiet.py:4: UnusedCheckWarning*")
