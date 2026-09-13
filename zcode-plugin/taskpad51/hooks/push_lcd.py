#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode hook -> TaskPad hub LCD + OLED (automatic, not model-driven)."""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

HUB = (os.environ.get("TASKPAD_HUB") or "http://127.0.0.1:8765").rstrip("/")
DEBUG = Path(r".\last_hook.json")
HOOK_LOG = Path(r".\hooks_ring.jsonl")
STATE = Path(r".\hook_inflight.json")

TOOL_MAP = [
    (re.compile(r"(?i)(edit|write|multiedit|notebookedit|apply.?patch|strreplace|create.?file)"), "editing"),
    (re.compile(r"(?i)(read|readfile|view)"), "reading"),
    (re.compile(r"(?i)(grep|glob|search|semantic|rg)"), "search"),
    (re.compile(r"(?i)(shell|bash|powershell|cmd|terminal|run)"), "shell"),
    (re.compile(r"(?i)(webfetch|websearch|fetch|browser)"), "web"),
    (re.compile(r"(?i)(todo|task)"), "plan"),
]


def post(path: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        HUB + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2) as r:
        r.read()


def lcd_ascii(s: str) -> str:
    return "".join(ch for ch in (s or "") if 32 <= ord(ch) < 127)


def ascii16(s: str) -> str:
    return lcd_ascii(s)[:16]


def basename_only(path: str) -> str:
    p = lcd_ascii(path).replace("\\", "/").rstrip("/")
    return p.split("/")[-1] if p else ""


def tool_input(ev: dict) -> dict:
    ti = ev.get("toolInput") or ev.get("tool_input") or ev.get("input") or {}
    return ti if isinstance(ti, dict) else {}


def call_id(ev: dict) -> str:
    return str(ev.get("toolCallId") or ev.get("tool_call_id") or ev.get("call_id") or "")


def classify_tool(tool: str) -> str:
    t = tool or ""
    for rx, label in TOOL_MAP:
        if rx.search(t):
            return label
    short = re.sub(r"^mcp__[^_]+__", "", t)
    short = short.replace("taskpad_", "tp_")[:16]
    return short or "tool"


def is_write_tool(tool: str, kind: str) -> bool:
    t = (tool or "").lower()
    return kind == "editing" and any(
        x in t for x in ("write", "create", "notebook", "patch")
    ) or t == "write"


def extract_path(ev: dict) -> str:
    ti = tool_input(ev)
    for k in (
        "file_path", "filePath", "path", "target_file", "targetFile", "filepath",
    ):
        v = ti.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for k in ("file_path", "filePath", "path"):
        v = ev.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for k in ("command", "cmd"):
        v = ti.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().splitlines()[0].strip()[:80]
    return ""


def short_dir(path: str, max_len: int = 18) -> str:
    pth = lcd_ascii(path or "").replace("\\", "/").rstrip("/")
    if not pth:
        return "-"
    parts = [x for x in pth.split("/") if x]
    if len(parts) >= 2:
        s = parts[-2] + "/" + parts[-1]
    else:
        s = parts[-1] if parts else "-"
    if len(s) > max_len:
        s = s[-max_len:]
    return s or "-"


def fmt_ctx(used, total) -> str:
    def _n(x):
        try:
            n = float(x)
        except Exception:
            return None
        if n >= 1_000_000:
            v = n / 1_000_000.0
            return f"{v:.0f}m" if abs(v - round(v)) < 0.05 else f"{v:.1f}m"
        if n >= 1000:
            v = n / 1000.0
            return f"{v:.0f}k" if abs(v - round(v)) < 0.05 else f"{v:.1f}k"
        return str(int(n)) if abs(n - int(n)) < 0.05 else f"{n:.1f}"

    u, tot = _n(used), _n(total)
    if u and tot:
        return f"{u}/{tot}"
    if u:
        return f"{u}/?"
    return "n/a"



HOOKS_DIR = Path(__file__).resolve().parent
DEBUG_DIR = HOOKS_DIR  # last_hook / ring live next to push_lcd by default
PREFS_PATH = Path(__file__).resolve().parent / "oled_prefs.json"


def load_prefs() -> dict:
    try:
        d = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def pretty_model(name: str) -> str:
    n = lcd_ascii(name or "").strip()
    if not n:
        return "ZCode"
    # OLED-friendly short labels
    low = n.lower().replace("_", "-")
    if "flash" in low and "5.3" in low:
        return "5.3 Flash"
    if n in ("GLM-5.3", "glm-5.3"):
        return "5.3"
    if len(n) > 18:
        n = n[:18]
    return n


def resolve_path(path: str):
    """Expand 8.3 short paths when possible."""
    if not path or not isinstance(path, str):
        return None
    raw = Path(path)
    if raw.is_file():
        return raw
    try:
        import ctypes
        from ctypes import wintypes

        GetLongPathNameW = ctypes.windll.kernel32.GetLongPathNameW
        GetLongPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        GetLongPathNameW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(1024)
        n = GetLongPathNameW(path, buf, 1024)
        if n:
            longp = Path(buf.value)
            if longp.is_file():
                return longp
    except Exception:
        pass
    fixed = path.replace("ALLOWC~1", "USER").replace("allowc~1", "USER")
    if fixed != path:
        fp = Path(fixed)
        if fp.is_file():
            return fp
    return None


def read_usage_from_transcript(path: str) -> tuple:
    """Return (used_tokens, model_name_or_None) from transcript.jsonl."""
    try:
        p = resolve_path(path)
        if p is None:
            return None, None
        used = None
        model = None
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        for line in reversed(lines):
            try:
                o = json.loads(line)
            except Exception:
                continue
            stack = [o]
            while stack:
                cur = stack.pop()
                if isinstance(cur, dict):
                    if model is None:
                        for k in ("model", "modelName", "model_name", "modelId"):
                            v = cur.get(k)
                            if isinstance(v, str) and v.strip():
                                model = v.strip()
                                break
                    usage = cur.get("usage")
                    if isinstance(usage, dict):
                        for k in ("inputTokens", "input_tokens", "totalTokens", "total_tokens", "prompt_tokens"):
                            if k in usage and usage[k] is not None:
                                try:
                                    used = int(usage[k])
                                    break
                                except Exception:
                                    pass
                    if used is None:
                        for k in ("tokenCount", "inputTokens", "totalTokens"):
                            if k in cur and cur[k] is not None:
                                try:
                                    used = int(cur[k])
                                    break
                                except Exception:
                                    pass
                    for v in cur.values():
                        if isinstance(v, (dict, list)):
                            stack.append(v)
                elif isinstance(cur, list):
                    stack.extend(cur[:20])
            if used is not None:
                break
        return used, model
    except Exception:
        return None, None


ZCODE_ROLLOUT = Path.home() / ".zcode" / "cli" / "rollout"
USAGE_CACHE = Path(__file__).resolve().parent / "oled_usage_cache.json"


def _usage_from_model_io_obj(o: dict) -> tuple:
    """Return (used_context_tokens, model_or_None) from one model-io record."""
    if not isinstance(o, dict):
        return None, None
    model = o.get("model") if isinstance(o.get("model"), str) else None
    resp = o.get("response") if isinstance(o.get("response"), dict) else {}
    usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    used = None
    # Context fill ~= prompt/input (ZCode inputTokens includes cache read)
    for k in ("inputTokens", "input_tokens", "totalTokens", "total_tokens"):
        if usage.get(k) is not None:
            try:
                used = int(usage[k])
                break
            except Exception:
                pass
    if used is None:
        meta = resp.get("providerMetadata") if isinstance(resp.get("providerMetadata"), dict) else {}
        anth = meta.get("anthropic") if isinstance(meta.get("anthropic"), dict) else {}
        au = anth.get("usage") if isinstance(anth.get("usage"), dict) else {}
        try:
            inp = int(au.get("input_tokens") or 0)
            cache = int(au.get("cache_read_input_tokens") or 0)
            if inp or cache:
                used = inp + cache
        except Exception:
            pass
    return used, model


def read_usage_from_model_io(session_id) -> tuple:
    """Read latest usage from ~/.zcode/cli/rollout/model-io-<session>.jsonl."""
    try:
        path = None
        if session_id:
            cand = ZCODE_ROLLOUT / f"model-io-{session_id}.jsonl"
            if cand.is_file():
                path = cand
        if path is None and ZCODE_ROLLOUT.is_dir():
            files = sorted(
                ZCODE_ROLLOUT.glob("model-io-*.jsonl"),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )
            path = files[0] if files else None
        if path is None:
            return None, None
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - 524288))
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        if size > 524288 and lines:
            lines = lines[1:]
        for line in reversed(lines):
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            used, model = _usage_from_model_io_obj(o)
            if used is not None:
                try:
                    USAGE_CACHE.write_text(
                        json.dumps(
                            {"used": used, "model": model, "session": session_id},
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                return used, model
    except Exception:
        pass
    try:
        d = json.loads(USAGE_CACHE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("used") is not None:
            return int(d["used"]), d.get("model")
    except Exception:
        pass
    return None, None


def extract_oled_fields(ev: dict, status: str = "") -> dict:
    prefs = load_prefs()
    cwd = (
        ev.get("cwd")
        or ev.get("workingDirectory")
        or ev.get("working_directory")
        or ev.get("workspaceFolder")
        or ""
    )
    if not isinstance(cwd, str):
        cwd = ""
    directory = short_dir(cwd) if cwd else "-"
    fpath = extract_path(ev)
    fp = (fpath or "").replace("\\", "/")
    if fp and not fp.startswith("git ") and "/" in fp:
        parts = [x for x in fp.split("/") if x]
        if len(parts) >= 2:
            directory = short_dir("/".join(parts[:-1]))

    # model: prefs override > transcript > hook field
    model_raw = (
        prefs.get("model")
        or os.environ.get("TASKPAD_OLED_MODEL")
        or ""
    )
    used = None
    tpath = (
        ev.get("transcript_path")
        or ev.get("transcriptPath")
        or ""
    )
    if isinstance(tpath, str) and tpath:
        tu, tm = read_usage_from_transcript(tpath)
        if tu is not None:
            used = tu
        if not model_raw and tm:
            model_raw = tm
    # Live ZCode: hook temp transcript is often gone; model-io rollout has real usage
    sid = ev.get("sessionId") or ev.get("session_id") or ""
    if used is None:
        mu, mm = read_usage_from_model_io(sid if isinstance(sid, str) else None)
        if mu is not None:
            used = mu
        if not model_raw and mm:
            model_raw = mm
    if not model_raw:
        model_raw = (
            ev.get("model")
            or ev.get("modelName")
            or ev.get("model_name")
            or ev.get("llmModel")
            or "ZCode"
        )
    # If hook says GLM-5.3 but prefs/forced flash, prefs already won.
    model = pretty_model(str(model_raw))

    # usage from event first
    ev_used = (
        ev.get("contextUsed")
        or ev.get("context_used")
        or ev.get("inputTokens")
        or ev.get("tokensUsed")
        or ev.get("tokenCount")
    )
    if used is None and ev_used is not None:
        used = ev_used
    usage = ev.get("usage") or ev.get("tokenUsage") or {}
    if used is None and isinstance(usage, dict):
        used = (
            usage.get("inputTokens")
            or usage.get("input_tokens")
            or usage.get("totalTokens")
            or usage.get("total_tokens")
            or usage.get("prompt_tokens")
            or usage.get("used")
        )

    total = (
        prefs.get("context_total")
        or ev.get("contextTotal")
        or ev.get("context_total")
        or ev.get("contextWindow")
        or ev.get("context_window")
        or ev.get("maxTokens")
        or 1000000
    )
    if isinstance(usage, dict):
        total = (
            usage.get("context_limit")
            or usage.get("total")
            or usage.get("max")
            or total
        )

    st = lcd_ascii(status or "")[:18] or "-"
    return {
        "dir": directory,
        "model": model,
        "ctx": fmt_ctx(used, total),
        "status": st,
    }


def push_oled(ev: dict, status: str = "") -> None:
    try:
        post("/oled", extract_oled_fields(ev, status=status))
    except Exception:
        pass



def line_delta(old: str, new: str) -> tuple[int, int]:
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    if not old_lines and new_lines:
        return len(new_lines), 0
    if old_lines and not new_lines:
        return 0, len(old_lines)
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines
    ).get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            removed += i2 - i1
            added += j2 - j1
    return added, removed


def extract_stats(ev: dict, kind: str) -> tuple[int | None, int | None]:
    ti = tool_input(ev)
    old = ti.get("old_string") or ti.get("oldString") or ti.get("old_str")
    new = ti.get("new_string") or ti.get("newString") or ti.get("new_str")
    if isinstance(old, str) and isinstance(new, str):
        if len(old) > 12000 or len(new) > 12000:
            return None, None
        return line_delta(old, new)
    content = ti.get("content")
    if kind == "editing" and isinstance(content, str) and content and not old:
        n = len(content.splitlines()) or (1 if content else 0)
        if n and n <= 5000:
            return n, 0
    return None, None


def format_stats(added: int | None, removed: int | None) -> str:
    if added is None and removed is None:
        return ""
    return f"+{0 if added is None else added}/-{0 if removed is None else removed}"


def load_state() -> dict:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(st, dict) and isinstance(st.get("inflight"), dict):
            return st
    except Exception:
        pass
    return {"inflight": {}}


def save_state(st: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass


def prune_stale(st: dict, max_age: float = 45.0) -> None:
    now = time.time()
    for k, v in list(st.get("inflight", {}).items()):
        if now - float((v or {}).get("ts") or 0) > max_age:
            st["inflight"].pop(k, None)


def ring_log(ev: dict, name: str, tool: str) -> None:
    try:
        ti = tool_input(ev)
        slim = {
            "ts": time.time(),
            "hook": name,
            "tool": tool,
            "id": call_id(ev),
            "ti_keys": list(ti.keys())[:20],
            "path": extract_path(ev)[:200],
        }
        with HOOK_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")
    except Exception:
        pass


def show_working(note: str = "") -> None:
    post(
        "/progress",
        {
            "line0": "working",
            "line1": lcd_ascii(note)[:40] or "glm...",
            "timer": True,
            "chirp": True,
        },
    )


def show_ready() -> None:
    post("/ready", {"line0": "ready", "line1": "await next"})


def show_inflight(st: dict) -> None:
    items = list(st.get("inflight", {}).values())
    if not items:
        show_working()
        return
    edits = [i for i in items if i.get("kind") == "editing"]
    focus = edits if edits else items
    latest = max(focus, key=lambda x: float(x.get("ts") or 0))
    kind = str(latest.get("kind") or "tool")
    path = str(latest.get("path") or "")
    stats = str(latest.get("stats") or "")
    n = len(focus)
    short = {
        "editing": "edit",
        "reading": "read",
        "search": "find",
        "shell": "sh",
        "web": "web",
        "plan": "plan",
    }.get(kind, kind[:6])
    if kind == "editing" and not path:
        l0 = f"writing x{n}" if n > 1 else "writing"
        l1 = "pending..."
    elif n > 1:
        l0 = f"{short} x{n}"
        l1 = basename_only(path) or "..."
    elif kind == "editing" and stats:
        l0 = ascii16(f"{short} {stats}")
        l1 = basename_only(path) or "..."
    elif kind == "editing":
        l0 = "editing"
        l1 = basename_only(path) or "..."
    else:
        l0 = short if short != "reading" else "reading"
        l1 = basename_only(path) or lcd_ascii(path)[:40] or "..."
    post("/progress", {"line0": l0, "line1": l1, "scroll": True, "chirp": True})


def main() -> int:
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0

    try:
        if len(raw) > 8000:
            ti = tool_input(ev)
            slim = {
                k: ev.get(k)
                for k in (
                    "hookEventName", "hook_event_name", "tool_name", "toolName",
                    "toolCallId", "timestamp", "cwd", "model",
                )
            }
            slim["toolInput"] = {
                k: (
                    f"<content {len(str(ti.get(k) or ''))} chars>"
                    if k in ("content", "old_string", "new_string", "oldString", "newString")
                    else ti.get(k)
                )
                for k in list(ti.keys())[:20]
            }
            DEBUG.write_text(json.dumps(slim, ensure_ascii=False)[:8000], encoding="utf-8")
        else:
            DEBUG.write_text(raw if raw.strip() else "{}", encoding="utf-8")
    except Exception:
        pass

    name = (
        ev.get("hookEventName")
        or ev.get("hook_event_name")
        or ev.get("event")
        or ""
    )
    tool = str(ev.get("tool_name") or ev.get("toolName") or "")
    kind = classify_tool(tool)
    path = extract_path(ev)
    added = removed = None
    if name in ("PostToolUse", "PostToolUseFailure") or (
        name == "PreToolUse" and not is_write_tool(tool, kind)
    ):
        added, removed = extract_stats(ev, kind)
    elif name == "PreToolUse":
        ti = tool_input(ev)
        if ti.get("old_string") or ti.get("oldString"):
            added, removed = extract_stats(ev, kind)
    stats = format_stats(added, removed)
    cid = call_id(ev) or f"{kind}:{basename_only(path) or tool}:{time.time_ns()}"
    ring_log(ev, name, tool)

    try:
        if name == "SessionStart":
            save_state({"inflight": {}})
            post("/progress", {"line0": "ZCode ready", "line1": "hub linked", "chirp": True})
            push_oled(ev, "ready")
        elif name == "UserPromptSubmit":
            save_state({"inflight": {}})
            post("/progress", {"line0": "thinking", "timer": True, "chirp": True})
            push_oled(ev, "thinking")
        elif name in ("PermissionRequest", "Notification"):
            st = load_state()
            st.setdefault("inflight", {})[cid or "perm"] = {
                "kind": "editing",
                "path": path or "",
                "stats": "",
                "ts": time.time(),
                "label": "writing",
            }
            save_state(st)
            show_inflight(st)
            push_oled(ev, "writing")
        elif name == "PreToolUse":
            st = load_state()
            prune_stale(st)
            label_kind = kind
            st.setdefault("inflight", {})[cid] = {
                "kind": label_kind,
                "path": path,
                "stats": stats if kind == "editing" and stats else "",
                "ts": time.time(),
            }
            save_state(st)
            show_inflight(st)
            push_oled(ev, kind[:18] or "tool")
        elif name == "PostToolUse":
            st = load_state()
            prune_stale(st)
            st.get("inflight", {}).pop(cid, None)
            if not call_id(ev):
                for k, v in list(st.get("inflight", {}).items()):
                    if v.get("kind") == kind and (not path or v.get("path") == path):
                        st["inflight"].pop(k, None)
                        break
            for k, v in list(st.get("inflight", {}).items()):
                if v.get("label") == "writing" and not v.get("path"):
                    st["inflight"].pop(k, None)
            save_state(st)
            if not st.get("inflight"):
                if kind == "editing":
                    l0 = ascii16(f"edit {stats}") if stats else "wrote"
                    post(
                        "/progress",
                        {
                            "line0": l0,
                            "line1": basename_only(path) or "...",
                            "chirp": True,
                        },
                    )
                    time.sleep(0.2)
                show_working(basename_only(path))
            else:
                show_inflight(st)
            push_oled(ev, kind[:18] or "tool")
        elif name == "PostToolUseFailure":
            st = load_state()
            st.get("inflight", {}).pop(cid, None)
            save_state(st)
            post("/progress", {"line0": "tool FAIL", "line1": basename_only(path) or "err", "chirp": True})
            time.sleep(0.15)
            if st.get("inflight"):
                show_inflight(st)
            else:
                show_working()
            push_oled(ev, "fail")
        elif name == "Stop":
            save_state({"inflight": {}})
            show_ready()
            push_oled(ev, "ready")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
