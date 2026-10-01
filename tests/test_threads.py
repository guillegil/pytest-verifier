"""Recording checks from several threads of one test (M-15, L-9).

Both bugs were races, so these tests raise the chance of a thread switch at every bytecode
(``sys.setswitchinterval``) and repeat the run. With the run's lock in place they always pass.
"""
from __future__ import annotations

import sys
import threading
from typing import Any, Iterator

import pytest

from pytest_verifier import Verify
from pytest_verifier._run import Run, recording_verify

THREADS = 8
PER_THREAD = 40
ROUNDS = 5


@pytest.fixture(autouse=True)
def _switch_often() -> Iterator[None]:
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def _run_threads(target: Any, *args: Any) -> None:
    barrier = threading.Barrier(THREADS)
    errors: list[BaseException] = []

    def body(tid: int) -> None:
        try:
            barrier.wait()
            target(tid, *args)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=body, args=(tid,)) for tid in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def _record_mixed(tid: int, fv: Verify) -> None:
    for i in range(PER_THREAD):
        fv.equal(i, i, name=f"plain-{tid}-{i}")
        fv.guard(
            branches=[
                (False, "unselected", fv.equal(1, 2, name="unselected")),
                (True, "selected", fv.equal(1, 1, name="selected")),
            ],
            name=f"guard-{tid}-{i}",
        )
        fv.all_satisfy([1, 2], lambda x: fv.greater(x, 0, name="positive"), name=f"all-{tid}-{i}")


def test_m15_every_check_from_every_thread_is_recorded_once() -> None:
    for _ in range(ROUNDS):
        run = Run()
        _run_threads(_record_mixed, recording_verify(run))

        names = sorted(record["name"] for record in run.records)
        expected = sorted(
            f"{kind}-{tid}-{i}"
            for tid in range(THREADS)
            for i in range(PER_THREAD)
            for kind in ("plain", "guard", "all")
        )
        # No check lost, none duplicated, and no child left at the top level.
        assert names == expected
        assert all(record["passed"] is True for record in run.take_unjudged()[1])


def test_m15_one_failing_guard_among_threads_always_fails_the_test(
    pytester: pytest.Pytester,
) -> None:
    """0.3.1 lost the one failing guard in about one run in nine (the test passed)."""
    pytester.makepyfile(
        """
        import sys
        import threading

        import pytest

        sys.setswitchinterval(1e-7)

        @pytest.mark.parametrize("run", range(100))
        def test_threads(verify, run):
            barrier = threading.Barrier(2)

            def work(tid):
                barrier.wait()
                for i in range(100):
                    ok = not (tid == 0 and i == 50)  # one failing guard in the whole test
                    verify.guard([(True, "taken", verify.is_true(ok, name="taken"))],
                                 name=f"G{tid}-{i}")

            threads = [threading.Thread(target=work, args=(t,)) for t in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            sys.setswitchinterval(0.005)
        """
    )
    result = pytester.runpytest("-q", "-rN")
    result.assert_outcomes(failed=100)
    # Every run recorded all 200 guards, exactly one of them failed.
    assert str(result.stdout).count("1 of 200 checks failed") == 100


def test_l9_concurrent_first_checks_all_reach_the_results(pytester: pytest.Pytester) -> None:
    """0.3.1 created the results list lazily; racing first checks lost one in ~2% of runs."""
    pytester.makepyfile(
        """
        import sys
        import threading

        import pytest

        from pytest_verifier import get_check_results

        @pytest.mark.parametrize("run", range(300))
        def test_first_checks(verify, request, run):
            sys.setswitchinterval(1e-6)
            barrier = threading.Barrier(2)

            def work(tag):
                barrier.wait()
                verify.equal(1, 1, name=tag)

            threads = [threading.Thread(target=work, args=(f"t{i}",)) for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            sys.setswitchinterval(0.005)
            assert len(get_check_results(request.node)) == 2
        """
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=300)
