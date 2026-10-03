"""``verify.eventually`` and ``verify.stable``: one check sampled again and again over time.

Covers what the record keeps (``tries``, ``settled_at``, ``elapsed``, the kept try as the only
child, the bounded ``trace``) and its detail; the schedule (each try one interval after the
start of the last, no burst after a slow read, the last try at the deadline); how ``stable``
picks the try it keeps; samples that raise, usage errors, samples that return something that is
not a fresh check; invalid ``timeout``/``duration``/``interval``; ``pytest.skip``,
``pytest.exit`` and ``KeyboardInterrupt`` going on with no try left behind; the try scopes of
the run (a dropped try takes its checks with it); ``verify.require`` and ``--verify-fail-fast``;
the builder path (``checks.eventually``/``checks.stable``) and the unused-check warning;
nesting; the warning in a running event loop; margin delegation; JSON safety.

Every test runs on a fake clock: ``_sampling._clock`` and ``_sampling._sleep`` are replaced, and
a sample can move the clock on to simulate a slow read. Nothing here sleeps for real.
"""
from __future__ import annotations

import asyncio
import bdb
import datetime
import json
import math
import re
import unittest
import warnings
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, List, Sequence, Union

import pytest

from pytest_verifier import ChecksFailedError, checks
from pytest_verifier._checks import _sampling, margin, render_detail
from pytest_verifier._run import Run, recording_verify

ROOT = Path(__file__).resolve().parent.parent

#: Where the fake clock starts: trace times are relative to the start of the sampling.
_ORIGIN = 1000.0


class FakeClock:
    """A monotonic clock that moves only when slept on, or when a sample says a read took time."""

    def __init__(self) -> None:
        self.now = _ORIGIN
        self.sleeps: List[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0, "the sampling never sleeps for nothing"
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(_sampling, "_clock", fake)
    monkeypatch.setattr(_sampling, "_sleep", fake.sleep)
    return fake


class Reader:
    """A reading that changes over time: each call returns the next of *values* (then the last
    one again) and takes *takes* seconds of the fake clock (one number, or one per read)."""

    def __init__(
        self, clock: FakeClock, values: Sequence[Any], takes: Union[float, List[float]] = 0.0
    ) -> None:
        self.clock = clock
        self.values = list(values)
        self.takes = takes
        #: When each read started, in seconds from the clock's origin.
        self.starts: List[float] = []

    def __call__(self) -> Any:
        index = len(self.starts)
        self.starts.append(round(self.clock.now - _ORIGIN, 6))
        takes = self.takes
        if isinstance(takes, list):
            takes = takes[min(index, len(takes) - 1)]
        self.clock.now += takes
        return self.values[min(index, len(self.values) - 1)]


def _recording(**kwargs: Any):
    run = Run(**kwargs)
    run.phase = "call"
    return run, recording_verify(run)


def _names(run: Run) -> List[str]:
    return [record["name"] for record in run.records]


def _round_trip(record: Any) -> Any:
    return json.loads(json.dumps(record, allow_nan=False))


def _broken_read() -> float:
    raise OSError("no reading")


#: The line that raises in :func:`_broken_read`, as a rootdir-relative location.
_BROKEN_AT = f"tests/test_sampling.py:{_broken_read.__code__.co_firstlineno + 1}"


# ---------------------------------------------------------------------------
# eventually: what it records
# ---------------------------------------------------------------------------


class TestEventually:
    def test_passes_on_the_first_try(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [30])
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=5, interval=1, name="Cool"
        )
        assert record["passed"] is True
        assert record["description"] == "Verify 'Cool' passes within 5 s (every 1 s)"
        assert (record["timeout"], record["interval"]) == (5.0, 1.0)
        assert (record["tries"], record["settled_at"], record["elapsed"]) == (1, 0.0, 0.0)
        assert record["trace"] == [[0.0, 30, True]]
        [child] = record["child_checks"]
        assert (child["name"], child["actual"], child["passed"]) == ("T", 30, True)
        assert record["detail"] == "passed at try 1 (0 s): T — 30 < 40"
        assert clock.sleeps == []
        assert run.records == [record]

    def test_passes_on_a_later_try_and_keeps_that_try(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [50, 45, 39, 10])
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=5, interval=0.5, name="Cool"
        )
        assert record["passed"] is True
        assert (record["tries"], record["settled_at"], record["elapsed"]) == (3, 1.0, 1.0)
        assert record["trace"] == [[0.0, 50, False], [0.5, 45, False], [1.0, 39, True]]
        [child] = record["child_checks"]
        assert (child["actual"], child["passed"]) == (39, True)
        assert record["detail"] == "passed at try 3 (1 s): T — 39 < 40"
        assert read.starts == [0.0, 0.5, 1.0]  # no try after the one that passed
        assert clock.sleeps == [0.5, 0.5]
        assert _names(run) == ["Cool"]  # the failed tries are gone

    def test_settled_at_is_the_start_of_the_passing_try(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [50, 30], takes=0.25)
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=5, interval=1, name="Cool"
        )
        assert record["settled_at"] == 1.0
        assert record["elapsed"] == 1.25  # the passing read took time too
        assert record["detail"] == "passed at try 2 (1 s): T — 30 < 40"

    def test_never_passing_keeps_the_last_try(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [50, 48, 46, 44, 42])
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=1, interval=0.25, name="Cool"
        )
        assert record["passed"] is False
        assert (record["tries"], record["settled_at"], record["elapsed"]) == (5, None, 1.0)
        assert [entry[1] for entry in record["trace"]] == [50, 48, 46, 44, 42]
        [child] = record["child_checks"]
        assert (child["actual"], child["passed"]) == (42, False)
        assert record["detail"] == (
            "never passed in 1 s (5 tries), last: T — expected < 40, got 42"
        )
        assert "error" not in record and "sample_error" not in record
        assert _names(run) == ["Cool"]

    def test_a_value_that_never_changed_gets_a_note(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.eventually(
            lambda: verify.less(50, 40, name="T"), timeout=1, interval=0.25, name="Cool"
        )
        assert record["detail"] == (
            "never passed in 1 s (5 tries), last: T — expected < 40, got 50; the value never "
            "changed in 5 tries: is it read inside the sample?"
        )

    def test_no_unchanged_note_with_fewer_than_three_tries(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.eventually(
            lambda: verify.less(50, 40, name="T"), timeout=0.25, interval=0.25, name="Cool"
        )
        assert record["tries"] == 2
        assert "never changed" not in record["detail"]

    def test_no_unchanged_note_when_the_value_changed_in_the_middle(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [50] * 50 + [45] * 100 + [50] * 50)
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=49.75, interval=0.25, name="Cool"
        )
        assert record["tries"] == 200
        assert "never changed" not in record["detail"]

    def test_timeout_zero_tries_once(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [50, 30])
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=0, interval=1, name="Cool"
        )
        assert (record["tries"], record["passed"], record["elapsed"]) == (1, False, 0.0)
        assert record["detail"] == "never passed in 0 s (1 try), last: T — expected < 40, got 50"
        assert clock.sleeps == []
        assert record["timeout"] == 0.0


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------


class TestSchedule:
    def test_the_next_try_is_one_interval_after_the_start_of_the_last(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [50], takes=0.25)
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=3, interval=1, name="Cool"
        )
        assert read.starts == [0.0, 1.0, 2.0, 3.0]
        assert clock.sleeps == [0.75, 0.75, 0.75]  # the read time is not added to the interval
        assert [entry[0] for entry in record["trace"]] == [0.0, 1.0, 2.0, 3.0]
        assert record["elapsed"] == 3.25

    def test_a_slow_read_does_not_cause_a_burst(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [50], takes=[2.5, 0.25])
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=5, interval=1, name="Cool"
        )
        # Late after the slow read: the next try at once, then one interval after its start.
        # Missed slots are not made up (no 2.5, 2.5, 2.5).
        assert read.starts == [0.0, 2.5, 3.5, 4.5, 5.0]
        assert clock.sleeps == [0.75, 0.75, 0.25]
        assert [entry[0] for entry in record["trace"]] == read.starts

    def test_the_last_try_starts_at_the_deadline(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [50])
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=1, interval=0.75, name="Cool"
        )
        assert read.starts == [0.0, 0.75, 1.0]
        assert clock.sleeps == [0.75, 0.25]
        assert record["tries"] == 3

    def test_an_interval_longer_than_the_timeout_tries_at_the_deadline(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [50])
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=1, interval=5, name="Cool"
        )
        assert read.starts == [0.0, 1.0]
        assert record["tries"] == 2

    def test_a_try_that_starts_before_the_deadline_is_the_last(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [50], takes=0.75)
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=1, interval=0.5, name="Cool"
        )
        assert read.starts == [0.0, 0.75]  # the second try ends after the deadline: no more
        assert clock.sleeps == []
        assert (record["tries"], record["elapsed"]) == (2, 1.5)


# ---------------------------------------------------------------------------
# stable
# ---------------------------------------------------------------------------


class TestStable:
    def test_holding_takes_at_least_two_tries(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [30])
        record = verify.stable(
            lambda: verify.less(read(), 40, name="T"), duration=0.1, interval=1, name="Steady"
        )
        assert record["passed"] is True
        assert record["description"] == "Verify 'Steady' holds for 0.1 s (every 1 s)"
        assert (record["duration"], record["interval"]) == (0.1, 1.0)
        assert read.starts == [0.0, 0.1]
        assert (record["tries"], record["elapsed"]) == (2, 0.1)
        assert record["detail"] == "held for 0.1 s (2 tries), closest: T — 30 < 40"
        assert "settled_at" not in record
        assert _names(run) == ["Steady"]

    def test_duration_zero_tries_once(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.stable(lambda: verify.less(30, 40, name="T"), duration=0, name="Steady")
        assert (record["tries"], record["passed"]) == (1, True)
        assert clock.sleeps == []

    def test_it_runs_until_a_try_starts_at_the_duration(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [30])
        record = verify.stable(
            lambda: verify.less(read(), 40, name="T"), duration=1, interval=0.25, name="Steady"
        )
        assert read.starts == [0.0, 0.25, 0.5, 0.75, 1.0]
        assert (record["tries"], record["elapsed"]) == (5, 1.0)
        assert len(record["trace"]) == 5

    def test_it_keeps_the_passing_try_closest_to_its_limit(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [30, 38, 35])
        record = verify.stable(
            lambda: verify.less(read(), 40, name="T"), duration=1, interval=0.5, name="Steady"
        )
        assert record["passed"] is True
        [child] = record["child_checks"]
        assert child["actual"] == 38
        assert record["detail"] == "held for 1 s (3 tries), closest: T — 38 < 40"
        assert margin(record) == 2
        assert record["trace"] == [[0.0, 30, True], [0.5, 38, True], [1.0, 35, True]]
        assert _names(run) == ["Steady"]  # the other passing tries are dropped

    def test_without_margins_it_keeps_the_last_passing_try(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [1, 2, 3])
        record = verify.stable(
            lambda: verify.is_true(read(), name="On"), duration=1, interval=0.5, name="Steady"
        )
        [child] = record["child_checks"]
        assert child["actual"] == 3
        assert record["detail"] == "held for 1 s (3 tries), closest: On — 3 (truthy)"

    def test_it_fails_at_the_first_failed_try(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [30, 35, 45, 30])
        record = verify.stable(
            lambda: verify.less(read(), 40, name="T"), duration=10, interval=0.5, name="Steady"
        )
        assert record["passed"] is False
        assert read.starts == [0.0, 0.5, 1.0]  # no try after the failed one
        assert (record["tries"], record["elapsed"]) == (3, 1.0)
        [child] = record["child_checks"]
        assert (child["actual"], child["passed"]) == (45, False)
        assert record["detail"] == "failed at try 3 (1 s): T — expected < 40, got 45"
        assert record["trace"][-1] == [1.0, 45, False]
        assert _names(run) == ["Steady"]

    def test_a_sample_that_raises_fails_it_at_once(self, clock: FakeClock):
        _, verify = _recording(rootdir=str(ROOT))
        record = verify.stable(
            lambda: verify.less(_broken_read(), 40, name="T"), duration=5, name="Steady"
        )
        assert (record["tries"], record["passed"]) == (1, False)
        assert record["child_checks"] == []
        assert record["sample_error"] == f"raised OSError: no reading (at {_BROKEN_AT})"
        assert record["detail"] == (
            f"failed at try 1 (0 s): the sample raised OSError: no reading (at {_BROKEN_AT})"
        )


# ---------------------------------------------------------------------------
# The trace
# ---------------------------------------------------------------------------


class TestTrace:
    def test_it_keeps_the_first_and_last_50_tries(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, list(range(100, 300)))
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=49.75, interval=0.25, name="Cool"
        )
        assert record["tries"] == 200
        trace = record["trace"]
        assert len(trace) == 100
        kept = list(range(50)) + list(range(150, 200))
        assert trace == [[n * 0.25, 100 + n, False] for n in kept]

    def test_stable_trace_is_bounded_too(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.stable(
            lambda: verify.less(1, 40, name="T"), duration=59.75, interval=0.25, name="Steady"
        )
        assert record["tries"] == 240
        assert len(record["trace"]) == 100
        assert record["trace"][49][0] == 12.25 and record["trace"][50][0] == 47.5

    def test_a_value_that_is_not_a_plain_scalar_is_none(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.eventually(
            lambda: verify.length([1, 2], 3, name="n"), timeout=0, name="Count"
        )
        assert record["trace"] == [[0.0, None, False]]

    def test_a_try_without_a_check_has_no_value(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.eventually(_broken_read, timeout=0.25, interval=0.25, name="Cool")
        assert record["trace"] == [[0.0, None, False], [0.25, None, False]]


# ---------------------------------------------------------------------------
# Samples that raise
# ---------------------------------------------------------------------------


class TestSampleErrors:
    def test_a_raising_sample_fails_that_try_and_the_tries_go_on(self, clock: FakeClock):
        run, verify = _recording()
        calls: List[int] = []

        def sample():
            calls.append(1)
            if len(calls) < 3:
                _broken_read()
            return verify.less(30, 40, name="T")

        record = verify.eventually(sample, timeout=5, interval=0.5, name="Cool")
        assert (record["passed"], record["tries"], record["settled_at"]) == (True, 3, 1.0)
        assert "sample_error" not in record and "error" not in record
        assert record["trace"] == [[0.0, None, False], [0.5, None, False], [1.0, 30, True]]
        assert _names(run) == ["Cool"]

    def test_the_error_of_the_last_try_says_where_it_was_raised(self, clock: FakeClock):
        run, verify = _recording(rootdir=str(ROOT))
        record = verify.eventually(
            lambda: verify.less(_broken_read(), 40, name="T"),
            timeout=0.5,
            interval=0.25,
            name="Cool",
        )
        assert (record["passed"], record["tries"]) == (False, 3)
        assert record["child_checks"] == []
        assert record["sample_error"] == f"raised OSError: no reading (at {_BROKEN_AT})"
        assert "error" not in record  # a failed try, not a usage error
        assert record["detail"] == (
            "never passed in 0.5 s (3 tries), last: the sample raised OSError: no reading "
            f"(at {_BROKEN_AT})"
        )
        assert checks.evaluate(record) is False
        assert checks.evaluate(_round_trip(record)) is False
        assert _names(run) == ["Cool"]

    def test_an_assert_in_a_sample_is_a_failed_try(self, clock: FakeClock):
        _, verify = _recording()
        calls: List[int] = []

        def sample():
            calls.append(1)
            assert len(calls) >= 3, "not yet"
            return verify.is_true(True, name="On")

        record = verify.eventually(sample, timeout=1, interval=0.25, name="Cool")
        assert (record["passed"], record["tries"]) == (True, 3)

    def test_a_usage_error_ends_the_check_at_once(self, clock: FakeClock):
        _, verify = _recording(rootdir=str(ROOT))
        read = Reader(clock, [3.3])
        sample = lambda: verify.approx(read(), 3.3, name="V")  # noqa: E731 - no tolerance
        record = verify.eventually(sample, timeout=5, name="Cool")
        line = sample.__code__.co_firstlineno
        assert (record["passed"], record["tries"]) == (False, 1)
        assert record["error"] == (
            "sample raised ValueError: approx requires at least one of abs_tol or rel_tol "
            f"(at tests/test_sampling.py:{line})"
        )
        assert record["detail"] == f"stopped at try 1 (0 s) ({record['error']})"
        assert record["child_checks"] == []
        assert clock.sleeps == []
        assert checks.evaluate(record) is False
        assert checks.evaluate(_round_trip(record)) is False
        [result] = checks.evaluate_detailed(_round_trip(record))
        assert (result["passed"], result["error"]) == (False, record["error"])

    def test_a_usage_error_ends_stable_at_once_too(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.stable(
            lambda: verify.between(1, 5, 0, name="I"), duration=5, name="Steady"
        )
        assert (record["passed"], record["tries"]) == (False, 1)
        assert record["error"].startswith(
            "sample raised ValueError: between() low must not exceed high"
        )
        assert record["detail"].startswith("stopped at try 1 (0 s) (sample raised ValueError")

    def test_checks_record_in_a_sample_is_a_usage_error(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.eventually(
            lambda: checks.record(checks.is_true(1, name="On")), timeout=5, name="Cool"
        )
        assert record["tries"] == 1
        assert record["error"].startswith(
            "sample raised RuntimeError: checks.record() cannot record a check"
        )

    def test_a_sample_that_needs_an_argument_ends_at_once(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.eventually(
            lambda value: verify.is_true(value, name="On"), timeout=5, name="Cool"
        )
        assert record["tries"] == 1
        assert "missing 1 required positional argument: 'value'" in record["error"]

    def test_a_check_without_a_name_ends_the_check_at_once(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.eventually(
            lambda: verify.equal(1, 1), timeout=5, name="Cool"  # type: ignore[call-arg]
        )
        assert record["tries"] == 1
        assert re.match(
            r"sample raised TypeError: (Verify\.)?equal\(\) missing 1 required keyword-only "
            r"argument: 'name'",
            record["error"],
        )


# ---------------------------------------------------------------------------
# Samples that do not return a fresh check
# ---------------------------------------------------------------------------


class TestNotACheck:
    @pytest.mark.parametrize(
        "returned, reason",
        [
            (None, "NoneType, not a check (did you forget `return`?)"),
            (
                True,
                "bool, not a check: return a check, such as lambda: verify.greater(read(), 3.2, "
                "name=...), not a comparison",
            ),
            (5, "int, not a check"),
            ({"name": "x"}, "dict, not a check"),
        ],
        ids=["None", "bool", "int", "dict"],
    )
    @pytest.mark.parametrize("method", ["eventually", "stable"])
    def test_it_fails_at_once_with_a_hint(self, clock: FakeClock, method, returned, reason):
        run, verify = _recording()
        span = {"timeout": 5} if method == "eventually" else {"duration": 5}
        record = getattr(verify, method)(lambda: returned, name="Rail", **span)
        assert (record["passed"], record["tries"]) == (False, 1)
        assert record["error"] == f"sample returned {reason}"
        assert record["detail"] == f"stopped at try 1 (0 s) (sample returned {reason})"
        assert record["child_checks"] == []
        assert clock.sleeps == []
        assert _names(run) == ["Rail"]
        json.dumps(record, allow_nan=False)

    def test_a_used_raises_block_gets_its_own_hint(self, clock: FakeClock):
        _, verify = _recording()

        def sample():
            with verify.raises(ValueError, name="Reject") as raised:
                raise ValueError("out of range")
            return raised  # meant raised.check

        record = verify.eventually(sample, timeout=5, name="Cool")
        assert record["tries"] == 1
        assert record["error"] == (
            "sample returned a verify.raises() block, not a check: it must be used in a with "
            "statement; use a function that runs `with verify.raises(...) as raised:` and "
            "returns raised.check"
        )

    def test_an_unused_raises_block_gets_the_hint_too(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.eventually(
            lambda: verify.raises(ValueError, name="Reject"), timeout=5, name="Cool"
        )
        assert record["tries"] == 1
        assert "sample returned a verify.raises() block, not a check" in record["error"]

    def test_the_same_check_twice_is_a_usage_error(self, clock: FakeClock):
        run, verify = _recording()
        read_once = verify.less(50, 40, name="T")  # read outside the sample
        record = verify.eventually(lambda: read_once, timeout=5, name="Cool")
        assert (record["passed"], record["tries"]) == (False, 2)
        assert record["error"] == (
            "sample returned the same check twice, so nothing was measured again: make the "
            "check inside the sample, such as lambda: verify.less(read(), 40, name=...)"
        )
        assert record["detail"].startswith("stopped at try 2 (0.1 s) (sample returned the same")
        assert record["child_checks"] == []
        assert run.records == [read_once, record]  # made outside: not the sample's to drop

    def test_the_same_check_made_in_a_try_is_dropped_with_it(self, clock: FakeClock):
        run, verify = _recording()
        cache: List[Any] = []

        def sample():
            if not cache:
                cache.append(verify.less(30, 40, name="T"))
            return cache[0]

        record = verify.stable(sample, duration=5, name="Steady")
        assert record["error"].startswith("sample returned the same check twice")
        assert _names(run) == ["Steady"]

    def test_the_same_built_check_twice_is_a_usage_error(self, clock: FakeClock):
        built = checks.less(30, 40, name="T")
        record = checks.stable(lambda: built, duration=5, name="Steady")
        assert record["tries"] == 2
        assert record["error"].startswith("sample returned the same check twice")
        assert checks.evaluate(record) is False


# ---------------------------------------------------------------------------
# Usage errors of the call itself
# ---------------------------------------------------------------------------


_SAMPLE_MESSAGE = (
    r"eventually\(\) sample must be a callable with no arguments that makes the check, such as "
    r"lambda: verify\.less\(read\(\), 40, name=\.\.\.\); got "
)


_FINITE = "must be a finite number of seconds"
_SECONDS = "must be a number of seconds or a timedelta"
_MINUS_ONE_SECOND = datetime.timedelta(seconds=-1)


class TestArguments:
    @pytest.mark.parametrize("sample, kind", [(5, "int"), (None, "NoneType"), ("T", "str")])
    def test_a_sample_that_is_not_callable_is_a_type_error(self, sample, kind):
        with pytest.raises(TypeError, match=_SAMPLE_MESSAGE + kind):
            checks.eventually(sample, timeout=1, name="Cool")
        with pytest.raises(TypeError, match=r"stable\(\) sample must be a callable .* got " + kind):
            checks.stable(sample, duration=1, name="Steady")

    def test_a_recorded_check_instead_of_a_sample_is_a_type_error_and_absorbed(self):
        run, verify = _recording()
        verify.is_true(True, name="before")
        check = verify.less(50, 40, name="T")
        with pytest.raises(TypeError, match=_SAMPLE_MESSAGE + "dict"):
            verify.eventually(check, timeout=1, name="Cool")  # type: ignore[arg-type]
        assert _names(run) == ["before"]  # the misplaced check does not fail the test on its own
        check = verify.less(50, 40, name="T")
        with pytest.raises(TypeError, match=r"stable\(\) sample must be a callable"):
            verify.stable(check, duration=1, name="Steady")  # type: ignore[arg-type]
        assert _names(run) == ["before"]

    _INVALID = [
        ("timeout", -1, ValueError, f"timeout {_FINITE}, 0 or more; got -1"),
        ("timeout", math.nan, ValueError, f"timeout {_FINITE}, 0 or more; got nan"),
        ("timeout", math.inf, ValueError, f"timeout {_FINITE}, 0 or more; got inf"),
        ("timeout", Decimal("NaN"), ValueError, f"timeout {_FINITE}, 0 or more; got Decimal("),
        ("timeout", 10**400, ValueError, f"timeout {_FINITE}, 0 or more; got 1000"),
        ("timeout", _MINUS_ONE_SECOND, ValueError, f"timeout {_FINITE}, 0 or more; got -1.0"),
        ("timeout", True, TypeError, f"timeout {_SECONDS}, got bool"),
        ("timeout", "5", TypeError, f"timeout {_SECONDS}, got str"),
        ("timeout", None, TypeError, f"timeout {_SECONDS}, got NoneType"),
        ("interval", 0, ValueError, f"interval {_FINITE}, more than 0; got 0"),
        ("interval", -0.5, ValueError, f"interval {_FINITE}, more than 0; got -0.5"),
        ("interval", math.nan, ValueError, f"interval {_FINITE}, more than 0; got nan"),
        ("interval", datetime.timedelta(0), ValueError, f"interval {_FINITE}, more than 0; got 0"),
        ("interval", False, TypeError, f"interval {_SECONDS}, got bool"),
        ("interval", "0.1", TypeError, f"interval {_SECONDS}, got str"),
    ]

    @pytest.mark.parametrize(
        "field, value, error, message", _INVALID, ids=[f"{f}={v!r:.12}" for f, v, *_ in _INVALID]
    )
    def test_invalid_timeout_or_interval(self, field, value, error, message):
        run, verify = _recording()
        read = Reader(FakeClock(), [30])
        arguments = {"timeout": 1, field: value}
        with pytest.raises(error, match=re.escape("eventually() " + message)):
            verify.eventually(lambda: verify.less(read(), 40, name="T"), name="Cool", **arguments)
        assert read.starts == []  # nothing was sampled
        assert run.records == []

    @pytest.mark.parametrize(
        "field, value, error, message",
        [
            ("duration", -1, ValueError, f"duration {_FINITE}, 0 or more; got -1"),
            ("duration", math.nan, ValueError, f"duration {_FINITE}, 0 or more; got nan"),
            ("duration", True, TypeError, f"duration {_SECONDS}, got bool"),
            ("duration", "2", TypeError, f"duration {_SECONDS}, got str"),
            ("interval", 0, ValueError, f"interval {_FINITE}, more than 0; got 0"),
        ],
    )
    def test_invalid_duration_or_interval(self, field, value, error, message):
        arguments = {"duration": 1, field: value}
        with pytest.raises(error, match=re.escape("stable() " + message)):
            checks.stable(lambda: checks.less(1, 2, name="T"), name="Steady", **arguments)

    def test_timedeltas_and_other_real_numbers_are_seconds(self, clock: FakeClock):
        record = checks.eventually(
            lambda: checks.less(30, 40, name="T"),
            timeout=datetime.timedelta(seconds=2),
            interval=datetime.timedelta(milliseconds=250),
            name="Cool",
        )
        assert (record["timeout"], record["interval"]) == (2.0, 0.25)
        assert record["description"] == "Verify 'Cool' passes within 2 s (every 0.25 s)"
        record = checks.stable(
            lambda: checks.less(30, 40, name="T"),
            duration=Fraction(1, 2),
            interval=Decimal("0.25"),
            name="Steady",
        )
        assert (record["duration"], record["interval"]) == (0.5, 0.25)
        assert type(record["duration"]) is float and type(record["interval"]) is float
        assert record["tries"] == 3
        json.dumps(record, allow_nan=False)

    def test_name_is_required_and_not_blank(self):
        with pytest.raises(TypeError, match="name"):
            checks.eventually(lambda: checks.less(1, 2, name="T"), timeout=1)  # type: ignore
        with pytest.raises(ValueError, match=r"eventually\(\) name must not be empty"):
            checks.eventually(lambda: checks.less(1, 2, name="T"), timeout=1, name=" ")
        with pytest.raises(ValueError, match=r"stable\(\) name must not be empty"):
            checks.stable(lambda: checks.less(1, 2, name="T"), duration=1, name="")


# ---------------------------------------------------------------------------
# Exceptions that go on
# ---------------------------------------------------------------------------


def _skip() -> None:
    pytest.skip("no bench")


def _exit() -> None:
    pytest.exit("operator quit")


def _fail() -> None:
    pytest.fail("bench on fire")


def _interrupt() -> None:
    raise KeyboardInterrupt


def _unittest_skip() -> None:
    raise unittest.SkipTest("no bench")


def _debugger_quit() -> None:
    raise bdb.BdbQuit


_GOING_ON = [
    (_skip, pytest.skip.Exception),
    (_exit, pytest.exit.Exception),
    (_fail, pytest.fail.Exception),
    (_interrupt, KeyboardInterrupt),
    (_unittest_skip, unittest.SkipTest),
    (_debugger_quit, bdb.BdbQuit),
]
_GOING_ON_IDS = ["skip", "exit", "fail", "ctrl-c", "unittest-skip", "debugger-quit"]


class TestPassingThrough:
    @pytest.mark.parametrize("stop, expected", _GOING_ON, ids=_GOING_ON_IDS)
    def test_eventually_lets_it_through_and_leaves_no_try(self, clock: FakeClock, stop, expected):
        run, verify = _recording()
        before = verify.is_true(True, name="before")
        calls: List[int] = []

        def sample():
            calls.append(1)
            verify.is_true(False, name=f"aux {len(calls)}")
            if len(calls) == 3:
                stop()
            return verify.less(50, 40, name="T")

        with pytest.raises(expected):
            verify.eventually(sample, timeout=5, interval=0.5, name="Cool")
        assert len(calls) == 3
        assert run.records == [before]

    @pytest.mark.parametrize("stop, expected", _GOING_ON, ids=_GOING_ON_IDS)
    def test_stable_lets_it_through_and_leaves_no_try(self, clock: FakeClock, stop, expected):
        run, verify = _recording()
        calls: List[int] = []

        def sample():
            calls.append(1)
            verify.is_true(True, name="aux")
            if len(calls) == 2:
                stop()
            return verify.less(30, 40, name="T")

        with pytest.raises(expected):
            verify.stable(sample, duration=5, interval=0.5, name="Steady")
        assert len(calls) == 2
        assert run.records == []

    @pytest.mark.parametrize("stop, expected", _GOING_ON, ids=_GOING_ON_IDS)
    def test_the_builder_lets_it_through(self, clock: FakeClock, stop, expected):
        with pytest.raises(expected):
            checks.eventually(stop, timeout=5, name="Cool")

    def test_ctrl_c_while_waiting_leaves_no_try(
        self, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
    ):
        run, verify = _recording()

        def interrupted(seconds: float) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_sampling, "_sleep", interrupted)
        with pytest.raises(KeyboardInterrupt):
            verify.eventually(lambda: verify.less(50, 40, name="T"), timeout=5, name="Cool")
        assert run.records == []

    def test_a_skip_in_a_nested_sample_leaves_no_try(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [30, 30, 50])

        def inner():
            value = read()
            if len(read.starts) == 3:
                pytest.skip("no bench")
            return verify.less(value, 40, name="T")

        with pytest.raises(pytest.skip.Exception):
            verify.stable(
                lambda: verify.eventually(inner, timeout=1, name="Inner"),
                duration=5,
                interval=1,
                name="Outer",
            )
        assert run.records == []

    def test_a_skip_after_failed_tries_stays_a_skip(self, pytester: pytest.Pytester):
        pytester.makeconftest(_FAKE_CLOCK_CONFTEST)
        pytester.makepyfile("""
            import pytest

            def test_bench(verify):
                calls = []

                def sample():
                    calls.append(1)
                    verify.is_true(False, name="Link")
                    if len(calls) == 3:
                        pytest.skip("bench is off")
                    return verify.less(50, 40, name="T")

                verify.eventually(sample, timeout=5, name="Cool")
        """)
        for options in [(), ("--verify-fail-fast",)]:
            result = pytester.runpytest("-rs", *options)
            result.assert_outcomes(skipped=1)
            result.stdout.fnmatch_lines(["*SKIPPED*bench is off*"])
            result.stdout.no_fnmatch_line("*Soft assertion failures*")

    def test_pytest_exit_in_a_sample_ends_the_session(self, pytester: pytest.Pytester):
        pytester.makeconftest(_FAKE_CLOCK_CONFTEST)
        pytester.makepyfile("""
            import pytest

            def test_bench(verify):
                verify.stable(lambda: pytest.exit("operator quit"), duration=5, name="Steady")

            def test_never_runs():
                pass
        """)
        result = pytester.runpytest()
        assert result.ret == pytest.ExitCode.INTERRUPTED
        result.stdout.fnmatch_lines(["*Exit: operator quit*"])
        result.assert_outcomes()


_FAKE_CLOCK_CONFTEST = """
    import pytest
    from pytest_verifier._checks import _sampling

    class FakeClock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    @pytest.fixture(autouse=True)
    def fake_clock(monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(_sampling, "_clock", clock)
        monkeypatch.setattr(_sampling, "_sleep", clock.sleep)
        return clock
"""


# ---------------------------------------------------------------------------
# Try scopes: the checks a try records
# ---------------------------------------------------------------------------


class TestTryScopes:
    def test_checks_of_a_dropped_try_leave_the_run(self, clock: FakeClock):
        run, verify = _recording()
        verify.is_true(True, name="before")
        read = Reader(clock, [50, 45, 30])

        def sample():
            value = read()
            verify.equal(len(read.starts), 3, name="Try")  # fails in tries 1 and 2
            return verify.less(value, 40, name="T")

        record = verify.eventually(sample, timeout=5, interval=0.5, name="Cool")
        verify.is_true(True, name="after")
        assert _names(run) == ["before", "Try", "Cool", "after"]
        # The kept try's other check stays on its own, with its verdict.
        assert run.records[1]["actual"] == 3 and run.records[1]["passed"] is True
        assert record["child_checks"][0]["actual"] == 30

    def test_the_kept_try_keeps_its_failed_other_checks(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [50, 50, 50])

        def sample():
            verify.is_true(False, name="Link")
            return verify.less(read(), 40, name="T")

        record = verify.eventually(sample, timeout=1, interval=0.5, name="Cool")
        assert record["tries"] == 3
        assert _names(run) == ["Link", "Cool"]  # one Link: the last try's
        assert run.records[0]["passed"] is False

    def test_stable_drops_the_passing_tries_it_does_not_keep(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [30, 38, 35])

        def sample():
            value = read()
            verify.equal(value, value, name=f"Read {value}")
            return verify.less(value, 40, name="T")

        verify.stable(sample, duration=1, interval=0.5, name="Steady")
        assert _names(run) == ["Read 38", "Steady"]

    def test_stable_keeps_the_failed_try_and_drops_the_rest(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [30, 45])

        def sample():
            value = read()
            verify.equal(value, value, name=f"Read {value}")
            return verify.less(value, 40, name="T")

        verify.stable(sample, duration=1, interval=0.5, name="Steady")
        assert _names(run) == ["Read 45", "Steady"]

    def test_a_sample_built_with_checks_is_recorded_in_its_try(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [50, 45, 30])
        record = verify.eventually(
            lambda: checks.less(read(), 40, name="T"), timeout=5, interval=0.5, name="Cool"
        )
        assert record["passed"] is True
        [child] = record["child_checks"]
        assert (child["actual"], child["passed"], child["detail"]) == (30, True, "30 < 40")
        assert _names(run) == ["Cool"]


    def test_sections_reach_the_sampling_check_and_its_tries(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [50, 30])

        def sample():
            verify.is_true(True, name="Link")
            return verify.less(read(), 40, name="T")

        with verify.section("3V3"):
            record = verify.eventually(sample, timeout=1, name="Cool")
        assert record["section"] == ["3V3"]
        assert record["child_checks"][0]["section"] == ["3V3"]
        assert [r.get("section") for r in run.records] == [["3V3"], ["3V3"]]


class TestSession:
    def test_only_the_kept_try_reaches_the_report(self, pytester: pytest.Pytester):
        pytester.makeconftest(_FAKE_CLOCK_CONFTEST)
        pytester.makepyfile("""
            def test_settles(verify):
                values = iter([50, 45, 30])
                verify.eventually(lambda: verify.less(next(values), 40, name="T"), timeout=5,
                                  name="Cool")

            def test_never(verify):
                verify.eventually(lambda: verify.less(50, 40, name="T"), timeout=1,
                                  interval=0.5, name="Hot")
                print("REACHED")
        """)
        result = pytester.runpytest("-s", "--verify-json", "checks.jsonl")
        result.assert_outcomes(passed=1, failed=1)
        result.stdout.fnmatch_lines([
            "*REACHED*",  # soft: the test body went on
            "1 of 1 checks failed: Hot — never passed in 1 s (3 tries), last: T — expected < 40,"
            " got 50; the value never changed in 3 tries: is it read inside the sample?",
        ])
        lines = [
            json.loads(line)
            for line in (pytester.path / "checks.jsonl").read_text("utf-8").splitlines()
        ]
        assert [(line["check"]["name"], line["check"]["passed"]) for line in lines] == [
            ("Cool", True),
            ("Hot", False),
        ]
        settled = lines[0]["check"]
        assert settled["trace"] == [[0.0, 50, False], [0.1, 45, False], [0.2, 30, True]]
        assert settled["child_checks"][0]["actual"] == 30
        assert checks.evaluate(settled) is True


# ---------------------------------------------------------------------------
# verify.require and fail-fast
# ---------------------------------------------------------------------------


class TestStops:
    def test_require_inside_a_sample_does_not_stop_while_sampling(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [50, 45, 30])
        record = verify.eventually(
            lambda: verify.require.less(read(), 40, name="T"), timeout=5, name="Cool"
        )
        assert (record["passed"], record["tries"]) == (True, 3)
        assert _names(run) == ["Cool"]

    def test_the_returned_required_check_is_judged_by_the_sampling_check(self, clock: FakeClock):
        run, verify = _recording()
        record = verify.eventually(
            lambda: verify.require.less(50, 40, name="T"), timeout=0.25, name="Cool"
        )
        assert record["passed"] is False  # soft: verify.eventually, not verify.require
        assert _names(run) == ["Cool"]

    def test_a_failed_required_check_of_the_kept_try_stops_after_sampling(
        self, clock: FakeClock
    ):
        run, verify = _recording()
        read = Reader(clock, [50, 30])

        def sample():
            value = read()
            verify.require.is_true(False, name="Link")
            return verify.less(value, 40, name="T")

        with pytest.raises(ChecksFailedError) as raised:
            verify.eventually(sample, timeout=5, name="Cool")
        assert read.starts == [0.0, 0.1]  # sampling went on to the passing try
        assert str(raised.value).splitlines()[0] == (
            "1 of 2 checks failed, stopped at [0]: Link — expected True, got False"
        )
        assert _names(run) == ["Link", "Cool"]
        assert run.records[1]["passed"] is True

    def test_failed_required_checks_of_dropped_tries_never_stop(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [50, 45, 30])

        def sample():
            value = read()
            verify.require.less(value, 40, name="Early")
            return verify.less(value, 40, name="T")

        record = verify.eventually(sample, timeout=5, name="Cool")
        assert record["passed"] is True
        assert _names(run) == ["Early", "Cool"]

    def test_require_eventually_stops_when_it_never_passes(self, clock: FakeClock):
        run, verify = _recording()
        with pytest.raises(ChecksFailedError) as raised:
            verify.require.eventually(lambda: verify.less(50, 40, name="T"), timeout=0, name="Cool")
        assert str(raised.value).splitlines()[0] == (
            "1 of 1 checks failed, stopped at [0]: Cool — never passed in 0 s (1 try), last: "
            "T — expected < 40, got 50"
        )
        assert _names(run) == ["Cool"]

    def test_require_stable_passing_does_not_stop(self, clock: FakeClock):
        _, verify = _recording()
        record = verify.require.stable(lambda: verify.less(1, 2, name="T"), duration=1, name="S")
        assert record["passed"] is True

    def test_a_require_in_a_nested_sample_waits_for_the_outer_sampling(self, clock: FakeClock):
        run, verify = _recording()
        calls: List[int] = []

        def inner():
            calls.append(1)
            verify.require.is_true(False, name="Link")
            return verify.less(30, 40, name="T")

        with pytest.raises(ChecksFailedError):
            verify.stable(
                lambda: verify.eventually(inner, timeout=1, name="Inner"),
                duration=0.5,
                interval=0.25,
                name="Outer",
            )
        assert len(calls) == 3  # every outer try ran
        assert _names(run) == ["Link", "Outer"]

    def test_fail_fast_applies_after_sampling(self, clock: FakeClock):
        run, verify = _recording(fail_fast=True)
        read = Reader(clock, [50, 45, 30])
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=5, name="Cool"
        )
        assert record["passed"] is True  # the failed tries did not stop the test
        with pytest.raises(ChecksFailedError) as raised:
            verify.eventually(lambda: verify.less(50, 40, name="T"), timeout=0.2, name="Never")
        assert "stopped at [1]: Never — never passed in 0.2 s (3 tries)" in str(raised.value)

    def test_fail_fast_in_a_session(self, pytester: pytest.Pytester):
        pytester.makeconftest(_FAKE_CLOCK_CONFTEST)
        pytester.makepyfile("""
            def test_settles(verify):
                values = iter([50, 45, 30])
                verify.eventually(lambda: verify.less(next(values), 40, name="T"), timeout=5,
                                  name="Cool")
                verify.is_true(True, name="after")

            def test_never(verify):
                verify.eventually(lambda: verify.less(50, 40, name="T"), timeout=1,
                                  interval=0.25, name="Cool")
                print("REACHED never")

            def test_other_check(verify):
                def sample():
                    verify.is_true(False, name="Link")
                    return verify.less(30, 40, name="T")

                verify.stable(sample, duration=1, name="Steady")
                print("REACHED other")
        """)
        result = pytester.runpytest("--verify-fail-fast", "-s")
        result.assert_outcomes(passed=1, failed=2)
        result.stdout.no_fnmatch_line("*REACHED*")
        result.stdout.fnmatch_lines([
            "*1 of 1 checks failed, stopped at [[]0[]]: Cool — never passed in 1 s (5 tries)*",
            "*1 of 2 checks failed, stopped at [[]0[]]: Link — expected True, got False*",
        ])


# ---------------------------------------------------------------------------
# The builder: pytest_verifier.checks
# ---------------------------------------------------------------------------


class TestBuilder:
    def test_checks_eventually_evaluates_each_try_when_taken(self, clock: FakeClock):
        read = Reader(clock, [50, 45, 30])
        built = checks.eventually(
            lambda: checks.less(read(), 40, name="T"), timeout=5, interval=1, name="Cool"
        )
        assert "passed" not in built and "detail" not in built
        assert (built["tries"], built["settled_at"]) == (3, 2.0)
        [child] = built["child_checks"]
        assert (child["actual"], child["passed"], child["detail"]) == (30, True, "30 < 40")
        assert built["trace"] == [[0.0, 50, False], [1.0, 45, False], [2.0, 30, True]]
        assert checks.evaluate(built) is True
        assert checks.evaluate(_round_trip(built)) is True
        assert render_detail(built, True) == "passed at try 3 (2 s): T — 30 < 40"

    def test_a_try_keeps_its_verdict_when_the_value_changes_later(self, clock: FakeClock):
        data = [1, 2, 3]
        built = checks.eventually(lambda: checks.length(data, 3, name="n"), timeout=0, name="N")
        data.append(4)
        assert checks.evaluate(built) is True
        assert built["child_checks"][0]["actual_length"] == 3

    def test_checks_stable_failing(self, clock: FakeClock):
        read = Reader(clock, [30, 45])
        built = checks.stable(
            lambda: checks.less(read(), 40, name="T"), duration=1, interval=0.5, name="Steady"
        )
        assert checks.evaluate(built) is False
        assert checks.evaluate(_round_trip(built)) is False
        [result] = checks.evaluate_detailed(built)
        assert result["passed"] is False
        assert render_detail(built, False) == (
            "failed at try 2 (0.5 s): T — expected < 40, got 45"
        )

    def test_record_of_a_built_sampling_check(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [50, 30])
        record = verify.record(
            checks.eventually(lambda: checks.less(read(), 40, name="T"), timeout=1, name="Cool")
        )
        assert record["passed"] is True
        assert record["detail"] == "passed at try 2 (0.1 s): T — 30 < 40"
        assert _names(run) == ["Cool"]

    def test_checks_require_refuses_without_sampling(self, clock: FakeClock):
        read = Reader(clock, [30])
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require.eventually(
                lambda: checks.less(read(), 40, name="T"), timeout=1, name="Cool"
            )
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require.stable(lambda: checks.less(read(), 40, name="T"), duration=1, name="S")
        assert read.starts == []

    def test_discarded_tries_do_not_warn_as_unused(self, pytester: pytest.Pytester):
        pytester.makeconftest(_FAKE_CLOCK_CONFTEST)
        pytester.makepyfile("""
            from pytest_verifier import checks

            def test_built(verify):
                values = iter([50, 45, 30])
                verify.record(checks.eventually(
                    lambda: checks.less(next(values), 40, name="T"), timeout=5, name="Cool"))
                verify.record(checks.stable(
                    lambda: checks.less(30, 40, name="T"), duration=1, name="Steady"))

            def test_evaluated():
                values = iter([50, 45, 30])
                assert checks.evaluate(checks.eventually(
                    lambda: checks.less(next(values), 40, name="T"), timeout=5, name="Cool"))

            def test_fixture_with_built_samples(verify):
                values = iter([50, 45, 30])
                verify.eventually(lambda: checks.less(next(values), 40, name="T"), timeout=5,
                                  name="Cool")
                verify.stable(lambda: checks.less(30, 40, name="T"), duration=1, name="Steady")

            def test_unused():
                checks.eventually(lambda: checks.less(30, 40, name="T"), timeout=0, name="Lost")
        """)
        result = pytester.runpytest("-W", "error::pytest_verifier.UnusedCheckWarning")
        # Only the sampling check that was never used warns (an error in its teardown).
        result.assert_outcomes(passed=4, errors=1)
        result.stdout.fnmatch_lines(["*UnusedCheckWarning: 1 check was built*'Lost' (eventually)*"])
        result.stdout.no_fnmatch_line("*'T' (less)*")


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------


class TestNesting:
    def test_an_eventually_inside_a_stable_sample(self, clock: FakeClock):
        run, verify = _recording()
        read = Reader(clock, [50, 30, 50, 35])
        record = verify.stable(
            lambda: verify.eventually(
                lambda: verify.less(read(), 40, name="T"), timeout=1, interval=0.25, name="Inner"
            ),
            duration=0.5,
            interval=0.5,
            name="Outer",
        )
        assert (record["passed"], record["tries"]) == (True, 2)
        assert read.starts == [0.0, 0.25, 0.5, 0.75]
        [inner] = record["child_checks"]
        assert inner["check_type"] == "eventually" and inner["passed"] is True
        # The inner check with the smaller margin (35 against 40) is kept.
        assert inner["child_checks"][0]["actual"] == 35
        assert record["detail"] == (
            "held for 0.5 s (2 tries), closest: Inner — passed at try 2 (0.25 s): T — 35 < 40"
        )
        assert _names(run) == ["Outer"]
        assert margin(record) == 5
        assert checks.evaluate(_round_trip(record)) is True

    def test_a_failing_inner_check_fails_the_outer_one_at_once(self, clock: FakeClock):
        run, verify = _recording()
        record = verify.stable(
            lambda: verify.eventually(
                lambda: verify.less(50, 40, name="T"), timeout=0.5, interval=0.25, name="Inner"
            ),
            duration=5,
            name="Outer",
        )
        assert (record["passed"], record["tries"]) == (False, 1)
        assert record["detail"].startswith(
            "failed at try 1 (0 s): Inner — never passed in 0.5 s (3 tries)"
        )
        assert _names(run) == ["Outer"]
        json.dumps(record, allow_nan=False)


# ---------------------------------------------------------------------------
# A running event loop
# ---------------------------------------------------------------------------


class TestEventLoop:
    @pytest.mark.parametrize("method", ["eventually", "stable"])
    def test_a_running_event_loop_gives_a_warning(self, clock: FakeClock, method):
        _, verify = _recording()
        span = {"timeout": 0} if method == "eventually" else {"duration": 0}

        async def test_body():
            sample = lambda: verify.is_true(True, name="On")  # noqa: E731
            return getattr(verify, method)(sample, name="X", **span)

        with pytest.warns(RuntimeWarning, match=rf"verify\.{method}\(\) waits with time\.sleep, "
                          r"which blocks the event loop of this async test"):
            record = asyncio.run(test_body())
        assert record["passed"] is True  # it still checks

    def test_the_builder_warns_too(self, clock: FakeClock):
        async def test_body():
            return checks.stable(lambda: checks.is_true(True, name="On"), duration=0, name="X")

        with pytest.warns(RuntimeWarning, match=r"asyncio\.to_thread"):
            asyncio.run(test_body())

    def test_no_warning_without_a_running_loop(self, clock: FakeClock):
        _, verify = _recording()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            verify.eventually(lambda: verify.is_true(True, name="On"), timeout=0, name="X")

    @pytest.mark.parametrize("builder", [False, True], ids=["fixture", "checks"])
    def test_the_warning_points_at_the_caller(self, clock: FakeClock, builder):
        _, verify = _recording()
        maker = checks if builder else verify

        async def test_body():
            maker.eventually(lambda: maker.is_true(True, name="On"), timeout=0, name="X")

        with pytest.warns(RuntimeWarning) as caught:
            asyncio.run(test_body())
        assert [Path(w.filename).name for w in caught] == ["test_sampling.py"]


# ---------------------------------------------------------------------------
# Margins
# ---------------------------------------------------------------------------


class TestMargins:
    def test_a_sampling_check_has_the_margin_of_its_kept_try(self, clock: FakeClock):
        _, verify = _recording()
        read = Reader(clock, [50, 30])
        record = verify.eventually(
            lambda: verify.less(read(), 40, name="T"), timeout=5, name="Cool"
        )
        assert margin(record) == margin(record["child_checks"][0]) == 10
        assert margin(_round_trip(record)) == 10
        failed = verify.eventually(lambda: verify.less(45, 40, name="T"), timeout=0, name="Hot")
        assert margin(failed) == -5

    def test_no_margin_without_a_kept_check_or_a_limit(self, clock: FakeClock):
        _, verify = _recording()
        assert margin(verify.eventually(_broken_read, timeout=0, name="Cool")) is None
        assert margin(verify.eventually(lambda: None, timeout=0, name="Cool")) is None
        on = verify.stable(lambda: verify.is_true(True, name="On"), duration=0, name="Steady")
        assert margin(on) is None

    def test_the_session_summary_shows_the_margin(self, pytester: pytest.Pytester):
        pytester.makeconftest(_FAKE_CLOCK_CONFTEST)
        pytester.makepyfile("""
            def test_cooling(verify):
                values = iter([50, 45, 30])
                verify.eventually(lambda: verify.less(next(values), 40, name="T", units="C"),
                                  timeout=5, name="Cool")
        """)
        result = pytester.runpytest("--verify-summary=stats")
        assert result.ret == pytest.ExitCode.OK
        result.stdout.fnmatch_lines([
            "*pytest-verifier: checks by name*",
            "  ✓ Cool: 1 passed; margin 10",
            "  ✓ T: 1 passed; 30C, margin 10C",  # the dropped tries do not count
        ])


# ---------------------------------------------------------------------------
# JSON safety
# ---------------------------------------------------------------------------


def _hostile_float() -> Any:
    return float("nan")


_RECORDS = {
    "passed": lambda v: v.eventually(lambda: v.less(1, 2, name="T"), timeout=1, name="R"),
    "never": lambda v: v.eventually(lambda: v.less(5, 2, name="T"), timeout=0.5, name="R"),
    "nan": lambda v: v.eventually(lambda: v.less(_hostile_float(), 2, name="T"), timeout=0.2,
                                  name="R"),
    "inf": lambda v: v.stable(lambda: v.less(-math.inf, 2, name="T"), duration=0.2, name="R"),
    "raised": lambda v: v.eventually(_broken_read, timeout=0.2, name="R"),
    "usage": lambda v: v.stable(lambda: None, duration=1, name="R"),
    "text": lambda v: v.eventually(lambda: v.equal("a\udcff" * 20_000, "b", name="T"),
                                   timeout=0.2, name="R"),
    "nested": lambda v: v.stable(
        lambda: v.eventually(lambda: v.greater(3, 2, name="T"), timeout=0, name="I"),
        duration=0.2, name="R"),
}


@pytest.mark.parametrize("make", list(_RECORDS.values()), ids=list(_RECORDS))
def test_records_are_json_safe_and_keep_their_verdict(clock: FakeClock, make):
    run, verify = _recording()
    record = make(verify)
    text = json.dumps(record, allow_nan=False)
    text.encode("utf-8")
    json.dumps(record, ensure_ascii=False).encode("utf-8")
    copy = json.loads(text)
    assert checks.evaluate(record) is record["passed"]
    assert checks.evaluate(copy) is record["passed"]
    assert render_detail(copy, copy["passed"]) == record["detail"]
    assert run.records == [record]
    for entry in record["trace"]:
        assert isinstance(entry, list) and len(entry) == 3
        assert isinstance(entry[0], float) and isinstance(entry[2], bool)


@pytest.mark.parametrize("make", list(_RECORDS.values()), ids=list(_RECORDS))
def test_built_checks_are_json_safe_and_agree_with_the_fixture(clock: FakeClock, make):
    built = make(checks)
    clock.now = _ORIGIN
    _, verify = _recording()
    recorded = make(verify)
    text = json.dumps(built, allow_nan=False)
    assert checks.evaluate(built) is recorded["passed"]
    assert checks.evaluate(json.loads(text)) is recorded["passed"]
