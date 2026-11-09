import logging
import os
import shutil
import sys
from pathlib import Path

import kagglehub
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

COMPETITION = "jigsaw-toxic-comment-classification-challenge"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "raw_data"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"
PLACEHOLDER_PREFIX = "your_"


def ensure_env_file() -> bool:
    """Create .env from .env.example if missing. Return True if it was created."""
    if ENV_FILE.exists():
        return False
    shutil.copy(ENV_EXAMPLE_FILE, ENV_FILE)
    logger.warning("%s was missing and has been created from .env.example", ENV_FILE)
    return True


def credentials_are_set() -> bool:
    """Check that the Kaggle API token is present and is not a placeholder."""
    token = os.environ.get("KAGGLE_API_TOKEN", "")
    return bool(token) and not token.startswith(PLACEHOLDER_PREFIX)


def download_raw_data(output_dir: Path = RAW_DATA_DIR) -> Path:
    """Download the Kaggle competition files into the given directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading competition '%s' into %s", COMPETITION, output_dir)
    path = Path(kagglehub.competition_download(COMPETITION, output_dir=str(output_dir)))
    logger.info("Competition files available at %s", path)
    return path


def main() -> int:
    created = ensure_env_file()
    load_dotenv(ENV_FILE)
    if created or not credentials_are_set():
        logger.error(
            "Fill in KAGGLE_API_TOKEN in %s, then run this script again.",
            ENV_FILE,
        )
        return 1
    download_raw_data()
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main())
