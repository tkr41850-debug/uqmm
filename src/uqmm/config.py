"""VMConfig dataclass and JSON (de)serialization.

See docs/design/config.md for the schema and rationale.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal, Self, get_args

OS = Literal["alpine", "debian", "ubuntu"]
State = Literal["created", "failed", "creating"]


@dataclass
class VMConfig:
    name: str
    os: OS
    version: str

    image: str | None = None

    vcpus: int = 2
    memory_mb: int = 2048
    disk_size_gb: int = 20

    ssh_port: int | None = None
    user: str = "root"
    ssh_authorized_keys: list[str] = field(default_factory=list)
    hostname: str | None = None

    state: State = "created"

    def __post_init__(self) -> None:
        if self.vcpus < 1:
            raise ValueError(f"vcpus must be >= 1, got {self.vcpus}")
        if self.vcpus > 64:
            raise ValueError(f"vcpus {self.vcpus} looks like a typo (max 64)")
        if self.memory_mb < 64:
            raise ValueError(f"memory_mb must be >= 64, got {self.memory_mb}")
        if self.memory_mb > 1_048_576:
            raise ValueError(f"memory_mb {self.memory_mb} looks like a typo (max 1048576)")

    def effective_hostname(self) -> str:
        return self.hostname if self.hostname is not None else self.name

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, blob: str) -> Self:
        data: object = json.loads(blob)
        if not isinstance(data, dict):
            raise ValueError("config JSON must be an object")
        known = {f.name for f in fields(cls)}
        filtered: dict[str, Any] = {
            k: v
            for k, v in data.items()  # pyright: ignore[reportUnknownVariableType]
            if k in known
        }
        if filtered.get("os") not in get_args(OS):
            raise ValueError(f"invalid os {filtered.get('os')!r}; expected one of {get_args(OS)}")
        state_val = filtered.get("state", "created")
        if state_val not in get_args(State):
            raise ValueError(f"invalid state {state_val!r}; expected one of {get_args(State)}")
        return cls(**filtered)

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.to_json())
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.from_json(path.read_text())
