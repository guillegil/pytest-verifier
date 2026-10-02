"""``verify.section()``: group the checks recorded in a ``with`` block."""
from __future__ import annotations

import asyncio
import contextvars
import json
import sys
import threading

import pytest

from pytest_verifier import ChecksFailedError, checks
from pytest_verifier._exceptions import for_terminal, format_summary
from pytest_verifier._run import Run, recording_verify


def _recording():
    run = Run()
    run.phase = "call"
    return run, recording_verify(run)


def test_checks_in_a_section_carry_its_title():
    run, verify = _recording()
    before = verify.equal(1, 1, name="before")
    with verify.section("3V3"):
        inside = verify.equal(1, 1, name="Vout")
    after = verify.equal(1, 1, name="after")
    assert inside["section"] == ["3V3"]
    assert "section" not in before
    assert "section" not in after
    json.dumps(run.records, allow_nan=False)


def test_sections_nest():
    _, verify = _recording()
    with verify.section("3V3"):
        with verify.section("Load"):
            inner = verify.equal(1, 1, name="Vout")
        outer = verify.equal(1, 1, name="Iout")
    assert inner["section"] == ["3V3", "Load"]
    assert outer["section"] == ["3V3"]


def test_a_section_ends_when_its_block_raises():
    _, verify = _recording()
    with pytest.raises(KeyError):
        with verify.section("3V3"):
            raise KeyError("x")
    assert "section" not in verify.equal(1, 1, name="after")


def test_a_section_object_can_be_reused_and_nested_in_itself():
    _, verify = _recording()
    rail = verify.section("3V3")
    with rail:
        with rail:
            twice = verify.equal(1, 1, name="a")
        once = verify.equal(1, 1, name="b")
    with rail:
        again = verify.equal(1, 1, name="c")
    outside = verify.equal(1, 1, name="d")
    assert twice["section"] == ["3V3", "3V3"]
    assert once["section"] == ["3V3"]
    assert again["section"] == ["3V3"]
    assert "section" not in outside


def test_required_checks_are_in_the_same_section():
    _, verify = _recording()
    with verify.section("Link"):
        required = verify.require.is_true(True, name="up")
        with verify.require.section("Data"):
            soft = verify.equal(1, 1, name="rx")
    assert required["section"] == ["Link"]
    assert soft["section"] == ["Link", "Data"]


def test_a_recorded_check_gets_the_section_of_the_record_call():
    _, verify = _recording()
    with verify.section("Built here"):
        built = checks.equal(1, 1, name="a")
    with verify.section("Recorded here"):
        record = verify.record(built)
    assert "section" not in built
    assert record["section"] == ["Recorded here"]


def test_a_copy_of_a_record_gets_the_section_it_is_recorded_in():
    _, verify = _recording()
    with verify.section("First"):
        first = verify.equal(1, 1, name="a")
    copy = verify.record(dict(first))
    with verify.section("Second"):
        moved = verify.record(dict(first))
    assert "section" not in copy
    assert moved["section"] == ["Second"]
    assert first["section"] == ["First"]


def test_absorbed_children_keep_their_own_section():
    run, verify = _recording()
    with verify.section("Rails"):
        child = verify.equal(1, 1, name="3V3")
    with verify.section("Summary"):
        parent = verify.all_satisfy([child], lambda c: c, name="All rails")
    assert run.records == [parent]
    assert parent["section"] == ["Summary"]
    assert parent["child_checks"][0]["section"] == ["Rails"]


def test_a_lazy_child_is_in_the_section_it_is_called_in():
    run, verify = _recording()
    with verify.section("Mode"):
        parent = verify.conditional(
            1, cases={1: lambda: verify.equal(2, 2, name="one")}, name="mode"
        )
    assert parent["section"] == ["Mode"]
    assert parent["cases"]["1"]["section"] == ["Mode"]
    assert run.records == [parent]


@pytest.mark.parametrize("title", [42, None, b"3V3", ["3V3"]])
def test_a_title_must_be_a_string(title):
    _, verify = _recording()
    with pytest.raises(TypeError, match=r"^section\(\) title must be a string"):
        verify.section(title)


@pytest.mark.parametrize("title", ["", "   ", "\n"])
def test_a_title_must_not_be_empty(title):
    _, verify = _recording()
    with pytest.raises(ValueError, match=r"^section\(\) title must not be empty$"):
        verify.section(title)


def test_checks_cannot_open_a_section():
    with pytest.raises(RuntimeError, match=r"checks\.section\(\) cannot group checks"):
        checks.section("3V3")
    with pytest.raises(RuntimeError, match=r"checks\.section\(\) cannot group checks"):
        checks.require.section("3V3")


def test_a_usage_error_comes_before_the_builder_error():
    with pytest.raises(TypeError):
        checks.section(42)  # type: ignore[arg-type]


def test_a_finished_test_cannot_open_a_section():
    run, verify = _recording()
    section = verify.section("late")
    run.closed = True
    with pytest.raises(RuntimeError, match="already finished"):
        with section:
            pass


def test_a_section_of_another_test_does_not_apply():
    _, first = _recording()
    _, second = _recording()
    with first.section("First test"):
        record = second.equal(1, 1, name="a")
    assert "section" not in record


def test_concurrent_tasks_keep_their_own_sections():
    _, verify = _recording()
    records = {}

    async def rail(name: str, first: asyncio.Event, second: asyncio.Event) -> None:
        with verify.section(name):
            first.set()
            await second.wait()
            records[name] = verify.equal(1, 1, name="Vout")

    async def main() -> None:
        a_in, b_in = asyncio.Event(), asyncio.Event()
        await asyncio.gather(rail("3V3", a_in, b_in), rail("5V0", b_in, a_in))

    asyncio.run(main())
    assert records["3V3"]["section"] == ["3V3"]
    assert records["5V0"]["section"] == ["5V0"]


def test_a_task_created_in_a_section_is_in_it():
    _, verify = _recording()

    async def check() -> dict:
        return verify.equal(1, 1, name="a")

    async def main() -> dict:
        with verify.section("Async"):
            task = asyncio.ensure_future(check())
        return await task

    assert asyncio.run(main())["section"] == ["Async"]


def test_exiting_in_another_context_leaves_that_context_alone():
    _, verify = _recording()
    section = verify.section("Fixture")
    contextvars.copy_context().run(section.__enter__)
    section.__exit__(None, None, None)  # an async fixture finished in another task
    assert "section" not in verify.equal(1, 1, name="a")


@pytest.mark.skipif(
    bool(getattr(sys.flags, "thread_inherit_context", 0)),
    reason="threads start in a copy of the context",
)
def test_a_thread_starts_outside_the_section():
    _, verify = _recording()
    records = []
    with verify.section("Main"):
        thread = threading.Thread(target=lambda: records.append(verify.equal(1, 1, name="t")))
        thread.start()
        thread.join()
        context = contextvars.copy_context()
        copied = threading.Thread(
            target=lambda: context.run(lambda: records.append(verify.equal(1, 1, name="c")))
        )
        copied.start()
        copied.join()
    assert "section" not in records[0]
    assert records[1]["section"] == ["Main"]


class TestSummary:
    def _results(self):
        return [
            {
                "check_type": "equal",
                "name": "Vout",
                "description": "",
                "passed": False,
                "detail": "expected 3.3, got 3.8",
                "section": ["3V3", "Load"],
            },
            {
                "check_type": "equal",
                "name": "Iout",
                "description": "",
                "passed": True,
                "detail": "1 == 1",
                "section": ["3V3"],
            },
            {"check_type": "equal", "name": "Temp", "description": "", "passed": True,
             "detail": "1 == 1"},
        ]

    def test_names_follow_their_section_titles(self):
        lines = format_summary(self._results()).split("\n")
        assert lines[0] == "1 of 3 checks failed: 3V3 › Load › Vout — expected 3.3, got 3.8"
        assert "  ✗ [0] 3V3 › Load › Vout — expected 3.3, got 3.8" in lines
        assert "  ✓ [1] 3V3 › Iout — 1 == 1" in lines
        assert "  ✓ [2] Temp — 1 == 1" in lines

    def test_titles_are_one_line(self):
        results = self._results()
        results[0]["section"] = ["a\nb"]
        assert format_summary(results).split("\n")[0].startswith("1 of 3 checks failed: a\\nb › ")

    @pytest.mark.parametrize("section", ["3V3", [], None, 5])
    def test_a_malformed_section_shows_only_the_name(self, section):
        results = self._results()
        results[0]["section"] = section
        assert "  ✗ [0] Vout — expected 3.3, got 3.8" in format_summary(results).split("\n")

    def test_a_terminal_that_cannot_show_the_separator_gets_an_escape(self):
        text = format_summary(self._results())
        assert "  ok [1] 3V3 \\u203a Iout \\u2014 1 == 1" in for_terminal(text, "ascii")

    def test_the_error_names_the_section(self):
        error = ChecksFailedError(self._results())
        assert str(error).startswith("1 of 3 checks failed: 3V3 › Load › Vout")


def test_sections_in_a_session(pytester: pytest.Pytester):
    pytester.makeconftest(
        """
        import json

        def pytest_runtest_logreport(report):
            for check in getattr(report, "verify_checks", None) or []:
                print("CHECK", json.dumps([check["name"], check.get("section")]))
        """
    )
    pytester.makepyfile(
        """
        import pytest

        RAILS = {"3V3": 3.8, "5V0": 5.0}

        @pytest.fixture
        def board(verify):
            with verify.section("Board"):
                verify.is_true(True, name="Powered")
                yield
                verify.is_true(True, name="Off")

        def test_rails(verify):
            for rail, measured in RAILS.items():
                with verify.section(rail):
                    verify.approx(measured, float(rail.replace("V", ".")), abs_tol=0.05,
                                  name="Vout", units="V")
            verify.equal(1, 1, name="Done")

        def test_fixture_section(board, verify):
            verify.equal(1, 1, name="Body")
        """
    )
    result = pytester.runpytest("-s", "-rf")
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(
        [
            "*  ✗ [[]0] 3V3 › Vout (test_sections_in_a_session.py:15)"
            " — expected 3.3V ± 0.05V, got 3.8V",
            "*  ✓ [[]1] 5V0 › Vout — *",
            "*  ✓ [[]2] Done — *",
            "FAILED *::test_rails - 1 of 3 checks failed: 3V3 › Vout — expected *",
        ]
    )
    # A fixture's section stays open while the test body runs, as any ``with`` block would.
    result.stdout.fnmatch_lines(
        [
            '*CHECK [[]"Powered", [[]"Board"]]',
            '*CHECK [[]"Body", [[]"Board"]]',
            '*CHECK [[]"Off", [[]"Board"]]',
        ]
    )
