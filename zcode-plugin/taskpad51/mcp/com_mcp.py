#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TaskPad51 MCP: talk STC89C52 over COM (stdio MCP JSON-RPC + Content-Length)."""
from __future__ import annotations
import json
import os
import sys
import threading
import time
import queue
from typing import Any, Optional

try:
    import serial
    from serial import SerialException
except ImportError:
    serial = None
    SerialException = Exception  # type: ignore

PORT = os.environ.get("TASKPAD_COM", "COM3").strip() or "COM3"
BAUD = int(os.environ.get("TASKPAD_BAUD", "9600") or 9600)

_ser: Any = None
_rx_q: "queue.Queue[str]" = queue.Queue()
_reader_stop = threading.Event()
_lock = threading.Lock()


def log(msg: str) -> None:
    sys.stderr.write(f"[taskpad-com] {msg}\n")
    sys.stderr.flush()


def _reader() -> None:
    global _ser
    buf = b""
    while not _reader_stop.is_set():
        s = _ser
        if s is None or not s.is_open:
            time.sleep(0.05)
            continue
        try:
            chunk = s.read(256)
        except Exception as e:
            log(f"read err: {e}")
            time.sleep(0.2)
            continue
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("ascii", errors="ignore").strip("\r")
            if line:
                _rx_q.put(line)
                log(f"<<< {line}")


def connect() -> dict:
    global _ser
    if serial is None:
        return {"ok": False, "error": "pyserial not installed"}
    with _lock:
        if _ser and getattr(_ser, "is_open", False):
            return {"ok": True, "port": PORT, "already": True}
        try:
            _ser = serial.Serial()
            _ser.port = PORT
            _ser.baudrate = BAUD
            _ser.timeout = 0.05
            _ser.dsrdtr = False
            _ser.rtscts = False
            _ser.open()
            try:
                _ser.setDTR(False)
                _ser.setRTS(False)
            except Exception:
                pass
        except SerialException as e:
            return {"ok": False, "error": str(e), "port": PORT}
        _reader_stop.clear()
        threading.Thread(target=_reader, daemon=True).start()
        send_line("PING")
        return {"ok": True, "port": PORT, "baud": BAUD}


def disconnect() -> dict:
    global _ser
    _reader_stop.set()
    with _lock:
        if _ser:
            try:
                _ser.close()
            except Exception:
                pass
            _ser = None
    return {"ok": True}


def send_line(line: str) -> None:
    s = _ser
    if not s or not s.is_open:
        raise RuntimeError("not connected")
    data = (line.strip() + "\n").encode("ascii", errors="ignore")
    s.write(data)
    log(f">>> {line}")


def wait_event(timeout_sec: float = 300.0, match_prefix: Optional[str] = None) -> dict:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            line = _rx_q.get(timeout=0.2)
        except queue.Empty:
            continue
        if match_prefix and not line.startswith(match_prefix):
            # put back non-matching? keep simple: ignore others except keep HELLO
            continue
        return {"ok": True, "event": line}
    return {"ok": False, "error": "timeout"}


def lcd(row: int, text: str) -> dict:
    t = (text or "")[:16]
    send_line(f"LCD|L{int(row)}|{t}")
    return {"ok": True}


TOOLS = [
    {
        "name": "taskpad_connect",
        "description": "Open COM port to TaskPad51 board",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "taskpad_disconnect",
        "description": "Close COM port",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "taskpad_wait_event",
        "description": "Wait for a serial line event (BTN:START, BTN:ACK, HELLO, TARGET:...)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_sec": {"type": "number", "default": 300},
                "match_prefix": {"type": "string", "description": "optional prefix filter e.g. BTN:START"},
            },
        },
    },
    {
        "name": "taskpad_lcd",
        "description": "Write LCD line 0 or 1 (max 16 chars)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "row": {"type": "integer"},
                "text": {"type": "string"},
            },
            "required": ["row", "text"],
        },
    },
    {
        "name": "taskpad_set_status",
        "description": "Send STAT|idle|run|done|fail frames",
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
        "description": "Mark task done -> board C4 buzzer until ACK",
        "inputSchema": {
            "type": "object",
            "properties": {"tag": {"type": "string", "default": "zcode"}, "summary": {"type": "string"}},
        },
    },
    {
        "name": "taskpad_fail",
        "description": "Mark task failed",
        "inputSchema": {
            "type": "object",
            "properties": {"tag": {"type": "string", "default": "zcode"}, "summary": {"type": "string"}},
        },
    },
    {
        "name": "taskpad_idle",
        "description": "Return board to idle",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name: str, args: dict) -> Any:
    if name == "taskpad_connect":
        return connect()
    if name == "taskpad_disconnect":
        return disconnect()
    if name == "taskpad_wait_event":
        return wait_event(float(args.get("timeout_sec", 300)), args.get("match_prefix"))
    if name == "taskpad_lcd":
        return lcd(int(args.get("row", 0)), str(args.get("text", "")))
    if name == "taskpad_set_status":
        st = args.get("state", "idle")
        tag = args.get("tag", "zcode")
        if st == "idle":
            send_line("STAT|idle")
        else:
            send_line(f"STAT|{st}|{tag}")
        return {"ok": True}
    if name == "taskpad_done":
        tag = args.get("tag", "zcode")
        summary = str(args.get("summary", "done"))[:16]
        send_line(f"STAT|done|{tag}")
        lcd(0, "DONE! press ACK")
        lcd(1, summary)
        return {"ok": True}
    if name == "taskpad_fail":
        tag = args.get("tag", "zcode")
        summary = str(args.get("summary", "fail"))[:16]
        send_line(f"STAT|fail|{tag}")
        lcd(0, "FAIL")
        lcd(1, summary)
        return {"ok": True}
    if name == "taskpad_idle":
        send_line("BUZZ|off")
        send_line("STAT|idle")
        return {"ok": True}
    raise ValueError(f"unknown tool {name}")


def read_message() -> Optional[dict]:
    # LSP-style Content-Length framing
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
    log(f"start port={PORT} baud={BAUD}")
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
                        "serverInfo": {"name": "taskpad-com", "version": "0.2.0"},
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
        # ignore unknowns
        if mid is not None:
            write_message({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}})


if __name__ == "__main__":
    main()