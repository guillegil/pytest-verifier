"""The check builders under plain function names, for tests that build descriptors directly."""
from __future__ import annotations

from pytest_verifier import _checks

build_equal = _checks.EQUAL.build
build_not_equal = _checks.NOT_EQUAL.build
build_approx = _checks.APPROX.build
build_greater = _checks.GREATER.build
build_greater_equal = _checks.GREATER_EQUAL.build
build_less = _checks.LESS.build
build_less_equal = _checks.LESS_EQUAL.build
build_between = _checks.BETWEEN.build
build_is_true = _checks.IS_TRUE.build
build_is_false = _checks.IS_FALSE.build
build_is_none = _checks.IS_NONE.build
build_is_not_none = _checks.IS_NOT_NONE.build
build_contains = _checks.CONTAINS.build
build_not_contains = _checks.NOT_CONTAINS.build
build_matches = _checks.MATCHES.build
build_is_instance = _checks.IS_INSTANCE.build
build_length = _checks.LENGTH.build
build_all_satisfy = _checks.ALL_SATISFY.build
build_conditional = _checks.CONDITIONAL.build
build_guard = _checks.GUARD.build
build_fail = _checks.FAIL.build
