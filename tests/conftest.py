import pytest

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _wide_inner_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytester sessions inherit COLUMNS (or the real terminal's width with -s), and pytest cuts
    # -r short summary lines to it: pin a wide terminal so that line matches do not depend on it.
    monkeypatch.setenv("COLUMNS", "200")
