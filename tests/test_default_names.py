"""A check on a value without ``name=`` is named by the source text of that value."""
from __future__ import annotations

import ast
import functools
import json
import sys
from typing import Any, Iterator, List

import pytest

from pytest_verifier import checks
from pytest_verifier import _source
from pytest_verifier._run import Run, recording_verify


class Register:
    def __init__(self, **fields: int) -> None:
        self.__dict__.update(fields)


class Block:
    esmi3_32dat = Register(txfe=1, tx8e=0)
    esmi3_ctl = Register(tx=0)


@pytest.fixture(params=["positions", "lines"])
def lookup(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Both ways of finding the call: instruction positions (3.11+) and the line (3.9/3.10)."""
    if request.param == "positions" and sys.version_info < (3, 11):
        pytest.skip("instruction positions need Python 3.11+")
    monkeypatch.setattr(_source, "use_positions", request.param == "positions")
    _source.clear_caches()
    yield request.param
    _source.clear_caches()


def _recording() -> Any:
    run = Run()
    run.phase = "call"
    return run, recording_verify(run)


class TestDerivedNames:
    def test_attribute(self, lookup: str) -> None:
        dat = Block.esmi3_32dat
        check = checks.equal(dat.txfe, 1)
        assert check["name"] == "dat.txfe"
        assert check["description"] == "Verify 'dat.txfe' == 1"
        assert check["auto_name"] is True

    def test_alias_variable(self, lookup: str) -> None:
        write_count = 4
        assert checks.greater(write_count, 3)["name"] == "write_count"

    def test_getattr_expression(self, lookup: str) -> None:
        channel = 3
        ctl = getattr(Block, f"esmi{channel}_ctl")
        assert checks.equal(ctl.tx, 0)["name"] == "ctl.tx"
        assert checks.equal(getattr(ctl, "tx"), 0)["name"] == 'getattr(ctl, "tx")'

    def test_keyword_actual(self, lookup: str) -> None:
        reading = 3.31
        check = checks.approx(expected=3.3, actual=reading, abs_tol=0.05)
        assert check["name"] == "reading"

    def test_keyword_haystack(self, lookup: str) -> None:
        lines = ["OK", "READY"]
        assert checks.contains(needle="OK", haystack=lines)["name"] == "lines"
        assert checks.not_contains(lines, "ERR")["name"] == "lines"

    def test_multi_line_call(self, lookup: str) -> None:
        dat = Block.esmi3_32dat
        check = checks.equal(
            dat.tx8e,
            1,
            note="at least 8 empty positions",
        )
        assert check["name"] == "dat.tx8e"

    def test_multi_line_value_is_one_line(self, lookup: str) -> None:
        check = checks.length(
            [1,
             2],
            2,
        )
        assert check["name"] == "[1, 2]"

    def test_method_on_the_next_line(self, lookup: str) -> None:
        value = 5
        check = (checks
                 .less(value, 6))
        assert check["name"] == "value"

    def test_every_value_method(self, lookup: str) -> None:
        x, text, word = 1.0, "v1.2", 1
        names = [
            checks.equal(x, 1.0)["name"],
            checks.not_equal(x, 2.0)["name"],
            checks.bits(word, 1)["name"],
            checks.approx(x, 1.0, rel_tol=0.1)["name"],
            checks.greater(x, 0)["name"],
            checks.greater_equal(x, 0)["name"],
            checks.less(x, 2)["name"],
            checks.less_equal(x, 2)["name"],
            checks.between(x, 0, 2)["name"],
            checks.is_true(x)["name"],
            checks.is_false(not x)["name"],
            checks.is_none(None)["name"],
            checks.is_not_none(x)["name"],
            checks.contains(text, "v")["name"],
            checks.not_contains(text, "w")["name"],
            checks.matches(text, r"^v")["name"],
            checks.is_instance(x, float)["name"],
            checks.length(text, 4)["name"],
        ]
        assert names == (
            ["x", "x", "word"] + ["x"] * 7 + ["not x", "None", "x"] + ["text"] * 3 + ["x", "text"]
        )

    def test_a_given_name_wins_and_has_no_flag(self, lookup: str) -> None:
        check = checks.equal(1, 1, name="FIFO not full")
        assert check["name"] == "FIFO not full"
        assert "auto_name" not in check

    def test_in_a_loop_the_call_is_found_once(
        self, lookup: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: List[str] = []
        find = _source._find

        def counting(*args: Any) -> str:
            calls.append(args[4])
            return find(*args)

        monkeypatch.setattr(_source, "_find", counting)
        names = [checks.less(index, 10)["name"] for index in range(5)]
        for index in range(5):
            names.append(checks.greater_equal(index, 0)["name"])
        assert names == ["index"] * 10
        assert calls == ["less", "greater_equal"]

    def test_lambda_sample(self, lookup: str) -> None:
        reading = 2
        run, verify = _recording()
        record = verify.eventually(lambda: verify.equal(reading, 2), timeout=1, name="Ready")
        assert record["child_checks"][0]["name"] == "reading"

    def test_factory_of_all_satisfy(self, lookup: str) -> None:
        run, verify = _recording()
        record = verify.all_satisfy([1, 2], lambda item: verify.greater(item, 0), name="All")
        assert [child["name"] for child in record["child_checks"]] == ["item", "item"]


class TestOnTheLine:
    def test_two_calls_on_one_line(self, lookup: str) -> None:
        a, b = 1, 2
        first, second = checks.equal(a, 1), checks.equal(b, 2)
        if lookup == "positions":
            assert (first["name"], second["name"]) == ("a", "b")
        else:  # the line alone cannot tell which call runs
            assert (first["name"], second["name"]) == ("value", "value")

    def test_two_calls_with_the_same_value(self, lookup: str) -> None:
        a = 1
        first, second = checks.greater(a, 0), checks.greater(a, -1)
        assert (first["name"], second["name"]) == ("a", "a")


class TestIndirectCalls:
    """A check method reached through another call is never named by that call's argument."""

    def test_map(self, lookup: str) -> None:
        flags = [1, 2]
        made = list(map(checks.is_true, flags))
        assert [check["name"] for check in made] == ["value", "value"]

    def test_bound_method_as_factory(self, lookup: str) -> None:
        flags = [1, 2]
        check = checks.all_satisfy(flags, checks.is_true, name="all set")
        assert [child["name"] for child in check["child_checks"]] == ["value", "value"]

    def test_partial_as_factory(self, lookup: str) -> None:
        values = [1, 2]
        factory = functools.partial(checks.greater, threshold=0)
        check = checks.all_satisfy(values, factory, name="pos")
        assert [child["name"] for child in check["child_checks"]] == ["value", "value"]

    def test_lambda_factory_names_its_own_argument(self, lookup: str) -> None:
        values = [1, 2]
        check = checks.all_satisfy(values, lambda v: checks.greater(v, 0), name="pos")
        assert [child["name"] for child in check["child_checks"]] == ["v", "v"]

    def test_alias_of_the_method(self, lookup: str) -> None:
        eq = checks.equal
        x = 1
        assert eq(x, 1)["name"] == "value"

    def test_fixture_map_and_factory(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest_verifier

            def test_flags(verify, request):
                flags = [1, 1]
                list(map(verify.is_true, flags))
                verify.all_satisfy(flags, verify.is_true, name="all set")
                verify.is_true(flags[0])
            """
        )
        pytester.makeconftest(
            """
            import json, pytest, pytest_verifier

            @pytest.hookimpl(hookwrapper=True)
            def pytest_runtest_teardown(item):
                yield
                names = []
                for record in pytest_verifier.get_check_results(item):
                    names.append(record["name"])
                    names.extend(c["name"] for c in record.get("child_checks") or [])
                print("NAMES=" + json.dumps(names))
            """
        )
        result = pytester.runpytest("-s")
        result.assert_outcomes(passed=1)
        line = next(x for x in result.outlines if x.startswith("NAMES=") or "NAMES=" in x)
        names = json.loads(line.split("NAMES=", 1)[1])
        assert names == ["value", "value", "all set", "value", "value", "flags[0]"]


class TestSegments:
    def test_inside_a_multi_line_f_string(self, lookup: str) -> None:
        dat = Block.esmi3_32dat
        other = 1
        text = f"""
            {other} {checks.equal(dat.tx8e, 0)['name']}"""
        assert text.split() == ["1", "dat.tx8e"]

    def test_a_segment_that_is_not_the_node_is_unparsed(self) -> None:
        call = ast.parse("f(a.b, 1)", mode="eval").body
        assert isinstance(call, ast.Call)
        # Offsets that point at other text (as Python 3.9 gives in multi-line f-strings).
        call.args[0].col_offset, call.args[0].end_col_offset = 4, 9
        assert _source._argument_text(call, "f(a.b, 1)", "actual") == "a.b"


class TestFallback:
    def test_exec_without_source(self) -> None:
        namespace: dict = {"checks": checks}
        exec("check = checks.equal(1 + 1, 2)", namespace)
        assert namespace["check"]["name"] == _source.FALLBACK == "value"
        assert namespace["check"]["auto_name"] is True

    def test_starred_arguments(self, lookup: str) -> None:
        arguments = (1, 1)
        assert checks.equal(*arguments)["name"] == "value"

    def test_a_long_expression_is_cut(self, lookup: str) -> None:
        check = checks.is_true(
            "a" * 50 + "b" * 50 + "c" * 50 + "d" * 50 != "and so on, until the name is too long"
            and "e" * 50 + "f" * 50 != "a name of more than one hundred and twenty characters"
        )
        assert len(check["name"]) == _source.NAME_LIMIT
        assert check["name"].endswith("...")

    def test_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def broken(*args: Any) -> str:
            raise RuntimeError("broken")

        monkeypatch.setattr(_source, "_find", broken)
        _source.clear_caches()
        assert checks.equal(3, 3)["name"] == "value"

    def test_a_source_that_does_not_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_source.linecache, "getlines", lambda *args: ["def (:\n"])
        _source.clear_caches()
        assert checks.equal(3, 3)["name"] == "value"
        _source.clear_caches()


class TestFixture:
    def test_inside_a_rewritten_assert(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            class Reg:
                txfe = 1

            def test_assert(verify):
                dat = Reg()
                assert verify.equal(dat.txfe, 1)["name"] == "dat.txfe"
                assert verify.equal(
                    dat.txfe, 1
                )["name"] == "dat.txfe"
            """
        )
        pytester.runpytest().assert_outcomes(passed=1)

    def test_require_and_record(self, lookup: str) -> None:
        run, verify = _recording()
        link = object()
        record = verify.require.is_not_none(link, note="opened by the fixture")
        assert record["name"] == "link"
        assert record["auto_name"] is True
        assert record["note"] == "opened by the fixture"
        built = checks.equal(link, link)
        assert verify.record(built)["name"] == "link"
        json.dumps(run.records)

    def test_in_a_test_file(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import json
            from pytest_verifier import get_check_results

            class Reg:
                txfe, tx8e, tx = 1, 0, 0

            def test_fifo(verify, request):
                dat = ctl = Reg()
                # CMD FIFO empty
                verify.equal(dat.txfe, 1)
                verify.equal(dat.tx8e, 1, note="at least 8 empty positions")
                verify.equal(ctl.tx, 0, name="FIFO not full")
                records = get_check_results(request.node)
                assert [r["name"] for r in records] == ["dat.txfe", "dat.tx8e", "FIFO not full"]
                assert [r.get("auto_name") for r in records] == [True, True, None]
                json.dumps(records)
            """
        )
        result = pytester.runpytest("-rf")
        result.assert_outcomes(failed=1)
        output = result.stdout.str()
        assert (
            "1 of 3 checks failed: dat.tx8e — expected 1, got 0 (at least 8 empty positions)"
            in output
        )
        assert (
            "  ✗ [1] dat.tx8e (test_in_a_test_file.py:11) — expected 1, got 0 "
            "(at least 8 empty positions)" in output
        )
        assert "  ✓ [0] dat.txfe — 1 == 1\n" in output
