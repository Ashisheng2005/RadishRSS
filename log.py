# ====== Logger Setup ======
import logging
import os

logging.basicConfig(level=logging.INFO)

def setup_logger(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("fetch_papers")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    fh = logging.FileHandler(
        os.path.join(log_dir, "fetch_papers.log"), encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
    ))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger