"""``verify.raises``: the soft check of a ``with`` block that must raise, and :class:`Raises`.

Covers the verdicts (a class, a tuple, ``match`` with strings, compiled patterns and notes),
the soft failures (nothing raised, the expected type with another message, the ``re.escape``
hint), unexpected exceptions (recorded, then propagated with their own traceback), the
exceptions that pass through untouched (``KeyboardInterrupt``, ``pytest.skip``, ``pytest.exit``,
``unittest.SkipTest``, ``bdb.BdbQuit``, stop errors, exception groups holding a stop), usage
errors, the ``Raises`` object (``value``, ``type``, ``check``, single use), a block never used in
a ``with`` statement, ``checks.raises``, ``verify.require.raises`` and ``--verify-fail-fast``,
where the record says it was made (``location``) and raised (``raised_at``), its fields and
detail texts, and that records are JSON-safe and judged alike by ``checks.evaluate()``.
"""
from __future__ import annotations

import bdb
import builtins
import enum
import json
import re
import sys
import unittest
from typing import Any, Dict, List, Tuple

import pytest

import pytest_verifier
from pytest_verifier import ChecksFailedError, Raises, checks
from pytest_verifier._checks import _sampling
from pytest_verifier._run import Run, recording_verify


class OutOfRange(ValueError):
    """A DUT error, as a driver would raise it."""


class Message(str, enum.Enum):
    """Error texts kept as constants, as a driver might."""

    RANGE = "out of range"


class HostileError(ValueError):
    """An exception whose message cannot be read."""

    def __str__(self) -> str:
        raise RuntimeError("hostile message")


def _recording(**kwargs: Any) -> Tuple[Run, Any]:
    run = Run(**kwargs)
    run.phase = "call"
    return run, recording_verify(run)


def _line() -> int:
    """The line number of the caller."""
    return sys._getframe(1).f_lineno


def _round_trip(record: Any) -> Dict[str, Any]:
    return json.loads(json.dumps(record, allow_nan=False))


def _assert_judged_alike(record: Any) -> None:
    """``checks.evaluate()`` agrees with the recorded verdict: on the record, after a JSON
    round trip, and on a copy without ``passed`` (judged again from the stored fields)."""
    verdict = record["passed"]
    assert checks.evaluate(record) is verdict
    copy = _round_trip(record)
    assert checks.evaluate(copy) is verdict
    rejudged = {key: value for key, value in copy.items() if key not in ("passed", "detail")}
    assert checks.evaluate(rejudged) is verdict
    [detailed] = checks.evaluate_detailed(copy)
    assert detailed["passed"] is verdict


def _set_7_volts() -> None:
    raise OutOfRange("7 V is out of range")


# ---------------------------------------------------------------------------
# Passing blocks
# ---------------------------------------------------------------------------


def test_the_expected_class_passes_and_is_suppressed():
    run, verify = _recording()
    with verify.raises(ValueError, name="Reject 7 V") as raised:
        raise ValueError("7 V is out of range")
    record = raised.check
    assert record["passed"] is True
    assert record["check_type"] == "raises"
    assert record["description"] == "Verify 'Reject 7 V' raises ValueError"
    assert record["detail"] == "raised ValueError: 7 V is out of range"
    assert isinstance(raised.value, ValueError) and raised.type is ValueError
    assert run.records == [record] and run.records[0] is record
    _assert_judged_alike(record)


def test_a_subclass_of_the_expected_class_passes():
    _, verify = _recording()
    with verify.raises(ValueError, name="Reject") as raised:
        _set_7_volts()
    record = raised.check
    assert record["passed"] is True
    assert record["expected_type"] == "ValueError"
    assert record["raised_type"] == "OutOfRange"
    assert raised.type is OutOfRange


def test_a_tuple_passes_for_any_of_its_classes():
    _, verify = _recording()
    with verify.raises((KeyError, ValueError), name="Lookup") as raised:
        raise KeyError("ch9")
    record = raised.check
    assert record["passed"] is True
    assert record["description"] == "Verify 'Lookup' raises KeyError | ValueError"
    assert record["expected_type"] == "KeyError | ValueError"
    assert record["expected_types"] == ["builtins.KeyError", "builtins.ValueError"]
    assert record["detail"] == "raised KeyError: 'ch9'"


def test_a_nested_tuple_is_flattened():
    _, verify = _recording()
    with verify.raises((KeyError, (TypeError, OutOfRange)), name="Nested") as raised:
        _set_7_volts()
    record = raised.check
    assert record["passed"] is True
    assert record["expected_type"] == "KeyError | TypeError | OutOfRange"
    assert record["expected_types"][-1] == f"{__name__}.OutOfRange"


def test_match_searches_the_message():
    _, verify = _recording()
    with verify.raises(ValueError, match=r"\d V is out", name="Reject") as raised:
        _set_7_volts()
    record = raised.check
    assert record["passed"] is True
    assert record["description"] == r"Verify 'Reject' raises ValueError matching /\d V is out/"
    assert record["match"] == r"\d V is out" and record["flags"] == 0
    assert record["type_check"] is True and record["match_check"] is True
    _assert_judged_alike(record)


def test_a_compiled_pattern_keeps_its_flags():
    _, verify = _recording()
    pattern = re.compile("OUT OF .*$", re.IGNORECASE | re.MULTILINE)
    with verify.raises(ValueError, match=pattern, name="Reject") as raised:
        _set_7_volts()
    record = raised.check
    assert record["passed"] is True
    assert record["match"] == "OUT OF .*$"
    assert record["flags"] == int(re.IGNORECASE | re.MULTILINE)
    assert type(record["flags"]) is int
    assert record["description"].endswith("matching /OUT OF .*$/im")
    _assert_judged_alike(record)


def test_a_compiled_pattern_without_flags_stores_none():
    _, verify = _recording()
    with verify.raises(ValueError, match=re.compile("range"), name="Reject") as raised:
        _set_7_volts()
    assert raised.check["flags"] == 0  # re.UNICODE is implied for a str pattern


def test_a_case_sensitive_match_fails_on_another_case():
    _, verify = _recording()
    with verify.raises(ValueError, match="OUT OF RANGE", name="Reject") as raised:
        _set_7_volts()
    assert raised.check["passed"] is False
    assert raised.check["match_check"] is False


def test_a_str_enum_pattern_matches_by_its_value():
    _, verify = _recording()
    with verify.raises(ValueError, match=Message.RANGE, name="Reject") as raised:
        _set_7_volts()
    assert raised.check["passed"] is True
    _assert_judged_alike(raised.check)


def test_a_str_enum_pattern_is_shown_as_its_text():
    _, verify = _recording()
    with verify.raises(ValueError, match=Message.RANGE, name="Reject") as raised:
        raise ValueError("unit not supported")
    record = raised.check
    assert record["match"] == "out of range" and type(record["match"]) is str
    assert record["description"] == "Verify 'Reject' raises ValueError matching /out of range/"
    assert record["detail"].startswith("expected ValueError matching /out of range/, got ")


def test_an_empty_message_matches_an_anchored_empty_pattern():
    _, verify = _recording()
    with verify.raises(OutOfRange, match="^$", name="Bare") as raised:
        raise OutOfRange()
    record = raised.check
    assert record["passed"] is True
    assert record["raised_message"] == ""
    assert record["detail"] == "raised OutOfRange"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="add_note() is Python 3.11+")
def test_match_finds_text_in_the_notes():
    _, verify = _recording()
    error = ValueError("setpoint rejected")
    error.add_note("channel 3 is over its limit")
    with verify.raises(ValueError, match="channel 3", name="Reject") as raised:
        raise error
    record = raised.check
    assert record["passed"] is True
    assert record["raised_message"] == "setpoint rejected\nchannel 3 is over its limit"
    assert record["detail"] == r"raised ValueError: setpoint rejected\nchannel 3 is over its limit"
    _assert_judged_alike(record)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="add_note() is Python 3.11+")
def test_a_note_that_does_not_match_still_fails():
    _, verify = _recording()
    error = ValueError("setpoint rejected")
    error.add_note("channel 3")
    with verify.raises(ValueError, match="channel 4", name="Reject") as raised:
        raise error
    assert raised.check["passed"] is False


def test_notes_set_by_hand_are_searched_on_every_python():
    _, verify = _recording()
    error = ValueError("setpoint rejected")
    error.__notes__ = ["channel 3", 42]  # type: ignore[attr-defined]  # a non-str note is skipped
    pattern = re.compile("^channel 3$", re.MULTILINE)
    with verify.raises(ValueError, match=pattern, name="Reject") as raised:
        raise error
    assert raised.check["passed"] is True
    assert raised.check["raised_message"] == "setpoint rejected\nchannel 3"


def test_an_exception_that_is_not_an_exception_subclass_passes_when_named():
    _, verify = _recording()
    with verify.raises(SystemExit, match="^2$", name="Exit code") as raised:
        sys.exit(2)
    assert raised.check["passed"] is True
    assert raised.check["raised_message"] == "2"


# ---------------------------------------------------------------------------
# Soft failures: what the code under test did
# ---------------------------------------------------------------------------


def test_nothing_raised_is_a_failed_check_and_the_block_goes_on():
    run, verify = _recording()
    ran = []
    with verify.raises(KeyError, name="Missing key") as raised:
        ran.append("block")
    ran.append("after")
    record = raised.check
    assert ran == ["block", "after"]
    assert record["passed"] is False
    assert record["detail"] == "expected KeyError, nothing was raised"
    assert record["raised_type"] is None and record["raised_message"] is None
    assert record["raised_at"] is None
    assert record["type_check"] is False and record["match_check"] is None
    assert "error" not in record
    assert raised.value is None and raised.type is None
    assert run.records == [record]
    _assert_judged_alike(record)


def test_nothing_raised_names_the_pattern_in_the_detail():
    _, verify = _recording()
    with verify.raises(ValueError, match="out of range", name="Reject") as raised:
        pass
    assert raised.check["detail"] == (
        "expected ValueError matching /out of range/, nothing was raised"
    )


def test_the_expected_type_with_another_message_fails_softly():
    run, verify = _recording()
    with verify.raises(ValueError, match="out of range", name="Reject") as raised:
        line = _line() + 1
        raise OutOfRange("unit not supported")
    after = verify.equal(1, 1, name="after")  # the test goes on
    record = raised.check
    assert record["passed"] is False
    assert record["type_check"] is True and record["match_check"] is False
    assert record["raised_type"] == "OutOfRange"
    assert record["raised_message"] == "unit not supported"
    assert record["detail"] == (
        "expected ValueError matching /out of range/, got OutOfRange: unit not supported "
        f"(raised at {record['raised_at']})"
    )
    assert record["raised_at"].endswith(f":{line}")
    assert isinstance(raised.value, OutOfRange)  # the expected type, even without a match
    assert run.records == [record, after]
    _assert_judged_alike(record)


def test_a_pattern_found_literally_gets_a_re_escape_hint():
    _, verify = _recording()
    with verify.raises(ValueError, match="limit (V)", name="Reject") as raised:
        raise ValueError("above the limit (V)")
    record = raised.check
    assert record["passed"] is False
    assert record["detail"].endswith(
        "; match is a regular expression: use re.escape() to match it as text"
    )


def test_no_hint_when_the_pattern_is_not_in_the_message():
    _, verify = _recording()
    with verify.raises(ValueError, match="limit (V)", name="Reject") as raised:
        raise ValueError("unit not supported")
    assert "re.escape" not in raised.check["detail"]


def test_no_hint_when_nothing_was_raised():
    _, verify = _recording()
    with verify.raises(ValueError, match="limit (V)", name="Reject") as raised:
        pass
    assert "re.escape" not in raised.check["detail"]


def test_an_escaped_pattern_matches_the_text():
    _, verify = _recording()
    with verify.raises(ValueError, match=re.escape("limit (V)"), name="Reject") as raised:
        raise ValueError("above the limit (V)")
    assert raised.check["passed"] is True


# ---------------------------------------------------------------------------
# Unexpected exceptions: recorded, then propagated
# ---------------------------------------------------------------------------


def test_an_unexpected_exception_is_recorded_then_propagates():
    run, verify = _recording()
    with pytest.raises(ZeroDivisionError) as excinfo:
        with verify.raises(ValueError, name="Reject") as raised:
            line = _line() + 1
            1 / 0  # noqa: B018
    [record] = run.records
    assert record["passed"] is False
    assert record["type_check"] is False and record["match_check"] is None
    assert record["raised_type"] == "ZeroDivisionError"
    assert record["raised_message"] == "division by zero"
    assert record["raised_at"].endswith(f":{line}")
    assert record["detail"] == (
        "expected ValueError, got ZeroDivisionError: division by zero "
        f"(raised at {record['raised_at']})"
    )
    assert raised.check is record
    assert raised.value is None and raised.type is None
    # Its own traceback: the innermost entry is the line that raised, not the block's exit.
    traceback = excinfo.value.__traceback__
    while traceback.tb_next is not None:
        traceback = traceback.tb_next
    assert traceback.tb_lineno == line
    assert excinfo.value.__context__ is None
    _assert_judged_alike(record)


def test_an_unexpected_exception_from_a_helper_names_the_helper_line():
    run, verify = _recording()

    def driver() -> None:
        raise TimeoutError("no reply")

    line = driver.__code__.co_firstlineno + 1
    with pytest.raises(TimeoutError):
        with verify.raises(KeyError, match="ch", name="Lookup"):
            driver()
    [record] = run.records
    assert record["raised_at"].endswith(f":{line}")
    assert record["match_check"] is None  # the type did not match: match was not searched
    assert record["detail"].startswith(
        "expected KeyError matching /ch/, got TimeoutError: no reply (raised at "
    )


def test_an_inner_block_records_what_an_outer_block_expects():
    run, verify = _recording()
    with verify.raises(KeyError, name="outer") as outer:
        with verify.raises(ValueError, name="inner") as inner:
            raise KeyError("ch9")
    assert inner.check["passed"] is False and outer.check["passed"] is True
    assert [record["name"] for record in run.records] == ["inner", "outer"]


# ---------------------------------------------------------------------------
# Exceptions that pass through and record nothing
# ---------------------------------------------------------------------------

_PASSING_THROUGH = [
    pytest.param(lambda: KeyboardInterrupt(), id="KeyboardInterrupt"),
    pytest.param(lambda: SystemExit(1), id="SystemExit"),
    pytest.param(lambda: pytest.skip.Exception("no bench"), id="pytest.skip"),
    pytest.param(lambda: pytest.fail.Exception("rig broke"), id="pytest.fail"),
    pytest.param(lambda: pytest.exit.Exception("bye"), id="pytest.exit"),
    pytest.param(lambda: unittest.SkipTest("no bench"), id="unittest.SkipTest"),
    pytest.param(lambda: bdb.BdbQuit(), id="bdb.BdbQuit"),
]


@pytest.mark.parametrize("make", _PASSING_THROUGH)
def test_an_exception_that_ends_the_test_passes_through(make):
    run, verify = _recording()
    error = make()
    with pytest.raises(BaseException) as excinfo:
        with verify.raises(ValueError, name="Reject") as raised:
            raise error
    assert excinfo.value is error
    assert run.records == []
    assert raised.value is None
    with pytest.raises(RuntimeError):
        raised.check  # noqa: B018
    # The block was used: nothing is reported for it at the end of the phase either.
    assert run.take_unjudged() == (0, [])


@pytest.mark.parametrize("make", _PASSING_THROUGH)
def test_an_exception_that_ends_the_test_is_checked_when_expected(make):
    run, verify = _recording()
    error = make()
    with verify.raises(type(error), name="Expected") as raised:
        raise error
    assert raised.check["passed"] is True
    assert raised.value is error and raised.type is type(error)
    assert run.records == [raised.check]


def test_exception_and_base_exception_do_not_name_them():
    run, verify = _recording()
    with pytest.raises(KeyboardInterrupt):
        with verify.raises(BaseException, match="x", name="any"):
            raise KeyboardInterrupt("x")
    with pytest.raises(unittest.SkipTest):
        with verify.raises(Exception, match="x", name="any"):
            raise unittest.SkipTest("x")
    with pytest.raises(bdb.BdbQuit):
        with verify.raises((KeyError, Exception), match="x", name="any"):
            raise bdb.BdbQuit("x")
    assert run.records == []


def test_a_required_check_that_stops_the_test_passes_through():
    run, verify = _recording()
    with pytest.raises(ChecksFailedError) as excinfo:
        with verify.raises(AssertionError, name="Expects an assertion") as raised:
            verify.require.equal(1, 2, name="Link up")
    assert excinfo.value.stops_test is True
    assert [record["name"] for record in run.records] == ["Link up"]
    assert raised.value is None


def test_a_fail_fast_stop_passes_through_even_when_expected():
    run, verify = _recording(fail_fast=True)
    with pytest.raises(ChecksFailedError):
        with verify.raises(ChecksFailedError, name="Expects a stop"):
            verify.equal(1, 2, name="soft")
    assert [record["name"] for record in run.records] == ["soft"]


def test_a_checks_failed_error_that_stops_nothing_is_an_ordinary_exception():
    _, verify = _recording()
    failed = checks.fail("bad")
    with verify.raises(ChecksFailedError, name="Summary") as raised:
        raise ChecksFailedError([dict(failed, passed=False)])
    assert raised.check["passed"] is True


_needs_groups = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="exception groups are Python 3.11+"
)


@_needs_groups
@pytest.mark.parametrize("nested", [False, True], ids=["group", "nested group"])
def test_an_exception_group_holding_a_stop_passes_through(nested):
    group_type = getattr(builtins, "ExceptionGroup")
    run, verify = _recording()
    with pytest.raises(group_type):
        with verify.raises(group_type, name="Group"):
            try:
                verify.require.is_true(False, name="Link up")
            except ChecksFailedError as stop:
                members = [stop, ValueError("other")]
                group = group_type("tasks", members)
                raise group_type("outer", [group]) if nested else group
    assert [record["name"] for record in run.records] == ["Link up"]


@_needs_groups
def test_an_exception_group_without_a_stop_is_checked():
    group_type = getattr(builtins, "ExceptionGroup")
    _, verify = _recording()
    with verify.raises(group_type, match="tasks", name="Group") as raised:
        raise group_type("tasks", [ValueError("a"), KeyError("b")])
    record = raised.check
    assert record["passed"] is True
    assert record["raised_type"] == "ExceptionGroup"
    assert record["raised_message"] == "tasks (2 sub-exceptions)"


@_needs_groups
def test_a_base_exception_group_passes_through_unless_named():
    group_type = getattr(builtins, "BaseExceptionGroup")
    run, verify = _recording()
    with pytest.raises(group_type):
        with verify.raises(ValueError, name="Reject"):
            raise group_type("tasks", [KeyboardInterrupt()])
    assert run.records == []
    with verify.raises(group_type, name="Group") as raised:
        raise group_type("tasks", [KeyboardInterrupt()])
    assert raised.check["passed"] is True


# ---------------------------------------------------------------------------
# Usage errors: raised by verify.raises() itself, before the block runs
# ---------------------------------------------------------------------------

_USAGE_ERRORS = [
    pytest.param((Exception,), {}, TypeError, r"Exception is too broad without match=",
                 id="Exception"),
    pytest.param((BaseException,), {}, TypeError, r"BaseException is too broad", id="Base"),
    pytest.param(((OutOfRange, Exception),), {}, TypeError, r"Exception is too broad",
                 id="Exception in a tuple"),
    pytest.param((ValueError,), {"match": ""}, ValueError, r"match must not be empty",
                 id="empty match"),
    pytest.param((ValueError,), {"match": re.compile("")}, ValueError,
                 r"match must not be empty", id="empty compiled match"),
    pytest.param((Exception,), {"match": ""}, ValueError, r"match must not be empty",
                 id="Exception with an empty match"),
    pytest.param((ValueError,), {"match": "limit (V"}, ValueError,
                 r"match is not a valid regular expression", id="invalid regex"),
    pytest.param((ValueError,), {"match": 3}, TypeError,
                 r"match must be a string or a compiled pattern, got int", id="int match"),
    pytest.param((ValueError,), {"match": b"x"}, TypeError,
                 r"match must be a string or a compiled pattern, got bytes", id="bytes match"),
    pytest.param((ValueError,), {"match": re.compile(b"x")}, TypeError,
                 r"not a bytes pattern", id="bytes pattern"),
    pytest.param((int,), {}, TypeError,
                 r"expected_exception must be an exception class or a tuple of them, got "
                 r"<class 'int'>", id="int"),
    pytest.param(("ValueError",), {}, TypeError, r"got 'ValueError'", id="class name"),
    pytest.param((ValueError("x"),), {}, TypeError, r"got ValueError\('x'\)", id="instance"),
    pytest.param(((),), {}, TypeError, r"got \(\)", id="empty tuple"),
    pytest.param(((ValueError, int),), {}, TypeError, r"got <class 'int'>", id="int in tuple"),
    pytest.param(([ValueError],), {}, TypeError, r"expected_exception must be", id="list"),
    pytest.param((None,), {}, TypeError, r"got None", id="None"),
    pytest.param((ValueError,), {"name": ""}, ValueError, r"raises\(\) name must not be empty",
                 id="empty name"),
    pytest.param((ValueError,), {"name": "  "}, ValueError, r"name must not be empty",
                 id="blank name"),
    pytest.param((ValueError,), {"name": 3}, TypeError, r"raises\(\) name must be a str",
                 id="int name"),
]


@pytest.mark.parametrize("args, kwargs, error, message", _USAGE_ERRORS)
def test_a_usage_error_raises_at_the_call_and_records_nothing(args, kwargs, error, message):
    run, verify = _recording()
    kwargs = dict({"name": "Reject"}, **kwargs)
    with pytest.raises(error, match=message):
        verify.raises(*args, **kwargs)
    assert run.records == [] and run.blocks == []
    # Not a block that was never used: nothing is reported at the end of the phase.
    assert run.take_unjudged() == (0, [])


@pytest.mark.skipif(sys.version_info < (3, 10), reason="X | Y unions are Python 3.10+")
def test_a_union_is_not_accepted():
    _, verify = _recording()
    union = eval("KeyError | ValueError")  # parsed at run time: Python 3.9 cannot build it
    with pytest.raises(TypeError, match="expected_exception must be an exception class"):
        verify.raises(union, name="Reject")


def test_name_is_required():
    _, verify = _recording()
    with pytest.raises(TypeError, match="name"):
        verify.raises(ValueError)  # type: ignore[call-arg]


def test_a_usage_error_does_not_run_the_block():
    _, verify = _recording()
    ran = []
    with pytest.raises(TypeError):
        with verify.raises(Exception, name="any"):
            ran.append("block")
    assert ran == []


def test_exception_with_match_is_accepted():
    _, verify = _recording()
    with verify.raises(Exception, match="out of range", name="any") as raised:
        _set_7_volts()
    assert raised.check["passed"] is True
    assert raised.check["expected_types"] == ["builtins.Exception"]


# ---------------------------------------------------------------------------
# The Raises object
# ---------------------------------------------------------------------------


def test_verify_raises_returns_a_raises_context_manager():
    _, verify = _recording()
    block = verify.raises(ValueError, name="Reject")
    assert isinstance(block, Raises)
    assert Raises[ValueError] is not None  # generic at runtime
    assert "Raises" in pytest_verifier.__all__
    with block as entered:
        raise ValueError("x")
    assert entered is block


def test_check_before_the_block_ends_is_a_runtime_error():
    _, verify = _recording()
    block = verify.raises(ValueError, name="Reject")
    with pytest.raises(RuntimeError, match=r"made when the with block ends"):
        block.check  # noqa: B018
    with block:
        with pytest.raises(RuntimeError, match=r"made when the with block ends"):
            block.check  # noqa: B018
        raise ValueError("x")
    assert block.check["passed"] is True


def test_value_and_type_are_none_before_the_block_ends():
    _, verify = _recording()
    block = verify.raises(ValueError, name="Reject")
    assert block.value is None and block.type is None
    with block:
        raise ValueError("x")


def test_a_block_can_run_only_once():
    run, verify = _recording()
    block = verify.raises(ValueError, name="Reject")
    with block:
        raise ValueError("x")
    ran = []
    with pytest.raises(RuntimeError, match=r"can run only once"):
        with block:
            ran.append("second")
    assert ran == []
    assert len(run.records) == 1 and block.check["passed"] is True


def test_check_is_readable_after_a_required_block_stopped_the_test():
    run, verify = _recording()
    with pytest.raises(ChecksFailedError):
        with verify.require.raises(KeyError, name="Must raise") as raised:
            pass
    assert raised.check is run.records[-1]
    assert raised.check["passed"] is False


# ---------------------------------------------------------------------------
# A verify.raises() never used in a with statement
# ---------------------------------------------------------------------------


def test_a_block_never_entered_fails_at_the_end_of_the_phase():
    run, verify = _recording()
    line = _line() + 1
    block = verify.raises(ValueError, match="range", name="Forgotten")
    assert run.records == []  # nothing until the phase ends
    start, pending = run.take_unjudged()
    assert start == 0
    [record] = pending
    assert record["name"] == "Forgotten" and record["passed"] is False
    assert record["error"].startswith("verify.raises() was never used in a with statement")
    assert "with verify.raises(...):" in record["detail"]
    assert record["location"].endswith(f":{line}")
    assert record["phase"] == "call"
    assert run.records[0]["name"] == "Forgotten"
    json.dumps(run.records[0], allow_nan=False)
    assert checks.evaluate(_round_trip(run.records[0])) is False
    with pytest.raises(RuntimeError):
        block.check  # noqa: B018
    # Reported once.
    assert run.take_unjudged() == (1, [])


def test_an_entered_block_is_not_reported_as_unused():
    run, verify = _recording()
    with verify.raises(ValueError, name="Used"):
        raise ValueError("x")
    _, pending = run.take_unjudged()
    assert [record["name"] for record in pending] == ["Used"]


def test_checks_raises_is_a_runtime_error():
    with pytest.raises(RuntimeError, match=r"checks.raises\(\) cannot check a block"):
        checks.raises(ValueError, name="Reject")
    with pytest.raises(RuntimeError, match=r"checks.require cannot stop a test"):
        checks.require.raises(ValueError, name="Reject")


# ---------------------------------------------------------------------------
# verify.require.raises and fail-fast
# ---------------------------------------------------------------------------


def test_require_raises_stops_at_the_end_of_a_failed_block():
    run, verify = _recording()
    ran = []
    with pytest.raises(ChecksFailedError) as excinfo:
        with verify.require.raises(KeyError, name="Must raise"):
            ran.append("block")
        ran.append("after")
    assert ran == ["block"]
    assert excinfo.value.stops_test is True
    assert str(excinfo.value).startswith(
        "1 of 1 checks failed, stopped at [0]: Must raise — expected KeyError, nothing was raised"
    )
    [record] = run.records
    assert record["passed"] is False


def test_require_raises_with_another_message_stops_and_keeps_the_value():
    run, verify = _recording()
    with pytest.raises(ChecksFailedError) as excinfo:
        with verify.require.raises(KeyError, match="ch1", name="Must raise") as raised:
            raise KeyError("ch2")
    assert excinfo.value.stops_test is True
    assert isinstance(raised.value, KeyError)
    assert run.records[0]["match_check"] is False


def test_require_raises_that_passes_does_not_stop():
    run, verify = _recording()
    with verify.require.raises(KeyError, name="Must raise") as raised:
        raise KeyError("ch2")
    assert raised.check["passed"] is True and run.records == [raised.check]


def test_fail_fast_stops_at_a_failed_block():
    run, verify = _recording(fail_fast=True)
    with pytest.raises(ChecksFailedError) as excinfo:
        with verify.raises(KeyError, name="Must raise"):
            pass
    assert excinfo.value.stops_test is True
    assert run.records[0]["passed"] is False


def test_fail_fast_leaves_a_failed_block_in_teardown_soft():
    run, verify = _recording(fail_fast=True)
    run.phase = "teardown"
    with verify.raises(KeyError, name="Must raise") as raised:
        pass
    assert raised.check["passed"] is False
    assert raised.check["phase"] == "teardown"


# ---------------------------------------------------------------------------
# Where the check was made and where the exception was raised
# ---------------------------------------------------------------------------


def test_the_location_is_the_with_line_and_raised_at_the_raising_line():
    _, verify = _recording()
    with_line = _line() + 1
    with verify.raises(ValueError, name="Reject") as raised:
        x = 1
        y = x + 1
        _set_7_volts()
        del y
    record = raised.check
    assert record["location"].endswith(f"test_raises.py:{with_line}")
    raise_line = _set_7_volts.__code__.co_firstlineno + 1
    assert record["raised_at"].endswith(f"test_raises.py:{raise_line}")


def test_raised_at_skips_the_plugin_frames_of_a_usage_error():
    _, verify = _recording()
    with verify.raises(ValueError, match="tolerance|abs_tol|rel_tol", name="Usage") as raised:
        line = _line() + 1
        verify.approx(1.0, 1.0, name="no tolerance")
    assert raised.check["passed"] is True
    assert raised.check["raised_at"].endswith(f"test_raises.py:{line}")


def test_a_block_in_a_section_gets_the_section():
    _, verify = _recording()
    with verify.section("3V3"), verify.raises(ValueError, name="Reject") as raised:
        raise ValueError("x")
    assert raised.check["section"] == ["3V3"]


def test_a_check_returned_by_a_factory_is_absorbed():
    run, verify = _recording()

    def reject(volts: int) -> Any:
        with verify.raises(ValueError, match="out of range", name=f"Reject {volts} V") as block:
            if volts > 5:
                raise ValueError(f"{volts} V is out of range")
        return block.check

    record = verify.all_satisfy([7, 3], reject, name="Rejects")
    assert run.records == [record]
    assert record["passed"] is False
    assert [child["passed"] for child in record["child_checks"]] == [True, False]


# ---------------------------------------------------------------------------
# The record: fields, details, JSON safety
# ---------------------------------------------------------------------------


def test_the_record_fields():
    _, verify = _recording()
    with verify.raises((KeyError, OutOfRange), match="range", name="Reject") as raised:
        _set_7_volts()
    record = dict(raised.check)
    for key in ("raised_at", "location"):
        assert isinstance(record.pop(key), str)
    assert record == {
        "check_type": "raises",
        "name": "Reject",
        "description": "Verify 'Reject' raises KeyError | OutOfRange matching /range/",
        "expected_type": "KeyError | OutOfRange",
        "expected_types": ["builtins.KeyError", f"{__name__}.OutOfRange"],
        "match": "range",
        "flags": 0,
        "raised_type": "OutOfRange",
        "raised_message": "7 V is out of range",
        "type_check": True,
        "match_check": True,
        "passed": True,
        "detail": "raised OutOfRange: 7 V is out of range",
        "phase": "call",
    }


def test_a_message_whose_str_raises_fails_a_match_with_an_error():
    _, verify = _recording()
    with verify.raises(ValueError, match="x", name="Hostile") as raised:
        raise HostileError()
    record = raised.check
    assert record["passed"] is False
    assert record["raised_type"] == "HostileError" and record["raised_message"] is None
    assert record["match_check"] is False
    assert record["error"] == "match cannot be searched: str() of the exception raised"
    assert record["detail"].startswith("expected ValueError matching /x/, got HostileError (")
    assert record["detail"].endswith(f"({record['error']})")
    _assert_judged_alike(record)


def test_a_message_whose_str_raises_passes_without_match():
    _, verify = _recording()
    with verify.raises(ValueError, name="Hostile") as raised:
        raise HostileError()
    record = raised.check
    assert record["passed"] is True and record["detail"] == "raised HostileError"
    assert "error" not in record
    _assert_judged_alike(record)


def test_a_long_message_is_capped_but_the_verdict_stands():
    _, verify = _recording()
    with verify.raises(ValueError, match="needle$", name="Long") as raised:
        raise ValueError("x" * 50_000 + "needle")
    record = raised.check
    assert record["passed"] is True
    assert len(record["raised_message"]) <= 10_000
    assert len(record["detail"]) < 400
    _assert_judged_alike(record)  # the stored match_check decides, not the capped text


def test_control_characters_are_escaped_in_the_detail():
    _, verify = _recording()
    with verify.raises(ValueError, match="two", name="Lines") as raised:
        raise ValueError("one\ntwo\x00")
    record = raised.check
    assert record["raised_message"] == "one\ntwo\x00"
    assert record["detail"] == r"raised ValueError: one\ntwo\x00"
    assert "\n" not in record["detail"]


def test_lone_surrogates_encode_as_utf8():
    _, verify = _recording()
    with verify.raises(ValueError, name="Surrogate") as raised:
        raise ValueError("bad \ud800 name")
    json.dumps(raised.check, ensure_ascii=False, allow_nan=False).encode("utf-8")


def test_every_record_is_json_safe_and_judged_alike():
    run, verify = _recording()
    with verify.raises(ValueError, name="pass"):
        raise ValueError("x")
    with verify.raises(ValueError, name="nothing"):
        pass
    with verify.raises(ValueError, match="volts (V)", name="no match"):
        raise ValueError("volts (V)")
    with pytest.raises(KeyError):
        with verify.raises(ValueError, name="unexpected"):
            raise KeyError("k")
    with verify.raises(ValueError, match="x", name="hostile"):
        raise HostileError()
    with verify.raises(ValueError, match=re.compile("X", re.I), name="flags"):
        raise ValueError("x")
    verify.raises(ValueError, name="never")
    run.take_unjudged()
    assert [record["name"] for record in run.records] == [
        "pass", "nothing", "no match", "unexpected", "hostile", "flags", "never"
    ]
    assert [record["passed"] for record in run.records] == [
        True, False, False, False, False, True, False
    ]
    for record in run.records:
        _assert_judged_alike(record)


def test_a_hand_built_descriptor_compares_type_names():
    built = {
        "check_type": "raises",
        "name": "Reject",
        "description": "Verify 'Reject' raises KeyError | ValueError",
        "expected_type": "KeyError | ValueError",
        "raised_type": "ValueError",
    }
    assert checks.evaluate(built) is True
    assert checks.evaluate(dict(built, raised_type="TypeError")) is False
    assert checks.evaluate(dict(built, raised_type=None)) is False
    matching = dict(built, match="OUT", flags=int(re.I), raised_message="out of range")
    assert checks.evaluate(matching) is True
    assert checks.evaluate(dict(matching, flags=0)) is False


# ---------------------------------------------------------------------------
# In a pytest session
# ---------------------------------------------------------------------------


def _reports(pytester: pytest.Pytester, *args: str) -> Dict[str, List[Dict[str, Any]]]:
    """Run the session in process and return each test's recorded checks, all phases."""
    reprec = pytester.inline_run("-p", "no:cacheprovider", *args)
    found: Dict[str, List[Dict[str, Any]]] = {}
    for report in reprec.getreports("pytest_runtest_logreport"):
        name = report.nodeid.split("::")[-1]
        found.setdefault(name, []).extend(getattr(report, "verify_checks", None) or [])
    return found


def test_locations_in_a_session(pytester: pytest.Pytester):
    pytester.makepyfile(
        helpers="""
        def driver():
            raise ValueError("bad unit")


        def reject(verify):
            with verify.raises(ValueError, name="in helper"):
                driver()
        """,
        test_where="""
        from helpers import driver, reject

        def test_single(verify):
            with verify.raises(ValueError, name="single"):
                setup = 1
                driver()

        def test_multi_line(verify):
            with verify.raises(
                ValueError,
                match="volts",
                name="multi",
            ):
                driver()

        def test_helper(verify):
            reject(verify)

        def test_raised_here(verify):
            with verify.raises(KeyError, name="here"):
                {}["ch9"]
        """,
    )
    pytester.syspathinsert()
    found = _reports(pytester)
    [single] = found["test_single"]
    assert single["location"] == "test_where.py:4"
    assert single["raised_at"] == "helpers.py:2"
    assert "called_from" not in single
    [multi] = found["test_multi_line"]
    assert multi["location"] == "test_where.py:9"
    assert multi["passed"] is False
    assert "(raised at helpers.py:2)" in multi["detail"]
    [helper] = found["test_helper"]
    assert helper["location"] == "helpers.py:6"
    assert helper["called_from"] == "test_where.py:17"
    [here] = found["test_raised_here"]
    assert here["raised_at"] == "test_where.py:21" and here["passed"] is True
    for records in found.values():
        for record in records:
            json.dumps(record, allow_nan=False)


def test_soft_failures_in_a_session(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_soft="""
        def test_soft(verify):
            with verify.raises(KeyError, name="Missing key"):
                pass
            print("AFTER SOFT")
            with verify.raises(ValueError, match="limit (V)", name="Reject"):
                raise ValueError("above the limit (V)")
            print("AFTER MATCH")
        """
    )
    result = pytester.runpytest("-s", "-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*AFTER SOFT*",
            "*AFTER MATCH*",
            "*2 of 2 checks failed: Missing key — expected KeyError, nothing was raised (+1 more)",
            "*✗ [[]0[]] Missing key (test_soft.py:2) — expected KeyError, nothing was raised",
            "*✗ [[]1[]] Reject (test_soft.py:5) — expected ValueError matching /limit (V)/, got "
            "ValueError: above the limit (V) (raised at test_soft.py:6); match is a regular "
            "expression: use re.escape() to match it as text",
        ]
    )


def test_an_unexpected_exception_in_a_session(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_unexpected="""
        def test_unexpected(verify):
            with verify.raises(ValueError, name="Reject"):
                1 / 0
            print("NOT REACHED")
        """
    )
    result = pytester.runpytest("-s", "-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            ">*1 / 0",
            "E * ZeroDivisionError: division by zero",
            "*Soft assertion failures*",
            "*✗ [[]0[]] Reject (test_unexpected.py:2) — expected ValueError, got "
            "ZeroDivisionError: division by zero (raised at test_unexpected.py:3)",
        ]
    )
    result.stdout.no_fnmatch_line("*NOT REACHED*")


def test_a_block_never_used_fails_the_test(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_never="""
        def test_never(verify):
            raised = verify.raises(ValueError, name="Forgotten")
            verify.equal(1, 1, name="after")
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*1 of 2 checks failed: Forgotten — expected ValueError, nothing was raised "
            "(verify.raises() was never used in a with statement*",
            "*✗ [[]0[]] Forgotten (test_never.py:2)*",  # where it was made: before "after"
        ]
    )


def test_skip_and_exit_in_a_block_in_a_session(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_through="""
        import unittest
        import pytest

        def test_skip(verify):
            with verify.raises(ValueError, name="Reject"):
                pytest.skip("no bench")

        def test_unittest_skip(verify):
            with verify.raises(Exception, match="bench", name="Reject"):
                raise unittest.SkipTest("no bench")

        def test_exit(verify):
            with verify.raises(ValueError, name="Reject"):
                pytest.exit("operator quit")

        def test_not_run():
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "-rs")
    result.assert_outcomes(skipped=2)
    result.stdout.fnmatch_lines(["*operator quit*"])
    assert result.ret == pytest.ExitCode.INTERRUPTED
    result.stdout.no_fnmatch_line("*checks failed*")


def test_require_raises_stops_the_test_in_a_session(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_require="""
        def test_require(verify):
            with verify.require.raises(KeyError, name="Must raise"):
                pass
            print("NOT REACHED")

        def test_require_passes(verify):
            with verify.require.raises(KeyError, name="Must raise"):
                {}["ch9"]
            print("REACHED")
        """
    )
    result = pytester.runpytest("-s", "-p", "no:cacheprovider")
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(
        [
            "*REACHED*",
            "*1 of 1 checks failed, stopped at [[]0[]]: Must raise — expected KeyError, "
            "nothing was raised",
        ]
    )
    result.stdout.no_fnmatch_line("*NOT REACHED*")


def test_fail_fast_stops_at_a_failed_block_in_a_session(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_fast="""
        def test_fast(verify):
            with verify.raises(KeyError, name="Passes"):
                {}["ch9"]
            print("AFTER PASS")
            with verify.raises(ValueError, match="range", name="Wrong message"):
                raise ValueError("unit")
            print("NOT REACHED")
            verify.equal(1, 1, name="never made")
        """
    )
    result = pytester.runpytest("-s", "-p", "no:cacheprovider", "--verify-fail-fast")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*AFTER PASS*",
            "*1 of 2 checks failed, stopped at [[]1[]]: Wrong message — expected ValueError "
            "matching /range/, got ValueError: unit (raised at test_fast.py:6)",
        ]
    )
    result.stdout.no_fnmatch_line("*NOT REACHED*")
    result.stdout.no_fnmatch_line("*never made*")


@pytest.mark.parametrize("mode", ["require", "fail-fast"])
def test_an_unexpected_exception_keeps_its_traceback_when_the_check_stops(
    pytester: pytest.Pytester, mode: str
):
    block = "verify.require.raises" if mode == "require" else "verify.raises"
    pytester.makepyfile(
        test_stop=f"""
        def test_stop(verify):
            with {block}(KeyError, name="Must raise"):
                1 / 0
        """
    )
    args = ["--verify-fail-fast"] if mode == "fail-fast" else []
    result = pytester.runpytest("-p", "no:cacheprovider", *args)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([">*1 / 0", "E * ZeroDivisionError: division by zero"])


# ---------------------------------------------------------------------------
# Bug and corner-case hunt (0.10.0): blocks in tries, composites and phases
# ---------------------------------------------------------------------------


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sampling on a clock that moves only when slept on: nothing here sleeps for real."""
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(_sampling, "_clock", lambda: now[0])
    monkeypatch.setattr(_sampling, "_sleep", sleep)


class BusyError(Exception):
    """The DUT is busy: not the error a block expects."""


def _busy_reject(verify: Any) -> Any:
    """A required block that gets an unexpected exception, which goes on."""
    with verify.require.raises(ValueError, name="Reject 7 V") as raised:
        raise BusyError("busy")
    return raised.check  # pragma: no cover - not reached


def test_a_block_made_in_a_dropped_try_is_dropped_with_it(no_wait):
    run, verify = _recording()
    reads = iter([OSError("bus busy"), OSError("bus busy"), None])

    def sample():
        raised = verify.raises(ValueError, match="range", name="Reject 7 V")
        error = next(reads)
        if error is not None:
            raise error  # the try fails before its with statement
        with raised:
            _set_7_volts()
        return raised.check

    record = verify.eventually(sample, timeout=1, name="Reject settles")
    assert (record["passed"], record["tries"]) == (True, 3)
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Reject settles"]


def test_a_block_never_entered_in_the_kept_try_is_reported(no_wait):
    run, verify = _recording()

    def sample():
        verify.raises(ValueError, name="Forgotten")
        return verify.equal(1, 1, name="Ready")

    verify.eventually(sample, timeout=1, name="Settles")
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Forgotten", "Settles"]
    assert pending[0]["error"].startswith("verify.raises() was never used in a with statement")


def test_a_sample_that_returns_an_unused_block_is_reported_once(no_wait):
    run, verify = _recording()
    record = verify.eventually(
        lambda: verify.raises(ValueError, name="Reject"), timeout=1, name="Rejects"
    )
    assert record["error"].startswith(
        "sample returned a verify.raises() block, not a check: it must be used in a with "
        "statement"
    )
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Rejects"]


def test_a_sample_that_returns_a_used_block_is_told_to_return_its_check(no_wait):
    run, verify = _recording()

    def sample():
        with verify.raises(ValueError, name="Reject") as raised:
            raise ValueError("out of range")
        return raised  # meant raised.check

    record = verify.eventually(sample, timeout=1, name="Rejects")
    assert record["error"] == (
        "sample returned a verify.raises() block, not a check: return raised.check, not the "
        "block"
    )


def test_a_block_never_entered_keeps_its_section_and_its_place():
    run, verify = _recording()
    first = verify.equal(1, 1, name="before")
    with verify.section("3V3"):
        line = _line() + 1
        verify.raises(ValueError, name="Reject 7 V")
        inside = verify.equal(1, 1, name="Vout")
    after = verify.equal(1, 1, name="after")
    start, pending = run.take_unjudged()
    assert start == 0
    assert [check["name"] for check in pending] == ["before", "Reject 7 V", "Vout", "after"]
    # The records the test already holds keep their identity.
    assert run.records[0] is first and run.records[2] is inside and run.records[3] is after
    record = run.records[1]
    assert record["section"] == ["3V3"]
    assert record["location"].endswith(f":{line}")
    assert record["phase"] == "call"
    assert record["passed"] is False


def test_a_block_never_entered_goes_after_the_checks_an_earlier_phase_judged():
    run, verify = _recording()
    block = verify.raises(ValueError, name="Made in the body")
    verify.equal(1, 1, name="body")
    run.blocks, kept = [], run.blocks  # as if made after the body's checks were judged
    assert [check["name"] for check in run.take_unjudged()[1]] == ["body"]
    run.phase = "teardown"
    verify.equal(1, 1, name="teardown")
    run.blocks = kept
    start, pending = run.take_unjudged()
    assert start == 1
    assert [check["name"] for check in pending] == ["Made in the body", "teardown"]
    with pytest.raises(RuntimeError):
        block.check  # noqa: B018


_BEFORE_WITH = {
    "stop": 'verify.require.is_true(False, name="DUT ready")',
    "error": 'raise ConnectionError("bench lost")',
    "skip": 'pytest.skip("no bench")',
    "xfail": 'pytest.xfail("known DUT bug")',
}

_OUTCOMES = {
    "stop": {"failed": 1},
    "error": {"failed": 1},
    "skip": {"skipped": 1},
    "xfail": {"xfailed": 1},
}


@pytest.mark.parametrize("ending", sorted(_BEFORE_WITH))
def test_a_block_the_test_never_reached_is_not_reported(pytester: pytest.Pytester, ending: str):
    pytester.makepyfile(
        test_cut=f"""
        import pytest

        def test_cut(verify):
            reject = verify.raises(ValueError, name="Reject 7 V")
            {_BEFORE_WITH[ending]}
            with reject:
                raise ValueError("x")
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "-rA")
    result.assert_outcomes(**_OUTCOMES[ending])
    result.stdout.no_fnmatch_line("*never used*")
    if ending == "stop":
        result.stdout.fnmatch_lines(["1 of 1 checks failed, stopped at [[]0[]]: DUT ready *"])
    else:
        result.stdout.no_fnmatch_line("*checks failed*")
        result.stdout.no_fnmatch_line("*Soft assertion failures*")


def test_a_skip_in_a_sample_before_its_with_stays_a_skip(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_skip="""
        import pytest

        def test_skip(verify):
            def sample():
                raised = verify.raises(ValueError, name="Reject 7 V")
                pytest.skip("no bench")
                with raised:
                    raise ValueError("x")
            verify.eventually(sample, timeout=1, name="Rejects")
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(skipped=1)


def test_a_testcase_skip_before_the_with_stays_a_skip(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_case="""
        import unittest

        import pytest

        class TestBench(unittest.TestCase):
            @pytest.fixture(autouse=True)
            def _verify(self, verify):
                self.verify = verify

            def test_reject(self):
                reject = self.verify.raises(ValueError, name="Reject 7 V")
                self.skipTest("no bench")
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(skipped=1)


def test_blocks_never_entered_in_fixtures(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_fixtures="""
        import pytest

        @pytest.fixture
        def dut(verify):
            verify.equal(1, 1, name="setup ok")
            verify.raises(ValueError, name="never entered in setup")
            yield
            verify.equal(1, 2, name="td check")
            verify.raises(ValueError, name="never entered in teardown")

        def test_bench(dut, verify):
            verify.equal(1, 1, name="body ok")
        """
    )
    reprec = pytester.inline_run("-p", "no:cacheprovider")
    reports = {report.when: report for report in reprec.getreports("pytest_runtest_logreport")}
    call, teardown = reports["call"], reports["teardown"]
    assert [(check["name"], check["phase"], check["passed"]) for check in call.verify_checks] == [
        ("setup ok", "setup", True),
        ("never entered in setup", "setup", False),
        ("body ok", "call", True),
    ]
    assert call.verify_checks[1]["location"] == "test_fixtures.py:6"
    assert teardown.failed
    assert "RuntimeError" not in teardown.longreprtext
    assert teardown.longreprtext.startswith("2 of 2 checks failed: td check")
    assert [(check["name"], check["phase"]) for check in teardown.verify_checks] == [
        ("td check", "teardown"),
        ("never entered in teardown", "teardown"),
    ]


def test_a_block_never_entered_with_setup_only(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_setup_only="""
        import pytest

        @pytest.fixture
        def dut(verify):
            verify.raises(ValueError, name="never entered")
            yield

        def test_bench(dut):
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "--setup-only")
    result.stdout.no_fnmatch_line("*RuntimeError*")
    result.stdout.fnmatch_lines(["*1 of 1 checks failed: never entered*"])


@pytest.mark.parametrize("where", ["eventually", "conditional", "guard", "all_satisfy"])
def test_a_required_block_whose_exception_is_taken_stops_once_recorded(no_wait, where: str):
    run, verify = _recording()

    def reject(*_: Any) -> Any:
        return _busy_reject(verify)

    with pytest.raises(ChecksFailedError) as excinfo:
        if where == "eventually":
            verify.eventually(reject, timeout=0.3, name="Rejects")
        elif where == "conditional":
            verify.conditional("on", cases={"on": reject}, name="Rejects")
        elif where == "guard":
            verify.guard([(True, "on", reject)], name="Rejects")
        else:
            verify.all_satisfy([7], reject, name="Rejects")
        verify.equal(1, 1, name="not reached")
    assert excinfo.value.stops_test is True
    assert str(excinfo.value).startswith("2 of 2 checks failed, stopped at [0]: Reject 7 V")
    assert [check["name"] for check in run.records] == ["Reject 7 V", "Rejects"]


def test_a_required_block_in_a_dropped_try_does_not_stop(no_wait):
    run, verify = _recording()
    errors = iter([BusyError("busy"), ValueError("out of range")])

    def sample():
        with verify.require.raises(ValueError, name="Reject 7 V") as raised:
            raise next(errors)
        return raised.check

    record = verify.eventually(sample, timeout=1, name="Rejects")
    assert (record["passed"], record["tries"]) == (True, 2)
    assert [check["name"] for check in run.records] == ["Rejects"]


def test_a_required_block_whose_exception_the_test_catches_stops_no_later_composite():
    run, verify = _recording()
    with pytest.raises(BusyError):
        _busy_reject(verify)
    record = verify.all_satisfy([1], lambda item: verify.equal(item, 1, name="one"), name="Ones")
    assert record["passed"] is True
    assert [check["name"] for check in run.records] == ["Reject 7 V", "Ones"]


@pytest.mark.parametrize("fail_fast", [False, True], ids=["require", "fail-fast"])
def test_a_stop_at_the_end_of_a_block_hides_the_exception_it_took(fail_fast: bool):
    _, verify = _recording(fail_fast=fail_fast)
    block = verify.raises if fail_fast else verify.require.raises
    with pytest.raises(ChecksFailedError) as excinfo:
        with block(ValueError, match="range", name="Reject") as raised:
            raise ValueError("bad value")
    assert excinfo.value.stops_test is True
    assert excinfo.value.__suppress_context__ is True
    assert excinfo.value.__cause__ is None
    assert isinstance(raised.value, ValueError)


def test_a_required_block_with_another_message_prints_only_the_summary(
    pytester: pytest.Pytester,
):
    pytester.makepyfile(
        test_message="""
        def test_message(verify):
            with verify.require.raises(ValueError, match="range", name="Reject"):
                raise ValueError("bad value")
        """
    )
    for tb in ("auto", "line"):
        result = pytester.runpytest("-p", "no:cacheprovider", f"--tb={tb}")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*1 of 1 checks failed, stopped at [[]0[]]: Reject *"])
        result.stdout.no_fnmatch_line("*During handling*")
        result.stdout.no_fnmatch_line("bad value")


class NotesError(ValueError):
    """An exception whose notes cannot be read, though its message can."""

    @property
    def __notes__(self) -> List[str]:
        raise RuntimeError("hostile notes")


def test_notes_that_cannot_be_read_are_no_notes():
    _, verify = _recording()
    with verify.raises(ValueError, match="hello", name="Notes") as raised:
        raise NotesError("hello")
    record = raised.check
    assert record["passed"] is True
    assert record["raised_message"] == "hello"
    assert "error" not in record
    _assert_judged_alike(record)


# ---------------------------------------------------------------------------
# Review of the hunt fixes: blocks made by code that a try or a composite took the error of
# ---------------------------------------------------------------------------


def _build_composite(verify: Any, where: str, child: Any) -> Any:
    """A composite named "Rejects" that calls *child* (with no argument) as its user code."""
    if where == "conditional":
        return verify.conditional("on", cases={"on": child}, name="Rejects")
    if where == "guard":
        return verify.guard([(True, "on", child)], name="Rejects")
    return verify.all_satisfy([7], lambda _: child(), name="Rejects")


def test_a_block_made_in_a_kept_try_that_raised_is_not_reported(no_wait):
    run, verify = _recording()

    def sample():
        raised = verify.raises(ValueError, name="Reject 7 V")
        raise BusyError("busy")  # every try, the kept one too, fails before its with
        with raised:  # pragma: no cover - not reached
            _set_7_volts()
        return raised.check  # pragma: no cover - not reached

    record = verify.eventually(sample, timeout=0.3, name="Rejects")
    assert record["passed"] is False
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Rejects"]
    assert "BusyError: busy" in pending[0]["detail"]


@pytest.mark.parametrize("where", ["conditional", "guard", "all_satisfy"])
def test_a_block_made_by_a_lazy_child_that_raised_is_not_reported(where: str):
    run, verify = _recording()

    def reject():
        raised = verify.raises(ValueError, name="Reject 7 V")
        raise BusyError("busy")
        with raised:  # pragma: no cover - not reached
            _set_7_volts()
        return raised.check  # pragma: no cover - not reached

    record = _build_composite(verify, where, reject)
    assert "BusyError: busy" in record["error"]
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Rejects"]


@pytest.mark.parametrize("where", ["conditional", "guard", "all_satisfy"])
def test_a_lazy_child_that_returns_an_unused_block_is_reported_once(where: str):
    run, verify = _recording()
    record = _build_composite(verify, where, lambda: verify.raises(ValueError, name="Reject"))
    assert "returned a verify.raises() block, not a check: it must be used" in record["error"]
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Rejects"]


@pytest.mark.parametrize("where", ["conditional", "guard", "all_satisfy"])
def test_a_block_a_lazy_child_forgot_is_still_reported(where: str):
    run, verify = _recording()

    def ready():
        verify.raises(ValueError, name="Forgotten")  # no with: a real mistake
        return verify.equal(1, 1, name="Ready")

    assert _build_composite(verify, where, ready)["passed"] is True
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Forgotten", "Rejects"]
    assert pending[0]["error"].startswith("verify.raises() was never used in a with statement")


def test_a_block_made_by_a_guard_condition_that_raised_is_not_reported():
    run, verify = _recording()

    def ready():
        verify.raises(ValueError, name="Reject 7 V")
        raise BusyError("busy")

    record = verify.guard([(ready, "on", verify.equal(1, 1, name="Ready"))], name="Mode")
    assert "BusyError: busy" in record["error"]
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Mode"]


def test_a_block_a_guard_condition_returns_is_still_reported():
    run, verify = _recording()
    condition = lambda: verify.raises(ValueError, name="Forgotten")  # noqa: E731 - truthy
    record = verify.guard([(condition, "on", verify.equal(1, 1, name="Ready"))], name="Mode")
    assert record["passed"] is True
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Forgotten", "Mode"]


def test_a_block_made_by_a_nested_lazy_child_goes_when_an_outer_one_raises():
    run, verify = _recording()

    def inner():
        verify.raises(ValueError, name="Reject 7 V")
        return verify.equal(1, 1, name="Inner ready")

    def outer():
        verify.conditional("on", cases={"on": inner}, name="Inner")
        raise BusyError("busy")  # after the inner composite: its block is never reached

    record = verify.conditional("on", cases={"on": outer}, name="Outer")
    assert "BusyError: busy" in record["error"]
    _, pending = run.take_unjudged()
    assert [check["name"] for check in pending] == ["Inner", "Outer"]


@pytest.mark.parametrize("where", ["eventually", "conditional", "guard", "all_satisfy"])
def test_a_required_block_whose_exception_the_user_code_catches_stops_nothing(
    no_wait, where: str
):
    """As at the top level of the test: only an exception that a try or a composite takes as
    a failure makes the stop wait for it."""
    run, verify = _recording()

    def ready(*_: Any) -> Any:
        with pytest.raises(BusyError):
            _busy_reject(verify)
        return verify.equal(1, 1, name="Ready")

    if where == "eventually":
        record = verify.eventually(ready, timeout=0.3, name="Rejects")
    else:
        record = _build_composite(verify, where, ready)
    assert record["passed"] is True
    after = verify.equal(1, 1, name="after")
    assert [check["name"] for check in run.records] == ["Reject 7 V", "Rejects", "after"]
    assert run.records[2] is after


def test_a_required_block_taken_by_a_lazy_child_in_a_sample_stops_after_the_sampling(no_wait):
    run, verify = _recording()

    def sample():
        return verify.conditional("on", cases={"on": lambda: _busy_reject(verify)}, name="Mode")

    with pytest.raises(ChecksFailedError) as excinfo:
        verify.eventually(sample, timeout=0.3, name="Rejects")
    assert str(excinfo.value).startswith("2 of 2 checks failed, stopped at [0]: Reject 7 V")
    assert [check["name"] for check in run.records] == ["Reject 7 V", "Rejects"]
