"""Exporting checks: junit ``<property>`` elements (``verify_junit_properties``) and a JSON Lines
file (``--verify-json``)."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from pytest_verifier.plugin import _JSON_PLUGIN, _JUNIT_PLUGIN, _JsonLines

from .test_regressions_lifecycle import _RERUN_CONFTEST, _RERUN_CONFTEST_LOGS_TEARDOWN

#: The two ways pytest-rerunfailures logs a repeated attempt: before 16.6.1 it stops at the
#: report it marks "rerun"; from 16.6.1 it also logs the attempt's teardown report.
_RERUNS = pytest.mark.parametrize(
    "rerun_conftest", [_RERUN_CONFTEST, _RERUN_CONFTEST_LOGS_TEARDOWN], ids=["older", "16.6.1+"]
)

_FLAKY = """
    import pytest

    ATTEMPTS = {"n": 0}

    @pytest.fixture
    def rig(verify):
        yield
        verify.is_true(True, name=f"teardown of attempt {ATTEMPTS['n']}")

    def test_flaky(rig, verify):
        ATTEMPTS["n"] += 1
        verify.equal(ATTEMPTS["n"], 2, name="attempt")
"""

_TESTS = """
    import pytest

    @pytest.fixture
    def rig(verify):
        verify.equal(1, 1, name="in setup")
        yield
        verify.equal(1, 2, name="in teardown")

    def test_rig(rig, verify):
        with verify.section("3V3"):
            verify.approx(3.8, 3.3, abs_tol=0.05, name="Vout", units="V")
        verify.equal("ü", "ü", name="Text")

    def test_plain(verify):
        verify.less(1, 2, name="Low")
"""


def _properties(pytester: pytest.Pytester, *args: str) -> dict:
    """``{test name: [(name, value), ...]}`` of a junit report made with *args*.

    A test that failed and also errored at teardown has two test cases; the last one holds
    every property (older pytest writes them to that one only).
    """
    result = pytester.runpytest("--junitxml=out.xml", "-o", "junit_family=xunit1", *args)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    root = ET.parse(str(pytester.path / "out.xml")).getroot()
    return {
        case.get("name"): [(prop.get("name"), prop.get("value")) for prop in case.iter("property")]
        for case in root.iter("testcase")
    }


class TestJunitProperties:
    def test_none_by_default(self, pytester: pytest.Pytester):
        pytester.makepyfile(_TESTS)
        assert _properties(pytester) == {"test_rig": [], "test_plain": []}

    def test_all_checks(self, pytester: pytest.Pytester):
        pytester.makepyfile(_TESTS)
        cases = _properties(pytester, "-o", "verify_junit_properties=all")
        assert cases == {
            "test_rig": [
                ("verify[0] in setup", "passed: 1 == 1"),
                ("verify[1] 3V3 › Vout", "failed: expected 3.3V ± 0.05V, got 3.8V"),
                ("verify[2] Text", "passed: 'ü' == 'ü'"),
                ("verify[3] in teardown", "failed: expected 2, got 1"),
            ],
            "test_plain": [("verify[0] Low", "passed: 1 < 2")],
        }

    def test_failed_checks_only(self, pytester: pytest.Pytester):
        pytester.makepyfile(_TESTS)
        cases = _properties(pytester, "-o", "verify_junit_properties= failed ")
        assert cases == {
            "test_rig": [
                ("verify[1] 3V3 › Vout", "failed: expected 3.3V ± 0.05V, got 3.8V"),
                ("verify[3] in teardown", "failed: expected 2, got 1"),
            ],
            "test_plain": [],
        }

    def test_record_property_still_works_alongside(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            def test_a(verify, record_property):
                record_property("board", "rev B")
                verify.equal(1, 2, name="a")
        """)
        cases = _properties(pytester, "-o", "verify_junit_properties=all")
        assert cases["test_a"] == [("board", "rev B"), ("verify[0] a", "failed: expected 2, got 1")]

    def test_an_unknown_value_is_a_usage_error(self, pytester: pytest.Pytester):
        pytester.makepyfile(_TESTS)
        result = pytester.runpytest("-o", "verify_junit_properties=yes")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        result.stderr.fnmatch_lines(
            ["ERROR: verify_junit_properties must be none, failed or all, not 'yes'"]
        )

    @pytest.mark.parametrize(
        "args, warned",
        [
            (["--junitxml=out.xml"], True),  # xunit2, pytest's default family
            (["--junitxml=out.xml", "-o", "junit_family=xunit1"], False),
            (["--junitxml=out.xml", "-o", "junit_family=legacy"], False),
            ([], False),  # no junit report
        ],
    )
    def test_a_family_without_properties_warns(self, pytester: pytest.Pytester, args, warned):
        pytester.makepyfile("def test_a(verify):\n    verify.is_true(True, name='a')\n")
        result = pytester.runpytest("-o", "verify_junit_properties=all", *args)
        result.assert_outcomes(passed=1, warnings=1 if warned else 0)
        if warned:
            result.stdout.fnmatch_lines(["*junit_family 'xunit2' does not allow*xunit1*"])

    @_RERUNS
    def test_a_rerun_keeps_only_the_last_attempt(self, pytester: pytest.Pytester, rerun_conftest):
        pytester.makeconftest(rerun_conftest)
        pytester.makepyfile(_FLAKY)
        result = pytester.runpytest(
            "--junitxml=out.xml", "-o", "junit_family=xunit1", "-o", "verify_junit_properties=all"
        )
        result.assert_outcomes(passed=1)
        root = ET.parse(str(pytester.path / "out.xml")).getroot()
        cases = [
            [(prop.get("name"), prop.get("value")) for prop in case.iter("property")]
            for case in root.iter("testcase")
        ]
        # 16.6.1+ makes a test case of the repeated attempt too: it gets none of its checks.
        assert cases[:-1] == [[]] * (len(cases) - 1)
        assert cases[-1] == [
            ("verify[0] attempt", "passed: 2 == 2"),
            ("verify[1] teardown of attempt 2", "passed: True"),
        ]

    def test_names_are_valid_xml(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            def test_odd_names(verify):
                verify.equal(1, 2, name="file caf\\udce9.csv")
                with verify.section("s\\uffff"):
                    verify.equal(1, 2, name="n\\ufffe")
        """)
        cases = _properties(pytester, "-o", "verify_junit_properties=all")
        assert [name for name, _ in cases["test_odd_names"]] == [
            "verify[0] file caf\\udce9.csv",
            "verify[1] s\\uffff › n\\ufffe",
        ]

    def test_a_check_judged_earlier_keeps_its_index(self, pytester: pytest.Pytester):
        """A teardown composite of checks the test body made: they were judged and numbered
        with the body, so they stay on their own and every index is used once."""
        pytester.makepyfile("""
            import pytest

            @pytest.fixture
            def rig(verify):
                made = []
                yield made
                verify.all_satisfy(made, lambda c: c, name="all rails")
                verify.equal(1, 2, name="teardown fail")

            def test_absorb(rig, verify):
                rig.append(verify.equal(1, 1, name="a"))
                verify.equal(1, 1, name="b")
        """)
        cases = _properties(pytester, "-o", "verify_junit_properties=all", "--verify-json=o.jsonl")
        assert [name for name, _ in cases["test_absorb"]] == [
            "verify[0] a", "verify[1] b", "verify[2] all rails", "verify[3] teardown fail",
        ]
        lines = _lines(pytester.path / "o.jsonl")
        assert [(line["index"], line["check"]["name"]) for line in lines] == [
            (0, "a"), (1, "b"), (2, "all rails"), (3, "teardown fail"),
        ]
        assert lines[2]["check"]["child_checks"][0]["name"] == "a"

    def test_a_worker_never_warns(self, pytester: pytest.Pytester):
        config = pytester.parseconfig("--junitxml=out.xml", "-o", "verify_junit_properties=all")
        config.workerinput = {}  # type: ignore[attr-defined]
        config._do_configure()
        try:
            assert config.pluginmanager.get_plugin(_JUNIT_PLUGIN) is None
        finally:
            config._ensure_unconfigure()

    def test_warns_when_a_conftest_turns_the_report_on(self, pytester: pytest.Pytester):
        pytester.makeconftest("""
            def pytest_configure(config):
                config.option.xmlpath = "auto.xml"
        """)
        pytester.makepyfile("def test_a(verify):\n    verify.is_true(True, name='a')\n")
        result = pytester.runpytest("-o", "verify_junit_properties=all")
        result.assert_outcomes(passed=1, warnings=1)
        result.stdout.fnmatch_lines(["*junit_family 'xunit2' does not allow*xunit1*"])


def _lines(path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestJsonLines:
    def test_one_line_per_check(self, pytester: pytest.Pytester):
        pytester.makepyfile(_TESTS)
        pytester.runpytest("--verify-json", "out.jsonl").assert_outcomes(
            failed=1, passed=1, errors=1
        )
        lines = _lines(pytester.path / "out.jsonl")
        assert {line["attempt"] for line in lines} == {1}
        summary = [
            (line["nodeid"].split("::")[1], line["when"], line["outcome"], line["index"],
             line["check"]["name"], line["check"]["passed"])
            for line in lines
        ]
        assert summary == [
            ("test_rig", "call", "failed", 0, "in setup", True),
            ("test_rig", "call", "failed", 1, "Vout", False),
            ("test_rig", "call", "failed", 2, "Text", True),
            ("test_rig", "teardown", "failed", 3, "in teardown", False),
            ("test_plain", "call", "passed", 0, "Low", True),
        ]
        vout = lines[1]["check"]
        assert vout["section"] == ["3V3"]
        assert vout["detail"] == "expected 3.3V ± 0.05V, got 3.8V"
        assert vout["phase"] == "call" and vout["actual"] == 3.8
        assert lines[0]["check"]["phase"] == "setup"
        assert lines[2]["check"]["actual"] == "ü"
        assert '"ü"' in (pytester.path / "out.jsonl").read_text(encoding="utf-8")

    def test_the_path_is_like_junitxml_s(self, pytester: pytest.Pytester, monkeypatch):
        pytester.makepyfile("def test_a(verify):\n    verify.is_true(True, name='a')\n")
        monkeypatch.setenv("HOME", str(pytester.path / "home"))
        monkeypatch.setenv("USERPROFILE", str(pytester.path / "home"))
        pytester.runpytest("--verify-json", "~/runs/a.jsonl").assert_outcomes(passed=1)
        assert _lines(pytester.path / "home" / "runs" / "a.jsonl")[0]["check"]["name"] == "a"
        pytester.runpytest("--verify-json", "reports/b.jsonl").assert_outcomes(passed=1)
        assert len(_lines(pytester.path / "reports" / "b.jsonl")) == 1
        monkeypatch.setenv("VERIFY_OUT", str(pytester.path / "env"))
        pytester.runpytest("--verify-json", "$VERIFY_OUT/c.jsonl").assert_outcomes(passed=1)
        assert len(_lines(pytester.path / "env" / "c.jsonl")) == 1

    def test_help_keeps_the_last_results(self, pytester: pytest.Pytester):
        (pytester.path / "out.jsonl").write_text("last run\n", encoding="utf-8")
        assert pytester.runpytest("--verify-json", "out.jsonl", "--markers").ret == 0
        assert (pytester.path / "out.jsonl").read_text(encoding="utf-8") == "last run\n"

    def test_text_that_utf_8_cannot_encode(self, pytester: pytest.Pytester):
        """``os.listdir`` makes lone surrogates of a file name that is not UTF-8."""
        pytester.makepyfile("""
            def test_listdir(verify):
                verify.contains(["caf\\udce9.csv"], "report.csv", name="written \\udce9")

            def test_next(verify):
                verify.is_true(True, name="next")
        """)
        result = pytester.runpytest("--verify-json", "out.jsonl")
        result.assert_outcomes(failed=1, passed=1)
        first = _lines(pytester.path / "out.jsonl")[0]["check"]
        assert first["name"] == "written \\udce9"
        assert first["haystack"] == ["caf\\udce9.csv"]

    def test_a_hand_built_check_with_a_key_that_is_not_text(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            from pytest_verifier import checks

            def test_odd_key(verify):
                check = checks.is_true(True, name="odd")
                check[(1, 2)] = 3
                verify.record(check)
        """)
        pytester.runpytest("--verify-json", "out.jsonl").assert_outcomes(passed=1)
        assert _lines(pytester.path / "out.jsonl")[0]["check"]["(1, 2)"] == 3

    def test_one_line_per_check_whatever_the_text(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            def test_separators(verify):
                verify.equal("a\\u2028b\\u2029c\\x85d", "x", name="separators")
        """)
        pytester.runpytest("--verify-json", "out.jsonl").assert_outcomes(failed=1)
        text = (pytester.path / "out.jsonl").read_text(encoding="utf-8")
        assert len(text.splitlines()) == 1
        assert json.loads(text)["check"]["actual"] == "a\u2028b\u2029c\x85d"

    def test_the_file_is_replaced_and_always_written(self, pytester: pytest.Pytester):
        (pytester.path / "out.jsonl").write_text("old\n", encoding="utf-8")
        pytester.makepyfile("def test_a():\n    pass\n")
        pytester.runpytest("--verify-json", "out.jsonl").assert_outcomes(passed=1)
        assert (pytester.path / "out.jsonl").read_text(encoding="utf-8") == ""

    def test_an_unwritable_path_is_a_usage_error(self, pytester: pytest.Pytester):
        (pytester.path / "taken").write_text("a file, not a folder", encoding="utf-8")
        result = pytester.runpytest("--verify-json", "taken/out.jsonl")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        result.stderr.fnmatch_lines(["ERROR: --verify-json: cannot write *out.jsonl: *"])

    @_RERUNS
    def test_reruns_are_numbered_by_attempt(self, pytester: pytest.Pytester, rerun_conftest):
        pytester.makeconftest(rerun_conftest)
        pytester.makepyfile(_FLAKY)
        pytester.runpytest("--verify-json", "out.jsonl").assert_outcomes(passed=1)
        lines = [
            (line["attempt"], line["when"], line["outcome"], line["index"], line["check"]["name"])
            for line in _lines(pytester.path / "out.jsonl")
        ]
        repeated = [(1, "call", "rerun", 0, "attempt")]
        if rerun_conftest is _RERUN_CONFTEST_LOGS_TEARDOWN:
            repeated.append((1, "teardown", "passed", 1, "teardown of attempt 1"))
        assert lines == repeated + [
            (2, "call", "passed", 0, "attempt"),
            (2, "teardown", "passed", 1, "teardown of attempt 2"),
        ]

    def test_an_xdist_worker_does_not_write(self, pytester: pytest.Pytester):
        config = pytester.parseconfig("--verify-json", "out.jsonl")
        config.workerinput = {}  # type: ignore[attr-defined]
        config._do_configure()
        try:
            assert config.pluginmanager.get_plugin(_JSON_PLUGIN) is None
        finally:
            config._ensure_unconfigure()
        assert not (pytester.path / "out.jsonl").exists()

    def test_tests_on_several_workers_are_numbered_apart(self, tmp_path):
        """With ``--dist each`` the same test runs on several workers at once, and their
        reports interleave on the controller."""
        path = tmp_path / "out.jsonl"
        writer = _JsonLines(str(path))
        writer.pytest_sessionstart()

        def report(node, when, *names):
            checks = [{"name": name, "passed": True} for name in names]
            return SimpleNamespace(
                nodeid="t.py::test", when=when, outcome="passed", node=node, verify_checks=checks
            )

        for each in [
            report("gw0", "setup"),
            report("gw1", "setup"),
            report("gw0", "call", "a", "b"),
            report("gw1", "call", "a"),
            report("gw1", "teardown", "c"),
            report("gw0", "teardown", "c"),
        ]:
            writer.pytest_runtest_logreport(each)
        writer.pytest_unconfigure()
        assert [(line["check"]["name"], line["index"]) for line in _lines(path)] == [
            ("a", 0), ("b", 1), ("a", 0), ("c", 1), ("c", 2),
        ]
        assert {line["attempt"] for line in _lines(path)} == {1}
