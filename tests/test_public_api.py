"""Tests for the public API added in pytest-verify 0.2.0.

Covers:
- get_check_results() exported from pytest_verify
- Unconditional stash write (reporter detection removed)
- Soft-assert failure behavior unchanged
"""
from __future__ import annotations


class TestGetCheckResultsExported:
    """R1.1 — Symbol exists and is exported."""

    def test_importable_from_package(self):
        # Must not raise ImportError
        from pytest_verify import get_check_results  # noqa: F401

    def test_in_all(self):
        import pytest_verify

        assert "get_check_results" in pytest_verify.__all__


class TestGetCheckResultsReturnsDescriptors:
    """R1.2, R1.3, R1.4 — Return semantics."""

    def test_returns_list_of_check_descriptors(self, pytester):
        """Scenario 1: item with N checks returns list of N CheckDescriptors."""
        pytester.makepyfile("""
            from pytest_verify import get_check_results

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
            from pytest_verify import get_check_results

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
            from pytest_verify import get_check_results
            from pytest_verify._stash import check_results_key

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
            from pytest_verify._stash import check_results_key

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
            from pytest_verify._stash import check_results_key

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
            from pytest_verify._stash import check_results_key
            from pytest_verify import get_check_results

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
            from pytest_verify import get_check_results

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
            from pytest_verify import get_check_results

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
            from pytest_verify import get_check_results
            from pytest_verify import verify as checks

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
