from pathlib import Path
from shutil import rmtree

template = """
summary: |
  {summary}
kill-timeout: {kill_timeout}m
systems:
  - ubuntu-24.04

execute: |
  # set CWD to charm root
  pushd "$SPREAD_PATH"

  # change ownership of the whole repo to self
  chown -R $(id -u):$(id -g) $PWD

  # run integration tests
  make integration ARGS="{test_path} --dump-jdl $SPREAD_PATH/jdl"
"""

TESTS_ROOT = Path(__file__).parent.parent.absolute()
# project-root/tests
SPREAD_ROOT = TESTS_ROOT / "spread"
# project-root/tests/spread
GENERATED_TASK_SUFFIX = "_generated"


def _render_task(path: Path, summary: None | str = None, kill_timeout: int = 90):
    SPREAD_ROOT.mkdir(exist_ok=True)

    test_name = path.name.split(".")[0]
    task_root = SPREAD_ROOT / (test_name + GENERATED_TASK_SUFFIX)
    task_root.mkdir(exist_ok=True)
    spread_task_path = task_root / "task.yaml"
    test_raw = template.format(
        summary=summary or f"Run integration test: {test_name!r}.",
        test_path=path,
        kill_timeout=kill_timeout,
    )
    print(f"dropping {spread_task_path}")
    spread_task_path.write_text(test_raw)


def _cleanup_existing_generated_tasks():
    for subdir in SPREAD_ROOT.glob("*" + GENERATED_TASK_SUFFIX):
        if subdir.is_dir():
            rmtree(subdir)


def main():
    """Cleanup any previously generated tasks and generate them again."""
    _cleanup_existing_generated_tasks()
    itests_root = TESTS_ROOT / "integration"
    print(itests_root)
    for file in itests_root.glob("test_*.py"):
        _render_task(file)


if __name__ == "__main__":
    main()
