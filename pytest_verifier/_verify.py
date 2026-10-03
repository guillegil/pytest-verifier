"""The ``Verify`` front-end: one typed method per check type.

Each method builds a descriptor with its check type and hands it to the instance's sink. The
sink of ``pytest_verifier.checks`` returns it unevaluated; the fixture's sink judges and records
it (see :mod:`pytest_verifier._run`).

Methods that hand a check to the sink set ``__tracebackhide__``: when a required check stops
the test, pytest's tracebacks and ``--pdb`` show the test's line, not these frames.
"""
from __future__ import annotations

import datetime
import re
from types import TracebackType
from typing import (
    Any,
    Callable,
    Container,
    ContextManager,
    Dict,
    Generic,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Sized,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from . import _unused
from ._checks import (
    ALL_SATISFY,
    APPROX,
    BETWEEN,
    CONDITIONAL,
    CONTAINS,
    EQUAL,
    FAIL,
    GREATER,
    GREATER_EQUAL,
    GUARD,
    IS_FALSE,
    IS_INSTANCE,
    IS_NONE,
    IS_NOT_NONE,
    IS_TRUE,
    LENGTH,
    LESS,
    LESS_EQUAL,
    MATCHES,
    NOT_CONTAINS,
    NOT_EQUAL,
    RAISES,
    child_checks,
)
from ._checks._raises import exception_classes, expected_instance, handles
from ._checks._sampling import EVENTUALLY, STABLE, Sampler
from ._descriptors import CheckDescriptor, Child, ClassInfo, loose_children
from ._evaluator import evaluate as _evaluate
from ._limits import LimitRow, limit_checks
from ._evaluator import evaluate_detailed as _evaluate_detailed
from ._exceptions import hide_stop_frames
from ._settle import settle

#: The type of the items an ``all_satisfy`` factory receives.
_Item = TypeVar("_Item")

#: The exception type a ``raises`` block expects.
_E = TypeVar("_E", bound=BaseException)

_RECORD_NEEDS_FIXTURE = (
    "checks.record() cannot record a check: pytest_verifier.checks only builds checks. Request "
    "the 'verify' fixture in the test and call verify.record() on it."
)

_SECTION_NEEDS_FIXTURE = (
    "checks.section() cannot group checks: pytest_verifier.checks only builds checks, and only "
    "recorded checks belong to a section. Request the 'verify' fixture in the test and use "
    "verify.section() on it."
)

_RAISES_NEEDS_FIXTURE = (
    "checks.raises() cannot check a block: pytest_verifier.checks only builds checks, and a "
    "raises block means something only when its check is recorded. Request the 'verify' "
    "fixture in the test and use `with verify.raises(...):` on it."
)

_REQUIRE_NEEDS_FIXTURE = (
    "checks.require cannot stop a test: pytest_verifier.checks only builds checks. Request the "
    "'verify' fixture in the test and use verify.require on it."
)


class Sink:
    """Where a :class:`Verify` sends the checks it builds.

    This one, used by ``pytest_verifier.checks``, returns them unevaluated.
    """

    def check(self, descriptor: CheckDescriptor, site: Any = None) -> CheckDescriptor:
        """Take a check that has no children; *site* is where it was made, from :meth:`where`
        (``None``: here)."""
        _unused.built(descriptor)
        return descriptor

    def ended(
        self,
        descriptor: CheckDescriptor,
        site: Any,
        keep: Callable[[CheckDescriptor], None],
        stop: bool,
    ) -> None:
        """Take the check of a ``raises`` block that ended: *keep* gets it before a required
        check stops the test, and *stop* False records it without stopping (an exception
        that goes on stops the test anyway)."""
        keep(self.check(descriptor, site))

    def block(self, raises: Raises[Any]) -> Any:
        """Take a ``raises`` block when it is made, and return where it is made: its check is
        taken when the block ends, but belongs to the ``with`` line."""
        raise RuntimeError(_RAISES_NEEDS_FIXTURE)

    def entered(self, raises: Raises[Any]) -> None:
        """The ``raises`` block is being entered."""

    def origin(self, traceback: Optional[TracebackType]) -> Optional[str]:
        """Where the exception with *traceback* was raised, for a ``raises`` check."""
        return None

    def composite(
        self, build: Callable[[], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
        """Take a check that has children, built by calling *build*.

        *arguments* are the containers of its children, for cleanup when *build* raises.
        """
        try:
            descriptor = build()
        except BaseException:
            _unused.used(*loose_children(*arguments))
            raise
        _unused.used(*child_checks(descriptor))
        _unused.built(descriptor)
        return descriptor

    def sampling(
        self, build: Callable[[Sampler], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
        """Take a check that samples (``eventually``, ``stable``), built by calling *build*
        with the sampler that takes its tries. Here a try's check is settled when it is made,
        so the built check keeps the verdicts its tries had then."""
        try:
            descriptor = build(_SettlingSampler())
        except BaseException:
            _unused.used(*loose_children(*arguments))
            raise
        _unused.built(descriptor)
        return descriptor

    def batch(self, descriptors: List[CheckDescriptor]) -> List[CheckDescriptor]:
        """Take checks that have no children and were built together (a limits table)."""
        for descriptor in descriptors:
            _unused.built(descriptor)
        return descriptors

    def record(self, descriptor: CheckDescriptor, call: str = "record()") -> CheckDescriptor:
        """Take a check built elsewhere; *call* names the method, for usage errors."""
        _unused.used(descriptor)  # the error below already says what went wrong
        raise RuntimeError(_RECORD_NEEDS_FIXTURE)

    def hard(self) -> Sink:
        """The sink of ``require``: its checks stop the test when they fail."""
        return _NO_REQUIRE

    def section(self, title: str) -> ContextManager[None]:
        """Group the checks recorded while the block runs under *title*."""
        raise RuntimeError(_SECTION_NEEDS_FIXTURE)


class _NoRequire(Sink):
    """``checks.require``: only the fixture can stop a test."""

    def check(self, descriptor: CheckDescriptor, site: Any = None) -> CheckDescriptor:
        raise RuntimeError(_REQUIRE_NEEDS_FIXTURE)

    def block(self, raises: Raises[Any]) -> Any:
        raise RuntimeError(_REQUIRE_NEEDS_FIXTURE)

    def composite(
        self, build: Callable[[], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
        _unused.used(*loose_children(*arguments))
        raise RuntimeError(_REQUIRE_NEEDS_FIXTURE)

    def sampling(
        self, build: Callable[[Sampler], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
        _unused.used(*loose_children(*arguments))
        raise RuntimeError(_REQUIRE_NEEDS_FIXTURE)

    def batch(self, descriptors: List[CheckDescriptor]) -> List[CheckDescriptor]:
        raise RuntimeError(_REQUIRE_NEEDS_FIXTURE)

    def record(self, descriptor: CheckDescriptor, call: str = "record()") -> CheckDescriptor:
        _unused.used(descriptor)
        raise RuntimeError(_REQUIRE_NEEDS_FIXTURE)

    def hard(self) -> Sink:
        return self


class _SettlingSampler(Sampler):
    """The tries of ``checks.eventually``/``checks.stable``: each check is judged and
    snapshotted when it is made, as the fixture would record it, so that evaluating the built
    check later agrees with the sampling."""

    def keep(self, check: Any, attempt: Any) -> Any:
        _unused.used(check)
        return settle(check)[0]


_BUILD_ONLY = Sink()
_NO_REQUIRE = _NoRequire()


class Verify:
    """Soft-assertion builder.

    As ``pytest_verifier.checks``, methods return unevaluated :class:`CheckDescriptor`
    dicts (no ``passed`` field).

    When wrapped by the pytest fixture, the fixture evaluates each descriptor
    immediately after construction and sets the ``passed`` field.

    No check raises because of the value it checks: a comparison that raises, or whose
    result has no clear truth value (a numpy array), makes the check fail with an ``error``
    note. Only invalid arguments (a missing ``name``, ``approx`` without a tolerance, a
    negative tolerance) raise ``TypeError``/``ValueError``. A failed check stops the test
    (raises ``ChecksFailedError``) only when it is made through the fixture's
    :attr:`require`, or with ``--verify-fail-fast``.
    """

    #: Where the built checks go; the fixture's instance has a recording sink.
    _sink: Sink = _BUILD_ONLY

    # ------------------------------------------------------------------
    # Equality & approximation
    # ------------------------------------------------------------------

    def equal(
        self, actual: Any, expected: Any, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        """Check that *actual* equals *expected*.

        Args:
            actual: The value under test.
            expected: The expected value.
            name: Human-readable label for the check.
            units: Optional unit suffix (e.g. ``"V"``, ``"A"``).

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(EQUAL.build(actual, expected, name=name, units=units))

    def not_equal(
        self, actual: Any, expected: Any, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        """Check that *actual* does not equal *expected*.

        Args:
            actual: The value under test.
            expected: The value that *actual* must differ from.
            name: Human-readable label for the check.
            units: Optional unit suffix.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(NOT_EQUAL.build(actual, expected, name=name, units=units))

    def approx(
        self,
        actual: Any,
        expected: Any,
        *,
        abs_tol: Optional[float] = None,
        rel_tol: Optional[float] = None,
        name: str,
        units: Optional[str] = None,
    ) -> CheckDescriptor:
        """Check that *actual* is approximately equal to *expected*.

        At least one of *abs_tol* or *rel_tol* must be provided.

        Args:
            actual: The value under test.
            expected: The expected value.
            abs_tol: Absolute tolerance.
            rel_tol: Relative tolerance (fraction, e.g. 0.01 = 1%).
            name: Human-readable label for the check.
            units: Optional unit suffix.

        Returns:
            A :class:`CheckDescriptor` dict.

        Raises:
            ValueError: If neither *abs_tol* nor *rel_tol* is provided.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(
            APPROX.build(actual, expected, abs_tol=abs_tol, rel_tol=rel_tol, name=name, units=units)
        )

    # ------------------------------------------------------------------
    # Ordering & range
    # ------------------------------------------------------------------

    def greater(
        self, actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        """Check that *actual* > *threshold*.

        Args:
            actual: The value under test.
            threshold: The lower bound (exclusive).
            name: Human-readable label for the check.
            units: Optional unit suffix.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(GREATER.build(actual, threshold, name=name, units=units))

    def greater_equal(
        self, actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        """Check that *actual* >= *threshold*.

        Args:
            actual: The value under test.
            threshold: The lower bound (inclusive).
            name: Human-readable label for the check.
            units: Optional unit suffix.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(GREATER_EQUAL.build(actual, threshold, name=name, units=units))

    def less(
        self, actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        """Check that *actual* < *threshold*.

        Args:
            actual: The value under test.
            threshold: The upper bound (exclusive).
            name: Human-readable label for the check.
            units: Optional unit suffix.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(LESS.build(actual, threshold, name=name, units=units))

    def less_equal(
        self, actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        """Check that *actual* <= *threshold*.

        Args:
            actual: The value under test.
            threshold: The upper bound (inclusive).
            name: Human-readable label for the check.
            units: Optional unit suffix.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(LESS_EQUAL.build(actual, threshold, name=name, units=units))

    def between(
        self,
        actual: Any,
        low: float,
        high: float,
        *,
        inclusive: bool = True,
        name: str,
        units: Optional[str] = None,
    ) -> CheckDescriptor:
        """Check that *actual* is between *low* and *high*.

        Args:
            actual: The value under test.
            low: Lower bound.
            high: Upper bound.
            inclusive: If ``True`` (default), bounds are inclusive.
            name: Human-readable label for the check.
            units: Optional unit suffix.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(
            BETWEEN.build(actual, low, high, inclusive=inclusive, name=name, units=units)
        )

    # ------------------------------------------------------------------
    # Boolean & identity
    # ------------------------------------------------------------------

    def is_true(self, actual: Any, *, name: str) -> CheckDescriptor:
        """Check that ``bool(actual)`` is ``True``.

        Args:
            actual: The value under test.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(IS_TRUE.build(actual, name=name))

    def is_false(self, actual: Any, *, name: str) -> CheckDescriptor:
        """Check that ``bool(actual)`` is ``False``.

        Args:
            actual: The value under test.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(IS_FALSE.build(actual, name=name))

    def is_none(self, actual: Any, *, name: str) -> CheckDescriptor:
        """Check that *actual* is ``None``.

        Args:
            actual: The value under test.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(IS_NONE.build(actual, name=name))

    def is_not_none(self, actual: Any, *, name: str) -> CheckDescriptor:
        """Check that *actual* is not ``None``.

        Args:
            actual: The value under test.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(IS_NOT_NONE.build(actual, name=name))

    # ------------------------------------------------------------------
    # String & container
    # ------------------------------------------------------------------

    def contains(
        self, haystack: Union[Container[Any], Iterable[Any]], needle: Any, *, name: str
    ) -> CheckDescriptor:
        """Check that *needle* is in *haystack*.

        Args:
            haystack: The container to search.
            needle: The item to search for.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(CONTAINS.build(haystack, needle, name=name))

    def not_contains(
        self, haystack: Union[Container[Any], Iterable[Any]], needle: Any, *, name: str
    ) -> CheckDescriptor:
        """Check that *needle* is **not** in *haystack*.

        Args:
            haystack: The container to search.
            needle: The item to search for.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(NOT_CONTAINS.build(haystack, needle, name=name))

    def matches(
        self, actual: Any, pattern: Union[str, "re.Pattern[str]"], *, name: str
    ) -> CheckDescriptor:
        """Check that *actual* matches the regular-expression *pattern*.

        Args:
            actual: The string to test.
            pattern: A regex pattern (searched with ``re.search``), as a string or compiled
                with ``re.compile``. A compiled pattern is stored as its source and ``flags``.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(MATCHES.build(actual, pattern, name=name))

    # ------------------------------------------------------------------
    # Type / collection / conditional
    # ------------------------------------------------------------------

    def is_instance(self, actual: Any, expected_type: ClassInfo, *, name: str) -> CheckDescriptor:
        """Check that *actual* is an instance of *expected_type*.

        Same as ``isinstance(actual, expected_type)``, computed when the check is built. The
        descriptor stores the type names as strings (``expected_type``, plus the qualified
        names in ``expected_types``) and the result in ``instance_check``.

        Args:
            actual: The value under test.
            expected_type: A class, a tuple of classes, or a union such as ``Optional[int]``
                (``int | None`` from Python 3.10).
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.

        Raises:
            TypeError: If *expected_type* is not something ``isinstance`` accepts.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(IS_INSTANCE.build(actual, expected_type, name=name))

    def length(self, actual: Sized, expected: int, *, name: str) -> CheckDescriptor:
        """Check that ``len(actual)`` equals *expected*.

        Args:
            actual: The sized object under test.
            expected: The expected length.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(LENGTH.build(actual, expected, name=name))

    def all_satisfy(
        self,
        items: Iterable[_Item],
        descriptor_factory: Callable[[_Item], CheckDescriptor],
        *,
        name: str,
    ) -> CheckDescriptor:
        """Check that every item in *items* satisfies *descriptor_factory*.

        The factory is invoked immediately for each item, and the resulting
        child descriptors are stored in the returned descriptor. If *items* cannot be
        iterated, or the factory raises or returns something that is not a check, the check
        fails and its ``error`` says why. With the fixture, the checks the factory makes
        belong to this check.

        Args:
            items: An iterable of items.
            descriptor_factory: A callable that receives one item and returns
                a :class:`CheckDescriptor`.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.composite(
            lambda: ALL_SATISFY.build(items, descriptor_factory, name=name), ()
        )

    def conditional(
        self,
        switch_value: Any,
        *,
        cases: Mapping[Any, Child],
        default: Optional[Child] = None,
        name: str,
    ) -> CheckDescriptor:
        """Conditionally evaluate a check based on *switch_value*.

        The case whose key equals *switch_value* is selected. Enum members compare by
        their value, and an ``int`` matches its decimal string (``1`` and ``"1"``), so
        ``cases={0: ..., 1: ...}`` and ``cases={"0": ..., "1": ...}`` behave the same.
        If no case matches, *default* is used. If no default is provided and no case
        matches, the check fails. In the descriptor the keys are stored as strings
        (enum members as their value).

        A case or default can also be a zero-argument callable that returns the check,
        such as ``lambda: verify.equal(...)``. Only the selected one is called, and the
        others are stored as ``None``.

        Args:
            switch_value: Value to match against *cases* keys.
            cases: Mapping of keys (ints, strings, enum members, ...) to checks or callables
                that return one. Two keys that would be stored as the same string raise
                ``ValueError``.
            default: Fallback check (or callable) when no case matches.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.composite(
            lambda: CONDITIONAL.build(switch_value, cases=cases, default=default, name=name),
            (cases, default),
        )

    def guard(
        self,
        branches: Sequence[Tuple[object, str, Child]],
        *,
        default: Optional[Child] = None,
        name: str,
    ) -> CheckDescriptor:
        """Evaluate an ordered if/elif/else chain of guarded checks.

        Each branch is a ``(condition, label, check)`` tuple. The first branch
        whose condition is truthy is selected; if none match, *default* is used,
        and if there is no default the check fails. The *label* identifies the
        branch in the failure summary.

        A check (or the default) can also be a zero-argument callable that returns it, and a
        condition can be a callable that returns its truth value. Conditions are called in
        order until one is true, and only the selected check is called; checks that were not
        called are stored as ``None``.

        Args:
            branches: Ordered ``(condition, label, check)`` tuples.
            default: Fallback check (or callable) when no condition is truthy.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.composite(
            lambda: GUARD.build(branches, default=default, name=name),
            (branches, default),
        )

    def eventually(
        self,
        sample: Callable[[], CheckDescriptor],
        *,
        timeout: Union[float, datetime.timedelta],
        interval: Union[float, datetime.timedelta] = 0.1,
        name: str,
    ) -> CheckDescriptor:
        """Check that a check passes within *timeout* seconds, trying it again until it does.

        *sample* is a zero-argument callable that reads the value and makes the check, such as
        ``lambda: verify.less(read_temp(), 40, name="Temperature")``. It is called at once,
        then each try starts *interval* seconds after the start of the one before (at once when
        that one took longer), until a try passes or *timeout* has passed; the last try starts
        at the timeout::

            verify.eventually(lambda: verify.equal(dut.state(), "READY", name="State"),
                              timeout=5, name="Boots")

        A try passes when the check it returns passes and so does every other check it
        records. The check passes when a try passed, and keeps that try, or the last one, as
        its child; the other checks that failed the kept try are named in ``also_failed``.
        It records ``tries``, ``elapsed`` and ``settled_at`` (seconds from the call to the
        start of the passing try), and a ``trace`` of ``[seconds, value, passed]`` for the
        first and the last 50 tries. A sample that raises an ``Exception`` fails that try and
        the tries go on; one raised by pytest-verifier itself (a usage error), a sample that
        returns something that is not a check (an ``async def`` sample returns a coroutine),
        or the same check again, fails the check at once. ``pytest.skip``, ``pytest.exit``
        and ``KeyboardInterrupt`` go on, and no try is kept.

        Every check recorded while a try runs belongs to it, and so does the check the sample
        returns when another thread recorded it: a try that is not kept is dropped with its
        checks, so failed tries never fail or stop the test. The kept try's other checks stay
        on their own, and ``verify.require``/fail-fast apply to them once the check is
        recorded. A plain thread (not ``asyncio.to_thread``) runs outside the try: the other
        checks it records stay whatever happens to the try, and a required or fail-fast check
        it records that fails stops that thread at once, even one the sample would return.
        With ``pytest_verifier.checks`` each try is evaluated when it is taken.

        It waits with ``time.sleep``, in the calling thread: in an ``async`` test it blocks
        the event loop (a ``RuntimeWarning`` says so).

        Args:
            sample: A callable with no arguments that reads the value and returns a check.
            timeout: How long to keep trying, in seconds or a ``timedelta`` (0: try once).
            interval: Seconds from the start of one try to the next (more than 0).
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.

        Raises:
            TypeError: If *sample* is not a callable, or *timeout* or *interval* is not a
                number of seconds.
            ValueError: If *timeout* is negative, *interval* is not more than 0, or either is
                not finite.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.sampling(
            lambda sampler: EVENTUALLY.build(
                sample, timeout=timeout, interval=interval, name=name, sampler=sampler
            ),
            (sample,),
        )

    def stable(
        self,
        sample: Callable[[], CheckDescriptor],
        *,
        duration: Union[float, datetime.timedelta],
        interval: Union[float, datetime.timedelta] = 0.1,
        name: str,
    ) -> CheckDescriptor:
        """Check that a check keeps passing for *duration* seconds, trying it again and again.

        *sample* is a zero-argument callable that reads the value and makes the check, such as
        ``lambda: verify.approx(psu.vout(), 3.3, abs_tol=0.05, name="Vout", units="V")``.
        It is called at once, then each try starts *interval* seconds after the start of the
        one before (at once when that one took longer), until a try fails or a try has started
        at or after *duration*, and at least twice when *duration* is more than 0::

            verify.stable(lambda: verify.less(ripple(), 0.05, name="Ripple", units="V"),
                          duration=2, interval=0.2, name="Ripple steady")

        The check passes when every try passed: a try passes when the check it returns passes
        and so does every other check it records. It keeps the try that failed (its failed
        checks stay recorded, named in ``also_failed``), or the passing one closest to its
        limit (the last, for checks without a numeric limit), as its child, and records
        ``tries``, ``elapsed`` and a ``trace`` as :meth:`eventually` does. A sample that raises
        an ``Exception`` fails its try; usage errors, ``pytest.skip``, threads and the other
        tries' checks behave as in :meth:`eventually`.

        It waits with ``time.sleep``, in the calling thread: in an ``async`` test it blocks
        the event loop (a ``RuntimeWarning`` says so).

        Args:
            sample: A callable with no arguments that reads the value and returns a check.
            duration: How long the check must hold, in seconds or a ``timedelta`` (0: try
                once).
            interval: Seconds from the start of one try to the next (more than 0).
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.

        Raises:
            TypeError: If *sample* is not a callable, or *duration* or *interval* is not a
                number of seconds.
            ValueError: If *duration* is negative, *interval* is not more than 0, or either is
                not finite.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.sampling(
            lambda sampler: STABLE.build(
                sample, duration=duration, interval=interval, name=name, sampler=sampler
            ),
            (sample,),
        )

    def limits(
        self,
        measurements: Mapping[Any, Any],
        table: Mapping[str, Union[LimitRow, Mapping[str, Any]]],
        *,
        on_missing: Literal["fail", "ignore"] = "fail",
    ) -> Dict[str, CheckDescriptor]:
        """Check measurements against a table of limits: one check per row, in table order.

        *table* maps each check's name to its row, and the measurement of the same name is the
        value the row checks. A row holds the arguments of a check method after the value
        (``expected``, ``abs_tol``, ``rel_tol``, ``threshold``, ``low``, ``high``,
        ``inclusive``, ``units``, ``needle``, ``pattern``, ``expected_type``) and, under
        ``"check"``, the method: ``equal``, ``not_equal``, ``approx``, ``greater``,
        ``greater_equal``, ``less``, ``less_equal``, ``between``, ``is_true``, ``is_false``,
        ``is_none``, ``is_not_none``, ``contains``, ``not_contains``, ``matches``, ``length``
        or ``is_instance``. Without ``"check"`` the limits say which: ``low`` and ``high`` make
        a ``between``; ``low`` alone a ``greater_equal`` and ``high`` alone a ``less_equal``
        (``greater``/``less`` with ``inclusive=False``); ``expected`` with ``abs_tol`` or
        ``rel_tol`` an ``approx``; ``expected`` alone an ``equal``, except for a ``float``,
        which needs a tolerance or ``"check": "equal"``. ``low``/``high`` can also stand for
        the ``threshold`` of ``greater``/``greater_equal``/``less``/``less_equal``. A ``None``
        value counts as not given, ``"source"`` (text) is kept on the record as
        ``limit_source``, and the table is never changed::

            LIMITS = {
                "Vout": {"expected": 3.3, "abs_tol": 0.05, "units": "V"},
                "Ripple": {"high": 0.05, "units": "V"},
                "FW": {"check": "matches", "pattern": "^v2"},
            }
            verify.limits({"Vout": 3.31, "Ripple": 0.02, "FW": "v2.4"}, LIMITS)

        Limits are strict: ``low``, ``high``, ``threshold``, the tolerances and the
        ``expected`` of ``approx`` must be finite numbers (not text, NaN or infinity),
        ``rel_tol`` a fraction below 1 (``0.02`` is 2%), ``inclusive`` a ``bool``, and the
        ``expected`` of ``length`` a whole number. Measurement names match row names as text:
        an ``int`` key matches its decimal string and an enum member its value. Measurements
        the table has no row for are not checked.

        Every row is built before any check is recorded: an unknown check or argument, a
        missing one, or an invalid one raises and records nothing. The checks are then
        recorded together, so ``verify.require.limits`` (and fail-fast) stops at the first
        failed row only after the whole table is recorded.

        Args:
            measurements: The measured values, by name.
            table: The rows, by name, such as the result of
                :func:`pytest_verifier.load_limits`.
            on_missing: For a row without a measurement: ``"fail"`` records its check as
                failed, with an ``error`` that starts with ``"not measured"`` and names a
                measurement that looks like it (the limits stay in the record); ``"ignore"``
                makes no check for it.

        Returns:
            The checks made, by name, in table order.

        Raises:
            TypeError: If a row is not a mapping, names an argument its check does not take
                or misses one, has a limit of the wrong type, or a measurement name is not a
                str, an int or an enum member.
            ValueError: If the table is empty, a row names an unknown check or has an invalid
                argument, no row has a measurement, or *on_missing* is another value.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return limit_checks(self._sink, measurements, table, on_missing)

    def raises(
        self,
        expected_exception: Union[Type[_E], Tuple[Type[_E], ...]],
        *,
        match: Optional[Union[str, "re.Pattern[str]"]] = None,
        name: str,
    ) -> Raises[_E]:
        """Check that a ``with`` block raises *expected_exception*, without stopping the test.

        The check is recorded when the block ends. It passes when the block raised an instance
        of *expected_exception* (a class or a tuple of classes) whose message (``str()``, and
        its notes) *match* finds, with ``re.search``, when *match* is given::

            with verify.raises(ValueError, match="out of range", name="Reject 7 V") as raised:
                psu.set_voltage(7)
            # raised.value is the exception, raised.check the recorded check

        The expected exception is caught, and the test goes on. If nothing was raised, or the
        message does not match, the check fails and the test goes on too. Any other exception
        makes the check fail and then goes on, with its traceback, as with ``pytest.raises``:
        it is a bug or a broken bench, not the behaviour under test. Exceptions that are not
        an ``Exception`` (``KeyboardInterrupt``, ``pytest.skip``) and the error of a required
        check that stopped the test go on unchanged, and make no check.

        Put only the call that must raise in the block: the statements after it do not run.
        A ``verify.raises()`` that is never used in a ``with`` statement becomes a failed check
        at the end of the test phase.

        Args:
            expected_exception: The exception class, or a tuple of them. ``Exception`` and
                ``BaseException`` need *match*: alone, a typo in the block would pass.
            match: A regular expression (a string or a compiled pattern) that the message
                (``str()`` of the exception, and its notes) must contain; not empty.
            name: Human-readable label for the check.

        Returns:
            A :class:`Raises` context manager; ``raised.check`` is the check once the block
            ended, ``raised.value`` the expected exception or ``None``.

        Raises:
            TypeError: If *expected_exception* is not an exception class or a tuple of them,
                or is too broad without *match*.
            ValueError: If *match* is empty or not a valid regular expression.
            RuntimeError: When called on ``pytest_verifier.checks``: only the fixture can
                record a block's check.
        """
        return Raises(self._sink, expected_exception, match=match, name=name)

    def fail(self, msg: str, *, name: Optional[str] = None) -> CheckDescriptor:
        """Unconditionally failing check.

        Args:
            msg: Failure message.
            name: Optional label (defaults to *msg*).

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.check(FAIL.build(msg, name=name))

    def record(self, check: CheckDescriptor) -> CheckDescriptor:
        """Record a check that was built elsewhere, for example by a helper that uses
        ``pytest_verifier.checks``.

        Only the fixture records checks. The check is judged and recorded like one made
        through the fixture, and a check the fixture already recorded is returned as is.

        Args:
            check: A check descriptor.

        Returns:
            The recorded check, with ``passed`` set.

        Raises:
            TypeError: If *check* is not a check descriptor.
            RuntimeError: When called on ``pytest_verifier.checks``.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.record(check)

    def section(self, title: str) -> ContextManager[None]:
        """Group the checks recorded in a ``with`` block under *title*.

        Every check recorded while the block runs gets a ``section``: the titles of the
        sections it is in, outermost first (``["3V3"]``, or ``["3V3", "Load"]`` when sections
        nest). Summaries show it before the check's name::

            for rail in RAILS:
                with verify.section(rail.name):  # summaries say "3V3 › Vout"
                    verify.approx(rail.vout(), rail.nominal, rel_tol=0.02, name="Vout")

        A check gets the section in which it is recorded: ``verify.record(check)`` gives a
        check built elsewhere the section of that call. Sections are kept in a context
        variable: an asyncio task created in the block is in the section, and a thread is only
        when it runs in a copy of the context (``asyncio.to_thread``,
        ``contextvars.copy_context().run``, or any thread on free-threaded Python 3.14+). A
        section opened around a fixture's ``yield`` covers the test body when the fixture and
        the test run in one context, as a sync fixture does.

        Args:
            title: The title of the section: a string with more than whitespace in it.

        Returns:
            A context manager for a ``with`` statement.

        Raises:
            TypeError: If *title* is not a string.
            ValueError: If *title* is empty or only whitespace.
            RuntimeError: When called on ``pytest_verifier.checks``.
        """
        if not isinstance(title, str):
            raise TypeError(f"section() title must be a string, not {type(title).__name__}")
        if not title.strip():
            raise ValueError("section() title must not be empty")
        return self._sink.section(title)

    @property
    def require(self) -> Require:
        """The same checks, made required: a failed one stops the test at once.

        Use it for a check whose failure makes the rest of the test meaningless, such as a
        connection that could not be opened. ``verify.require.is_not_none(conn, name="Link")``
        records the check like ``verify.is_not_none`` and, if it failed, raises
        ``ChecksFailedError`` right away with every check made so far. Called with a check,
        ``verify.require(check)`` records it like :meth:`record` and stops the same way.

        The checks stay recorded: if the test catches the error and goes on, it still fails at
        the end of the phase. A required check stops the test from inside a lazy child or an
        ``all_satisfy`` factory too.

        On ``pytest_verifier.checks``, its checks and calling it raise ``RuntimeError``.
        """
        required = self.__dict__.get("_required")
        if required is None:
            required = Require()
            required._sink = self._sink.hard()
            self.__dict__["_required"] = required
        return required

    # ------------------------------------------------------------------
    # Evaluation helpers (pytest_verifier.checks)
    # ------------------------------------------------------------------

    @staticmethod
    def evaluate(*descriptors: CheckDescriptor) -> bool:
        """Evaluate descriptors without side effects.

        Every descriptor is evaluated. A check that cannot be evaluated counts as failed.
        A descriptor recorded by the fixture keeps its recorded verdict.

        Returns:
            ``True`` only if **all** descriptors pass.
        """
        return _evaluate(*descriptors)

    @staticmethod
    def evaluate_detailed(*descriptors: CheckDescriptor) -> list[dict[str, Any]]:
        """Evaluate descriptors and return detailed result dicts.

        Each result contains ``passed``, ``details``, ``seq``, and ``t``, plus ``error``
        when the check could not be evaluated.
        """
        return _evaluate_detailed(*descriptors)


class Raises(Generic[_E]):
    """A ``verify.raises`` block: its check is made when the block ends.

    Use it once, in a ``with`` statement; one that is never used in a ``with`` statement
    becomes a failed check at the end of the test phase::

        with verify.raises(ValueError, match="out of range", name="Reject 7 V") as raised:
            psu.set_voltage(7)
        raised.check   # the check, once the block has ended
        raised.value   # the ValueError, or None

    Attributes:
        value: The exception the block raised, when it is of an expected type (whether or not
            *match* found it in its message); else ``None``.
    """

    def __init__(
        self,
        sink: Sink,
        expected_exception: Union[Type[_E], Tuple[Type[_E], ...]],
        *,
        match: Optional[Union[str, "re.Pattern[str]"]] = None,
        name: str,
    ) -> None:
        RAISES.build(None, expected_exception, match=match, name=name)  # usage errors raise now
        self._expected = exception_classes(expected_exception)
        self._arguments: Tuple[Any, Any, str] = (expected_exception, match, name)
        self._sink = sink
        self._entered = False
        self._check: Optional[CheckDescriptor] = None
        self.value: Optional[_E] = None
        # Where the check is made: here, on the ``with`` line (``__exit__`` sees another line
        # on Python 3.9). A sink that cannot record refuses here, before the block runs.
        self._site = sink.block(self)

    @property
    def check(self) -> CheckDescriptor:
        """The check made when the block ended.

        Raises:
            RuntimeError: Before the block has ended.
        """
        if self._check is None:
            raise RuntimeError(
                "verify.raises(): no check yet: it is made when the with block ends (not when "
                "it ended with an exception that makes none, such as pytest.skip); read "
                ".check after the block"
            )
        return self._check

    @property
    def type(self) -> Optional[Type[_E]]:
        """The class of :attr:`value`, or ``None``."""
        return None if self.value is None else type(self.value)

    def __enter__(self) -> Raises[_E]:
        if self._entered:
            raise RuntimeError(
                "a verify.raises() block can run only once: call verify.raises() again for "
                "another block"
            )
        self._entered = True
        self._sink.entered(self)
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> bool:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        if exc is not None and not handles(exc, self._expected):
            return False
        expected = expected_instance(exc, self._expected)
        if expected:
            self.value = exc  # type: ignore[assignment]
        expected_exception, match, name = self._arguments
        where = None if exc is None else self._sink.origin(exc.__traceback__)
        descriptor = RAISES.build(
            exc, expected_exception, match=match, name=name, raised_at=where
        )
        # Only what the code under test did is soft: nothing raised, or the expected type with
        # another message. Any other exception goes on, with its traceback, after the check;
        # it stops the test itself, so a required check does not replace it.
        self._sink.ended(descriptor, self._site, self._keep, stop=exc is None or expected)
        return expected

    def _keep(self, check: CheckDescriptor) -> None:
        self._check = check

    def unentered(self) -> Optional[Tuple[CheckDescriptor, Any]]:
        """The failed check of a block that was never entered, with its site; else ``None``."""
        if self._entered:
            return None
        self._entered = True  # reported once
        expected_exception, match, name = self._arguments
        descriptor = RAISES.build(None, expected_exception, match=match, name=name)
        descriptor["error"] = (
            "verify.raises() was never used in a with statement: write "
            "`with verify.raises(...):` around the code that must raise"
        )
        return descriptor, self._site


class Require(Verify):
    """``verify.require``: every check it makes stops the test when it fails.

    It has the methods of :class:`Verify`, and calling it with a check records that check the
    way :meth:`Verify.record` does, then stops the test if the check failed.
    """

    def __call__(self, check: CheckDescriptor) -> CheckDescriptor:
        """Record *check* and stop the test if it failed.

        Args:
            check: A check descriptor, for example the result of a ``verify.*`` call or one
                built with ``pytest_verifier.checks``.

        Returns:
            The recorded check, with ``passed`` set to ``True``.

        Raises:
            ChecksFailedError: If the check failed. The error lists every check made so far.
            TypeError: If *check* is not a check descriptor.
            RuntimeError: When used on ``pytest_verifier.checks``.
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        return self._sink.record(check, "require()")

    @property
    def require(self) -> Require:
        """This object: its checks are already required."""
        return self
