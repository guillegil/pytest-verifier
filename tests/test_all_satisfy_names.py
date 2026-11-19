"""``all_satisfy`` keeps words for reports: ``items_name`` (the source text of ``items``) and
``item_name`` (the factory's parameter), so a procedure can read ``for each w in waveforms``."""
from __future__ import annotations

import functools
import json
from typing import Any

from pytest_verifier import checks
from pytest_verifier._checks._composites import parameter_name
from pytest_verifier._run import Run, recording_verify

INDEX_ERROR_BIT = 4


def _recording() -> Any:
    run = Run()
    run.phase = "call"
    return run, recording_verify(run)


def _is_zero(word: int) -> Any:
    return checks.equal(word & INDEX_ERROR_BIT, 0, name="Index error")


class TestDescriptor:
    def test_names_from_the_call_and_the_lambda(self) -> None:
        waveforms = [0, 1, 8]
        d = checks.all_satisfy(
            waveforms, lambda w: checks.equal(w & INDEX_ERROR_BIT, 0), name="Index error"
        )
        assert d["items_name"] == "waveforms"
        assert d["item_name"] == "w"

    def test_a_def_factory_names_its_parameter(self) -> None:
        d = checks.all_satisfy(range(3), _is_zero, name="Index error")
        assert d["items_name"] == "range(3)"
        assert d["item_name"] == "word"

    def test_an_expression_is_its_text(self) -> None:
        data = {"w": [1, 2]}
        d = checks.all_satisfy(data["w"], lambda x: checks.greater(x, 0), name="Positive")
        assert d["items_name"] == 'data["w"]'

    def test_a_partial_has_no_item_name(self) -> None:
        d = checks.all_satisfy([1, 2], functools.partial(checks.less, 0), name="Less")
        assert "item_name" not in d
        assert d["items_name"] == "[1, 2]"

    def test_without_source_there_is_no_items_name(self) -> None:
        scope: dict = {"checks": checks}
        exec("d = checks.all_satisfy([1], lambda v: checks.is_true(v), name='T')", scope)
        assert "items_name" not in scope["d"]
        assert scope["d"]["item_name"] == "v"

    def test_description_and_verdict_are_unchanged(self) -> None:
        d = checks.all_satisfy([1, 2], lambda v: checks.greater(v, 0), name="Positive")
        assert d["description"] == "Verify all items in 'Positive' satisfy condition (2 items)"
        assert checks.evaluate(d) is True


class TestParameterName:
    def test_kinds(self) -> None:
        def two(a: Any, b: Any = 1) -> Any:
            return a

        def none() -> Any:
            return None

        def only_star(*args: Any) -> Any:
            return args

        assert parameter_name(lambda item: item) == "item"
        assert parameter_name(two) == "a"
        assert parameter_name(none) is None
        assert parameter_name(only_star) is None
        assert parameter_name(len) is None
        assert parameter_name(str) is None
        assert parameter_name(object().__eq__) is None


class TestFixture:
    def test_recorded_and_json_safe(self) -> None:
        _, verify = _recording()
        waveforms = [0, 4]
        record = verify.all_satisfy(
            waveforms,
            lambda w: verify.equal(w & INDEX_ERROR_BIT, 0, name="Index error"),
            name="Index Error is 0",
        )
        assert record["items_name"] == "waveforms"
        assert record["item_name"] == "w"
        assert record["passed"] is False
        assert len(record["child_checks"]) == 2
        json.dumps(record)

    def test_require_names_too(self) -> None:
        _, verify = _recording()
        flags = [True]
        record = verify.require.all_satisfy(flags, lambda f: verify.is_true(f), name="Flags")
        assert record["items_name"] == "flags"
