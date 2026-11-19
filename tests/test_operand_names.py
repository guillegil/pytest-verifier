"""``actual_name=`` and ``expected_name=``: how a test says the operands of a check are spelled.

Each is kept with this run's values (``block.reg3.F``) and, when it is known and differs, with
every interpolation as ``<expression>`` (``block.reg<channel>.F``): from a template string
(Python 3.14+), or from the source of an f-string argument.
"""
from __future__ import annotations

import enum
import functools
import json
import sys
import textwrap
import typing
from decimal import Decimal
from typing import Any, Callable, Dict, Iterator, List, Optional

import pytest

from pytest_verifier import ChecksFailedError, Verify, checks
from pytest_verifier import _operands, _source
from pytest_verifier._exceptions import format_summary
from pytest_verifier._operands import symbolic_text
from pytest_verifier._run import Run, recording_verify
from pytest_verifier._settle import settle

FIELDS = ("actual_name", "actual_name_symbolic", "expected_name", "expected_name_symbolic")

needs_templates = pytest.mark.skipif(
    sys.version_info < (3, 14), reason="template strings need Python 3.14+"
)


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


def _spelled(check: Any) -> Dict[str, Any]:
    """The operand-name fields of *check*."""
    return {field: check[field] for field in FIELDS if field in check}


#: Every method that takes ``actual_name=``: a call that passes *extra* keyword arguments.
VALUE_METHODS: Dict[str, Callable[..., Any]] = {
    "equal": lambda v, **extra: v.equal(1, 1, **extra),
    "not_equal": lambda v, **extra: v.not_equal(1, 2, **extra),
    "bits": lambda v, **extra: v.bits(1, 1, **extra),
    "approx": lambda v, **extra: v.approx(1.0, 1.0, abs_tol=0.1, **extra),
    "greater": lambda v, **extra: v.greater(2, 1, **extra),
    "greater_equal": lambda v, **extra: v.greater_equal(1, 1, **extra),
    "less": lambda v, **extra: v.less(1, 2, **extra),
    "less_equal": lambda v, **extra: v.less_equal(1, 1, **extra),
    "between": lambda v, **extra: v.between(1, 0, 2, **extra),
    "is_true": lambda v, **extra: v.is_true(1, **extra),
    "is_false": lambda v, **extra: v.is_false(0, **extra),
    "is_none": lambda v, **extra: v.is_none(None, **extra),
    "is_not_none": lambda v, **extra: v.is_not_none(1, **extra),
    "contains": lambda v, **extra: v.contains("abc", "b", **extra),
    "not_contains": lambda v, **extra: v.not_contains("abc", "x", **extra),
    "matches": lambda v, **extra: v.matches("v1", r"^v", **extra),
    "is_instance": lambda v, **extra: v.is_instance(1, int, **extra),
    "length": lambda v, **extra: v.length("ab", 2, **extra),
}

#: The ones with one compared operand, which also take ``expected_name=``.
COMPARING = (
    "equal", "not_equal", "bits", "approx", "greater", "greater_equal", "less", "less_equal",
    "length",
)

#: Checks that take neither.
OTHERS: Dict[str, Callable[..., Any]] = {
    "all_satisfy": lambda v, **extra: v.all_satisfy(
        [1], lambda n: v.is_true(n, name="n"), name="A", **extra
    ),
    "conditional": lambda v, **extra: v.conditional(
        1, cases={1: v.is_true(1, name="n")}, name="C", **extra
    ),
    "guard": lambda v, **extra: v.guard([(True, "on", v.is_true(1, name="n"))], name="G", **extra),
    "eventually": lambda v, **extra: v.eventually(
        lambda: v.is_true(1, name="n"), timeout=0, name="E", **extra
    ),
    "stable": lambda v, **extra: v.stable(
        lambda: v.is_true(1, name="n"), duration=0, name="S", **extra
    ),
    "fail": lambda v, **extra: v.fail("boom", **extra),
    "raises": lambda v, **extra: v.raises(ValueError, name="R", **extra),
    "limits": lambda v, **extra: v.limits({"a": 1}, {"a": {"check": "equal", "expected": 1}},
                                          **extra),
}


class Text(str, enum.Enum):
    WDATA = "wdata"


class TestArguments:
    @pytest.mark.parametrize("method", sorted(VALUE_METHODS))
    def test_every_value_check_takes_actual_name(self, method: str) -> None:
        check = VALUE_METHODS[method](checks, actual_name="block.reg3.F")
        assert _spelled(check) == {"actual_name": "block.reg3.F"}
        assert check["name"] == "block.reg3.F"
        assert check["description"].startswith("Verify 'block.reg3.F' ")
        assert checks.evaluate(check)

    @pytest.mark.parametrize("method", COMPARING)
    def test_checks_with_one_compared_operand_take_expected_name(self, method: str) -> None:
        check = VALUE_METHODS[method](checks, name="R", expected_name="wdata")
        assert _spelled(check) == {"expected_name": "wdata"}
        assert " wdata (" in check["description"]
        assert checks.evaluate(check)

    @pytest.mark.parametrize("method", sorted(set(VALUE_METHODS) - set(COMPARING)))
    def test_the_other_value_checks_reject_expected_name(self, method: str) -> None:
        with pytest.raises(TypeError, match="unexpected keyword argument 'expected_name'"):
            VALUE_METHODS[method](checks, name="R", expected_name="wdata")

    @pytest.mark.parametrize("argument", ["actual_name", "expected_name"])
    @pytest.mark.parametrize("method", sorted(OTHERS))
    def test_checks_without_a_value_reject_both(self, method: str, argument: str) -> None:
        run, verify = _recording()
        with pytest.raises(TypeError, match=f"unexpected keyword argument '{argument}'"):
            OTHERS[method](verify, **{argument: "x"})

    def test_require_takes_them_and_checks_require_still_refuses(self) -> None:
        run, verify = _recording()
        record = verify.require.equal(1, 1, actual_name="reg3.F", expected_name="wdata")
        assert _spelled(record) == {"actual_name": "reg3.F", "expected_name": "wdata"}
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require.equal(1, 1, actual_name="reg3.F")

    @pytest.mark.parametrize("argument", ["actual_name", "expected_name"])
    @pytest.mark.parametrize("value", [3, b"reg", ["reg"], object()])
    def test_must_be_text(self, argument: str, value: Any) -> None:
        message = rf"equal\(\) {argument} must be a str, a template string or None, got "
        with pytest.raises(TypeError, match=message):
            checks.equal(1, 1, name="R", **{argument: value})

    @pytest.mark.parametrize("value", [None, "", "   ", "\n\t"])
    def test_blank_text_counts_as_not_given(self, value: Optional[str], lookup: str) -> None:
        reading = 1
        check = checks.equal(reading, 1, actual_name=value, expected_name=value)
        assert _spelled(check) == {}
        assert check["name"] == "reading"  # the default name: the source text of the value
        assert check["description"] == "Verify 'reading' == 1"

    def test_stripped(self) -> None:
        check = checks.equal(1, 1, actual_name="  reg3.F\n", expected_name=" wdata ")
        assert _spelled(check) == {"actual_name": "reg3.F", "expected_name": "wdata"}
        assert check["description"] == "Verify 'reg3.F' == wdata (1)"

    def test_a_str_enum_member_is_stored_as_its_text(self) -> None:
        check = checks.equal(1, 1, actual_name=Text.WDATA, expected_name=Text.WDATA)
        assert check["name"] == check["actual_name"] == check["expected_name"] == "wdata"
        assert {type(check[key]) for key in ("name", "actual_name", "expected_name")} == {str}

    def test_public_hints_resolve(self) -> None:
        # Tools evaluate public annotations with get_type_hints, on every supported Python.
        for method in VALUE_METHODS:
            hints = typing.get_type_hints(getattr(Verify, method))
            assert str in typing.get_args(hints["actual_name"])
            assert ("expected_name" in hints) is (method in COMPARING)
        hint = typing.get_type_hints(Verify.equal)["expected_name"]
        if sys.version_info >= (3, 14):
            from string import templatelib

            assert set(typing.get_args(hint)) == {str, templatelib.Template, type(None)}
        else:
            assert hint == Optional[str]


class TestFStrings:
    """The symbolic form of a ``str`` comes from the source, when it is an f-string there."""

    def test_both_forms(self, lookup: str) -> None:
        channel, wdata = 3, 243
        check = checks.equal(
            240, wdata, actual_name=f"tb.dev.map.block.reg{channel}.F", expected_name="wdata"
        )
        assert _spelled(check) == {
            "actual_name": "tb.dev.map.block.reg3.F",
            "actual_name_symbolic": "tb.dev.map.block.reg<channel>.F",
            "expected_name": "wdata",
        }

    def test_both_names_as_f_strings(self, lookup: str) -> None:
        channel = 3
        check = checks.less(1, 2, actual_name=f"reg{channel}.F", expected_name=f"limit[{channel}]")
        assert _spelled(check) == {
            "actual_name": "reg3.F",
            "actual_name_symbolic": "reg<channel>.F",
            "expected_name": "limit[3]",
            "expected_name_symbolic": "limit[<channel>]",
        }

    def test_a_plain_literal_has_no_symbolic_form(self, lookup: str) -> None:
        check = checks.equal(1, 1, actual_name="block.reg3.F", expected_name="wdata")
        assert _spelled(check) == {"actual_name": "block.reg3.F", "expected_name": "wdata"}

    def test_an_f_string_without_fields_has_none(self, lookup: str) -> None:
        check = checks.equal(1, 1, actual_name=f"block.reg3.F")  # noqa: F541
        assert _spelled(check) == {"actual_name": "block.reg3.F"}

    def test_a_variable_has_none(self, lookup: str) -> None:
        channel = 3
        spelling = f"block.reg{channel}.F"
        check = checks.equal(1, 1, actual_name=spelling)
        assert _spelled(check) == {"actual_name": "block.reg3.F"}

    def test_a_call_or_a_sum_has_none(self, lookup: str) -> None:
        channel = 3
        first = checks.equal(1, 1, actual_name="block.reg{}.F".format(channel))
        second = checks.equal(1, 1, actual_name="block." + f"reg{channel}.F")
        assert _spelled(first) == _spelled(second) == {"actual_name": "block.reg3.F"}

    def test_keyword_arguments_from_a_dict_have_none(self, lookup: str) -> None:
        channel = 3
        names = {"actual_name": f"block.reg{channel}.F"}
        assert _spelled(checks.equal(1, 1, **names)) == {"actual_name": "block.reg3.F"}

    def test_multi_line_call(self, lookup: str) -> None:
        channel, wdata = 3, 243
        check = checks.equal(
            240,
            wdata,
            actual_name=(
                f"tb.dev.map.block.reg{channel}.F"
            ),
            expected_name=f"wdata[{channel}]",
        )
        assert check["actual_name_symbolic"] == "tb.dev.map.block.reg<channel>.F"
        assert check["expected_name_symbolic"] == "wdata[<channel>]"

    def test_implicit_concatenation(self, lookup: str) -> None:
        channel = 3
        check = checks.equal(
            1,
            1,
            actual_name="tb.dev.map."
            f"block.reg{channel}"
            ".F",
        )
        assert check["actual_name"] == "tb.dev.map.block.reg3.F"
        assert check["actual_name_symbolic"] == "tb.dev.map.block.reg<channel>.F"

    def test_format_spec_and_conversion_are_left_out(self, lookup: str) -> None:
        channel, lane, width = 3, "a", 4
        check = checks.equal(
            1, 1, actual_name=f"reg{channel:02d}.{lane!r}.{channel:{width}d}.{lane!s:>3}"
        )
        assert check["actual_name"] == "reg03.'a'.   3.  a"
        assert check["actual_name_symbolic"] == "reg<channel>.<lane>.<channel>.<lane>"

    def test_an_expression(self, lookup: str) -> None:
        channel = 3
        check = checks.equal(1, 1, actual_name=f"reg{channel + 1}.F")
        assert check["actual_name"] == "reg4.F"
        assert check["actual_name_symbolic"] == "reg<channel + 1>.F"

    def test_a_debug_field(self, lookup: str) -> None:
        channel = 3
        check = checks.equal(1, 1, actual_name=f"reg {channel=}")
        assert check["actual_name"] == "reg channel=3"
        assert check["actual_name_symbolic"] == "reg channel=<channel>"

    def test_nested_quotes(self, lookup: str) -> None:
        index = {"k": 3}
        check = checks.equal(1, 1, actual_name=f"reg[{index['k']}].F")
        assert check["actual_name"] == "reg[3].F"
        assert check["actual_name_symbolic"] == "reg[<index['k']>].F"

    def test_doubled_braces_are_literal_text(self, lookup: str) -> None:
        channel = 3
        check = checks.equal(1, 1, actual_name=f"set{{{channel}}}")
        assert check["actual_name"] == "set{3}"
        assert check["actual_name_symbolic"] == "set{<channel>}"

    def test_only_fields(self, lookup: str) -> None:
        block, channel = "blk", 3
        check = checks.equal(1, 1, actual_name=f"{block}{channel}")
        assert _spelled(check) == {
            "actual_name": "blk3",
            "actual_name_symbolic": "<block><channel>",
        }

    def test_two_checks_on_one_line(self, lookup: str) -> None:
        a, b = 1, 2
        equal = checks.equal
        first, second = equal(1, 1, actual_name=f"r{a}"), equal(2, 2, actual_name=f"r{b}")
        assert (first["actual_name"], second["actual_name"]) == ("r1", "r2")
        if lookup == "positions":
            assert first["actual_name_symbolic"] == "r<a>"
            assert second["actual_name_symbolic"] == "r<b>"
        else:  # the line alone cannot tell which call runs: no symbolic form, never a wrong one
            assert "actual_name_symbolic" not in first
            assert "actual_name_symbolic" not in second

    def test_two_checks_on_one_line_spelled_alike(self, lookup: str) -> None:
        a = 1
        less = checks.less
        first, second = less(0, 1, actual_name=f"r{a}"), less(0, 2, actual_name=f"r{a}")
        assert first["actual_name_symbolic"] == second["actual_name_symbolic"] == "r<a>"

    def test_in_a_loop_the_source_is_read_once(
        self, lookup: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: List[str] = []
        find = _source._find_spellings

        def counting(*args: Any) -> Any:
            calls.append(args[4])
            return find(*args)

        monkeypatch.setattr(_source, "_find_spellings", counting)
        made = [checks.equal(n, n, actual_name=f"reg{n}.F") for n in range(5)]
        assert [check["actual_name"] for check in made] == [f"reg{n}.F" for n in range(5)]
        assert [check["actual_name_symbolic"] for check in made] == ["reg<n>.F"] * 5
        assert calls == ["equal"]

    def test_no_names_no_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def never(*args: Any) -> Any:
            raise AssertionError("the source was read")

        monkeypatch.setattr(_operands, "spellings", never)
        monkeypatch.setattr(_operands, "spell", never)
        assert checks.equal(1, 1, name="R")["description"] == "Verify 'R' == 1"
        assert checks.equal(1, 1, name="R", actual_name=None, expected_name=None)

    def test_a_plain_name_skips_nothing_but_finds_nothing(self, lookup: str) -> None:
        # A literal is looked up like an f-string (the argument may be either); once per site.
        made = [checks.equal(1, 1, actual_name="reg.F") for _ in range(3)]
        assert all(_spelled(check) == {"actual_name": "reg.F"} for check in made)

    def test_a_helper_that_passes_the_text_on(self, lookup: str) -> None:
        def check_register(value: int, spelling: str) -> Any:
            return checks.equal(value, 1, actual_name=spelling)

        channel = 3
        check = check_register(1, f"block.reg{channel}.F")
        assert _spelled(check) == {"actual_name": "block.reg3.F"}

    def test_partial(self, lookup: str) -> None:
        channel = 3
        made = functools.partial(checks.equal, actual_name=f"block.reg{channel}.F")
        assert _spelled(made(1, 1)) == {"actual_name": "block.reg3.F"}

    def test_an_alias_of_the_method(self, lookup: str) -> None:
        # The call is found by the name the method is called by, as for the default name.
        channel = 3
        eq = checks.less
        assert _spelled(eq(1, 2, actual_name=f"reg{channel}")) == {"actual_name": "reg3"}

    def test_an_indirect_call_never_takes_the_f_string_of_a_direct_call_on_its_line(
        self, lookup: str
    ) -> None:
        # The text of the alias only shares the literal start of the f-string beside it, so
        # the safeguard alone would keep "reg<channel>" for it.
        soft = checks.equal
        channel, label = 3, "reg_status"
        made = []
        for direct in (True, False):
            made.append(checks.equal(1, 1, actual_name=f"reg{channel}") if direct else soft(1, 1, actual_name=label))  # noqa: E501
        assert _spelled(made[1]) == {"actual_name": "reg_status"}
        assert made[0]["actual_name"] == "reg3"
        if lookup == "positions":  # the instruction says which call runs
            assert made[0]["actual_name_symbolic"] == "reg<channel>"
        else:  # the line cannot: no symbolic text for either
            assert "actual_name_symbolic" not in made[0]

    def test_a_partial_called_beside_a_direct_call(self, lookup: str) -> None:
        channel, name = 3, "reg3"
        bound = functools.partial(checks.equal, 1, 1)
        direct, indirect = checks.equal(2, 2, actual_name=f"reg{channel}"), bound(actual_name=name)  # noqa: E501
        assert _spelled(indirect) == {"actual_name": "reg3"}
        both = {"actual_name": "reg3", "actual_name_symbolic": "reg<channel>"}
        assert _spelled(direct) == (both if lookup == "positions" else {"actual_name": "reg3"})

    def test_keyword_arguments_unpacked_beside_a_direct_call(self, lookup: str) -> None:
        channel, soft, names = 3, checks.equal, {"actual_name": "reg3"}
        direct, indirect = checks.equal(2, 2, actual_name=f"reg{channel}"), soft(1, 1, **names)
        assert _spelled(indirect) == {"actual_name": "reg3"}
        assert direct["actual_name"] == "reg3"

    def test_other_calls_on_the_line_do_not_hide_the_f_string(self, lookup: str) -> None:
        channel, made = 3, []
        made.append(checks.equal(int("1"), 1, actual_name=f"reg{channel}", note=str(channel)))
        assert _spelled(made[0]) == {"actual_name": "reg3", "actual_name_symbolic": "reg<channel>"}

    def test_exec_without_source(self) -> None:
        namespace: Dict[str, Any] = {"checks": checks, "channel": 3}
        exec('check = checks.equal(1, 1, actual_name=f"reg{channel}.F")', namespace)
        assert _spelled(namespace["check"]) == {"actual_name": "reg3.F"}
        assert namespace["check"]["name"] == "reg3.F"

    def test_a_long_expression_is_cut(self, lookup: str) -> None:
        first_register_offset = second_register_offset = 100
        check = checks.equal(
            1,
            1,
            actual_name=f"""r{sum([first_register_offset, second_register_offset,
                                   first_register_offset, second_register_offset,
                                   first_register_offset, second_register_offset])}""",
        )
        assert check["actual_name"] == "r600"
        assert len(check["actual_name_symbolic"]) == len("r<>") + _source.NAME_LIMIT
        assert check["actual_name_symbolic"].endswith("...>")

    def test_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def broken(*args: Any) -> Any:
            raise RuntimeError("broken")

        monkeypatch.setattr(_source, "_find_spellings", broken)
        _source.clear_caches()
        channel = 3
        assert _spelled(checks.equal(1, 1, actual_name=f"reg{channel}")) == {"actual_name": "reg3"}
        _source.clear_caches()

    def test_a_source_that_does_not_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_source.linecache, "getlines", lambda *args: ["def (:\n"])
        _source.clear_caches()
        channel = 3
        assert _spelled(checks.equal(1, 1, actual_name=f"reg{channel}")) == {"actual_name": "reg3"}
        _source.clear_caches()

    def test_the_expressions_are_not_evaluated_again(self, lookup: str) -> None:
        reads: List[int] = []

        def channel() -> int:
            reads.append(1)
            return len(reads)

        check = checks.equal(1, 1, actual_name=f"reg{channel()}.F")
        assert check["actual_name"] == "reg1.F"
        assert check["actual_name_symbolic"] == "reg<channel()>.F"
        assert reads == [1]

    def test_inside_the_fixture(self, lookup: str) -> None:
        run, verify = _recording()
        channel = 3
        record = verify.equal(1, 1, actual_name=f"reg{channel}.F", expected_name=f"w{channel}")
        assert record["actual_name_symbolic"] == "reg<channel>.F"
        assert record["expected_name_symbolic"] == "w<channel>"
        required = verify.require.less(0, 1, actual_name=f"reg{channel}.F")
        assert required["actual_name_symbolic"] == "reg<channel>.F"


class TestSafeguard:
    """A symbolic form is kept only when the f-string found can have made the text received."""

    @pytest.mark.parametrize(
        "parts, received, expected",
        [
            (("reg", ("ch",), ".F"), "reg3.F", "reg<ch>.F"),
            (("reg", ("ch",), ".F"), "reg.F", "reg<ch>.F"),
            (("reg", ("ch",), ".F"), "REG3.F", None),
            (("reg", ("ch",), ".F"), "reg3.G", None),
            (("reg", ("ch",), ".F"), "xreg3.F", None),
            (("reg", ("ch",), ".F"), "reg3.F ", None),
            (("reg.F", ("ch",), ".F"), "reg.F", None),  # the literals may not overlap
            ((("a",), "-", ("b",)), "1-2", "<a>-<b>"),
            ((("a",), "-", ("b",)), "12", None),
            ((("a",), ("b",)), "anything", "<a><b>"),
            (("a", ("x",), "b", ("y",), "c"), "a1b2c", "a<x>b<y>c"),
            (("a", ("x",), "b", ("y",), "c"), "a1c2b", None),
            (("a", ("x",), "b", ("y",), "c"), "abbc", "a<x>b<y>c"),
            (("x=", ("x",)), "x=3", "x=<x>"),
            (("literal",), "literal", "literal"),
            (("literal",), "other", None),
            ((), "", ""),
            ((), "text", None),
        ],
    )
    def test_literal_parts_must_occur_in_order(
        self, parts: Any, received: str, expected: Optional[str]
    ) -> None:
        assert symbolic_text(parts, received) == expected

    def test_another_f_string_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The source found is not the one that made the text (a file edited after its import).
        found = {"actual_name": ("other.", ("x",)), "expected_name": ("w", ("n",))}
        monkeypatch.setattr(_source, "_find_spellings", lambda *args: found)
        _source.clear_caches()
        check = checks.equal(1, 1, actual_name="block.reg3.F", expected_name="w7")
        assert _spelled(check) == {
            "actual_name": "block.reg3.F",
            "expected_name": "w7",
            "expected_name_symbolic": "w<n>",
        }
        _source.clear_caches()

    def test_it_is_checked_against_the_text_before_stripping(self, lookup: str) -> None:
        channel = 3
        check = checks.equal(1, 1, actual_name=f"  reg{channel}.F  ")
        assert check["actual_name"] == "reg3.F"
        assert check["actual_name_symbolic"] == "reg<channel>.F"


class TestNames:
    def test_actual_name_names_the_check(self, lookup: str) -> None:
        channel = 3
        block_reg_f = 240
        check = checks.equal(block_reg_f, 243, actual_name=f"tb.dev.map.block.reg{channel}.F")
        assert check["name"] == "tb.dev.map.block.reg3.F"
        # The name is how the value is spelled, as a name taken from the source is.
        assert check["auto_name"] is True

    def test_a_given_name_stays_the_label(self, lookup: str) -> None:
        channel = 3
        check = checks.equal(
            240, 243, name="Stored word", actual_name=f"tb.dev.map.block.reg{channel}.F"
        )
        assert check["name"] == "Stored word"
        assert "auto_name" not in check
        assert check["actual_name"] == "tb.dev.map.block.reg3.F"
        assert check["actual_name_symbolic"] == "tb.dev.map.block.reg<channel>.F"
        assert check["description"] == "Verify 'Stored word' == 243"

    def test_expected_name_alone_keeps_the_default_name(self, lookup: str) -> None:
        reading, wdata = 240, 243
        check = checks.equal(reading, wdata, expected_name="wdata")
        assert check["name"] == "reading"
        assert check["auto_name"] is True
        assert check["description"] == "Verify 'reading' == wdata (243)"

    def test_haystack_name(self) -> None:
        check = checks.contains(["OK"], "OK", actual_name="reply.lines")
        assert check["name"] == check["actual_name"] == "reply.lines"
        assert check["description"] == "Verify 'reply.lines' contains 'OK'"

    def test_an_invalid_name_still_raises(self) -> None:
        with pytest.raises(TypeError, match=r"equal\(\) name must be a str"):
            checks.equal(1, 1, name=3, actual_name="reg")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="name must not be empty"):
            checks.equal(1, 1, name=" ", actual_name="reg")


class TestText:
    """``expected_name`` goes before the expected value wherever that is shown."""

    def _texts(self, check: Any) -> List[str]:
        record, passed = settle(check)
        return [check["description"], record["detail"]]

    def test_equal(self) -> None:
        failed = checks.equal(240, 243, name="Stored word", expected_name="wdata")
        assert self._texts(failed) == [
            "Verify 'Stored word' == wdata (243)", "expected wdata (243), got 240"
        ]
        passed = checks.equal(243, 243, name="Stored word", expected_name="wdata")
        assert self._texts(passed)[1] == "243 == wdata (243)"

    def test_units(self) -> None:
        check = checks.equal(3.2, 3.3, name="Vout", units="V", expected_name="nominal")
        assert self._texts(check) == [
            "Verify 'Vout' == nominal (3.3V)", "expected nominal (3.3V), got 3.2V"
        ]

    def test_type_notes(self) -> None:
        check = checks.equal(Decimal("0.1"), 0.1, name="Gain", expected_name="gain")
        assert self._texts(check)[1] == "expected gain (0.1 (float)), got 0.1 (Decimal)"

    def test_not_equal(self) -> None:
        failed = checks.not_equal(0, 0, name="State", expected_name="IDLE")
        assert self._texts(failed) == ["Verify 'State' != IDLE (0)", "expected ≠ IDLE (0), got 0"]
        passed = checks.not_equal(1, 0, name="State", expected_name="IDLE")
        assert self._texts(passed)[1] == "1 ≠ IDLE (0)"

    def test_approx(self) -> None:
        failed = checks.approx(
            3.5, 3.3, abs_tol=0.05, name="Vout", units="V", expected_name="nominal"
        )
        assert self._texts(failed) == [
            "Verify 'Vout' == nominal (3.3V) ± 0.05V",
            "expected nominal (3.3V) ± 0.05V, got 3.5V",
        ]
        passed = checks.approx(
            3.31, 3.3, abs_tol=0.05, name="Vout", units="V", expected_name="nominal"
        )
        assert self._texts(passed)[1] == "3.31V == nominal (3.3V) ± 0.05V"

    @pytest.mark.parametrize(
        "method, symbol, passing, failing",
        [
            ("greater", ">", 120, 100),
            ("greater_equal", ">=", 100, 90),
            ("less", "<", 90, 100),
            ("less_equal", "<=", 100, 120),
        ],
    )
    def test_ordering(self, method: str, symbol: str, passing: int, failing: int) -> None:
        build = getattr(checks, method)
        failed = build(failing, 100, name="T", units="Mbps", expected_name="floor")
        assert self._texts(failed) == [
            f"Verify 'T' {symbol} floor (100Mbps)",
            f"expected {symbol} floor (100Mbps), got {failing}Mbps",
        ]
        passed = build(passing, 100, name="T", units="Mbps", expected_name="floor")
        assert self._texts(passed)[1] == f"{passing}Mbps {symbol} floor (100Mbps)"

    def test_length(self) -> None:
        failed = checks.length([1, 2, 3], 4, name="words", expected_name="depth")
        assert self._texts(failed) == [
            "Verify 'words' has length depth (4)", "expected length depth (4), got length 3"
        ]
        passed = checks.length([1, 2, 3, 4], 4, name="words", expected_name="depth")
        assert self._texts(passed)[1] == "length depth (4)"

    def test_an_error_note_follows(self) -> None:
        check = checks.greater(None, 1, name="T", expected_name="floor")
        detail = settle(check)[0]["detail"]
        assert detail.startswith("expected > floor (1), got None (TypeError: ")

    def test_text_is_unchanged_without_names(self) -> None:
        for method, build in VALUE_METHODS.items():
            plain = build(checks, name="R")
            spelled = build(checks, name="R", actual_name="reg3.F")
            assert spelled["description"] == plain["description"], method
            assert settle(spelled)[0]["detail"] == settle(plain)[0]["detail"], method
            assert set(plain).isdisjoint(FIELDS), method
            assert set(spelled) - set(plain) == {"actual_name"}, method

    def test_one_line(self) -> None:
        check = checks.equal(
            1, 2, actual_name="reg\n3 \x1b[31m\ud800", expected_name="w\rda\u2028ta"
        )
        assert check["description"] == (
            "Verify 'reg\\n3 \\x1b[31m\\ud800' == w\\rda\\u2028ta (2)"
        )
        record, _ = settle(check)
        assert record["detail"] == "expected w\\rda\\u2028ta (2), got 1"
        summary = format_summary([record])
        assert len(summary.splitlines()) == 3
        for field in ("name", "actual_name", "expected_name", "description", "detail"):
            record[field].encode("utf-8")
        json.dumps(record, allow_nan=False)

    def test_long_names_are_cut_where_they_are_shown(self) -> None:
        check = checks.equal(1, 2, actual_name="a" * 1000, expected_name="e" * 1000)
        assert check["actual_name"] == "a" * 1000
        assert check["description"] == f"Verify '{'a' * 237}...' == {'e' * 237}... (2)"
        assert settle(check)[0]["detail"] == f"expected {'e' * 237}... (2), got 1"

    def test_summary_line(self) -> None:
        channel = 3
        record, _ = settle(
            checks.equal(
                240, 243, actual_name=f"tb.dev.map.block.reg{channel}.F", expected_name="wdata"
            )
        )
        assert format_summary([record]).splitlines()[0] == (
            "1 of 1 checks failed: tb.dev.map.block.reg3.F — expected wdata (243), got 240"
        )
        assert "expected wdata (243), got 240" in str(ChecksFailedError([record]))


class TestRecords:
    def test_record_fields_are_json_safe(self, lookup: str) -> None:
        run, verify = _recording()
        channel = 3
        verify.equal(
            240,
            243,
            name="Stored word",
            actual_name=f"tb.dev.map.block.reg{channel}.F",
            expected_name=f"wdata[{channel}]",
        )
        [record] = run.records
        assert _spelled(record) == {
            "actual_name": "tb.dev.map.block.reg3.F",
            "actual_name_symbolic": "tb.dev.map.block.reg<channel>.F",
            "expected_name": "wdata[3]",
            "expected_name_symbolic": "wdata[<channel>]",
        }
        assert record["passed"] is False
        assert record["detail"] == "expected wdata[3] (243), got 240"
        assert json.loads(json.dumps(run.records, allow_nan=False)) == run.records

    def test_evaluate_and_record_of_a_built_check(self, lookup: str) -> None:
        channel = 3
        built = checks.equal(1, 2, actual_name=f"reg{channel}.F", expected_name="wdata")
        assert checks.evaluate(built) is False
        [detailed] = checks.evaluate_detailed(built)
        assert detailed["passed"] is False and detailed["details"] is built
        run, verify = _recording()
        record = verify.record(built)
        assert _spelled(record) == _spelled(built)
        assert checks.evaluate(json.loads(json.dumps(record))) is False

    def test_a_child_keeps_its_names(self, lookup: str) -> None:
        run, verify = _recording()
        record = verify.all_satisfy(
            [1, 2],
            lambda n: verify.less(n, 3, actual_name=f"reg{n}.F", expected_name="limit"),
            name="All",
        )
        children = record["child_checks"]
        assert [child["name"] for child in children] == ["reg1.F", "reg2.F"]
        assert [child["actual_name_symbolic"] for child in children] == ["reg<n>.F"] * 2
        assert [child["detail"] for child in children] == ["1 < limit (3)", "2 < limit (3)"]

    @pytest.mark.parametrize("junk", [3, None, ["x"], {"a": 1}, b"bytes", "", "  "])
    def test_hand_built_fields_never_break_a_check(self, junk: Any) -> None:
        for method in COMPARING:
            built = dict(VALUE_METHODS[method](checks, name="R"))
            plain_detail = settle(built)[0]["detail"]
            for field in FIELDS:
                built[field] = junk
            record, passed = settle(built)
            assert passed is True and checks.evaluate(built) is True
            assert record["detail"] == plain_detail
            json.dumps(record, allow_nan=False)
            assert isinstance(format_summary([record]), str)

    def test_a_hand_built_expected_name_is_shown(self) -> None:
        built = {
            "check_type": "greater",
            "name": "T",
            "description": "Verify 'T' > floor (1)",
            "actual": 0,
            "threshold": 1,
            "expected_name": "floor",
        }
        assert settle(built)[0]["detail"] == "expected > floor (1), got 0"


_REGISTER_TEST = """
import types

tb = types.SimpleNamespace(dev=types.SimpleNamespace(map=types.SimpleNamespace(
    block=types.SimpleNamespace(reg3=types.SimpleNamespace(F=240)))))

def test_register(verify):
    channel, wdata = 3, 243
    block_reg_f = getattr(tb.dev.map.block, f"reg{channel}").F
    verify.equal(block_reg_f, wdata,
                 actual_name=f"tb.dev.map.block.reg{channel}.F",
                 expected_name="wdata")
    verify.less(block_reg_f, 250, name="Below the limit", expected_name=f"limit[{channel}]")
"""


class TestSessions:
    def test_failure_summary(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_REGISTER_TEST)
        result = pytester.runpytest("-rf")
        result.assert_outcomes(failed=1)
        output = result.stdout.str()
        assert (
            "1 of 2 checks failed: tb.dev.map.block.reg3.F — expected wdata (243), got 240"
            in output
        )
        assert "  ✓ [1] Below the limit — 240 < limit[3] (250)\n" in output

    def test_json_lines(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_REGISTER_TEST)
        pytester.runpytest("--verify-json=checks.jsonl")
        lines = (pytester.path / "checks.jsonl").read_text(encoding="utf-8").splitlines()
        first, second = (json.loads(line)["check"] for line in lines)
        assert _spelled(first) == {
            "actual_name": "tb.dev.map.block.reg3.F",
            "actual_name_symbolic": "tb.dev.map.block.reg<channel>.F",
            "expected_name": "wdata",
        }
        assert first["name"] == "tb.dev.map.block.reg3.F" and first["auto_name"] is True
        assert _spelled(second) == {
            "expected_name": "limit[3]",
            "expected_name_symbolic": "limit[<channel>]",
        }

    def test_get_check_results_and_report(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import json, pytest, pytest_verifier

            SEEN = []

            @pytest.hookimpl(hookwrapper=True)
            def pytest_runtest_teardown(item):
                yield
                for record in pytest_verifier.get_check_results(item):
                    SEEN.append(["item", record.get("actual_name_symbolic"),
                                 record.get("expected_name")])

            def pytest_runtest_logreport(report):
                for record in getattr(report, "verify_checks", None) or []:
                    SEEN.append(["report", record.get("actual_name_symbolic"),
                                 record.get("expected_name")])

            def pytest_sessionfinish(session):
                print("SEEN=" + json.dumps(SEEN))
            """
        )
        pytester.makepyfile(_REGISTER_TEST)
        result = pytester.runpytest("-s")
        line = next(x for x in result.outlines if "SEEN=" in x)
        seen = json.loads(line.split("SEEN=", 1)[1])
        names = [["tb.dev.map.block.reg<channel>.F", "wdata"], [None, "limit[3]"]]
        assert seen == [["report", *each] for each in names] + [["item", *each] for each in names]

    def test_require_and_fail_fast_still_stop(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_required(verify):
                channel = 3
                verify.require.equal(1, 2, actual_name=f"reg{channel}.F", expected_name="wdata")
                verify.fail("never reached")

            def test_soft(verify):
                channel = 3
                verify.equal(1, 2, actual_name=f"reg{channel}.F", expected_name="wdata")
                verify.fail("reached unless fail-fast")
            """
        )
        result = pytester.runpytest("-rf")
        result.assert_outcomes(failed=2)
        output = result.stdout.str()
        assert (
            "1 of 1 checks failed, stopped at [0]: reg3.F — expected wdata (2), got 1" in output
        )
        assert "2 of 2 checks failed: reg3.F — expected wdata (2), got 1 (+1 more)" in output
        result = pytester.runpytest("-rf", "--verify-fail-fast")
        result.assert_outcomes(failed=2)
        assert "never reached" not in result.stdout.str()
        assert "reached unless fail-fast" not in result.stdout.str()


def _run_templates(source: str, **names: Any) -> Dict[str, Any]:
    """Run *source*, which holds template strings: a syntax error before Python 3.14, so it
    is compiled here and not with this module."""
    namespace: Dict[str, Any] = {"checks": checks, **names}
    exec(compile(textwrap.dedent(source), "<templates>", "exec"), namespace)
    return namespace


@needs_templates
class TestTemplateStrings:
    def test_both_forms_without_source(self) -> None:
        made = _run_templates(
            '''
            channel, wdata = 3, 243
            check = checks.equal(
                240, wdata,
                actual_name=t"tb.dev.map.block.reg{channel}.F", expected_name=t"wdata",
            )
            '''
        )
        check = made["check"]
        assert _spelled(check) == {
            "actual_name": "tb.dev.map.block.reg3.F",
            "actual_name_symbolic": "tb.dev.map.block.reg<channel>.F",
            "expected_name": "wdata",
        }
        assert check["name"] == "tb.dev.map.block.reg3.F" and check["auto_name"] is True
        assert check["description"] == "Verify 'tb.dev.map.block.reg3.F' == wdata (243)"
        json.dumps(check)

    def test_formatted_as_an_f_string_would(self) -> None:
        made = _run_templates(
            '''
            channel, lane, width = 3, "a", 4
            template = checks.less(
                1, 2,
                actual_name=t"reg{channel:02d}.{lane!r}.{channel:{width}d}.{lane!s:>3}.{lane!a}",
                expected_name=t"limit[{channel + 1}] {channel=}",
            )
            plain = f"reg{channel:02d}.{lane!r}.{channel:{width}d}.{lane!s:>3}.{lane!a}"
            '''
        )
        check = made["template"]
        assert check["actual_name"] == made["plain"] == "reg03.'a'.   3.  a.'a'"
        assert check["actual_name_symbolic"] == "reg<channel>.<lane>.<channel>.<lane>.<lane>"
        assert check["expected_name"] == "limit[4] channel=3"
        assert check["expected_name_symbolic"] == "limit[<channel + 1>] channel=<channel>"

    def test_a_template_in_a_variable_or_a_helper_keeps_both_forms(self) -> None:
        made = _run_templates(
            '''
            def check_register(value, spelling):
                return checks.equal(value, 1, actual_name=spelling)

            channel = 3
            spelling = t"block.reg{channel}.F"
            check = check_register(1, spelling)
            '''
        )
        assert _spelled(made["check"]) == {
            "actual_name": "block.reg3.F",
            "actual_name_symbolic": "block.reg<channel>.F",
        }

    def test_without_fields_or_blank(self) -> None:
        made = _run_templates(
            '''
            reading = 1
            plain = checks.equal(reading, 1, actual_name=t" block.reg3.F ")
            blank = checks.equal(reading, 1, actual_name=t"  ", expected_name=t"")
            '''
        )
        assert _spelled(made["plain"]) == {"actual_name": "block.reg3.F"}
        assert _spelled(made["blank"]) == {}
        assert made["blank"]["name"] == "value"  # no source here: the fallback default name

    @pytest.mark.parametrize("field", ["{channel:zz}", "{channel!r:zz}", "{hostile}"])
    def test_a_formatting_error_raises_as_from_the_f_string(self, field: str) -> None:
        class Hostile:
            def __format__(self, spec: str) -> str:
                raise ValueError("hostile value")

        names = {"channel": 3, "hostile": Hostile()}
        with pytest.raises(ValueError) as from_f_string:
            _run_templates(f'text = f"reg{field}.F"', **names)
        for argument in ("actual_name", "expected_name"):
            with pytest.raises(ValueError) as from_template:
                _run_templates(f'checks.equal(1, 1, {argument}=t"reg{field}.F")', **names)
            assert str(from_template.value) == str(from_f_string.value)

    def test_a_formatting_error_records_nothing(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            '''
            import pytest
            from pytest_verifier import get_check_results

            def test_register(verify, request):
                channel = 3
                with pytest.raises(ValueError, match="Unknown format code 'z'"):
                    verify.equal(1, 2, actual_name=t"reg{channel:zz}")
                assert get_check_results(request.node) == []
            '''
        )
        pytester.runpytest().assert_outcomes(passed=1)

    def test_a_hand_made_interpolation_without_an_expression(self) -> None:
        from string import templatelib

        template = templatelib.Template("reg", templatelib.Interpolation(3), ".F")
        check = checks.equal(1, 1, actual_name=template)
        assert _spelled(check) == {"actual_name": "reg3.F"}

    def test_the_source_is_not_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def never(*args: Any) -> Any:
            raise AssertionError("the source was read")

        monkeypatch.setattr(_operands, "spellings", never)
        made = _run_templates(
            'check = checks.equal(1, 1, actual_name=t"reg{3}", expected_name=t"w{4}")'
        )
        assert made["check"]["expected_name_symbolic"] == "w<4>"

    def test_in_a_test_file(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            '''
            from pytest_verifier import get_check_results

            def test_register(verify, request):
                channel, wdata = 3, 243
                verify.equal(240, wdata, actual_name=t"block.reg{channel}.F",
                             expected_name=t"wdata[{channel}]")
                verify.require.less(1, 2, name="Below", actual_name=t"block.reg{channel}.F")
                first, second = get_check_results(request.node)
                assert first["actual_name_symbolic"] == "block.reg<channel>.F"
                assert first["expected_name_symbolic"] == "wdata[<channel>]"
                assert second["actual_name"] == "block.reg3.F"
            '''
        )
        result = pytester.runpytest("-rf")
        result.assert_outcomes(failed=1)
        assert (
            "1 of 2 checks failed: block.reg3.F — expected wdata[3] (243), got 240"
            in result.stdout.str()
        )


def test_template_strings_are_refused_as_any_other_object_before_3_14() -> None:
    if sys.version_info >= (3, 14):
        pytest.skip("template strings exist")

    class Template:  # what a backport might look like: not text
        strings = ("reg",)

    with pytest.raises(TypeError, match="actual_name must be a str, a template string or None"):
        checks.equal(1, 1, actual_name=Template())  # type: ignore[arg-type]
