"""Regression tests: descriptor data, types and value lifetime (bugs found in 0.3.1).

Every test here asserts the correct behaviour for a bug found by the 0.3.1 review
(see ``bugs-0.3.1.md``) and fixed in 0.4.0. Each one failed on 0.3.1 and keeps the bug
from coming back.

Bugs covered by this module:

* H-5: module-level ``is_instance`` matches by bare class name, so unrelated same-name
  classes pass and runtime-checkable Protocols fail.
* M-2: ``is_instance`` crashes on tuples of types, PEP 604 unions and typing constructs.
* M-3: descriptors are not JSON-serializable for common inputs (enum ``switch_value``,
  sets, bytes, Decimal, datetime, custom objects, NaN/inf).
* M-4: the failure summary renders live objects, so it shows post-mutation values that
  contradict the recorded verdict.
* M-5: every checked value is kept alive through ``item.stash`` until the session ends.
* L-6: ``ChecksFailedError`` cannot be pickled or copied.
* L-10: ``typing.get_type_hints`` fails on Python 3.9 for ``CheckDescriptor`` and most
  ``Verify`` methods.
"""
from __future__ import annotations

import _pydecimal
import asyncio
import concurrent.futures
import copy
import datetime
import decimal
import enum
import inspect
import json
import pickle
import re
import sys
import types
import typing
from typing import Any, Callable

import pytest

from pytest_verifier import CheckDescriptor, Verify, get_check_results
from pytest_verifier import checks as mverify
from pytest_verifier._exceptions import ChecksFailedError
from pytest_verifier._run import Run, recording_verify


# ── H-5: module-level is_instance matches by bare class name ────────


def _make_class(qualified_name: str) -> type:
    """Create an empty class named like ``module.Name`` (a same-named class elsewhere)."""
    module, _, name = qualified_name.rpartition(".")
    return types.new_class(name, exec_body=lambda ns: ns.update(__module__=module))


_VendorAConfig = _make_class("vendor_a.Config")
_VendorBConfig = _make_class("vendor_b.Config")


@pytest.mark.parametrize(
    ("actual", "expected_type"),
    [
        pytest.param(_VendorAConfig(), _VendorBConfig, id="vendor_a.Config-vs-vendor_b.Config"),
        # ``_pydecimal`` sets ``__name__ = 'decimal'``, so both classes are ``decimal.Decimal``
        # by qualified name too: only a verdict computed from the type object (the fix in
        # bugs-0.3.1.md) tells them apart.
        pytest.param(decimal.Decimal(1), _pydecimal.Decimal, id="decimal-vs-_pydecimal"),
        pytest.param(
            concurrent.futures.CancelledError(),
            asyncio.CancelledError,
            id="futures-vs-asyncio-CancelledError",
        ),
    ],
)
def test_h5_unrelated_same_name_class_does_not_pass(actual, expected_type):
    """An object whose class merely shares ``expected_type.__name__`` is not an instance."""
    if isinstance(actual, expected_type):
        pytest.skip("the two classes are the same (or related) on this interpreter")
    descriptor = mverify.is_instance(actual, expected_type, name="obj")
    assert mverify.evaluate(descriptor) is False


@typing.runtime_checkable
class _Readable(typing.Protocol):
    def read(self) -> bytes: ...


class _Device:
    """Structurally satisfies ``_Readable`` without inheriting from it."""

    def read(self) -> bytes:
        return b""


def test_h5_runtime_checkable_protocol_instance_passes():
    """``isinstance`` accepts a structural match of a runtime-checkable Protocol."""
    assert isinstance(_Device(), _Readable)
    descriptor = mverify.is_instance(_Device(), _Readable, name="device")
    assert mverify.evaluate(descriptor) is True


def test_h5_fixture_composite_with_same_name_module_child_fails(pytester):
    """A fixture ``all_satisfy`` whose (module-built) children all fail ``isinstance`` must
    fail the test. In 0.3.1 the name-based evaluator passes them and the test goes green."""
    pytester.makepyfile("""
        import types

        from pytest_verifier import checks as mverify


        def _make_class(module):
            return types.new_class("Config", exec_body=lambda ns: ns.update(__module__=module))


        VendorAConfig = _make_class("vendor_a")
        VendorBConfig = _make_class("vendor_b")


        def test_configs(verify):
            verify.all_satisfy(
                [VendorAConfig(), VendorAConfig()],
                lambda cfg: mverify.is_instance(cfg, VendorBConfig, name="cfg"),
                name="all configs are vendor_b.Config",
            )
    """)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)


# ── M-2: is_instance on tuples, unions and typing constructs ────────


def _build_is_instance(actual: Any, expected_type: Any) -> CheckDescriptor:
    """Build a module ``is_instance`` descriptor, turning the M-2 crash into a test failure."""
    try:
        return mverify.is_instance(actual, expected_type, name="value")
    except AttributeError as exc:
        pytest.fail(f"is_instance crashed while building for {expected_type!r}: {exc!r}")


@pytest.mark.parametrize("value", [1, 2.5, "1"], ids=["int", "float", "str"])
def test_m2_tuple_of_types_builds_and_matches_isinstance(value):
    """``(int, float)`` is accepted and the verdict equals ``isinstance(value, (int, float))``."""
    descriptor = _build_is_instance(value, (int, float))
    assert mverify.evaluate(descriptor) is isinstance(value, (int, float))


@pytest.mark.skipif(sys.version_info < (3, 10), reason="PEP 604 unions need Python 3.10+")
@pytest.mark.parametrize("value", [1, "1", 2.5], ids=["int", "str", "float"])
def test_m2_pep604_union_builds_and_matches_isinstance(value):
    """``int | str`` is accepted and the verdict equals ``isinstance(value, int | str)``."""
    union = int | str
    descriptor = _build_is_instance(value, union)
    assert mverify.evaluate(descriptor) is isinstance(value, union)


def test_m2_typing_optional_is_rejected_or_matches_isinstance():
    """``typing.Optional[int]`` is either rejected with a clear ``TypeError`` or evaluated like
    ``isinstance``. In 0.3.1, 3.9 crashed with AttributeError, and 3.10+ builds an
    'is instance of Optional' check that fails for ``1``."""
    try:
        descriptor = mverify.is_instance(1, typing.Optional[int], name="value")
    except TypeError:
        return  # a clean, explicit rejection is acceptable
    except AttributeError as exc:
        pytest.fail(f"is_instance crashed while building for Optional[int]: {exc!r}")
    assert mverify.evaluate(descriptor) is True, descriptor["description"]


def test_m2_fixture_tuple_of_types_check_passes(pytester):
    """Through the fixture, a tuple-of-types check that holds lets the test pass."""
    pytester.makepyfile("""
        def test_numeric(verify):
            verify.is_instance(1, (int, float), name="numeric")
            verify.equal(1, 1, name="later check")
    """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


# ── M-3: JSON-serializability of descriptors ────────────────────────


class _Mode(enum.Enum):
    ACTIVE = 1


class _Obj:
    """An arbitrary user object with no JSON representation."""


def _assert_json_safe(data: Any) -> None:
    """Assert ``data`` serializes as standard JSON (no ``NaN``/``Infinity`` tokens)."""
    try:
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        pytest.fail(f"not JSON-serializable: {type(exc).__name__}: {exc}")


def test_m3_module_conditional_with_enum_switch_value_is_json_serializable():
    """The README's enum-keyed ``conditional`` gives a JSON-serializable descriptor: case keys
    are stringified, so ``switch_value`` must be JSON-safe too."""
    descriptor = mverify.conditional(
        _Mode.ACTIVE, cases={_Mode.ACTIVE: mverify.equal(1, 1, name="a")}, name="M"
    )
    _assert_json_safe(descriptor)


def test_m3_fixture_conditional_with_enum_switch_value_is_json_serializable(verify, request):
    """What a reporter reads through ``get_check_results`` for an enum switch is JSON-safe."""
    verify.conditional(
        _Mode.ACTIVE, cases={_Mode.ACTIVE: verify.equal(1, 1, name="a")}, name="M"
    )
    _assert_json_safe(get_check_results(request.node))


# Passing fixture checks on common non-JSON inputs (all pass, so no teardown failure).
_M3_FIXTURE_CALLS: dict[str, Callable[[Any], Any]] = {
    "set": lambda v: v.equal({1, 2}, {1, 2}, name="ids"),
    "bytes": lambda v: v.equal(b"ab", b"ab", name="frame"),
    "decimal": lambda v: v.equal(decimal.Decimal("3.3"), decimal.Decimal("3.3"), name="price"),
    "datetime": lambda v: v.equal(
        datetime.datetime(2021, 1, 1), datetime.datetime(2021, 1, 1), name="stamp"
    ),
    "custom-object": lambda v: v.is_not_none(_Obj(), name="handle"),
    "nan": lambda v: v.is_not_none(float("nan"), name="reading"),
    "inf-bound": lambda v: v.between(5.0, 0.0, float("inf"), name="open range"),
}


@pytest.mark.parametrize("case", list(_M3_FIXTURE_CALLS))
def test_m3_fixture_results_are_json_serializable_for_common_inputs(verify, request, case):
    """``json.dumps(..., allow_nan=False)`` of the recorded results succeeds for any input.

    If the fix narrows the documented JSON guarantee instead, remove this test with it.
    """
    _M3_FIXTURE_CALLS[case](verify)
    _assert_json_safe(get_check_results(request.node))


# ── M-4: reports must show the values that were compared ────────────


def _summary_line(pytester: pytest.Pytester, source: str, name: str) -> str:
    """Run ``source`` (one soft-failing test) and return the ``[seq] name`` summary line.

    Any ``[seq]`` index is accepted, so a fix that renumbers composite children still matches.
    """
    pytester.makepyfile(source)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    pattern = re.compile(r"\[\d+\] " + re.escape(name) + r"\b")
    lines = [line for line in result.outlines if pattern.search(line)]
    assert lines, f"no summary line for check {name!r}"
    return lines[0]


def test_m4_failed_equal_reports_the_value_that_was_compared(pytester):
    """The dict was ``{'mode': 'idle'}`` when compared; the summary must not claim
    ``expected {'mode': 'run'}, got {'mode': 'run'}`` for a failed check."""
    line = _summary_line(pytester, """
        def test_state(verify):
            state = {"mode": "idle"}
            verify.equal(state, {"mode": "run"}, name="state")
            state["mode"] = "run"
    """, "state")
    assert "idle" in line, line


def test_m4_failed_contains_reports_the_haystack_that_was_searched(pytester):
    """The log was ``['boot']`` when searched; the summary must not show the later append."""
    line = _summary_line(pytester, """
        def test_log(verify):
            log = ["boot"]
            verify.contains(log, "ready", name="log check")
            log.append("ready")
    """, "log check")
    assert "'boot', 'ready'" not in line, line


def test_m4_passed_equal_reports_the_value_that_was_compared(pytester):
    """A passing check must not be rendered as ``[1, 2, 99] == [1, 2]``."""
    line = _summary_line(pytester, """
        def test_readings(verify):
            readings = [1, 2]
            verify.equal(readings, [1, 2], name="initial readings")
            readings.append(99)
            verify.fail("force summary")
    """, "initial readings")
    assert "99" not in line, line


def test_m4_all_satisfy_failed_count_uses_recorded_child_verdicts(pytester):
    """One child failed when checked; mutating it afterwards must not make the summary say
    ``got 0 failed`` for a failed ``all_satisfy``."""
    line = _summary_line(pytester, """
        def test_buffers(verify):
            buffers = [[0], [1]]
            verify.all_satisfy(
                buffers,
                lambda b: verify.equal(b, [0], name="buffer"),
                name="buffers zeroed",
            )
            buffers[1][0] = 0
    """, "buffers zeroed")
    assert not re.search(r"\b0 failed", line), line


# ── M-5: checked values must not outlive their test ─────────────────

# Inner conftest: ``track`` records weak references; at session end, after a full GC, it
# dumps which tracked objects are still alive.
_M5_CONFTEST = """
    import gc
    import json
    import weakref

    import pytest

    _REFS = {}


    @pytest.fixture
    def track():
        def _track(label, obj):
            _REFS[label] = weakref.ref(obj)

        return _track


    def pytest_sessionfinish(session):
        gc.collect()
        alive = {label: ref() is not None for label, ref in _REFS.items()}
        (session.config.rootpath / "alive.json").write_text(json.dumps(alive))
"""


@pytest.mark.parametrize(
    "call",
    [
        pytest.param('verify.length(capture, 1024, name="capture size")', id="length"),
        pytest.param('verify.equal(capture, capture, name="capture")', id="equal"),
        pytest.param('verify.is_not_none(capture, name="capture")', id="is_not_none"),
    ],
)
def test_m5_checked_object_is_collectable_after_its_test(pytester, call):
    """An object passed to a check is freed once its test finishes, like the same object in a
    test that does not use ``verify``."""
    pytester.makeconftest(_M5_CONFTEST)
    pytester.makepyfile(f"""
        class Capture:
            \"\"\"Stands in for a large capture, frame or waveform.\"\"\"

            def __init__(self):
                self.data = bytearray(1024)

            def __len__(self):
                return len(self.data)


        def test_with_verify(verify, track):
            capture = Capture()
            track("checked", capture)
            {call}


        def test_without_verify(track):
            capture = Capture()
            track("control", capture)
            assert len(capture) == 1024
    """)
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=2)
    alive = json.loads((pytester.path / "alive.json").read_text())
    assert alive["control"] is False  # sanity: pytest itself frees the test's objects
    assert alive["checked"] is False, "object passed to a check is still alive at session end"


# ── L-6: ChecksFailedError round-trips through pickle / copy ────────


def _checks_failed_error() -> ChecksFailedError:
    failed = dict(mverify.equal(1, 2, name="X"), passed=False)
    passed = dict(mverify.equal(3, 3, name="Y"), passed=True)
    return ChecksFailedError([failed, passed])


@pytest.mark.parametrize(
    "clone",
    [
        pytest.param(lambda e: pickle.loads(pickle.dumps(e)), id="pickle"),
        pytest.param(copy.copy, id="copy"),
        pytest.param(copy.deepcopy, id="deepcopy"),
    ],
)
def test_l6_checks_failed_error_round_trips(clone):
    """Like any ``AssertionError``, the error survives pickling and copying intact (this is
    what a process-pool worker needs to re-raise it in the parent)."""
    error = _checks_failed_error()
    cloned = clone(error)
    assert type(cloned) is ChecksFailedError
    assert str(cloned) == str(error)
    assert cloned.results == error.results


# ── L-10: runtime type-hint resolution on every supported Python ────


def test_l10_check_descriptor_type_hints_resolve():
    """``typing.get_type_hints(CheckDescriptor)`` works (pydantic, typeguard, sphinx...)."""
    hints = typing.get_type_hints(CheckDescriptor)
    assert "abs_tol" in hints
    assert "default" in hints


@pytest.mark.parametrize(
    "cls", [Verify, type(recording_verify(Run()))], ids=["module", "fixture"]
)
def test_l10_public_verify_method_type_hints_resolve(cls):
    """Every public method's hints resolve at runtime, on the module-level ``Verify`` and on
    the object the ``verify`` fixture returns."""
    failing = []
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        try:
            typing.get_type_hints(method)
        except TypeError as exc:
            failing.append(f"{name}: {exc}")
    assert failing == []
