from pathlib import Path
from shutil import rmtree

template = """
summary: {summary}
kill-timeout: {kill_timeout}m
systems:
  - ubuntu-24.04

execute: |
  pushd "$SPREAD_PATH"
  make integration ARGS="{test_path}"
"""
restore_step = """
restore: |
  if [[ -z "${CI:-}" ]]; then
    juju destroy-model --no-prompt --destroy-storage testing
    juju add-model testing
  fi
"""
TESTS_ROOT = Path(__file__).parent.parent


def _render_task(path: Path, summary: str = "some test", kill_timeout: int = 90):
    spread_test_root = Path() / path.name.split(".")[0]
    spread_test_root.mkdir()
    spread_test_path = spread_test_root / "task.yaml"
    test_raw = template.format(
        summary=summary, test_path=path, kill_timeout=kill_timeout
    )
    print(f"dropping {spread_test_path}")
    spread_test_path.write_text(test_raw + restore_step)


def _clean_existing_dirs():
    for path in (TESTS_ROOT / "spread").glob("*"):
        if path.is_dir():
            rmtree(path)


def main():
    _clean_existing_dirs()
    itests_root = TESTS_ROOT.absolute().joinpath("integration")
    print(itests_root)
    for file in itests_root.glob("test_*.py"):
        _render_task(file)


if __name__ == "__main__":
    main()
