"""Minimal MCP Streamable-HTTP JSON-RPC surface for TaskPad hub."""
from __future__ import annotations
import json
import time
import uuid
from typing import Any, Optional, Tuple

PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "taskpad_connect",
        "description": "Check TaskPad COM hub health + current session (hub owns COM3)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "taskpad_session",
        "description": "Get current hub session snapshot (idle/run/done, joinable, last START age)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "taskpad_join",
        "description": "Join mid-flight: attach to an already-running START/run without pressing K3 again. Then do work and call taskpad_done.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "taskpad_disconnect",
        "description": "No-op; hub keeps COM3",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "taskpad_wait_event",
        "description": "Long-poll board events via hub (BTN:START, BTN:ACK, HELLO, ...)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_sec": {"type": "number", "default": 300},
                "match_prefix": {"type": "string"},
                "replay_last": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true and a run is already active, immediately return BTN:START (mid-join)",
                },
            },
        },
    },
    {
        "name": "taskpad_lcd",
        "description": "Write LCD line via hub (max 16 ASCII chars)",
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
            "properties": {
                "tag": {"type": "string", "default": "zcode"},
                "summary": {"type": "string"},
            },
        },
    },
    {
        "name": "taskpad_fail",
        "description": "Mark fail via hub",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "default": "zcode"},
                "summary": {"type": "string"},
            },
        },
    },
    {
        "name": "taskpad_idle",
        "description": "Return board to idle via hub",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_sessions: dict[str, float] = {}


def _tool_result(obj: Any, is_error: bool = False) -> dict:
    if isinstance(obj, dict) and "ok" in obj and not obj.get("ok", True):
        is_error = True
    return {
        "content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}],
        "isError": is_error,
    }


def call_hub_tool(bridge, name: str, args: dict) -> dict:
    args = args or {}
    if name == "taskpad_connect":
        snap = bridge.session_snapshot()
        return {
            "ok": True,
            "hub": True,
            "busy": bridge._busy,
            "target": bridge.target,
            "port": bridge.cfg.get("port"),
            "session": snap,
        }
    if name == "taskpad_session":
        return {"ok": True, **bridge.session_snapshot()}
    if name == "taskpad_join":
        return bridge.join_session()
    if name == "taskpad_disconnect":
        return {"ok": True, "note": "hub keeps COM3"}
    if name == "taskpad_wait_event":
        timeout = float(args.get("timeout_sec", 300))
        match = args.get("match_prefix")
        replay = args.get("replay_last", True)
        if isinstance(replay, str):
            replay = replay.lower() not in ("0", "false", "no")
        return bridge.wait_event(timeout, match, replay_last=bool(replay))
    if name == "taskpad_lcd":
        bridge.lcd(int(args.get("row", 0)), str(args.get("text", "")))
        return {"ok": True}
    if name == "taskpad_set_status":
        st = str(args.get("state", "idle"))
        tag = str(args.get("tag", "zcode"))
        if st == "idle":
            bridge.send("BUZZ|off")
            bridge.send("STAT|idle")
        else:
            bridge.send(f"STAT|{st}|{tag}")
        return {"ok": True}
    if name == "taskpad_done":
        tag = str(args.get("tag", "zcode"))
        summary = str(args.get("summary", "done"))[:16]
        bridge.send(f"STAT|done|{tag}")
        bridge.lcd(0, "DONE! press ACK")
        bridge.lcd(1, summary)
        bridge.signal_done(True, summary)
        return {"ok": True}
    if name == "taskpad_fail":
        tag = str(args.get("tag", "zcode"))
        summary = str(args.get("summary", "fail"))[:16]
        bridge.send(f"STAT|fail|{tag}")
        bridge.lcd(0, "FAIL")
        bridge.lcd(1, summary)
        bridge.signal_done(False, summary)
        return {"ok": True}
    if name == "taskpad_idle":
        bridge.send("BUZZ|off")
        bridge.send("STAT|idle")
        return {"ok": True}
    raise ValueError(f"unknown tool {name}")


def handle_rpc(bridge, msg: dict) -> Tuple[Optional[dict], Optional[str]]:
    """Return (jsonrpc_response_or_None, new_session_id_or_None)."""
    mid = msg.get("id", None)
    method = msg.get("method")
    params = msg.get("params") or {}

    if mid is None:
        return None, None

    if method == "initialize":
        sid = uuid.uuid4().hex
        _sessions[sid] = time.time()
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "taskpad-hub", "version": "0.4.0"},
            },
        }, sid

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}, None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}, None

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = call_hub_tool(bridge, name, args)
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": _tool_result(result),
            }, None
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": _tool_result({"ok": False, "error": str(e)}, True),
            }, None

    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return {"jsonrpc": "2.0", "id": mid, "result": {key: []}}, None

    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }, None


def dispatch(bridge, body: Any) -> Tuple[Any, Optional[str], int]:
    """Returns (response_body_or_None, session_id, http_status)."""
    session_id = None

    if isinstance(body, list):
        out = []
        for item in body:
            if not isinstance(item, dict):
                continue
            if item.get("method") and item.get("id") is None:
                continue
            resp, sid = handle_rpc(bridge, item)
            if sid:
                session_id = sid
            if resp is not None:
                out.append(resp)
        if not out:
            return None, session_id, 202
        return (out if len(out) > 1 else out[0]), session_id, 200

    if not isinstance(body, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"},
        }, None, 400

    if body.get("method") and body.get("id") is None:
        return None, None, 202

    resp, sid = handle_rpc(bridge, body)
    if sid:
        session_id = sid
    if resp is None:
        return None, session_id, 202
    return resp, session_id, 200
