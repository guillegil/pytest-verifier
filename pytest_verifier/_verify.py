"""The ``Verify`` front-end: one typed method per check type.

Each method builds a descriptor with its check type and hands it to the instance's sink. The
sink of ``pytest_verifier.checks`` returns it unevaluated; the fixture's sink judges and records
it (see :mod:`pytest_verifier._run`).
"""
from __future__ import annotations

import re
from typing import (
    Any,
    Callable,
    Container,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Sized,
    Tuple,
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
    child_checks,
)
from ._descriptors import CheckDescriptor, Child, ClassInfo, loose_children
from ._evaluator import evaluate as _evaluate
from ._evaluator import evaluate_detailed as _evaluate_detailed

#: The type of the items an ``all_satisfy`` factory receives.
_Item = TypeVar("_Item")

_RECORD_NEEDS_FIXTURE = (
    "checks.record() cannot record a check: pytest_verifier.checks only builds checks. Request "
    "the 'verify' fixture in the test and call verify.record() on it."
)


class Sink:
    """Where a :class:`Verify` sends the checks it builds.

    This one, used by ``pytest_verifier.checks``, returns them unevaluated.
    """

    def check(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        """Take a check that has no children."""
        _unused.built(descriptor)
        return descriptor

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

    def record(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        """Take a check built elsewhere."""
        _unused.used(descriptor)  # the error below already says what went wrong
        raise RuntimeError(_RECORD_NEEDS_FIXTURE)


_BUILD_ONLY = Sink()


class Verify:
    """Soft-assertion builder.

    As ``pytest_verifier.checks``, methods return unevaluated :class:`CheckDescriptor`
    dicts (no ``passed`` field).

    When wrapped by the pytest fixture, the fixture evaluates each descriptor
    immediately after construction and sets the ``passed`` field.

    No check raises because of the value it checks: a comparison that raises, or whose
    result has no clear truth value (a numpy array), makes the check fail with an ``error``
    note. Only invalid arguments (a missing ``name``, ``approx`` without a tolerance, a
    negative tolerance) raise ``TypeError``/``ValueError``.
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
        return self._sink.check(IS_TRUE.build(actual, name=name))

    def is_false(self, actual: Any, *, name: str) -> CheckDescriptor:
        """Check that ``bool(actual)`` is ``False``.

        Args:
            actual: The value under test.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        return self._sink.check(IS_FALSE.build(actual, name=name))

    def is_none(self, actual: Any, *, name: str) -> CheckDescriptor:
        """Check that *actual* is ``None``.

        Args:
            actual: The value under test.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
        return self._sink.check(IS_NONE.build(actual, name=name))

    def is_not_none(self, actual: Any, *, name: str) -> CheckDescriptor:
        """Check that *actual* is not ``None``.

        Args:
            actual: The value under test.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.
        """
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
            expected_type: A class, a tuple of classes, or a union such as ``int | None``.
            name: Human-readable label for the check.

        Returns:
            A :class:`CheckDescriptor` dict.

        Raises:
            TypeError: If *expected_type* is not something ``isinstance`` accepts.
        """
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
        return self._sink.composite(
            lambda: GUARD.build(branches, default=default, name=name),
            (branches, default),
        )

    def fail(self, msg: str, *, name: Optional[str] = None) -> CheckDescriptor:
        """Unconditionally failing check.

        Args:
            msg: Failure message.
            name: Optional label (defaults to *msg*).

        Returns:
            A :class:`CheckDescriptor` dict.
        """
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
        return self._sink.record(check)

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
