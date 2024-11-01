from pathlib import Path
from shutil import rmtree

template = """
summary: |
  {summary}
kill-timeout: {kill_timeout}m
systems:
  - ubuntu-24.04

execute: |
  make integration ARGS="{test_path}"
"""

TESTS_ROOT = Path(__file__).parent.parent
SPREAD_ROOT = TESTS_ROOT / "spread"
ITEST_TASKS_ROOT = SPREAD_ROOT / "integration_tests"


def _render_task(path: Path, summary: None | str = None, kill_timeout: int = 90):
    SPREAD_ROOT.mkdir(exist_ok=True)
    ITEST_TASKS_ROOT.mkdir(exist_ok=True)

    test_name = path.name.split(".")[0]
    spread_task_path = ITEST_TASKS_ROOT / f"{test_name}.yaml"
    test_raw = template.format(
        summary=summary or f"Run integration test: {test_name!r}.",
        test_path=path,
        kill_timeout=kill_timeout,
    )
    print(f"dropping {spread_task_path}")
    spread_task_path.write_text(test_raw)


def _clean_existing_dirs():
    if ITEST_TASKS_ROOT.exists():
        rmtree(ITEST_TASKS_ROOT)


def main():
    _clean_existing_dirs()
    itests_root = TESTS_ROOT.absolute().joinpath("integration")
    print(itests_root)
    for file in itests_root.glob("test_*.py"):
        _render_task(file)


if __name__ == "__main__":
    main()
