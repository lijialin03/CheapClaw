from functools import lru_cache
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


@lru_cache(maxsize=None)
def load_asset_text(asset_path: str) -> str:
    return (ASSETS_DIR / asset_path).read_text(encoding="utf-8")
