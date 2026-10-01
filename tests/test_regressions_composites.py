"""Regression tests for composite checks (guard / conditional / all_satisfy).

Every test here asserts the correct behaviour for a bug found by the 0.3.1 review
(see ``bugs-0.3.1.md``) and fixed in 0.4.0. Each one failed on 0.3.1 and keeps the bug
from coming back, except the H-4 tests, which pin the documented rule that replaced that fix.
"""
from __future__ import annotations

import enum
import json
import re
import sys

import pytest

from pytest_verify import get_check_results, verify
from pytest_verify._exceptions import ChecksFailedError

# ``str()`` of IntEnum members changed in Python 3.11 ('Level.ONE' -> '1'), and so did
# ``format()`` of (str, Enum) members. Bugs that hinge on that only show on 3.9 / 3.10.
PY_LT_311 = sys.version_info < (3, 11)


class Level(enum.IntEnum):
    STANDBY = 0
    ONE = 1


class StrMode(str, enum.Enum):
    ACTIVE = "active"


class Color(enum.Enum):
    RED = "red"


# Inner-run conftest: lets an inner test dump the names of its top-level recorded checks
# to ``recorded.json``. Used where the right pytest outcome depends on how the bug is fixed
# (raise vs. record a failure), so only the recorded results are asserted on.
_DUMP_CONFTEST = """
    import json

    import pytest

    from pytest_verify import get_check_results


    @pytest.fixture
    def dump_recorded(request):
        def dump(**extra):
            names = [r.get("name") for r in get_check_results(request.node)]
            path = request.config.rootpath / "recorded.json"
            path.write_text(json.dumps({"names": names, **extra}))

        return dump
"""


def _run_and_read_recorded(pytester: pytest.Pytester, source: str) -> dict:
    """Run ``source`` in an inner session and return what its ``dump_recorded`` call wrote."""
    pytester.makeconftest(_DUMP_CONFTEST)
    pytester.makepyfile(source)
    pytester.runpytest()
    dump = pytester.path / "recorded.json"
    assert dump.exists(), "inner test never reached dump_recorded()"
    return json.loads(dump.read_text())


def _render(descriptor: dict) -> str:
    """Evaluate a module-built descriptor and return its ChecksFailedError summary."""
    return str(ChecksFailedError([dict(descriptor, passed=verify.evaluate(descriptor))]))


# ======================================================================
# H-2: the fixture evaluates every guard branch / conditional case eagerly
# ======================================================================

def _carries_failed_verdict(child: dict | None) -> bool:
    """Whether an unmatched child would still render as a failed card.

    A fix may drop the child, clear its ``passed``, or mark it explicitly not applicable
    (any key naming it unevaluated / skipped / not applicable); none of those count.
    """
    if child is None or child.get("passed") is not False:
        return False
    markers = ("applicable", "skip", "evaluated")
    return not any(m in key.lower() for key in child for m in markers)


def test_h2_guard_unmatched_default_not_evaluated(pytester):
    """A disabled sensor (reading None) takes the first branch; the default that compares
    the reading must not run, so the guard passes and the later check is reached."""
    pytester.makepyfile("""
        def test_sensor_disabled(verify):
            reading = None
            verify.guard(
                branches=[(reading is None, "off", verify.is_none(reading, name="no reading"))],
                default=verify.greater(reading, 0, name="reading positive"),
                name="Sensor output",
            )
            verify.equal(1, 1, name="later check")
    """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_h2_guard_unmatched_branch_not_evaluated(pytester):
    """Same as above, but the check that cannot run sits in a later (unmatched) branch."""
    pytester.makepyfile("""
        def test_status_missing(verify):
            status = None
            verify.guard(
                branches=[
                    (status is None, "no status", verify.is_none(status, name="no status")),
                    (True, "has status", verify.contains(status, "OK", name="status ok")),
                ],
                name="Status",
            )
            verify.equal(1, 1, name="later check")
    """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_h2_conditional_unmatched_case_not_evaluated(pytester):
    """Mode 'off' is selected; the 'on' case compares a string to a float range and must
    not run."""
    pytester.makepyfile("""
        def test_vout_by_mode(verify):
            verify.conditional(
                "off",
                cases={
                    "on": verify.between("n/a", 3.2, 3.4, name="Vout"),
                    "off": verify.equal("n/a", "n/a", name="Vout off"),
                },
                name="Vout by mode",
            )
    """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_h2_module_path_length_in_unmatched_default_not_evaluated():
    """``length`` calls len() at build time, so it crashes even on the (otherwise lazy)
    module path when it sits in a branch that is never taken."""
    payload = None
    descriptor = verify.guard(
        branches=[(payload is None, "no payload", verify.is_none(payload, name="no payload"))],
        default=verify.length(payload, 3, name="payload length"),
        name="Payload",
    )
    assert verify.evaluate(descriptor) is True


def test_h2_unmatched_children_carry_no_failed_verdict(verify, request):
    """Children that were never selected must not keep a red ``passed: False`` inside a
    passing composite (a reporter would render them as failed cards)."""
    verify.conditional(
        1,
        cases={0: verify.equal(1, 2, name="zero"), 1: verify.equal(1, 1, name="one")},
        name="C",
    )
    verify.guard(
        [(True, "taken", verify.equal(1, 1, name="taken"))],
        default=verify.fail("unreachable"),
        name="G",
    )
    recorded = {r["name"]: r for r in get_check_results(request.node)}
    assert recorded["C"]["passed"] is True
    assert recorded["G"]["passed"] is True
    unmatched_case = next(
        (c for c in (recorded["C"].get("cases") or {}).values() if c.get("name") == "zero"),
        None,
    )
    assert not _carries_failed_verdict(unmatched_case), "unmatched case 0 keeps passed=False"
    assert not _carries_failed_verdict(recorded["G"].get("default")), (
        "unmatched guard default keeps passed=False"
    )


# ======================================================================
# H-3: composite parents re-evaluate children instead of using their verdicts
# ======================================================================

_ABC_CHILD_SOURCES = {
    "all_satisfy-Number": """
        import numbers

        def test_abc(verify):
            verify.all_satisfy(
                [1, 2.5, 3],
                lambda v: verify.is_instance(v, numbers.Number, name="num"),
                name="all numeric",
            )
    """,
    "guard-Mapping": """
        from collections.abc import Mapping

        def test_abc(verify):
            verify.guard([(True, "dict", verify.is_instance({}, Mapping, name="resp"))], name="g")
    """,
    "conditional-Sequence": """
        from collections.abc import Sequence

        def test_abc(verify):
            verify.conditional(
                "list", cases={"list": verify.is_instance([1], Sequence, name="p")}, name="c"
            )
    """,
}


@pytest.mark.parametrize(
    "source", list(_ABC_CHILD_SOURCES.values()), ids=list(_ABC_CHILD_SOURCES)
)
def test_h3_passing_abc_is_instance_children_pass_parent(pytester, source):
    """``is_instance`` against an ABC passes on its own (real isinstance), so the composite
    holding only passing children must pass too."""
    pytester.makepyfile(source)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


_SAME_NAME_PREAMBLE = """
    import pathlib

    class Path:  # unrelated class that merely shares pathlib.Path's __name__
        pass
"""

_SAME_NAME_SOURCES = {
    "all_satisfy": _SAME_NAME_PREAMBLE + """
    def test_same_name(verify):
        verify.all_satisfy(
            [Path(), Path()],
            lambda v: verify.is_instance(v, pathlib.Path, name="p"),
            name="paths",
        )
    """,
    "guard": _SAME_NAME_PREAMBLE + """
    def test_same_name(verify):
        verify.guard([(True, "local", verify.is_instance(Path(), pathlib.Path, name="p"))],
                     name="g")
    """,
    "conditional": _SAME_NAME_PREAMBLE + """
    def test_same_name(verify):
        verify.conditional(
            "x", cases={"x": verify.is_instance(Path(), pathlib.Path, name="p")}, name="c"
        )
    """,
}


@pytest.mark.parametrize(
    "source", list(_SAME_NAME_SOURCES.values()), ids=list(_SAME_NAME_SOURCES)
)
def test_h3_failing_same_name_children_fail_parent(pytester, source):
    """Every child fails (real isinstance), so the composite must fail. In 0.3.1 the parent's
    name-based re-check passes and the failing children are discarded: the test goes green."""
    pytester.makepyfile(source)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)


def test_h3_mutation_after_child_check_does_not_flip_parent(pytester):
    """The child passed when it was checked; mutating the object afterwards must not make
    the parent reach the opposite verdict."""
    pytester.makepyfile("""
        def test_steps(verify):
            queue = []

            def step(n):
                check = verify.equal(queue, [], name=f"queue empty before step {n}")
                queue.append(n)  # side effect after the check was recorded
                return check

            verify.all_satisfy([1], step, name="steps")
    """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_h3_failure_summary_counts_recorded_child_verdicts(pytester):
    """Two of three children pass (1 and 2.5 are Numbers); the summary must not recount
    them by name and report all three as failed."""
    pytester.makepyfile("""
        import numbers

        def test_mixed(verify):
            verify.all_satisfy(
                [1, 2.5, "x"],
                lambda v: verify.is_instance(v, numbers.Number, name=f"num {v!r}"),
                name="all numeric",
            )
    """)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.no_fnmatch_line("*got 3 failed*")


# ======================================================================
# H-4: a recorded check reused inside a composite is silently deleted
#
# Resolved in 0.4.0 as a documented rule rather than as the report proposed: a check passed to
# a composite belongs to it whenever it was built, because building ``cases`` or ``branches``
# in a variable first is common and looks exactly like reuse. A copy (``dict(check)``) keeps
# a check on its own as well.
# ======================================================================

@pytest.mark.parametrize("composite", ["conditional", "guard"])
def test_h4_children_built_before_the_call_belong_to_the_composite(pytester, composite):
    """Building the children in a variable first works like building them inline: an
    unselected child does not count (0.3.1 behaviour, kept)."""
    call = {
        "conditional": "verify.conditional(1, cases=children, name='Output')",
        "guard": "verify.guard(children, name='Output')",
    }[composite]
    children = {
        "conditional": """{
                0: verify.approx(3.3, 0.0, abs_tol=0.01, name="Standby", units="V"),
                1: verify.approx(3.3, 3.3, abs_tol=0.1, name="Active", units="V"),
            }""",
        "guard": """[
                (False, "standby", verify.approx(3.3, 0.0, abs_tol=0.01, name="Standby")),
                (True, "active", verify.approx(3.3, 3.3, abs_tol=0.1, name="Active")),
            ]""",
    }[composite]
    pytester.makepyfile(f"""
        from pytest_verify import get_check_results

        def test_output(verify, request):
            children = {children}
            {call}
            assert [c["name"] for c in get_check_results(request.node)] == ["Output"]
    """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_h4_a_copy_keeps_a_reused_check_on_its_own(pytester):
    """To use a recorded check on its own and inside a composite, pass a copy. The failing
    standalone check keeps failing the test, and the copy keeps its recorded verdict."""
    pytester.makepyfile("""
        from pytest_verify import get_check_results

        def test_reuse(verify, request):
            vout = verify.approx(4.1, 3.3, abs_tol=0.05, name="Vout", units="V")  # fails
            guard = verify.guard(
                [(True, "powered", dict(vout))],
                default=verify.is_true(True, name="unpowered ok"),
                name="Power check",
            )
            assert guard["passed"] is False  # the copy kept the recorded verdict
            names = [c["name"] for c in get_check_results(request.node)]
            assert names == ["Vout", "Power check"]
    """)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*2 of 2 checks failed*"])


# ======================================================================
# H-6: conditional matches cases by str(), not by Python equality
# ======================================================================

def _conditional_verdict(switch_value: object, cases: dict) -> bool:
    """Build a conditional whose default always fails, and evaluate it on the module path."""
    descriptor = verify.conditional(
        switch_value,
        cases=cases,
        default=verify.fail("fell through to default"),
        name="M",
    )
    return verify.evaluate(descriptor)


def test_h6_intenum_switch_matches_int_case_key():
    """Passed on 3.11+ in 0.3.1; on 3.9/3.10 str(Level.ONE) is 'Level.ONE' and falls through."""
    ok = verify.equal(1, 1, name="ok")
    assert _conditional_verdict(Level.ONE, {0: verify.fail("standby"), 1: ok}) is True


def test_h6_int_switch_matches_intenum_case_key():
    ok = verify.equal(1, 1, name="ok")
    assert _conditional_verdict(1, {Level.STANDBY: verify.fail("standby"), Level.ONE: ok}) is True


@pytest.mark.parametrize(
    ("switch_value", "case_key"),
    [(1.0, 1), (True, 1), (StrMode.ACTIVE, "active")],
    ids=["float-1.0-vs-int-1", "True-vs-int-1", "str-enum-vs-value"],
)
def test_h6_python_equal_switch_matches_case_key(switch_value, case_key):
    """A plain dict lookup finds these keys (``switch_value in cases``); so must conditional."""
    cases = {case_key: verify.equal(1, 1, name="ok")}
    assert switch_value in cases
    assert _conditional_verdict(switch_value, cases) is True


def test_h6_none_switch_does_not_match_string_none_key():
    """None is not equal to the string 'None', so the always-failing default applies."""
    cases = {"None": verify.equal(1, 1, name="literal 'None' case")}
    assert None not in cases
    assert _conditional_verdict(None, cases) is False


# ======================================================================
# L-1: case keys that collide after str() are silently overwritten
# ======================================================================

@pytest.mark.parametrize(
    ("switch_value", "other_key"),
    [(1, "1"), (True, "True"), (Color.RED, "Color.RED")],
    ids=["int-vs-str", "bool-vs-str", "enum-vs-str"],
)
def test_l1_colliding_case_keys_not_silently_overwritten(switch_value, other_key):
    """``{1: a, '1': b}`` must be rejected with ValueError at build time. If a fix instead
    keeps both keys, the case equal to the switch (``a``) must be the one selected."""
    cases = {
        switch_value: verify.equal(1, 1, name="case equal to the switch"),
        other_key: verify.fail("colliding case that overwrites it"),
    }
    try:
        descriptor = verify.conditional(switch_value, cases=cases, name="M")
    except (TypeError, ValueError):
        return  # rejected at build time: the documented fix
    assert verify.evaluate(descriptor) is True, "the colliding later key silently won"


def test_l1_overwritten_case_not_left_as_standalone_result(pytester):
    """The overwritten fixture child is never absorbed by the composite, so it stays a
    top-level failing check although it can never be selected."""
    recorded = _run_and_read_recorded(pytester, """
        def test_collision(verify, dump_recorded):
            try:
                verify.conditional(
                    2,
                    cases={
                        1: verify.fail("int-1 branch (never selected)"),
                        "1": verify.equal(1, 1, name="str-1 branch"),
                        2: verify.equal(1, 1, name="mode 2"),
                    },
                    name="Mode check",
                )
            except Exception:  # rejected at build time (ValueError is the documented fix)
                dump_recorded(rejected=True)
            else:
                dump_recorded(rejected=False)
    """)
    assert recorded["rejected"] or "int-1 branch (never selected)" not in recorded["names"], (
        recorded
    )


# ======================================================================
# L-3: an error in the composite (or its factory) leaves orphaned children
# ======================================================================

def test_l3_composite_evaluation_error_leaves_no_orphan_children(pytester):
    """The matched (module-built) child cannot be compared; the unmatched fixture-built
    branch must still not surface as a standalone failing check."""
    recorded = _run_and_read_recorded(pytester, """
        from pytest_verify import verify as mverify

        def test_parent_eval_raises(verify, dump_recorded):
            try:
                verify.guard(
                    [
                        (False, "unmatched", verify.equal(1, 2, name="unmatched branch")),
                        (True, "matched", mverify.greater(None, 5, name="matched (module)")),
                    ],
                    name="G",
                )
            except Exception:
                pass
            dump_recorded()
    """)
    assert "unmatched branch" not in recorded["names"], recorded


def test_l3_all_satisfy_factory_error_leaves_no_orphan_children(pytester):
    """The factory raises on the third item; the children it built for the first two items
    belong to the aborted all_satisfy and must not remain as standalone checks."""
    recorded = _run_and_read_recorded(pytester, """
        def test_factory_raises_midway(verify, dump_recorded):
            def factory(v):
                if v == 3:
                    raise KeyError("boom")
                return verify.greater(v, 1, name=f"v{v}")

            try:
                verify.all_satisfy([1, 2, 3], factory, name="partial")
            except Exception:  # a fix may record the error instead, or re-raise it wrapped
                pass
            dump_recorded()
    """)
    orphans = [n for n in recorded["names"] if n in ("v1", "v2")]
    assert orphans == [], recorded


# ======================================================================
# L-4: conditional renders the switch with format() but looks it up with str()
# ======================================================================

def _displayed_switches(descriptor: dict) -> list[str]:
    """Return every ``mode=<switch>`` value shown in the description and the summary."""
    rendered = descriptor["description"] + "\n" + _render(descriptor)
    return re.findall(r"\[mode=(.*?)(?: → .*?)?\]", rendered)


@pytest.mark.parametrize(
    ("switch_value", "case_key"),
    [(Level.ONE, 1), (StrMode.ACTIVE, "active")],
    ids=["IntEnum", "str-Enum"],
)
def test_l4_conditional_displays_the_switch_it_looked_up(switch_value, case_key):
    """On 3.9/3.10 the lookup uses str() ('Level.ONE', 'StrMode.ACTIVE') but the text uses
    format() ('1', 'active'), so it reads 'M [mode=1 → no match]' while case '1' exists.
    The shown switch must be the compared one: str(switch_value) or repr(switch_value)."""
    descriptor = verify.conditional(
        switch_value, cases={case_key: verify.equal(1, 1, name="ok")}, name="M"
    )
    allowed = {str(switch_value), repr(switch_value)}
    shown = _displayed_switches(descriptor)
    assert set(shown) <= allowed, (shown, _render(descriptor))


# ======================================================================
# M-6: conditional's evaluator ignores the stored matched_case
# ======================================================================

def _tuple_switch_round_trip() -> tuple[dict, dict]:
    ok = verify.equal(1, 1, name="ok")
    descriptor = verify.conditional((1, 2), cases={(1, 2): ok}, name="pair")
    return descriptor, json.loads(json.dumps(descriptor))


def test_m6_conditional_verdict_survives_json_round_trip():
    """A tuple switch becomes a list after JSON; the verdict must not change."""
    descriptor, round_tripped = _tuple_switch_round_trip()
    assert verify.evaluate(descriptor) is True
    assert verify.evaluate(round_tripped) is True


def test_m6_rendered_case_agrees_with_verdict_after_round_trip():
    """The summary renders the stored matched case (equal(1, 1)); the verdict must agree
    with it instead of reporting 'expected 1, got 1'."""
    _, round_tripped = _tuple_switch_round_trip()
    summary = _render(round_tripped)
    assert "expected 1, got 1" not in summary, summary
