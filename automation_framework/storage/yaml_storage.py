from pathlib import Path
from typing import Any

import yaml


def save_yaml(data: dict[str, Any], file_path: str | Path) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False)


def load_yaml(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path)

    try:
        with path.open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}
