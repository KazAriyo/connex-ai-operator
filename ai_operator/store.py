from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

from .clock import today
from .seed import build_seed

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "crm.json"


class CRMStore:
    def __init__(self, path: str | Path | None = None, data: dict | None = None):
        self.path = Path(path or os.environ.get("OPERATOR_DATA") or DEFAULT_PATH)
        self._lock = threading.RLock()
        self._in_memory = data is not None
        if data is not None:
            self.data = copy.deepcopy(data)
        elif self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if self.data.get("generated_on") != today().isoformat():
                self.reset()
        else:
            self.reset()

    def reset(self) -> None:
        with self._lock:
            self.data = build_seed(today())
            self.save()

    def save(self) -> None:
        if self._in_memory:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def leads(self) -> list[dict]:
        return self.data["leads"]

    @property
    def tasks(self) -> list[dict]:
        return self.data["tasks"]

    @property
    def events(self) -> list[dict]:
        return self.data["events"]

    def lead_by_id(self, lead_id: str) -> dict | None:
        lid = lead_id.strip().upper()
        return next((l for l in self.leads if l["id"] == lid), None)

    def next_id(self, prefix: str, items: list[dict]) -> str:
        nums = [int(i["id"].split("-")[1]) for i in items if i["id"].startswith(prefix + "-")]
        return f"{prefix}-{(max(nums) if nums else 0) + 1:03d}"

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)
