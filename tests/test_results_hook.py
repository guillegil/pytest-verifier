"""How other plugins read results: the ``phase`` of each record, the ``pytest_verify_results``
hook and ``report.verify_checks``."""
from __future__ import annotations

import json

_PHASES = """
    import pytest

    @pytest.fixture
    def rig(verify):
        verify.equal(1, 1, name="in setup")
        yield
        verify.equal(1, 2, name="in teardown")

    def test_phases(rig, verify):
        verify.equal(2, 2, name="in body")
"""


class _Collector:
    """A plugin that records every ``pytest_verify_results`` call."""

    def __init__(self):
        self.calls = []

    def pytest_verify_results(self, item, when, checks, passed):
        self.calls.append((item.name, when, checks, passed))


def test_every_record_says_its_phase(pytester):
    pytester.makepyfile(_PHASES + """
    import pytest_verifier

    @pytest.fixture(autouse=True)
    def show(request):
        yield
        print("PHASES", [(r["name"], r["phase"]) for r in
                         pytest_verifier.get_check_results(request.node)])
    """)
    result = pytester.runpytest("-s")
    result.stdout.fnmatch_lines([
        "*PHASES [[]('in setup', 'setup'), ('in body', 'call'),"
        " ('in teardown', 'teardown')[]]",
    ])
    result.assert_outcomes(passed=1, errors=1)


def test_hook_gets_the_checks_of_each_phase_once(pytester):
    pytester.makepyfile(_PHASES)
    collector = _Collector()
    reprec = pytester.inline_run(plugins=[collector])
    reprec.assertoutcome(passed=1, failed=1)  # the call passed, teardown failed
    assert [(name, when, [c["name"] for c in checks], passed)
            for name, when, checks, passed in collector.calls] == [
        ("test_phases", "call", ["in setup", "in body"], True),
        ("test_phases", "teardown", ["in teardown"], False),
    ]
    for _, _, checks, _ in collector.calls:
        json.dumps(checks, allow_nan=False)
        assert all(isinstance(c["passed"], bool) and c["detail"] for c in checks)


def test_hook_is_not_called_without_checks(pytester):
    pytester.makepyfile("""
        def test_nothing(verify):
            pass

        def test_no_fixture():
            pass
    """)
    collector = _Collector()
    pytester.inline_run(plugins=[collector]).assertoutcome(passed=2)
    assert collector.calls == []


def test_hook_reports_setup_checks_when_setup_fails(pytester):
    pytester.makepyfile("""
        import pytest

        @pytest.fixture
        def rig(verify):
            verify.equal(1, 2, name="probe")
            raise RuntimeError("no rig")

        def test_rig(rig):
            pass
    """)
    collector = _Collector()
    pytester.inline_run(plugins=[collector])
    assert [(when, [c["name"] for c in checks], passed)
            for _, when, checks, passed in collector.calls] == [("setup", ["probe"], False)]


def test_reports_carry_the_checks_they_judged(pytester):
    pytester.makepyfile(_PHASES)
    reprec = pytester.inline_run()
    reports = {r.when: r for r in reprec.getreports("pytest_runtest_logreport")}
    assert not hasattr(reports["setup"], "verify_checks")
    assert [c["name"] for c in reports["call"].verify_checks] == ["in setup", "in body"]
    assert [c["phase"] for c in reports["call"].verify_checks] == ["setup", "call"]
    assert [c["name"] for c in reports["teardown"].verify_checks] == ["in teardown"]
    assert reports["teardown"].failed


def test_report_checks_survive_serialization(pytester, request):
    """pytest-xdist sends reports between processes through these hooks."""
    pytester.makepyfile(_PHASES)
    reprec = pytester.inline_run()
    hook = request.config.hook
    for report in reprec.getreports("pytest_runtest_logreport"):
        data = hook.pytest_report_to_serializable(config=request.config, report=report)
        data = json.loads(json.dumps(data))
        copy = hook.pytest_report_from_serializable(config=request.config, data=data)
        assert getattr(copy, "verify_checks", None) == getattr(report, "verify_checks", None)


def test_an_optional_hook_works_without_the_plugin(pytester):
    pytester.makeconftest("""
        import pytest

        @pytest.hookimpl(optionalhook=True)
        def pytest_verify_results(item, when, checks, passed):
            print("RESULTS", when, [c["name"] for c in checks], passed)
    """)
    pytester.makepyfile("""
        def test_a(verify):
            verify.is_true(True, name="ok")
    """)
    result = pytester.runpytest("-s")
    result.stdout.fnmatch_lines(["*RESULTS call [[]'ok'[]] True*"])
    result.assert_outcomes(passed=1)
    result = pytester.runpytest("-p", "no:pytest_verifier")
    assert "pytest_verify_results" not in result.stdout.str()
    result.assert_outcomes(errors=1)  # the fixture is gone, but the hook is not an error

