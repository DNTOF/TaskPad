"""Grok Bot / Aster adapter — webhook routine or inbox file."""
from __future__ import annotations
import os
import json
import time
from pathlib import Path
import requests
from .base import Adapter, TaskResult, ProgressCb


class GrokAdapter(Adapter):
    name = "grok"

    def __init__(self, webhook_url: str = "", mode: str = "webhook"):
        self.webhook_url = (os.environ.get("GROK_WEBHOOK_URL") or webhook_url or "").strip()
        self.webhook_key = (os.environ.get("GROK_WEBHOOK_KEY") or "").strip()
        self.mode = mode
        self.inbox = Path(__file__).resolve().parents[1] / "inbox"
        self.inbox.mkdir(exist_ok=True)

    def start(self, prompt: str, on_progress: ProgressCb | None = None) -> TaskResult:
        if on_progress:
            on_progress("wake Aster")
        body = {
            "source": "TaskPad51",
            "prompt": prompt,
            "ts": time.time(),
            "instruction": (
                "User pressed START on the STC89C52 TaskPad. "
                "The prompt field is the real task (usually from their Windows clipboard). "
                "Do THAT work. Push LCD progress with taskpad_ctl while working; "
                "call done when finished; reply in the main chat so they can see you. "
                "Use ASCII ≤16 chars per LCD line."
            ),
        }
        if self.webhook_url:
            headers = {"Content-Type": "application/json"}
            if self.webhook_key:
                key = self.webhook_key
                if key.lower().startswith("bearer "):
                    headers["Authorization"] = key
                else:
                    headers["Authorization"] = f"Bearer {key}"
            try:
                r = requests.post(self.webhook_url, json=body, headers=headers, timeout=30)
                if r.status_code >= 400:
                    return TaskResult(False, f"HTTP {r.status_code}", r.text[:300])
                return TaskResult(True, "webhook ok", "posted")
            except requests.RequestException as e:
                return TaskResult(False, "net err", str(e))
        path = self.inbox / f"grok_{int(time.time())}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return TaskResult(True, "grok file", str(path))
