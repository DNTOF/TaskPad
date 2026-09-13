#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TaskPad51 MCP via COM hub (no direct serial). Talks to bridge HTTP."""
from __future__ import annotations
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

HUB = (os.environ.get("TASKPAD_HUB") or "http://127.0.0.1:8765").rstrip("/")


def log(msg: str) -> None:
    sys.stderr.write(f"[taskpad-hub] {msg}\n")
    sys.stderr.flush()


def http_json(method: str, path: str, payload: dict | None = None, timeout: float = 60.0) -> dict:
    url = HUB + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def get(path: str, timeout: float = 60.0) -> dict:
    return http_json("GET", path, None, timeout=timeout)


def post(path: str, payload: dict, timeout: float = 30.0) -> dict:
    return http_json("POST", path, payload, timeout=timeout)


TOOLS = [
    {
        "name": "taskpad_connect",
        "description": "Check TaskPad COM hub health (hub owns COM3; does not open serial here)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "taskpad_disconnect",
        "description": "No-op for hub mode (COM stays with hub)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "taskpad_wait_event",
        "description": "Long-poll hub for board events (BTN:START, BTN:ACK, HELLO, ...)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_sec": {"type": "number", "default": 300},
                "match_prefix": {"type": "string"},
            },
        },
    },
    {
        "name": "taskpad_lcd",
        "description": "Write LCD via hub (max 16 ASCII chars)",
        "inputSchema": {
            "type": "object",
            "properties": {"row": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["row", "text"],
        },
    },
    {
        "name": "taskpad_set_status",
        "description": "Send STAT via hub",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["idle", "run", "done", "fail"]},
                "tag": {"type": "string", "default": "zcode"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "taskpad_done",
        "description": "Mark done via hub (board beeps until ACK)",
        "inputSchema": {
            "type": "object",
            "properties": {"tag": {"type": "string", "default": "zcode"}, "summary": {"type": "string"}},
        },
    },
    {
        "name": "taskpad_fail",
        "description": "Mark fail via hub",
        "inputSchema": {
            "type": "object",
            "properties": {"tag": {"type": "string", "default": "zcode"}, "summary": {"type": "string"}},
        },
    },
    {
        "name": "taskpad_idle",
        "description": "Return board to idle via hub",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name: str, args: dict) -> Any:
    if name == "taskpad_connect":
        try:
            h = get("/health", timeout=5)
            h["hub"] = HUB
            return h if h.get("ok") else {"ok": False, "error": h, "hub": HUB}
        except Exception as e:
            return {"ok": False, "error": f"hub unreachable: {e}", "hub": HUB}
    if name == "taskpad_disconnect":
        return {"ok": True, "note": "hub keeps COM3"}
    if name == "taskpad_wait_event":
        timeout = float(args.get("timeout_sec", 300))
        match = args.get("match_prefix")
        q = f"/events?timeout={urllib.parse.quote(str(timeout))}"
        if match:
            q += f"&match={urllib.parse.quote(str(match))}"
        try:
            # HTTP timeout slightly above poll timeout
            return get(q, timeout=timeout + 5)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if name == "taskpad_lcd":
        return post("/lcd", {"row": int(args.get("row", 0)), "text": str(args.get("text", ""))})
    if name == "taskpad_set_status":
        return post("/status", {"state": args.get("state", "idle"), "tag": args.get("tag", "zcode")})
    if name == "taskpad_done":
        return post(
            "/status",
            {
                "state": "done",
                "tag": args.get("tag", "zcode"),
                "summary": str(args.get("summary", "done"))[:16],
            },
        )
    if name == "taskpad_fail":
        return post(
            "/status",
            {
                "state": "fail",
                "tag": args.get("tag", "zcode"),
                "summary": str(args.get("summary", "fail"))[:16],
            },
        )
    if name == "taskpad_idle":
        return post("/status", {"state": "idle"})
    raise ValueError(f"unknown tool {name}")


def read_message() -> Optional[dict]:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if b":" in line:
            k, v = line.decode("utf-8", errors="ignore").split(":", 1)
            headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    if n <= 0:
        return None
    body = sys.stdin.buffer.read(n)
    return json.loads(body.decode("utf-8"))


def write_message(msg: dict) -> None:
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    sys.stdout.buffer.flush()


def main() -> None:
    log(f"hub-mcp start hub={HUB}")
    while True:
        msg = read_message()
        if msg is None:
            break
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "taskpad-hub", "version": "0.3.0"},
                    },
                }
            )
            continue
        if method == "notifications/initialized":
            continue
        if method == "tools/list":
            write_message({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
            continue
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                result = call_tool(name, args)
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                            "isError": not bool(result.get("ok", True)) if isinstance(result, dict) else False,
                        },
                    }
                )
            except Exception as e:
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps({"ok": False, "error": str(e)})}],
                            "isError": True,
                        },
                    }
                )
            continue
        if method == "ping":
            write_message({"jsonrpc": "2.0", "id": mid, "result": {}})
            continue
        if mid is not None:
            write_message({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}})


if __name__ == "__main__":
    main()
