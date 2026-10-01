"""Unit tests for the ``ChecksFailedError`` message format (spec §7).

Covers both the normative structure (the ``N of M checks failed`` header, the
``✗``/``✓`` markers, the ``[seq]`` index, the name, failed-before-passed ordering)
and the per-type ``expected … got …`` / compact detail rendering.
"""

from tests._builders import (
    build_approx,
    build_between,
    build_conditional,
    build_contains,
    build_equal,
    build_greater,
    build_is_instance,
    build_is_true,
    build_length,
)
from pytest_verifier._exceptions import ChecksFailedError


def _evaluated(builder, *args, passed, **kwargs):
    d = builder(*args, **kwargs)
    d["passed"] = passed
    return d


class TestChecksFailedErrorMessage:
    def test_header_counts_failed_over_total(self):
        results = [
            _evaluated(build_equal, 1, 1, name="A", passed=True),
            _evaluated(build_equal, 1, 2, name="B", passed=False),
            _evaluated(build_equal, 3, 3, name="C", passed=True),
        ]
        msg = str(ChecksFailedError(results))
        assert msg.startswith("1 of 3 checks failed")

    def test_failed_marker_and_index_and_name(self):
        results = [
            _evaluated(build_equal, 1, 1, name="Pass1", passed=True),
            _evaluated(build_greater, 1, 5, name="Fail1", passed=False),
        ]
        msg = str(ChecksFailedError(results))
        assert "✗ [1] Fail1" in msg
        assert "✓ [0] Pass1" in msg

    def test_failed_listed_before_passed(self):
        results = [
            _evaluated(build_equal, 1, 1, name="GoodOne", passed=True),
            _evaluated(build_between, 9, 0, 1, name="BadOne", passed=False),
        ]
        msg = str(ChecksFailedError(results))
        assert msg.index("BadOne") < msg.index("GoodOne")

    def test_failed_detail_shows_actual_value(self):
        results = [_evaluated(build_greater, 3, 100, name="Throughput", units="Mbps", passed=False)]
        msg = str(ChecksFailedError(results))
        # The actual measured value must appear so the failure is diagnosable.
        assert "got 3Mbps" in msg

    def test_no_old_section_headers(self):
        results = [_evaluated(build_equal, 1, 2, name="X", passed=False)]
        msg = str(ChecksFailedError(results))
        assert "FAILED checks:" not in msg
        assert "PASSED checks:" not in msg


class TestChecksFailedErrorPerTypeDetail:
    """Byte-exact per-type ``expected … got …`` / compact rendering (spec §7 table)."""

    def _line(self, result):
        return str(ChecksFailedError([result])).splitlines()[-1]

    def test_equal_failed(self):
        r = _evaluated(build_equal, 1, 2, name="Code", passed=False)
        assert self._line(r) == "  ✗ [0] Code — expected 2, got 1"

    def test_approx_failed(self):
        r = _evaluated(build_approx, 3.8, 3.3, abs_tol=0.05, name="Output voltage", units="V", passed=False)
        assert self._line(r) == "  ✗ [0] Output voltage — expected 3.3V ± 0.05V, got 3.8V"

    def test_between_failed(self):
        r = _evaluated(build_between, 0.7, 0.1, 0.5, name="Current draw", units="A", passed=False)
        assert self._line(r) == "  ✗ [0] Current draw — expected [0.1A, 0.5A], got 0.7A"

    def test_greater_passed(self):
        r = _evaluated(build_greater, 87, 85, name="Efficiency", units="%", passed=True)
        assert self._line(r) == "  ✓ [0] Efficiency — 87% > 85%"

    def test_is_true_passed(self):
        r = _evaluated(build_is_true, True, name="PSU stable", passed=True)
        assert self._line(r) == "  ✓ [0] PSU stable — True"

    def test_contains_failed(self):
        r = _evaluated(build_contains, "log", "xyz", name="Log", passed=False)
        assert self._line(r) == "  ✗ [0] Log — expected to contain 'xyz', got 'log'"

    def test_length_failed(self):
        r = _evaluated(build_length, [1, 2], 3, name="Items", passed=False)
        assert self._line(r) == "  ✗ [0] Items — expected length 3, got length 2"

    def test_is_instance_failed(self):
        r = _evaluated(build_is_instance, [], dict, name="Data", passed=False)
        assert self._line(r) == "  ✗ [0] Data — expected instance of dict, got list"

    def test_conditional_passed_matches_spec_example(self):
        child = build_approx(3.29, 3.3, abs_tol=0.1, name="Active", units="V")
        r = _evaluated(build_conditional, 1, cases={"1": child}, name="Mode output", passed=True)
        assert self._line(r) == "  ✓ [0] Mode output [mode=1 → Active] — 3.29V == 3.3V ± 0.1V"

    def test_not_equal_failed(self):
        from tests._builders import build_not_equal

        r = build_not_equal(5, 5, name="Code")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] Code — expected ≠ 5, got 5"

    def test_less_equal_passed(self):
        from tests._builders import build_less_equal

        r = build_less_equal(50, 50, name="Ripple", units="mV")
        r["passed"] = True
        assert self._line(r) == "  ✓ [0] Ripple — 50mV <= 50mV"

    def test_is_not_none_failed_shows_repr(self):
        from tests._builders import build_is_not_none

        r = build_is_not_none(None, name="Response")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] Response — expected not None, got None"

    def test_matches_failed_shows_pattern_and_repr(self):
        from tests._builders import build_matches

        r = build_matches("abc", r"\d+", name="Firmware")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] Firmware — expected to match /\\d+/, got 'abc'"

    def test_all_satisfy_failed_counts_children(self):
        from tests._builders import build_all_satisfy

        r = build_all_satisfy([1, -2, 3, -4], lambda x: build_greater(x, 0, name=f"i{x}"), name="Positives")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] Positives — expected all 4 to pass, got 2 failed"

    def test_fail_renders_msg(self):
        from tests._builders import build_fail

        r = build_fail("power rail down", name="Rail")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] Rail — FAIL: power rail down"

    def test_is_false_failed(self):
        from tests._builders import build_is_false

        r = build_is_false(True, name="No error")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] No error — expected False, got True"

    def test_is_none_failed_shows_repr(self):
        from tests._builders import build_is_none

        r = build_is_none(5, name="Err")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] Err — expected None, got 5"

    def test_not_contains_failed(self):
        from tests._builders import build_not_contains

        r = build_not_contains("CRITICAL log", "CRITICAL", name="Errors")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] Errors — expected to not contain 'CRITICAL', got 'CRITICAL log'"

    def test_between_exclusive_failed_uses_parens(self):
        r = _evaluated(build_between, 0.5, 0.1, 0.5, inclusive=False, name="I", units="A", passed=False)
        assert self._line(r) == "  ✗ [0] I — expected (0.1A, 0.5A), got 0.5A"

    def test_conditional_no_match_no_default(self):
        child = build_approx(3.29, 3.3, abs_tol=0.1, name="Active", units="V")
        r = _evaluated(build_conditional, 99, cases={"1": child}, name="Mode", passed=False)
        assert self._line(r) == "  ✗ [0] Mode [mode=99 → no match]"

    def test_conditional_default_branch(self):
        from tests._builders import build_fail

        child = build_approx(3.29, 3.3, abs_tol=0.1, name="Active", units="V")
        r = _evaluated(
            build_conditional,
            99,
            cases={"1": child},
            default=build_fail("unknown mode", name="Fallback"),
            name="Mode",
            passed=False,
        )
        assert self._line(r) == "  ✗ [0] Mode [mode=99 → Fallback] — FAIL: unknown mode"

    def test_guard_matched_branch(self):
        from tests._builders import build_guard

        r = build_guard(
            [(False, "below floor", build_equal(1, 2, name="lo")),
             (True, "in range", build_equal(5, 5, name="hi"))],
            name="Sensor output",
        )
        r["passed"] = True
        assert self._line(r) == "  ✓ [0] Sensor output [→ in range] — 5 == 5"

    def test_guard_default_branch(self):
        from tests._builders import build_guard

        r = build_guard(
            [(False, "below floor", build_equal(1, 2, name="lo"))],
            default=build_approx(10.2, 10.0, abs_tol=0.5, name="formula", units="ns"),
            name="Sensor output",
        )
        r["passed"] = True
        assert self._line(r) == "  ✓ [0] Sensor output [→ default] — 10.2ns == 10.0ns ± 0.5ns"

    def test_guard_no_match_no_default(self):
        from tests._builders import build_guard

        r = build_guard([(False, "below floor", build_equal(1, 2, name="lo"))], name="Sensor output")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] Sensor output [→ no match]"

    def test_guard_matched_index_zero(self):
        # Index 0 must render (regression guard against `if matched` vs `is not None`).
        from tests._builders import build_guard

        r = build_guard([(True, "only", build_equal(1, 2, name="x"))], name="Sensor")
        r["passed"] = False
        assert self._line(r) == "  ✗ [0] Sensor [→ only] — expected 2, got 1"
