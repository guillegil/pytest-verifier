"""Lazy children: a ``conditional``/``guard`` child (or condition) given as a callable."""
from __future__ import annotations

import json
import threading

import pytest

from pytest_verify import verify as checks
from pytest_verify._checks import render_detail
from pytest_verify._run import Run, recording_verify


def _recording():
    run = Run()
    run.phase = "call"
    return run, recording_verify(run)


class _Calls:
    """A child factory that remembers whether it was called."""

    def __init__(self, check):
        self.check = check
        self.called = 0

    def __call__(self):
        self.called += 1
        return self.check


class TestConditionalBuild:
    def test_only_the_selected_case_is_called(self):
        one = _Calls(checks.equal(1, 1, name="one"))
        two = _Calls(checks.equal(2, 2, name="two"))
        d = checks.conditional(1, cases={1: one, 2: two}, name="Mode")
        assert (one.called, two.called) == (1, 0)
        assert d["cases"]["1"]["name"] == "one"
        assert d["cases"]["2"] is None
        assert checks.evaluate(d) is True

    def test_default_is_called_only_when_no_case_matches(self):
        default = _Calls(checks.equal(1, 2, name="fallback"))
        d = checks.conditional(1, cases={1: lambda: checks.is_true(True, name="t")},
                               default=default, name="Mode")
        assert default.called == 0
        assert d["default"] is None
        d = checks.conditional(9, cases={1: lambda: checks.is_true(True, name="t")},
                               default=default, name="Mode")
        assert default.called == 1
        assert d["default"]["name"] == "fallback"
        assert d["cases"]["1"] is None
        assert checks.evaluate(d) is False

    def test_eager_and_lazy_cases_mix(self):
        d = checks.conditional(
            2, cases={1: checks.equal(1, 1, name="eager"), 2: lambda: checks.fail("lazy")},
            name="Mode",
        )
        assert d["cases"]["1"]["name"] == "eager"
        assert d["cases"]["2"]["name"] == "lazy"
        assert checks.evaluate(d) is False

    def test_a_raising_child_fails_with_an_error(self):
        def boom():
            raise ValueError("no device")

        d = checks.conditional(1, cases={1: boom}, name="Mode")
        assert d["error"] == "case 1 raised ValueError: no device"
        assert d["cases"]["1"] is None
        [result] = checks.evaluate_detailed(d)
        assert result["passed"] is False
        assert result["error"] == "case 1 raised ValueError: no device"
        assert render_detail(d, False) == (
            "[mode=1 → no check] (case 1 raised ValueError: no device)"
        )

    def test_a_child_that_returns_no_check_fails(self):
        d = checks.conditional("a", cases={"a": lambda: None}, name="Mode")
        assert d["error"] == (
            "case 'a' returned NoneType, not a check (did you forget `return`?)"
        )
        assert checks.evaluate(d) is False

    def test_a_raising_default_fails(self):
        d = checks.conditional(5, cases={}, default=lambda: 1 / 0, name="Mode")
        assert d["error"] == "default raised ZeroDivisionError: division by zero"
        assert checks.evaluate(d) is False

    def test_non_callable_non_descriptor_case_is_a_usage_error(self):
        with pytest.raises(TypeError, match="or a callable that returns one, got int"):
            checks.conditional(1, cases={1: 5}, name="Mode")

    def test_built_descriptor_is_json_serializable(self):
        d = checks.conditional(
            1, cases={1: lambda: checks.equal(1, 1, name="a"), 2: lambda: checks.fail("b")},
            name="Mode",
        )
        json.dumps(d)


class TestGuardBuild:
    def test_only_the_selected_check_is_called(self):
        first = _Calls(checks.fail("first"))
        second = _Calls(checks.equal(1, 1, name="second"))
        d = checks.guard([(False, "a", first), (True, "b", second)], name="G")
        assert (first.called, second.called) == (0, 1)
        assert [b["check"] for b in d["branches"]][0] is None
        assert d["branches"][1]["check"]["name"] == "second"
        assert checks.evaluate(d) is True

    def test_callable_conditions_stop_at_the_first_true_one(self):
        calls = []

        def cond(index, value):
            def condition():
                calls.append(index)
                return value
            return condition

        d = checks.guard(
            [
                (cond(0, False), "a", checks.fail("a")),
                (cond(1, True), "b", checks.equal(1, 1, name="b")),
                (cond(2, True), "c", checks.fail("c")),
                (True, "d", checks.fail("d")),
            ],
            name="G",
        )
        assert calls == [0, 1]
        assert [b["condition"] for b in d["branches"]] == [False, True, None, True]
        assert d["matched_index"] == 1
        assert checks.evaluate(d) is True

    def test_default_is_lazy_too(self):
        default = _Calls(checks.equal(1, 1, name="fallback"))
        d = checks.guard([(True, "a", lambda: checks.is_true(1, name="a"))],
                         default=default, name="G")
        assert default.called == 0 and d["default"] is None
        d = checks.guard([(lambda: False, "a", lambda: checks.fail("a"))],
                         default=default, name="G")
        assert default.called == 1 and d["default"]["name"] == "fallback"
        assert d["branches"][0]["check"] is None
        assert checks.evaluate(d) is True

    def test_a_raising_condition_stops_the_chain(self):
        later = _Calls(True)

        def broken():
            raise RuntimeError("sensor offline")

        d = checks.guard(
            [(broken, "a", checks.fail("a")), (later, "b", checks.equal(1, 1, name="b"))],
            name="G",
        )
        assert later.called == 0
        assert d["matched_index"] is None
        assert d["error"] == "condition of branch 0 (a) raised RuntimeError: sensor offline"
        assert checks.evaluate(d) is False

    def test_a_raising_check_fails_with_an_error(self):
        d = checks.guard([(True, "on", lambda: [][0])], name="G")
        assert d["error"] == "branch 0 (on) raised IndexError: list index out of range"
        assert checks.evaluate(d) is False
        assert render_detail(d, False).startswith("[→ on] (branch 0 (on) raised IndexError")

    def test_a_descriptor_condition_is_still_rejected(self):
        with pytest.raises(TypeError, match="always truthy"):
            checks.guard([(checks.fail("x"), "a", lambda: checks.fail("y"))], name="G")


class TestWithTheFixture:
    def test_checks_made_by_the_selected_child_belong_to_the_composite(self):
        run, verify = _recording()
        record = verify.conditional(
            1, cases={1: lambda: verify.equal(1, 2, name="inner"),
                      2: lambda: verify.fail("never")},
            name="Mode",
        )
        assert [r["name"] for r in run.records] == ["Mode"]
        assert record["passed"] is False
        assert record["cases"]["1"]["passed"] is False
        assert record["cases"]["2"] is None
        assert record["detail"].startswith("[mode=1 → inner] — expected 2, got 1")

    def test_extra_checks_made_by_a_child_stay_on_their_own(self):
        run, verify = _recording()

        def child():
            verify.equal(1, 1, name="side")
            return verify.equal(2, 2, name="main")

        verify.guard([(True, "on", child)], name="G")
        assert [r["name"] for r in run.records] == ["side", "G"]
        assert run.records[1]["branches"][0]["check"]["name"] == "main"

    def test_a_child_that_returns_an_earlier_check_absorbs_it(self):
        run, verify = _recording()
        earlier = verify.equal(1, 2, name="earlier")
        record = verify.conditional(1, cases={1: lambda: earlier}, name="Mode")
        assert [r["name"] for r in run.records] == ["Mode"]
        assert record["passed"] is False

    def test_all_satisfy_factory_that_returns_earlier_checks_absorbs_them(self):
        run, verify = _recording()
        made = [verify.greater(x, 0, name=f"x{x}") for x in (1, 2)]
        verify.all_satisfy([0, 1], lambda i: made[i], name="All")
        assert [r["name"] for r in run.records] == ["All"]

    def test_nested_composites_keep_their_own_children(self):
        run, verify = _recording()
        record = verify.conditional(
            1,
            cases={1: lambda: verify.guard(
                [(True, "on", verify.equal(1, 1, name="eager")),
                 (False, "off", lambda: verify.fail("lazy"))],
                name="inner",
            )},
            name="outer",
        )
        assert [r["name"] for r in run.records] == ["outer"]
        inner = record["cases"]["1"]
        assert inner["name"] == "inner" and inner["passed"] is True
        assert inner["branches"][0]["check"]["name"] == "eager"
        assert inner["branches"][1]["check"] is None

    def test_a_raising_child_does_not_lose_the_checks_it_made(self):
        run, verify = _recording()

        def child():
            verify.equal(1, 1, name="before")
            raise ValueError("boom")

        record = verify.conditional(1, cases={1: child}, name="Mode")
        assert [r["name"] for r in run.records] == ["before", "Mode"]
        assert record["passed"] is False
        assert record["error"] == "case 1 raised ValueError: boom"

    def test_checks_from_another_thread_stay_at_the_top_level(self):
        run, verify = _recording()

        def child():
            worker = threading.Thread(target=lambda: verify.equal(1, 1, name="worker"))
            worker.start()
            worker.join()
            return verify.equal(2, 2, name="mine")

        verify.conditional(1, cases={1: child}, name="Mode")
        assert [r["name"] for r in run.records] == ["worker", "Mode"]

    def test_record_is_json_safe(self):
        run, verify = _recording()
        verify.guard(
            [(lambda: True, "on", lambda: verify.approx(1.0, 1.0, abs_tol=0.1, name="a"))],
            default=lambda: verify.fail("never"),
            name="G",
        )
        json.dumps(run.records, allow_nan=False)


def test_failed_lazy_child_fails_the_test(pytester):
    pytester.makepyfile("""
        def test_lazy(verify):
            verify.conditional(
                "fast",
                cases={"fast": lambda: verify.less(12, 10, name="latency", units="ms"),
                       "slow": lambda: verify.less(12, 100, name="latency", units="ms")},
                name="Mode",
            )
    """)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*Mode [[]mode=fast → latency[]] — expected < 10ms, got 12ms*"]
    )
