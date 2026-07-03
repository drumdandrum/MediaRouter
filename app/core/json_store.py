import json
from pathlib import Path
from typing import Any


class JsonStore:
    def __init__(self, path: Path, defaults: dict[str, Any]) -> None:
        self.path = path
        self.defaults = defaults
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            self.write(self.defaults)
            return dict(self.defaults)
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            data = {}
        return {**self.defaults, **data}

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        data = {**self.defaults, **values}
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return data

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        data = self.read()
        data.update(values)
        return self.write(data)
