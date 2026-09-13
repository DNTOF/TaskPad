"""Cursor Cloud Agents API adapter (official agent API, not chat-completions)."""
from __future__ import annotations
import os
import time
import requests
from .base import Adapter, TaskResult, ProgressCb


class CursorAdapter(Adapter):
    name = "cursor"

    def __init__(self, api_base: str, poll_seconds: float = 5, timeout_seconds: float = 600):
        self.api_base = api_base.rstrip("/")
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.api_key = os.environ.get("CURSOR_API_KEY", "").strip()

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def start(self, prompt: str, on_progress: ProgressCb | None = None) -> TaskResult:
        if not self.api_key:
            return TaskResult(False, "no API key", "Set CURSOR_API_KEY in bridge/.env (new key; revoke leaked ones)")
        if on_progress:
            on_progress("cursor launch")
        # Cloud Agents launch — minimal prompt-only agent when repo optional
        # Docs: POST https://api.cursor.com/v1/agents
        payload = {
            "prompt": {"text": prompt},
            "model": {"id": "composer-2"},
        }
        try:
            r = requests.post(f"{self.api_base}/v1/agents", headers=self._headers(), json=payload, timeout=60)
        except requests.RequestException as e:
            return TaskResult(False, "net err", str(e))
        if r.status_code >= 400:
            return TaskResult(False, f"HTTP {r.status_code}", r.text[:500])
        data = r.json() if r.content else {}
        agent_id = data.get("id") or data.get("agentId") or data.get("agent_id")
        if not agent_id:
            # Some responses nest under agent
            agent_id = (data.get("agent") or {}).get("id")
        if not agent_id:
            return TaskResult(False, "no agent id", str(data)[:500])

        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            if on_progress:
                on_progress("cursor poll")
            time.sleep(self.poll_seconds)
            try:
                gr = requests.get(f"{self.api_base}/v1/agents/{agent_id}", headers=self._headers(), timeout=30)
            except requests.RequestException as e:
                return TaskResult(False, "poll err", str(e))
            if gr.status_code >= 400:
                continue
            info = gr.json() if gr.content else {}
            status = (info.get("status") or info.get("state") or "").lower()
            if status in ("finished", "completed", "done", "success"):
                return TaskResult(True, "cursor done", str(info)[:500])
            if status in ("failed", "error", "cancelled", "canceled"):
                return TaskResult(False, "cursor fail", str(info)[:500])
        return TaskResult(False, "timeout", f"agent {agent_id}")
