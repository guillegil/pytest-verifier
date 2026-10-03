"""Tests for the public API added in pytest-verify 0.2.0.

Covers:
- get_check_results() exported from pytest_verifier
- The names added in 0.10.0 (LimitRow, Raises, load_limits) exported and importable
- Unconditional stash write (reporter detection removed)
- Soft-assert failure behavior unchanged
"""
from __future__ import annotations


class TestGetCheckResultsExported:
    """R1.1 — Symbol exists and is exported."""

    def test_importable_from_package(self):
        # Must not raise ImportError
        from pytest_verifier import get_check_results  # noqa: F401

    def test_in_all(self):
        import pytest_verifier

        assert "get_check_results" in pytest_verifier.__all__

    def test_every_type_in_a_public_signature_is_exported(self):
        import pytest_verifier

        for name in ("Verify", "Require", "CheckDescriptor", "GuardBranch", "ChecksFailedError"):
            assert name in pytest_verifier.__all__
            assert hasattr(pytest_verifier, name)
        assert isinstance(pytest_verifier.checks.require, pytest_verifier.Require)


class TestLabNamesExported:
    """0.10.0: the types and the function of verify.raises and verify.limits."""

    def test_importable_from_package(self):
        from pytest_verifier import LimitRow, Raises, load_limits
        from pytest_verifier._limits import LimitRow as private_row
        from pytest_verifier._limits import load_limits as private_load
        from pytest_verifier._verify import Raises as private_raises

        assert (LimitRow, Raises, load_limits) == (private_row, private_raises, private_load)

    def test_in_all(self):
        import pytest_verifier

        for name in ("LimitRow", "Raises", "load_limits"):
            assert name in pytest_verifier.__all__
            assert hasattr(pytest_verifier, name)
        assert len(set(pytest_verifier.__all__)) == len(pytest_verifier.__all__)

    def test_star_import_brings_them(self):
        namespace: dict = {}
        exec("from pytest_verifier import *", namespace)
        assert {"LimitRow", "Raises", "load_limits"} <= set(namespace)

    def test_their_kinds(self):
        import typing

        from pytest_verifier import LimitRow, Raises, load_limits

        assert isinstance(LimitRow, type) and issubclass(LimitRow, dict)
        assert LimitRow.__total__ is False  # every key of a row is optional
        assert {"check", "expected", "low", "high", "source"} <= set(LimitRow.__annotations__)
        assert typing.Generic in Raises.__mro__
        Raises[ValueError]  # generic over the expected exception type  # noqa: B018
        assert callable(load_limits) and load_limits.__doc__

    def test_public_hints_resolve_to_exported_names(self):
        # Tools evaluate public annotations with get_type_hints (also on Python 3.9).
        import typing

        import pytest_verifier
        from pytest_verifier import LimitRow, Raises, Verify, load_limits

        hints = {
            "raises": typing.get_type_hints(Verify.raises),
            "limits": typing.get_type_hints(Verify.limits),
            "eventually": typing.get_type_hints(Verify.eventually),
            "stable": typing.get_type_hints(Verify.stable),
            "load_limits": typing.get_type_hints(load_limits),
            "LimitRow": typing.get_type_hints(LimitRow),
            "Raises.__init__": typing.get_type_hints(Raises.__init__),
        }
        assert typing.get_origin(hints["raises"]["return"]) is Raises
        assert LimitRow in typing.get_args(typing.get_args(hints["limits"]["table"])[1])
        assert typing.get_args(hints["load_limits"]["return"])[1] is LimitRow
        assert hints["eventually"]["return"] is pytest_verifier.CheckDescriptor
        assert hints["stable"]["return"] is pytest_verifier.CheckDescriptor


class TestGetCheckResultsReturnsDescriptors:
    """R1.2, R1.3, R1.4 — Return semantics."""

    def test_returns_list_of_check_descriptors(self, pytester):
        """Scenario 1: item with N checks returns list of N CheckDescriptors."""
        pytester.makepyfile("""
            from pytest_verifier import get_check_results

            def test_inner(request, verify):
                verify.equal(1, 1, name="A")
                verify.greater(10, 5, name="B")
                item = request.node
                results = get_check_results(item)
                assert len(results) == 2
                assert all(isinstance(r, dict) for r in results)
                assert results[0]["name"] == "A"
                assert results[1]["name"] == "B"
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)

    def test_returns_empty_list_for_unchecked_item(self, pytester):
        """Scenario 2: item with no checks returns []."""
        pytester.makepyfile("""
            from pytest_verifier import get_check_results

            def test_inner(request):
                item = request.node
                results = get_check_results(item)
                assert results == []
                assert results is not None
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)

    def test_returned_list_is_a_copy(self, pytester):
        """R1.4 — Mutating the returned list does NOT affect the stash."""
        pytester.makepyfile("""
            from pytest_verifier import get_check_results
            from pytest_verifier._stash import check_results_key

            def test_inner(request, verify):
                verify.equal(1, 1, name="Only")
                item = request.node
                results = get_check_results(item)
                # Mutate the returned list
                results.clear()
                # Stash must still have 1 item
                stash_list = item.stash.get(check_results_key, [])
                assert len(stash_list) == 1
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)


class TestUnconditionalStashWrite:
    """R2.1, R2.2 — Stash is written regardless of reporter presence."""

    def test_stash_populated_without_reporter(self, pytester):
        """Scenario 3: stash populated even when reporter is NOT installed."""
        pytester.makepyfile("""
            from pytest_verifier._stash import check_results_key

            def test_no_reporter(request, verify):
                verify.equal(1, 1, name="X")
                # Reporter is NOT registered — stash must still be populated
                stash_list = request.node.stash.get(check_results_key, None)
                assert stash_list is not None, "stash not written"
                assert len(stash_list) == 1
        """)
        # Run WITHOUT any reporter plugin
        result = pytester.runpytest("-p", "no:pytest_reporter")
        result.assert_outcomes(passed=1)

    def test_stash_populated_with_is_instance(self, pytester):
        """R2.1 — is_instance also writes unconditionally."""
        pytester.makepyfile("""
            from pytest_verifier._stash import check_results_key

            def test_is_instance_stash(request, verify):
                verify.is_instance({}, dict, name="Dict")
                stash_list = request.node.stash.get(check_results_key, None)
                assert stash_list is not None
                assert len(stash_list) == 1
        """)
        result = pytester.runpytest("-p", "no:pytest_reporter")
        result.assert_outcomes(passed=1)


class TestSoftAssertFailureBehaviorUnchanged:
    """R2.4 / Scenario 4 — Soft-assert failure behavior must be unchanged."""

    def test_failing_check_causes_failed_outcome(self, pytester):
        pytester.makepyfile("""
            def test_fails(verify):
                verify.equal(1, 2, name="Bad")
        """)
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*checks failed*"])

    def test_longrepr_contains_checks_failed_error(self, pytester):
        pytester.makepyfile("""
            def test_fails(verify):
                verify.equal(1, 2, name="Mismatch")
        """)
        result = pytester.runpytest("-v")
        result.assert_outcomes(failed=1)
        # ChecksFailedError message should appear in the output
        result.stdout.fnmatch_lines(["*checks failed*"])

    def test_mixed_checks_all_stashed(self, pytester):
        """All checks (pass AND fail) are recorded in stash."""
        pytester.makepyfile("""
            from pytest_verifier._stash import check_results_key
            from pytest_verifier import get_check_results

            def test_mixed(request, verify):
                verify.equal(1, 1, name="Pass")
                verify.equal(1, 2, name="Fail")
                results = get_check_results(request.node)
                assert len(results) == 2
                assert results[0]["passed"] is True
                assert results[1]["passed"] is False
        """)
        result = pytester.runpytest()
        # test fails due to soft-assert (verify.equal(1,2)), but the inner
        # assertions about the stash should pass — the test fails at teardown
        result.assert_outcomes(failed=1)


class TestCompositeSchemaThroughReader:
    """R3/R4 — Composites are exposed via get_check_results() as a single parent
    descriptor with nested children and NO leaked top-level child entries.

    This is the contract pytest-reporter renders against: it reads children from
    inside the parent card, so a leaked branch/case/child would show as a phantom
    independent card (and an unmatched guard branch as a standalone failure)."""

    def test_guard_parent_only_with_nested_branches(self, pytester):
        pytester.makepyfile("""
            from pytest_verifier import get_check_results

            def test_g(request, verify):
                verify.guard(
                    branches=[
                        (False, "low", verify.equal(1, 2, name="lo")),
                        (True, "ok", verify.equal(1, 1, name="ok")),
                    ],
                    default=verify.equal(9, 9, name="def"),
                    name="G",
                )
                results = get_check_results(request.node)
                # Only the parent is a top-level entry — no leaked children.
                assert len(results) == 1
                parent = results[0]
                assert parent["check_type"] == "guard"
                assert parent["matched_index"] == 1
                # Children remain nested and JSON-serializable inside the parent.
                labels = [b["label"] for b in parent["branches"]]
                assert labels == ["low", "ok"]
                assert parent["branches"][0]["check"]["name"] == "lo"
                assert parent["default"]["name"] == "def"
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)

    def test_conditional_parent_only_with_nested_cases(self, pytester):
        pytester.makepyfile("""
            from pytest_verifier import get_check_results

            def test_c(request, verify):
                verify.conditional(
                    1,
                    cases={
                        0: verify.equal(1, 2, name="zero"),
                        1: verify.equal(1, 1, name="one"),
                    },
                    name="C",
                )
                results = get_check_results(request.node)
                assert len(results) == 1
                parent = results[0]
                assert parent["check_type"] == "conditional"
                assert parent["matched_case"] == "1"
                assert set(parent["cases"]) == {"0", "1"}
                assert parent["cases"]["1"]["name"] == "one"
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)

    def test_all_satisfy_parent_only_with_nested_children(self, pytester):
        pytester.makepyfile("""
            from pytest_verifier import get_check_results
            from pytest_verifier import checks

            def test_a(request, verify):
                verify.all_satisfy(
                    [1, 2, 3],
                    lambda x: checks.greater(x, 0, name=f"item_{x}"),
                    name="Positives",
                )
                results = get_check_results(request.node)
                assert len(results) == 1
                parent = results[0]
                assert parent["check_type"] == "all_satisfy"
                assert len(parent["child_checks"]) == 3
                assert parent["child_checks"][0]["check_type"] == "greater"
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)


class TestVersion:
    def test_version_is_the_installed_one(self):
        from importlib import metadata

        import pytest

        import pytest_verifier

        assert "__version__" in pytest_verifier.__all__
        try:
            installed = metadata.version("pytest-verifier")
        except metadata.PackageNotFoundError:
            pytest.skip("pytest-verifier is not installed")
        assert pytest_verifier.__version__ == installed

    def test_an_uninstalled_tree_says_so(self, monkeypatch):
        from importlib import metadata

        import pytest_verifier

        def missing(name):
            raise metadata.PackageNotFoundError(name)

        monkeypatch.setattr(metadata, "version", missing)
        assert pytest_verifier._installed_version() == "0+unknown"


class TestReadme:
    def test_links_work_on_pypi(self):
        # PyPI shows the README without heading anchors and outside the repository: every link
        # must be absolute.
        import re
        from pathlib import Path

        import pytest

        readme = Path(__file__).resolve().parents[1] / "README.md"
        if not readme.exists():
            pytest.skip("no README next to the tests")
        targets = re.findall(r"\]\(([^)\s]+)\)", readme.read_text(encoding="utf-8"))
        assert targets
        assert [t for t in targets if not t.startswith("https://")] == []
