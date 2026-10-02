"""Output options: ``--verify-show-passed``, ``--verify-ascii`` and ``--verify-summary``, and
the margins the summary shows."""
from __future__ import annotations

import pytest

from pytest_verifier import checks
from pytest_verifier._checks import margin
from pytest_verifier._run import Run, recording_verify
from pytest_verifier._summary import SessionSummary

_MANY = """
    def test_many(verify):
        for i in range(4):
            verify.equal(i, i, name=f"p{i}")
        verify.fail("boom")
"""


class TestShowPassed:
    @pytest.mark.parametrize(
        "args, shown, hidden",
        [
            (["--verify-show-passed=2"], 2, "2 more passed checks"),
            (["--verify-show-passed", "none"], 0, "4 more passed checks"),
            (["--verify-show-passed=all"], 4, None),
            (["-o", "verify_show_passed=1"], 1, "3 more passed checks"),
            (["-o", "verify_show_passed=1", "--verify-show-passed=3"], 3, "1 more passed check"),
            (["--verify-show-passed=0", "-vv"], 4, None),  # -vv always lists them all
        ],
    )
    def test_how_many_passed_checks_are_listed(
        self, pytester: pytest.Pytester, args, shown, hidden
    ):
        pytester.makepyfile(_MANY)
        result = pytester.runpytest(*args)
        result.assert_outcomes(failed=1)
        # With -vv, the short summary repeats the whole message: count each line once.
        listed = {line for line in result.outlines if line.startswith("  ✓ [")}
        assert len(listed) == shown
        if hidden is None:
            result.stdout.no_fnmatch_line("*more passed*")
        else:
            result.stdout.fnmatch_lines([f"  ✓ … {hidden} (-vv shows them)"])

    def test_a_check_that_stops_the_test_uses_it_too(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            def test_stop(verify):
                for i in range(4):
                    verify.equal(i, i, name=f"p{i}")
                verify.require.fail("boom")
        """)
        result = pytester.runpytest("--verify-show-passed=1")
        result.stdout.fnmatch_lines(
            ["*stopped at*", "  ✓ … 3 more passed checks (-vv shows them)"]
        )

    @pytest.mark.parametrize("value", ["-1", "ten", "", "1.5", "²", "١"])
    def test_an_invalid_value_is_a_usage_error(self, pytester: pytest.Pytester, value):
        pytester.makepyfile(_MANY)
        result = pytester.runpytest(f"--verify-show-passed={value}")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        result.stderr.fnmatch_lines(
            ["ERROR: verify_show_passed (--verify-show-passed) must be a number, all or none*"]
        )


class TestAscii:
    _TEST = """
        def test_a(verify):
            verify.between(5, 1, 3, name="rail", units="µV")
            verify.is_true(1, name="alive")

        def test_b(verify):
            verify.equal(1, 2, name="n")
            raise ValueError("hard failure")
    """

    @pytest.mark.parametrize("args", [["--verify-ascii"], ["-o", "verify_ascii=true"]])
    def test_the_terminal_gets_the_ascii_form(self, pytester: pytest.Pytester, args):
        pytester.makepyfile(self._TEST)
        result = pytester.runpytest("-rf", "--junitxml=out.xml", *args)
        result.assert_outcomes(failed=2)
        result.stdout.fnmatch_lines(
            [
                "1 of 2 checks failed: rail \\u2014 expected ?1\\xb5V, 3\\xb5V?, got 5\\xb5V",
                "",
                "  x ?0? rail (*.py:2) \\u2014 expected ?1\\xb5V, 3\\xb5V?, got 5\\xb5V",
                "",
                "  ok ?1? alive \\u2014 1 (truthy)",
            ]
        )
        result.stdout.fnmatch_lines(
            ["*Soft assertion failures*", "1 of 1 checks failed: n \\u2014 expected 2, got 1"]
        )
        result.stdout.fnmatch_lines(["FAILED *::test_a - 1 of 2 checks failed: rail \\u2014 *"])
        assert "✗" not in result.stdout.str() and "✓" not in result.stdout.str()

    def test_reports_keep_the_unicode_text(self, pytester: pytest.Pytester):
        import xml.etree.ElementTree as ET

        pytester.makeconftest("""
            def pytest_runtest_logreport(report):
                if report.failed:
                    print("LONGREPR", report.longreprtext.splitlines()[0])
        """)
        pytester.makepyfile(self._TEST)
        result = pytester.runpytest("-s", "--junitxml=out.xml", "--verify-ascii")
        result.stdout.fnmatch_lines(["*LONGREPR 1 of 2 checks failed: rail — expected *µV*"])
        root = ET.parse(str(pytester.path / "out.xml")).getroot()
        failure = next(root.iter("failure"))
        assert "  ✗ [0] rail (test_reports_keep_the_unicode_text.py:2) — " in (failure.text or "")

    @pytest.mark.parametrize("value", ["maybe", "yes please"])
    def test_an_invalid_value_is_a_usage_error(self, pytester: pytest.Pytester, value):
        pytester.makepyfile(self._TEST)
        result = pytester.runpytest("-o", f"verify_ascii={value}")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        result.stderr.fnmatch_lines(["ERROR: verify_ascii must be true or false: *"])

    def test_off_by_default(self, pytester: pytest.Pytester):
        pytester.makepyfile(self._TEST)
        result = pytester.runpytest("-o", "verify_ascii=false")
        result.stdout.fnmatch_lines(
            ["  ✗ [[]0] rail (*.py:2) — expected [[]1µV, 3µV], got 5µV"]
        )

    def test_it_ends_with_its_session(self, pytester: pytest.Pytester):
        """An inner session with --verify-ascii leaves the outer one's terminal alone."""
        from pytest_verifier.plugin import _ASCII_TERMINAL

        pytester.makepyfile("def test_a():\n    pass\n")
        pytester.runpytest("--verify-ascii").assert_outcomes(passed=1)
        assert _ASCII_TERMINAL.get() is False


_SESSION = """
    import pytest

    @pytest.mark.parametrize("temp", ["cold", "hot"])
    def test_rail(verify, temp):
        with verify.section("3V3"):
            verify.approx({"cold": 3.31, "hot": 3.36}[temp], 3.3, abs_tol=0.05, name="Vout",
                          units="V")
        verify.between({"cold": 0.2, "hot": 0.45}[temp], 0.1, 0.5, name="Current", units="A")
        verify.all_satisfy([1, 2, 3], lambda v: verify.less(v, 3, name="Channel"), name="Group")

    def test_text(verify):
        verify.equal("ok", "ok", name="Reply")
"""


class TestSessionSummary:
    def _section(self, result) -> list:
        lines = result.outlines
        start = next(
            i for i, line in enumerate(lines) if "pytest-verifier: checks by name" in line
        )
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("="))
        return lines[start + 1 : end]

    def test_off_by_default(self, pytester: pytest.Pytester):
        pytester.makepyfile(_SESSION)
        result = pytester.runpytest()
        result.stdout.no_fnmatch_line("*checks by name*")

    def test_failed(self, pytester: pytest.Pytester):
        pytester.makepyfile(_SESSION)
        assert self._section(pytester.runpytest("--verify-summary=failed")) == [
            "  ✗ 3V3 › Vout: 1 of 2 failed (first: test_failed.py::test_rail[hot])",
            "  ✗ Group: 2 of 2 failed (first: test_failed.py::test_rail[cold])",
            "  ✗ Channel: 2 of 6 failed (first: test_failed.py::test_rail[cold])",
        ]

    def test_all(self, pytester: pytest.Pytester):
        pytester.makepyfile(_SESSION)
        assert self._section(pytester.runpytest("-o", "verify_summary=all")) == [
            "  ✗ 3V3 › Vout: 1 of 2 failed (first: test_all.py::test_rail[hot])",
            "  ✗ Group: 2 of 2 failed (first: test_all.py::test_rail[cold])",
            "  ✗ Channel: 2 of 6 failed (first: test_all.py::test_rail[cold])",
            "  ✓ Current: 2 passed",
            "  ✓ Reply: 1 passed",
        ]

    def test_stats(self, pytester: pytest.Pytester):
        pytester.makepyfile(_SESSION)
        result = pytester.runpytest("-o", "verify_summary=failed", "--verify-summary=stats")
        assert self._section(result) == [
            "  ✗ 3V3 › Vout: 1 of 2 failed (first: test_stats.py::test_rail[hot]);"
            " 3.31V to 3.36V, margin -0.01V",
            "  ✗ Group: 2 of 2 failed (first: test_stats.py::test_rail[cold])",
            "  ✗ Channel: 2 of 6 failed (first: test_stats.py::test_rail[cold]);"
            " 1 to 3, margin 0",
            "  ✓ Current: 2 passed; 0.2A to 0.45A, margin 0.05A",
            "  ✓ Reply: 1 passed",
        ]

    def test_failed_when_everything_passed(self, pytester: pytest.Pytester):
        pytester.makepyfile("def test_a(verify):\n    verify.is_true(1, name='a')\n")
        result = pytester.runpytest("--verify-summary=failed")
        assert self._section(result) == ["  ✓ all 1 checks passed"]

    def test_nothing_without_checks(self, pytester: pytest.Pytester):
        pytester.makepyfile("def test_a():\n    pass\n")
        result = pytester.runpytest("--verify-summary=all")
        result.stdout.no_fnmatch_line("*checks by name*")

    def test_it_comes_before_the_short_summary(self, pytester: pytest.Pytester):
        pytester.makepyfile(_SESSION)
        result = pytester.runpytest("--verify-summary=failed", "-rf")
        result.stdout.fnmatch_lines(["*= FAILURES =*", "*checks by name*", "*short test summary*"])

    def test_the_ascii_form(self, pytester: pytest.Pytester):
        pytester.makepyfile(_SESSION)
        result = pytester.runpytest("--verify-summary=failed", "--verify-ascii")
        assert self._section(result)[0] == (
            "  x 3V3 \\u203a Vout: 1 of 2 failed (first: test_the_ascii_form.py::test_rail[hot])"
        )

    @pytest.mark.parametrize("logs_teardown", [False, True], ids=["older", "16.6.1+"])
    def test_reruns_count_once(self, pytester: pytest.Pytester, logs_teardown):
        from .test_regressions_lifecycle import _RERUN_CONFTEST, _RERUN_CONFTEST_LOGS_TEARDOWN

        pytester.makeconftest(_RERUN_CONFTEST_LOGS_TEARDOWN if logs_teardown else _RERUN_CONFTEST)
        pytester.makepyfile("""
            import pytest

            ATTEMPTS = {"n": 0, "teardown": 0}

            @pytest.fixture
            def rig(verify):
                yield
                verify.is_true(True, name="in teardown")

            def test_flaky(rig, verify):
                ATTEMPTS["n"] += 1
                verify.equal(ATTEMPTS["n"], 2, name="attempt")

            @pytest.fixture
            def flaky_teardown(verify):
                yield
                ATTEMPTS["teardown"] += 1
                verify.equal(ATTEMPTS["teardown"], 2, name="teardown attempt")

            def test_flaky_teardown(flaky_teardown, verify):
                verify.is_true(True, name="body")
        """)
        result = pytester.runpytest("--verify-summary=all")
        assert self._section(result) == [
            "  ✓ attempt: 1 passed",
            "  ✓ in teardown: 1 passed",
            "  ✓ body: 1 passed",
            "  ✓ teardown attempt: 1 passed",
        ]

    def test_huge_ints(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            def test_ids(verify):
                verify.equal(2**1100, 2**1100, name="id")
                verify.greater(10**400, 0, name="big")
                verify.less(1.5, 2, name="small")
        """)
        result = pytester.runpytest("--verify-summary=stats")
        assert result.ret == pytest.ExitCode.OK
        section = self._section(result)
        assert section[0].startswith("  ✓ id: 1 passed; 1")  # the int itself, bounded
        assert section[1].startswith("  ✓ big: 1 passed; 1")  # no margin: too large for a float
        assert "margin" not in section[1]
        assert section[2] == "  ✓ small: 1 passed; 1.5, margin 0.5"

    @pytest.mark.parametrize("value", ["on", "", "FAILED"])
    def test_an_invalid_value_is_a_usage_error(self, pytester: pytest.Pytester, value):
        pytester.makepyfile(_SESSION)
        result = pytester.runpytest("-o", f"verify_summary={value}")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        result.stderr.fnmatch_lines(
            ["ERROR: verify_summary (--verify-summary) must be off, failed, all or stats, *"]
        )

    def test_reports_from_several_workers_add_up(self):
        """An xdist controller gets JSON copies of the checks from each worker."""
        summary = SessionSummary("stats")
        rail = {"check_type": "less", "name": "Ripple", "threshold": 20, "units": "mV"}
        summary.add("t.py::test[a]", [dict(rail, actual=12.5, passed=True)])
        summary.add("t.py::test[b]", [dict(rail, actual=27.0, passed=False), "not a check"])
        summary.add("t.py::test[c]", [dict(rail, actual="n/a", passed=False)])
        assert summary.lines() == [
            "  ✗ Ripple: 2 of 3 failed (first: t.py::test[b]); 12.5mV to 27.0mV, margin -7mV"
        ]

    def test_unselected_children_are_not_counted(self):
        run = Run()
        run.phase = "call"
        verify = recording_verify(run)
        verify.conditional(
            1,
            cases={1: verify.equal(1, 1, name="one"), 2: verify.equal(1, 2, name="two")},
            name="Mode",
        )
        summary = SessionSummary("all")
        summary.add("t.py::test", run.records)
        assert summary.lines() == ["  ✓ Mode: 1 passed", "  ✓ one: 1 passed"]


class TestMargin:
    @pytest.mark.parametrize(
        "check, expected",
        [
            (checks.greater(5, 3, name="a"), 2),
            (checks.greater_equal(3, 3, name="a"), 0),
            (checks.less(5, 3, name="a"), -2),
            (checks.less_equal(2, 3, name="a"), 1),
            (checks.between(0.45, 0.1, 0.5, name="a"), 0.05),
            (checks.between(0.0, 0.1, 0.5, inclusive=False, name="a"), -0.1),
            (checks.approx(3.38, 3.3, abs_tol=0.05, name="a"), -0.03),
            (checks.approx(3.3, 3.3, abs_tol=0.01, rel_tol=0.02, name="a"), 0.066),
            (checks.approx(10, 10, rel_tol=0.1, name="a"), 1),
            # Exact, as the verdicts: ints are never rounded to floats.
            (checks.approx(10**17 + 9, 10**17, abs_tol=9, name="a"), 0),
            (checks.approx(10**17 + 10, 10**17, abs_tol=9, name="a"), -1),
            (checks.less(2**53 + 1, 2**53, name="a"), -1),
            (checks.between(2**53 + 1, 0, 2**53, name="a"), -1),
            (checks.greater(2**53, 2.0**53, name="a"), 0),
        ],
    )
    def test_how_far_inside_the_limit(self, check, expected):
        assert margin(check) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "check",
        [
            checks.equal(1, 1, name="a"),
            checks.greater(True, 0, name="a"),
            checks.greater("5", "3", name="a"),
            checks.greater(float("nan"), 0, name="a"),
            checks.greater(float("inf"), 0, name="a"),
            checks.greater(10**400, 0, name="a"),
            checks.between(None, 0, 1, name="a"),
            {"check_type": "less"},
            {"check_type": "no such type", "actual": 1},
        ],
    )
    def test_none_without_plain_numbers(self, check):
        assert margin(check) is None
