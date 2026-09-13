"""ZCode adapter (legacy webhook/file). Prefer ZCode plugin MCP COM direct. — webhook or local notify (no official task API)."""
from __future__ import annotations
import os
import json
import time
from pathlib import Path
import requests
from .base import Adapter, TaskResult, ProgressCb


class ZCodeAdapter(Adapter):
    name = "zcode"

    def __init__(self, webhook_url: str = "", mode: str = "webhook"):
        self.webhook_url = (os.environ.get("ZCODE_WEBHOOK_URL") or webhook_url or "").strip()
        self.mode = mode
        self.inbox = Path(__file__).resolve().parents[1] / "inbox"
        self.inbox.mkdir(exist_ok=True)

    def start(self, prompt: str, on_progress: ProgressCb | None = None) -> TaskResult:
        if on_progress:
            on_progress("zcode send")
        body = {"source": "TaskPad51", "prompt": prompt, "ts": time.time()}
        if self.webhook_url:
            try:
                r = requests.post(self.webhook_url, json=body, timeout=30)
                if r.status_code >= 400:
                    return TaskResult(False, f"HTTP {r.status_code}", r.text[:300])
                return TaskResult(True, "zcode webhook", "posted")
            except requests.RequestException as e:
                return TaskResult(False, "net err", str(e))
        # fallback: drop file for manual / future plugin pickup
        path = self.inbox / f"zcode_{int(time.time())}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return TaskResult(True, "zcode file", str(path))
