#!/usr/bin/env python3
"""Push TaskPad51 LCD progress via local bridge HTTP (for Grok Bot)."""
from __future__ import annotations
import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT = "http://127.0.0.1:8765"


def post(base: str, path: str, payload: dict) -> dict:
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def get(base: str, path: str = "/health") -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def main() -> int:
    p = argparse.ArgumentParser(description="TaskPad51 control client")
    p.add_argument("--base", default=DEFAULT)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    # Prefer these for mid-flight attach (mirror MCP taskpad_session / taskpad_join)
    sub.add_parser("session")
    sub.add_parser("join")
    sp = sub.add_parser("progress")
    sp.add_argument("line0")
    sp.add_argument("line1", nargs="?", default="")
    so = sub.add_parser("oled")
    so.add_argument("--dir", default="")
    so.add_argument("--model", default="")
    so.add_argument("--ctx", default="")
    so.add_argument("--status", default="")
    sp = sub.add_parser("done")
    sp.add_argument("summary", nargs="?", default="done")
    sp = sub.add_parser("fail")
    sp.add_argument("summary", nargs="?", default="fail")
    sub.add_parser("idle")
    args = p.parse_args()
    try:
        if args.cmd == "health":
            print(json.dumps(get(args.base), ensure_ascii=False))
            return 0
        if args.cmd == "session":
            print(json.dumps(get(args.base, "/session"), ensure_ascii=False))
            return 0
        if args.cmd == "join":
            print(json.dumps(get(args.base, "/join"), ensure_ascii=False))
            return 0
        if args.cmd == "progress":
            print(
                json.dumps(
                    post(args.base, "/progress", {"line0": args.line0, "line1": args.line1}),
                    ensure_ascii=False,
                )
            )
            return 0
        if args.cmd == "oled":
            print(
                json.dumps(
                    post(
                        args.base,
                        "/oled",
                        {
                            "dir": args.dir,
                            "model": args.model,
                            "ctx": args.ctx,
                            "status": args.status,
                        },
                    ),
                    ensure_ascii=False,
                )
            )
            return 0
        if args.cmd == "done":
            print(
                json.dumps(
                    post(args.base, "/done", {"summary": args.summary}),
                    ensure_ascii=False,
                )
            )
            return 0
        if args.cmd == "fail":
            print(
                json.dumps(
                    post(args.base, "/fail", {"summary": args.summary}),
                    ensure_ascii=False,
                )
            )
            return 0
        if args.cmd == "idle":
            print(json.dumps(post(args.base, "/idle", {}), ensure_ascii=False))
            return 0
    except urllib.error.URLError as e:
        print(f"bridge unreachable: {e}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
