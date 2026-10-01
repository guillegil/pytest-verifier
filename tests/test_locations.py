"""FEAT-1: every recorded check says where it was made."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pytest

import pytest_verifier
from pytest_verifier import checks
from pytest_verifier._exceptions import format_summary
from pytest_verifier._location import FunctionCode, display_path, locate, split
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

    def test_a_copy_recorded_in_the_test_body_drops_the_helpers_caller(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            test_copy="""
            def helper(verify):
                return verify.equal(1, 2, name="h")

            def test_copy(verify):
                original = helper(verify)
                verify.record(dict(original))
            """
        )
        original, copy = _records(pytester)["test_copy"]
        assert original["location"] == "test_copy.py:2"
        assert original["called_from"] == "test_copy.py:5"
        assert copy["location"] == "test_copy.py:6"
        assert "called_from" not in copy

    def test_a_decorator_that_hides_the_test(self, pytester: pytest.Pytester) -> None:
        # No functools.wraps: pytest sees the signature, but not the function behind it.
        pytester.makepyfile(
            test_hidden="""
            import inspect

            def logged(fn):
                def wrapper(*args, **kwargs):
                    return fn(*args, **kwargs)
                wrapper.__signature__ = inspect.signature(fn)
                return wrapper

            def helper(verify):
                verify.is_true(0, name="h")

            @logged
            def test_hidden(verify):
                verify.is_true(0, name="body")
                helper(verify)
            """
        )
        body, helper = _records(pytester)["test_hidden"]
        assert body["location"] == "test_hidden.py:14"
        assert "called_from" not in body
        assert helper["location"] == "test_hidden.py:10"
        assert helper["called_from"] == "test_hidden.py:15"

    def test_comprehensions_in_the_test_body(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            test_comp="""
            def test_comp(verify):
                values = [
                    verify.equal(x, 1, name=f"v{x}")
                    for x in [1, 2]
                ]
                found = {
                    x: verify.equal(x, 3, name="d") for x in [3]
                }
                total = sum(
                    1 for x in [4]
                    if verify.equal(x, 4, name="g")["passed"]
                )
            """
        )
        records = _records(pytester)["test_comp"]
        assert [(r["name"], r["location"]) for r in records] == [
            ("v1", "test_comp.py:3"),
            ("v2", "test_comp.py:3"),
            ("d", "test_comp.py:7"),
            ("g", "test_comp.py:11"),
        ]
        assert not any("called_from" in record for record in records)

    def test_test_code_matches_by_identity(self) -> None:
        # Two copies of the same function have equal code objects: only the test's own counts.
        source = "def test_x(helper):\n    return helper()\n"
        first: Dict[str, Any] = {}
        second: Dict[str, Any] = {}
        exec(compile(source, "/a/test_x.py", "exec"), first)
        exec(compile(source, "/b/test_x.py", "exec"), second)
        code = first["test_x"].__code__
        assert code == second["test_x"].__code__
        location, called_from = second["test_x"](lambda: locate(None, FunctionCode({code})))
        assert location is not None and called_from is None
        assert first["test_x"](lambda: locate(None, FunctionCode({code})))[1] == "/a/test_x.py:2"

    def test_this_package_is_recognized_by_module_name(self) -> None:
        source = "def fake():\n    return locate(None, FunctionCode())\n"
        # A plugin frame whose file name is not where the package is now (a moved venv).
        moved: Dict[str, Any] = {"__name__": "pytest_verifier._fake"}
        moved.update(locate=locate, FunctionCode=FunctionCode)
        exec(compile(source, "/elsewhere/pytest_verifier/_fake.py", "exec"), moved)
        path, _ = split(moved["fake"]()[0]) or ("", 0)
        assert os.path.samefile(path, __file__)
        # A user's module is the caller, whatever its file name.
        inside = os.path.join(os.path.dirname(pytest_verifier.__file__), "_user.py")
        user: Dict[str, Any] = {"__name__": "user_helpers"}
        user.update(locate=locate, FunctionCode=FunctionCode)
        exec(compile(source, inside, "exec"), user)
        assert user["fake"]()[0] == f"{inside}:2"

    def test_a_caller_outside_the_rootdir_is_left_out(self) -> None:
        # Library code that runs the test (a wrapper without functools.wraps) is no caller to
        # show.
        library: Dict[str, Any] = {}
        exec(compile("def wrapper(fn):\n    return fn()\n", "/outside/lib.py", "exec"), library)
        test = FunctionCode({library["wrapper"].__code__})
        root = os.path.dirname(os.path.abspath(__file__))
        location, called_from = library["wrapper"](lambda: locate(root, test))
        assert (location or "").startswith("test_locations.py:") and called_from is None
        assert library["wrapper"](lambda: locate(None, test))[1] == "/outside/lib.py:2"

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

    @pytest.mark.parametrize("length", [1, 500])
    def test_an_odd_caller_is_escaped_and_bounded(self, length: int) -> None:
        forged = "x\n  ✗ [9] forged:" + "9" * length
        odd = self._failed(location="lib/a.py:1", called_from=forged)
        lines = format_summary([odd]).splitlines()
        [line] = [text for text in lines if "[0]" in text]
        assert len(lines) == 3 and not any("[9]" in text for text in lines if text != line)
        assert len(line) < 300
        if length == 1:
            assert "called from x\\n  ✗ [9] forged:9)" in line

    def test_a_long_location_keeps_the_file_and_line(self) -> None:
        deep = "/" + "d/" * 150 + "rails.py:8"
        record = self._failed(location=deep, called_from="/" + "e/" * 150 + "test_psu.py:3")
        [line] = [text for text in format_summary([record]).splitlines() if "[0]" in text]
        assert "d/d/rails.py:8, called from ..." in line
        assert line.endswith("e/e/test_psu.py:3) — expected 2, got 1")
        assert len(line) < 500


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

    def test_a_helper_in_the_test_file(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            test_same="""
            def check(verify, v):
                verify.equal(v, 1, name="v")

            def test_helper(verify):
                check(verify, 1)
                check(verify, 2)
            """
        )
        result = pytester.runpytest("-p", "no:cacheprovider", "--tb=line")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            ["*test_same.py:6: 1 of 2 checks failed: v — expected 1, got 2"]
        )

    def test_the_line_of_the_check_the_message_names(self, pytester: pytest.Pytester) -> None:
        # The first failure comes from a fixture: the def line, not the line of a later one.
        pytester.makeconftest(
            """
            import pytest

            @pytest.fixture
            def psu(verify):
                verify.equal(5.0, 3.3, name="PSU setpoint")
            """
        )
        pytester.makepyfile(
            test_mix="""
            def test_mix(psu, verify):
                verify.equal(1, 1, name="ok")
                verify.equal(1, 2, name="later")
            """
        )
        result = pytester.runpytest("-p", "no:cacheprovider", "--tb=line")
        result.stdout.fnmatch_lines(["*test_mix.py:1: 2 of 3 checks failed: PSU setpoint *"])

    def test_the_check_that_stopped_the_test(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            test_stop="""
            def test_stop(verify):
                verify.equal(1, 2, name="soft")
                verify.require.equal(1, 2, name="hard")
            """
        )
        result = pytester.runpytest("-p", "no:cacheprovider", "--tb=line")
        result.stdout.fnmatch_lines(
            ["*test_stop.py:3: 2 of 2 checks failed, stopped at ?1?: hard *"]
        )

    def test_an_inherited_test(self, pytester: pytest.Pytester) -> None:
        # The test function lives in another file than the item: lines are paired with it.
        pytester.makepyfile(
            base_suite="""
            class BaseRailTests:
                def measure(self, verify):
                    pass

                def test_rail(self, verify):
                    verify.is_true(True, name="powered")
                    self.measure(verify)
            """,
            test_board="""
            from base_suite import BaseRailTests
            #
            #
            #
            #
            #
            #
            #
            #
            #
            class TestBoard(BaseRailTests):
                def measure(self, verify):
                    verify.between(3.6, 3.2, 3.4, name="3V3", units="V")
            """,
        )
        pytester.syspathinsert()
        result = pytester.runpytest("-p", "no:cacheprovider", "--tb=line", "test_board.py")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*base_suite.py:7: 1 of 2 checks failed: 3V3 *"])

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
