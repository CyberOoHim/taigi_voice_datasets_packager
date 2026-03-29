import functools
import os
import subprocess
from pathlib import Path
import logging

log = logging.getLogger(__name__)

# Weighted markers for project-root detection.
# .git / .hg are definitive roots and score highest; everything else is
# indicative.  We intentionally do NOT early-exit on a score threshold so
# that a monorepo sub-project with its own pyproject.toml doesn't win over the
# real workspace root.
_ROOT_MARKERS = {
    ".git": 10,
    ".hg": 10,
    "pyproject.toml": 3,
    "setup.py": 3,
    "setup.cfg": 3,
    "Makefile": 1,
    "requirements.txt": 1,
    ".agent": 1,
}


@functools.lru_cache(maxsize=1)
def get_project_root() -> Path:
    """
    Discover the project root directory using a layered strategy.
    Cached to prevent redundant disk I/O and subprocess calls.

    NOTE: The cache is keyed to the process lifetime.  In long-running
    processes (test-suites, servers) where cwd changes, call
    ``clear_cache()`` before re-invoking.
    """
    # 1. Explicit env override (validate it actually exists)
    env = os.environ.get("PROJECT_ROOT") or os.environ.get("SRT_CLIPPER_ROOT")
    if env:
        env_path = Path(env).resolve()
        if env_path.is_dir():
            return env_path
        log.warning("PROJECT_ROOT is set but is not a valid directory. Falling back...")

    script_dir = Path(__file__).resolve().parent

    # 2. Ask Git (scoped explicitly to the script's directory)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve()
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        pass

    # 3. Weighted marker walk — visit every ancestor; don't exit early.
    #    We walk ALL parents so that a monorepo sub-project with its own
    #    pyproject.toml doesn't shadow the real workspace root which has .git.
    best_candidate = script_dir
    best_score = 0

    for candidate in [script_dir, *script_dir.parents]:
        score = sum(
            w for m, w in _ROOT_MARKERS.items() if (candidate / m).exists()
        )
        if score > best_score:
            best_candidate = candidate
            best_score = score

    if best_score > 0:
        return best_candidate

    # 4. Ultimate fallback
    return Path.cwd()


def clear_cache() -> None:
    """
    Invalidate the cached project root.  Call this in long-running processes
    (test-suites, servers) when the working directory or repository layout may
    have changed since the last call to ``get_project_root()``.
    """
    get_project_root.cache_clear()
