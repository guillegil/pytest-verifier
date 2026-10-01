"""Regression tests: check evaluation and failure-summary rendering (bugs found in 0.3.1).

Every test here asserts the correct behaviour for a bug found by the 0.3.1 review
(see ``bugs-0.3.1.md``) and fixed in 0.4.0. Each one failed on 0.3.1 and keeps the bug
from coming back.

Bugs covered by this module:

* C-1: a non-bool comparison result is stored as ``passed``; elementwise (numpy-style)
  results crash the session with INTERNALERROR.
* C-2: building the failure summary inside ``pytest_runtest_makereport`` re-evaluates and
  ``str()``s live objects; any exception there is an INTERNALERROR.
* H-1: an exception while building or evaluating a fixture check aborts the test, so the
  offending check and every later check are lost.
* M-1: ``approx`` with ``rel_tol`` accepts values outside the advertised
  ``expected ± rel%`` band.
* L-2: ``approx`` coerces Decimal/Fraction/int to float.
* L-5: ints with more than ``sys.get_int_max_str_digits()`` digits crash description and
  summary rendering.

No third-party packages are used: numpy-style arrays, closed connections and detached ORM
rows are simulated with the small helper classes below. Their source is copied verbatim
into the inner pytester modules with :func:`inspect.getsource`.
"""
from __future__ import annotations

import inspect
import json
import sys
import textwrap
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from pytest_verify import verify as mverify


#: Python's int-to-str digit limit (0 = disabled, or an interpreter that predates it).
_INT_STR_LIMIT: int = getattr(sys, "get_int_max_str_digits", lambda: 0)()


# ── Helper objects (copied into inner pytester modules via inspect.getsource) ──


class _Elementwise(list):
    """Mimics a numpy bool array: its truth value is ambiguous."""

    def __bool__(self) -> bool:
        raise ValueError("The truth value of an array with more than one element is ambiguous.")


class _Arr(list):
    """Mimics a numpy array: ``==`` compares elementwise."""

    def __eq__(self, other: object) -> Any:  # type: ignore[override]
        return _Elementwise(a == b for a, b in zip(self, other))  # type: ignore[call-overload]

    __hash__ = None  # type: ignore[assignment]


class _Truthy:
    """Every comparison returns a truthy non-bool (like a lazy expression object)."""

    def __eq__(self, other: object) -> Any:  # type: ignore[override]
        return "yes"

    __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __eq__  # type: ignore[assignment]
    __hash__ = None  # type: ignore[assignment]


class _Conn:
    """A link that is unhealthy, and whose truth value raises once it is closed."""

    def __init__(self) -> None:
        self.open, self.healthy = True, False

    def close(self) -> None:
        self.open = False

    def __bool__(self) -> bool:
        if not self.open:
            raise RuntimeError("I/O operation on closed connection")
        return self.healthy


class _BadStr:
    """Never equal to anything; ``str()`` raises."""

    def __eq__(self, other: object) -> bool:
        return False

    __hash__ = object.__hash__

    def __str__(self) -> str:
        raise RuntimeError("str() not available")


class _Detached:
    """Like a detached ORM row: ``repr()`` (and so ``str()``) raises."""

    def __repr__(self) -> str:
        raise RuntimeError("DetachedInstanceError: session is closed")


class _BoolRaises:
    """Truth value cannot be determined at all."""

    def __bool__(self) -> bool:
        raise RuntimeError("truth value unavailable")


def _big_int() -> int:
    """Return an int with more digits than the active int-to-str limit allows."""
    limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    return 10 ** (max(limit, 4300) + 1)


_HELPERS = (_Elementwise, _Arr, _Truthy, _Conn, _BadStr, _Detached, _BoolRaises, _big_int)


def _inner_module(*bodies: str) -> str:
    """Build an inner test module: imports, the helper sources, then each of ``bodies``."""
    parts = [
        "import sys",
        "from typing import Any",
        "",
        "from pytest_verify import verify as mverify",
    ]
    parts += ["\n" + inspect.getsource(helper) for helper in _HELPERS]
    parts += ["\n" + textwrap.dedent(body) for body in bodies]
    return "\n".join(parts)


# Inner conftest that dumps what the verify fixture recorded right after the test body ran
# (before the makereport hook, so it survives an INTERNALERROR raised there). Only the
# public ``get_check_results`` API is used.
_RECORDING_CONFTEST = """
    import json

    import pytest

    from pytest_verify import get_check_results


    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(item):
        yield
        path = item.path.parent / "recorded_checks.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        data[item.name] = [
            {
                "name": r.get("name"),
                "passed": r.get("passed") if isinstance(r.get("passed"), bool) else None,
                "passed_type": type(r.get("passed")).__name__,
            }
            for r in get_check_results(item)
        ]
        path.write_text(json.dumps(data))
"""


def _recorded_checks(pytester: pytest.Pytester, test_name: str) -> list[dict[str, Any]]:
    """Return the ``[{name, passed, passed_type}]`` rows dumped for ``test_name``."""
    path = pytester.path / "recorded_checks.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    return data.get(test_name, [])


def _assert_session_completed(result: pytest.RunResult) -> None:
    """Assert the inner run ended with ordinary test failures, not an INTERNALERROR."""
    output = "\n".join(result.outlines + result.errlines)
    assert "INTERNALERROR" not in output
    assert result.ret == pytest.ExitCode.TESTS_FAILED


_TEST_AFTER = """

    def test_after():
        assert False, "a real failure in a later test must still be reported"
"""


# ── C-1: non-bool comparison results ────────────────────────────────


def test_c1_elementwise_comparison_fails_test_without_internalerror(pytester):
    """An ambiguous (numpy-style) comparison fails its test; every other test still runs."""
    pytester.makepyfile(_inner_module("""
        def test_1_array(verify):
            verify.equal(_Arr([1, 2]), _Arr([1, 3]), name="waveform")


        def test_2_unrelated_pass(verify):
            verify.equal(1, 1, name="unrelated")
    """, _TEST_AFTER))
    result = pytester.runpytest_subprocess()
    _assert_session_completed(result)
    result.assert_outcomes(passed=1, failed=2)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param('verify.equal(_Truthy(), 5, name="expr")', id="equal"),
        pytest.param('verify.not_equal(_Truthy(), 5, name="expr")', id="not_equal"),
        pytest.param('verify.greater(_Truthy(), 5, name="expr")', id="greater"),
        pytest.param('verify.greater_equal(_Truthy(), 5, name="expr")', id="greater_equal"),
        pytest.param('verify.less(_Truthy(), 5, name="expr")', id="less"),
        pytest.param('verify.less_equal(_Truthy(), 5, name="expr")', id="less_equal"),
        pytest.param('verify.between(_Truthy(), 1, 10, name="expr")', id="between"),
    ],
)
def test_c1_fixture_stores_non_bool_comparison_result_as_bool(pytester, call):
    """The fixture's recorded ``passed`` is a real bool whatever ``__eq__``/``__gt__`` return."""
    pytester.makeconftest(_RECORDING_CONFTEST)
    pytester.makepyfile(_inner_module(f"""
        def test_inner(verify):
            {call}
    """))
    pytester.runpytest()
    recorded = _recorded_checks(pytester, "test_inner")
    assert [r["passed_type"] for r in recorded] == ["bool"]


def test_c1_module_evaluate_detailed_passed_is_bool_for_truthy_result():
    """``evaluate_detailed`` reports ``passed`` as a bool, not the raw ``__eq__`` result."""
    descriptor = mverify.equal(_Truthy(), 5, name="expr")
    [result] = mverify.evaluate_detailed(descriptor)
    assert isinstance(result["passed"], bool), repr(result["passed"])


def test_c1_module_ambiguous_comparison_evaluates_as_failed():
    """An ambiguous elementwise comparison is a failed check on the module path too."""
    descriptor = mverify.equal(_Arr([1, 2]), _Arr([1, 3]), name="waveform")
    [result] = mverify.evaluate_detailed(descriptor)
    assert result["passed"] is False, repr(result["passed"])
    assert mverify.evaluate(descriptor) is False


# ── C-2: failure summary must never raise inside the makereport hook ──

_C2_SCENARIOS = {
    "bool-raises-after-cleanup": """
        def test_scenario(verify):
            conn = _Conn()
            verify.is_true(conn, name="link healthy")  # fails softly: link is unhealthy
            conn.close()  # ordinary cleanup; bool(conn) raises from now on
    """,
    "str-raises": """
        def test_scenario(verify):
            verify.equal(_BadStr(), 1, name="X")
    """,
    "repr-raises": """
        def test_scenario(verify):
            verify.is_none(_Detached(), name="row")
    """,
    "all-satisfy-unevaluated-child": """
        def test_scenario(verify):
            # all() short-circuits on -1 at record time, leaving the None child unevaluated.
            verify.all_satisfy(
                [-1, None], lambda v: mverify.greater(v, 0, name="r"), name="readings"
            )
    """,
    "all-satisfy-factory-returns-none": """
        def test_scenario(verify):
            verify.all_satisfy(
                [-1, 1],
                lambda v: mverify.greater(v, 0, name="r") if v < 0 else None,
                name="readings",
            )
    """,
}


@pytest.mark.parametrize("scenario", list(_C2_SCENARIOS))
def test_c2_failure_summary_never_crashes_session(pytester, scenario):
    """A soft failure whose live values misbehave later still fails only its own test."""
    pytester.makepyfile(_inner_module(_C2_SCENARIOS[scenario], _TEST_AFTER))
    result = pytester.runpytest_subprocess()
    _assert_session_completed(result)
    result.assert_outcomes(failed=2)


# ── H-1: odd inputs must be recorded as failed checks, not abort the test ──

# id -> (call recorded under the name "odd", expected verdict; None = either bool is fine
# because a correct fix may legitimately support that input).
_H1_CASES: dict[str, tuple[str, bool | None]] = {
    "greater-none": ('verify.greater(None, 100, name="odd", units="Mbps")', False),
    "approx-none": ('verify.approx(None, 3.3, abs_tol=0.05, name="odd")', False),
    "approx-negative-tol": ('verify.approx(3.3, 3.3, abs_tol=-0.1, name="odd")', False),
    "between-str": ('verify.between("3.3", 3.2, 3.4, name="odd")', False),
    "matches-none": ('verify.matches(None, "ok", name="odd")', False),
    "matches-invalid-regex": ('verify.matches("abc", "[a-", name="odd")', False),
    "contains-none-haystack": ('verify.contains(None, "x", name="odd")', False),
    "is-true-bool-raises": ('verify.is_true(_BoolRaises(), name="odd")', False),
    "length-none": ('verify.length(None, 3, name="odd")', False),
    "length-generator": ('verify.length((x for x in range(3)), 3, name="odd")', None),
    "is-instance-tuple": ('verify.is_instance(1, (int, float), name="odd")', None),
    "all-satisfy-none": (
        'verify.all_satisfy(None, lambda x: mverify.greater(x, 0, name="c"), name="odd")',
        False,
    ),
}

# Inputs that the planned builder validation (IMP-1 / IMP-2 in ``improvements-and-ideas.md``)
# may instead reject when the check is built, with TypeError/ValueError. Such a usage error is
# an equally valid fix, so it is accepted here. In 0.3.1 both of these built without complaint.
_H1_BUILD_TIME_REJECTION_OK = {
    "approx-negative-tol": lambda: mverify.approx(3.3, 3.3, abs_tol=-0.1, name="odd"),
    "between-str": lambda: mverify.between("3.3", 3.2, 3.4, name="odd"),
}


@pytest.mark.parametrize("case", list(_H1_CASES))
def test_h1_odd_input_is_recorded_and_later_checks_still_run(pytester, case):
    """The odd-input check is recorded and the checks after it still run."""
    call, verdict = _H1_CASES[case]
    if case in _H1_BUILD_TIME_REJECTION_OK:
        try:
            _H1_BUILD_TIME_REJECTION_OK[case]()
        except (TypeError, ValueError):
            return  # rejected as a usage error when built: also resolves the bug
    pytester.makeconftest(_RECORDING_CONFTEST)
    pytester.makepyfile(_inner_module(f"""
        def test_inner(verify):
            verify.equal(1, 2, name="first (fails softly)")
            {call}
            verify.equal(1, 1, name="subsequent check")
    """))
    pytester.runpytest_subprocess()
    recorded = _recorded_checks(pytester, "test_inner")
    assert [r["name"] for r in recorded] == ["first (fails softly)", "odd", "subsequent check"]
    assert [r["passed_type"] for r in recorded] == ["bool", "bool", "bool"]
    assert recorded[0]["passed"] is False
    assert recorded[2]["passed"] is True
    if verdict is not None:
        assert recorded[1]["passed"] is verdict


# ── M-1: approx rel_tol must honour the advertised 'expected ± rel%' band ──


@pytest.mark.parametrize(
    ("rel_tol", "actual"),
    [
        pytest.param(0.095, 110, id="9.5pct-110"),
        pytest.param(0.1, 111, id="10pct-111"),
        pytest.param(0.5, 200, id="50pct-200"),
        pytest.param(1.0, 1e9, id="100pct-1e9"),
        pytest.param(1.0, 1e300, id="100pct-1e300"),
        pytest.param(2.0, -1e300, id="200pct-neg-1e300"),
    ],
)
def test_m1_rel_tol_verdict_matches_advertised_band(rel_tol, actual):
    """``actual`` lies outside ``100 ± rel%``, so either the check fails (as with
    ``pytest.approx``) or the description stops advertising that band."""
    descriptor = mverify.approx(actual, 100, rel_tol=rel_tol, name="Gain")
    pct = f"{rel_tol * 100:.10g}"
    claims_band = f"== 100 ± {pct}%" in descriptor["description"]
    assert abs(actual - 100) > rel_tol * 100  # outside the stated band
    assert actual != pytest.approx(100, rel=rel_tol, abs=0)  # pytest.approx rejects it too
    assert mverify.evaluate(descriptor) is False or not claims_band, descriptor["description"]


# ── L-2: approx must compare Decimal / Fraction / int exactly ──


@pytest.mark.parametrize(
    ("actual", "expected", "abs_tol", "exact_verdict"),
    [
        # |1.1 - 1.0| == 0.1 exactly: on the boundary, so the check passes.
        pytest.param(Decimal("1.1"), Decimal("1.0"), Decimal("0.1"), True, id="decimal-boundary"),
        pytest.param(Fraction(11, 10), Fraction(1), Fraction(1, 10), True, id="fraction-boundary"),
        # A 1e-25 difference is invisible to float but is still larger than abs_tol=0.
        pytest.param(
            Decimal("0.1") + Decimal("1e-25"), Decimal("0.1"), 0, False, id="decimal-tiny-diff"
        ),
    ],
)
def test_l2_approx_compares_exact_numeric_types_exactly(actual, expected, abs_tol, exact_verdict):
    """``abs(actual - expected) <= abs_tol`` is decided in the native exact type."""
    descriptor = mverify.approx(actual, expected, abs_tol=abs_tol, name="price")
    assert mverify.evaluate(descriptor) is exact_verdict


def test_l2_approx_huge_int_does_not_overflow():
    """Ints too large for a C double are still compared (here: equal, so it passes)."""
    descriptor = mverify.approx(10**400, 10**400, abs_tol=1, name="x")
    assert mverify.evaluate(descriptor) is True


# ── L-5: ints above the int-to-str digit limit ──


@pytest.mark.parametrize(
    ("check", "args"),
    [
        pytest.param("equal", lambda n: (n, n), id="equal"),
        pytest.param("not_equal", lambda n: (n, n + 1), id="not_equal"),
        pytest.param("greater", lambda n: (n + 1, n), id="greater"),
        pytest.param("greater_equal", lambda n: (n, n), id="greater_equal"),
        pytest.param("less", lambda n: (n, n + 1), id="less"),
        pytest.param("less_equal", lambda n: (n, n), id="less_equal"),
        pytest.param("between", lambda n: (n, n - 1, n + 1), id="between"),
        pytest.param("contains", lambda n: ([n], n), id="contains"),
        pytest.param("not_contains", lambda n: ([0], n), id="not_contains"),
    ],
)
def test_l5_passing_check_on_huge_int_builds_and_passes(check, args):
    """A check that holds on a huge int builds its descriptor and evaluates to True."""
    descriptor = getattr(mverify, check)(*args(_big_int()), name="n")
    assert mverify.evaluate(descriptor) is True


def test_l5_fixture_passing_checks_on_huge_int_let_test_continue(pytester):
    """Through the fixture, the passing check does not abort the test and later checks run."""
    pytester.makepyfile(_inner_module("""
        def test_huge(verify):
            n = _big_int()
            verify.equal(n, _big_int(), name="huge")
            verify.greater(n, 0, name="positive")
    """))
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_l5_failing_check_on_huge_int_fails_test_without_internalerror(pytester):
    """A failed check holding a huge int renders its summary safely; later tests still run."""
    pytester.makepyfile(_inner_module("""
        def test_huge(verify):
            verify.is_none(_big_int(), name="n")
    """, _TEST_AFTER))
    result = pytester.runpytest_subprocess()
    _assert_session_completed(result)
    result.assert_outcomes(failed=2)
