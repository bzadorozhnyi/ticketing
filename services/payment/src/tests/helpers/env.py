import os
from pathlib import Path


def load_dotenv_into_environ(*candidates: str) -> None:
    """Writes a .env-style file's key=value pairs straight into os.environ.
    core.settings.Settings has no notion of files or test mode — it only reads
    os.environ — so this is the one place responsible for getting a test env
    file's values in there, strictly (no fallback merge with .env): whichever
    candidate is found must fully specify everything on its own.
    """
    path = next(
        (Path(candidate) for candidate in candidates if Path(candidate).exists()), None
    )
    if path is None:
        raise RuntimeError(f"None of the candidate env files exist: {list(candidates)}")

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()
