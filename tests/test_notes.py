"""``note=``: free text that explains a check, kept in its record and shown after its detail."""
from __future__ import annotations

import enum
import json
from typing import Any, List

import pytest

from pytest_verifier import ChecksFailedError, checks
from pytest_verifier._exceptions import format_summary, junit_property
from pytest_verifier._run import Run, recording_verify
from pytest_verifier._settle import settle


def _recording() -> Any:
    run = Run()
    run.phase = "call"
    return run, recording_verify(run)


class Reason(str, enum.Enum):
    EMPTY = "CMD FIFO empty"


class TestDescriptor:
    def test_stripped_and_stored(self) -> None:
        check = checks.equal(1, 1, name="txfe", note="  CMD FIFO empty \n")
        assert check["note"] == "CMD FIFO empty"

    @pytest.mark.parametrize("note", [None, "", "   ", "\n\t"])
    def test_absent_without_text(self, note: Any) -> None:
        assert "note" not in checks.equal(1, 1, name="txfe", note=note)

    def test_a_str_enum_member_is_stored_as_its_text(self) -> None:
        note = checks.equal(1, 1, name="txfe", note=Reason.EMPTY)["note"]
        assert note == "CMD FIFO empty" and type(note) is str

    @pytest.mark.parametrize("note", [3, b"bytes", ["a"]])
    def test_must_be_a_str(self, note: Any) -> None:
        with pytest.raises(TypeError, match=r"equal\(\) note must be a str or None, got "):
            checks.equal(1, 1, name="txfe", note=note)

    @pytest.mark.parametrize(
        "build",
        [
            lambda note: checks.all_satisfy([1], lambda n: checks.equal(n, 1), name="A", note=note),
            lambda note: checks.conditional(1, cases={1: checks.is_true(True)}, name="C",
                                            note=note),
            lambda note: checks.guard([(True, "on", checks.is_true(True))], name="G", note=note),
            lambda note: checks.eventually(lambda: checks.is_true(True), timeout=0, name="E",
                                           note=note),
            lambda note: checks.stable(lambda: checks.is_true(True), duration=0, name="S",
                                       note=note),
            lambda note: checks.fail("boom", note=note),
        ],
        ids=["all_satisfy", "conditional", "guard", "eventually", "stable", "fail"],
    )
    def test_every_check_with_a_name_takes_a_note(self, build: Any) -> None:
        check = build(" why ")
        assert check["note"] == "why"
        assert "auto_name" not in check
        with pytest.raises(TypeError, match=r"note must be a str or None"):
            build(1)

    def test_bits_takes_a_note(self) -> None:
        check = checks.bits(0xF0, 0xF3, name="CTRL", note=" after reset ")
        assert check["note"] == "after reset"
        assert "note" not in checks.bits(1, 1, name="CTRL", note="  ")

    def test_evaluation_ignores_it(self) -> None:
        assert checks.evaluate(checks.equal(1, 1, name="x", note="n"))
        assert not checks.evaluate(checks.equal(1, 2, name="x", note="n"))


class TestRecord:
    def test_record_keeps_the_note_and_is_json_safe(self) -> None:
        run, verify = _recording()
        verify.equal(1, 0, name="tx8e", note="at least 8 empty positions")
        record = run.records[0]
        assert record["note"] == "at least 8 empty positions"
        assert record["passed"] is False
        assert "note" not in record["detail"]
        json.dumps(run.records)

    def test_a_raises_block(self) -> None:
        run, verify = _recording()
        with verify.raises(ValueError, name="Reject", note="7 V is out of range") as raised:
            raise ValueError("out of range")
        assert raised.check["note"] == "7 V is out of range"
        with pytest.raises(TypeError, match=r"raises\(\) note must be a str or None"):
            verify.raises(ValueError, name="Reject", note=7)  # type: ignore[arg-type]

    def test_a_child_keeps_its_note(self) -> None:
        run, verify = _recording()
        record = verify.all_satisfy(
            [1, 2], lambda n: verify.less(n, 2, note="below two"), name="All", note="each"
        )
        assert record["note"] == "each"
        assert [child["note"] for child in record["child_checks"]] == ["below two"] * 2

    def test_lone_surrogates_stay_utf8_safe(self) -> None:
        record, _ = settle(checks.equal(1, 2, name="x", note="bad \ud800"))
        record["note"].encode("utf-8")


class TestSummary:
    def _results(self) -> List[Any]:
        return [
            settle(checks.equal(1, 1, name="dat.txfe", note="CMD FIFO empty"))[0],
            settle(checks.equal(0, 1, name="dat.tx8e", note="at least 8 empty positions"))[0],
            settle(checks.equal(0, 0, name="FIFO not full"))[0],
        ]

    def test_lines_and_header(self) -> None:
        lines = format_summary(self._results()).splitlines()
        assert lines == [
            "1 of 3 checks failed: dat.tx8e — expected 1, got 0 (at least 8 empty positions)",
            "",
            "  ✗ [1] dat.tx8e — expected 1, got 0 (at least 8 empty positions)",
            "",
            "  ✓ [0] dat.txfe — 1 == 1 (CMD FIFO empty)",
            "  ✓ [2] FIFO not full — 0 == 0",
        ]

    def test_error_message(self) -> None:
        error = ChecksFailedError(self._results())
        assert "(at least 8 empty positions)" in str(error).splitlines()[0]

    def test_a_note_stays_on_one_line(self) -> None:
        result = settle(checks.fail("x", note="two\nlines \x1b[31m"))[0]
        summary = format_summary([result])
        assert "(two\\nlines \\x1b[31m)" in summary
        assert len(summary.splitlines()) == 3

    def test_a_long_note_is_cut(self) -> None:
        result = settle(checks.fail("x", note="n" * 1000))[0]
        line = format_summary([result]).splitlines()[2]
        assert line.endswith("...)") and len(line) < 300

    def test_junit_property(self) -> None:
        name, value = junit_property(1, self._results()[1])
        assert name == "verify[1] dat.tx8e"
        assert value == "failed: expected 1, got 0 (at least 8 empty positions)"


_SESSION = """
def test_a(verify):
    verify.equal(1, 0, name="tx8e", note="at least 8 empty positions")
    verify.equal(1, 1, name="txfe", note="CMD FIFO empty")
    verify.equal(1, 1, name="mixed", note="first")

def test_b(verify):
    verify.equal(1, 1, name="txfe", note="CMD FIFO empty")
    verify.equal(1, 1, name="mixed", note="second")
"""


class TestOutputOptions:
    def test_session_summary(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_SESSION)
        result = pytester.runpytest("--verify-summary=all")
        result.stdout.re_match_lines(
            [
                r"  ✗ tx8e \(at least 8 empty positions\): 1 of 1 failed \(first: .*test_a\)",
                r"  ✓ txfe \(CMD FIFO empty\): 2 passed",
                r"  ✓ mixed: 2 passed",
            ]
        )

    def test_json_lines(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_SESSION)
        pytester.runpytest("--verify-json=checks.jsonl")
        lines = (pytester.path / "checks.jsonl").read_text(encoding="utf-8").splitlines()
        notes = [json.loads(line)["check"].get("note") for line in lines]
        assert notes == [
            "at least 8 empty positions", "CMD FIFO empty", "first", "CMD FIFO empty", "second"
        ]

    def test_junit_properties(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_SESSION)
        pytester.runpytest("--junitxml=junit.xml", "-o", "verify_junit_properties=failed")
        xml = (pytester.path / "junit.xml").read_text(encoding="utf-8")
        assert 'value="failed: expected 0, got 1 (at least 8 empty positions)"' in xml

    def test_report_verify_checks(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            NOTES = []

            def pytest_runtest_logreport(report):
                for check in getattr(report, "verify_checks", None) or []:
                    NOTES.append(check.get("note"))

            def pytest_sessionfinish(session):
                print("NOTES", NOTES)
            """
        )
        pytester.makepyfile(_SESSION)
        result = pytester.runpytest("-s")
        result.stdout.fnmatch_lines(["*NOTES ['at least 8 empty positions', *"])
