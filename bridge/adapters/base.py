"""Adapter interface for TaskPad51 bridge."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class TaskResult:
    ok: bool
    summary: str = ""
    detail: str = ""


ProgressCb = Callable[[str], None]  # short status for LCD (≤16 chars ideal)


class Adapter:
    name: str = "base"

    def start(self, prompt: str, on_progress: Optional[ProgressCb] = None) -> TaskResult:
        raise NotImplementedError
