"""FEAT-1: every recorded check says where it was made."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pytest

from pytest_verifier import checks
from pytest_verifier._exceptions import format_summary
from pytest_verifier._location import display_path, split
from pytest_verifier._run import Run, recording_verify


def _records(pytester: pytest.Pytester, *args: str) -> Dict[str, List[Dict[str, Any]]]:
    """Run the session in process and return each test's recorded checks, all phases."""
    reprec = pytester.inline_run("-p", "no:cacheprovider", *args)
    found: Dict[str, List[Dict[str, Any]]] = {}
    for report in reprec.getreports("pytest_runtest_logreport"):
        name = report.nodeid.split("::")[-1]
        found.setdefault(name, []).extend(getattr(report, "verify_checks", None) or [])
    return found


class TestRecordedLocation:
    def test_a_check_in_the_test_body(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            test_body="""
            def test_body(verify):
                verify.equal(1, 1, name="a")
                verify.equal(1, 2, name="b")
            """
        )
        [a, b] = _records(pytester)["test_body"]
        assert a["location"] == "test_body.py:2"
        assert b["location"] == "test_body.py:3"
        assert "called_from" not in a and "called_from" not in b

    def test_a_helper_in_another_module(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            helpers="""
            def check_rail(verify, value):
                return verify.between(value, 3.2, 3.4, name="rail", units="V")
            """,
            test_rails="""
            from helpers import check_rail

            def test_rails(verify):
                check_rail(verify, 3.3)
                check_rail(verify, 3.9)
            """,
        )
        pytester.syspathinsert()
        first, second = _records(pytester)["test_rails"]
        assert first["location"] == second["location"] == "helpers.py:2"
        assert first["called_from"] == "test_rails.py:4"
        assert second["called_from"] == "test_rails.py:5"

    def test_paths_are_relative_to_the_rootdir(self, pytester: pytest.Pytester) -> None:
        pytester.makeini("[pytest]\n")
        sub = pytester.mkpydir("tests") / "sub"
        sub.mkdir()
        source = "def test_deep(verify):\n    verify.is_true(1, name='x')\n"
        (sub / "test_deep.py").write_text(source)
        [record] = _records(pytester)["test_deep"]
        assert record["location"] == "tests/sub/test_deep.py:2"

    def test_a_fixture_check_has_no_caller_in_the_test(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import pytest

            @pytest.fixture
            def psu(verify):
                verify.equal(1, 1, name="setup")
                yield
                verify.equal(1, 1, name="teardown")
            """
        )
        pytester.makepyfile(test_fixture="def test_it(psu):\n    pass\n")
        setup, teardown = _records(pytester)["test_it"]
        assert (setup["location"], teardown["location"]) == ("conftest.py:5", "conftest.py:7")
        assert "called_from" not in setup and "called_from" not in teardown

    def test_lazy_children_and_factories(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            test_lazy="""
            def test_lazy(verify):
                verify.conditional(1, cases={1: lambda: verify.equal(1, 1, name="same line")},
                                   name="mode")
                verify.all_satisfy(
                    [1],
                    lambda x: verify.equal(x, 1, name="item"),
                    name="items",
                )
            """
        )
        mode, items = _records(pytester)["test_lazy"]
        assert mode["location"] == "test_lazy.py:2"
        [chosen] = [case for case in mode["cases"].values() if case]
        assert chosen["location"] == "test_lazy.py:2"
        assert "called_from" not in chosen  # the same line as the call
        assert items["location"] == "test_lazy.py:4"
        [item] = items["child_checks"]
        assert (item["location"], item["called_from"]) == ("test_lazy.py:6", "test_lazy.py:4")

    def test_decorated_tests_and_methods(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            test_decorated="""
            import functools

            def logged(fn):
                @functools.wraps(fn)
                def wrapper(*args, **kwargs):
                    return fn(*args, **kwargs)
                return wrapper

            def helper(verify):
                verify.is_true(0, name="h")

            @logged
            def test_wrapped(verify):
                helper(verify)

            class TestGroup:
                def test_method(self, verify):
                    helper(verify)
            """
        )
        found = _records(pytester)
        [wrapped] = found["test_wrapped"]
        [method] = found["test_method"]
        assert wrapped["called_from"] == "test_decorated.py:14"
        assert method["called_from"] == "test_decorated.py:18"

    def test_a_check_made_in_another_thread(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            test_thread="""
            import threading

            def test_thread(verify):
                worker = threading.Thread(target=lambda: verify.equal(1, 1, name="t"))
                worker.start()
                worker.join()
            """
        )
        [record] = _records(pytester)["test_thread"]
        assert record["location"] == "test_thread.py:4"
        assert "called_from" not in record

    def test_record_uses_the_line_of_the_record_call(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            test_record="""
            from pytest_verifier import checks

            def test_record(verify):
                built = checks.equal(1, 2, name="built")
                recorded = verify.record(built)
                assert verify.record(recorded) is recorded
                verify.record(dict(recorded))
            """
        )
        first, copy = _records(pytester)["test_record"]
        assert first["location"] == "test_record.py:5"
        assert copy["location"] == "test_record.py:7"

    def test_built_checks_and_evaluate_have_no_location(self) -> None:
        built = checks.equal(1, 2, name="x")
        assert "location" not in built
        [result] = checks.evaluate_detailed(built)
        assert "location" not in result

    def test_records_stay_json_safe(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(test_json="def test_json(verify):\n    verify.equal(1, 2, name='x')\n")
        [record] = _records(pytester)["test_json"]
        assert json.loads(json.dumps(record))["location"] == "test_json.py:2"

    def test_a_run_without_rootdir_keeps_absolute_paths(self) -> None:
        record = recording_verify(Run()).equal(1, 1, name="x")
        path, line = split(record["location"]) or ("", 0)
        assert os.path.isabs(path) and os.path.samefile(path, __file__)
        assert line > 0


class TestDisplayPath:
    def test_inside_and_outside_the_rootdir(self, tmp_path: Any) -> None:
        root = str(tmp_path / "root")
        inside = os.path.join(root, "pkg", "test_x.py")
        outside = str(tmp_path / "elsewhere" / "helper.py")
        assert display_path(inside, root) == "pkg/test_x.py"
        assert display_path(outside, root) == outside
        assert display_path(inside, None) == inside
        assert display_path("<string>", root) == "<string>"

    @pytest.mark.parametrize(
        "site, expected",
        [
            ("tests/test_x.py:12", ("tests/test_x.py", 12)),
            ("C:/work/test_x.py:3", ("C:/work/test_x.py", 3)),
            ("test_x.py", None),
            ("test_x.py:", None),
            (":4", None),
            (None, None),
            (12, None),
        ],
    )
    def test_split(self, site: Any, expected: Any) -> None:
        assert split(site) == expected


class TestSummaryShowsLocations:
    def _failed(self, **extra: Any) -> Dict[str, Any]:
        record = dict(checks.equal(1, 2, name="Vout"), passed=False, detail="expected 2, got 1")
        record.update(extra)
        return record

    def test_failed_lines_show_where_passed_lines_do_not(self) -> None:
        failed = self._failed(location="tests/test_psu.py:17")
        passed = dict(checks.equal(1, 1, name="ok"), passed=True, location="tests/test_psu.py:9")
        assert format_summary([failed, passed]).splitlines()[2:] == [
            "  ✗ [0] Vout (tests/test_psu.py:17) — expected 2, got 1",
            "",
            "  ✓ [1] ok — 1 == 1",
        ]

    def test_a_caller_in_another_file_and_in_the_same_file(self) -> None:
        other = self._failed(location="lib/rails.py:8", called_from="tests/test_psu.py:22")
        same = self._failed(location="tests/test_psu.py:5", called_from="tests/test_psu.py:22")
        lines = format_summary([other, same]).splitlines()[2:]
        assert lines == [
            "  ✗ [0] Vout (lib/rails.py:8, called from tests/test_psu.py:22) — expected 2, got 1",
            "  ✗ [1] Vout (tests/test_psu.py:5, called from line 22) — expected 2, got 1",
        ]

    def test_hand_built_locations_are_escaped_and_bounded(self) -> None:
        odd = self._failed(location="a\nb.py:1" + "x" * 500, called_from=7)
        [line] = [text for text in format_summary([odd]).splitlines() if "[0]" in text]
        assert "a\\nb.py:1" in line and len(line) < 300


class TestCrashLine:
    def test_tb_line_and_r_point_at_the_failed_check(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            helpers="def check(verify, v):\n    verify.equal(v, 1, name='v')\n",
            test_crash="""
            from helpers import check

            def test_direct(verify):
                verify.equal(1, 1, name="fine")
                verify.equal(1, 2, name="direct")

            def test_helper(verify):
                check(verify, 1)
                check(verify, 2)
            """,
        )
        pytester.syspathinsert()
        result = pytester.runpytest("-p", "no:cacheprovider", "--tb=line")
        result.assert_outcomes(failed=2)
        result.stdout.fnmatch_lines(
            [
                "*test_crash.py:5: 1 of 2 checks failed: direct — expected 2, got 1",
                "*test_crash.py:9: 1 of 2 checks failed: v — expected 1, got 2",
            ]
        )

    def test_a_fixture_check_keeps_the_def_line(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import pytest

            @pytest.fixture
            def bad(verify):
                verify.equal(1, 2, name="from fixture")
            """
        )
        pytester.makepyfile(test_fixture_crash="# one\n# two\ndef test_it(bad):\n    pass\n")
        result = pytester.runpytest("-p", "no:cacheprovider", "--tb=line")
        result.stdout.fnmatch_lines(["*test_fixture_crash.py:3: 1 of 1 checks failed: *"])
