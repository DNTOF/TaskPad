# -*- coding: utf-8 -*-
"""Rewrite zcode-plugin hooks.json to absolute paths for this checkout."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUSH = ROOT / "hooks" / "push_lcd.py"
PLUGIN_HOOKS = ROOT / "zcode-plugin" / "taskpad51" / "hooks" / "hooks.json"
EXAMPLE = ROOT / "zcode-plugin" / "taskpad51" / "hooks" / "hooks.json.example"

EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionEnd",
]


def main() -> int:
    py = sys.executable
    cmd = f'"{py}" "{PUSH}"'
    data = {"hooks": {}}
    for name in EVENTS:
        data["hooks"][name] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": cmd,
                        "timeoutMs": 5000,
                        "statusMessage": f"TaskPad {name}",
                    }
                ]
            }
        ]
    PLUGIN_HOOKS.parent.mkdir(parents=True, exist_ok=True)
    PLUGIN_HOOKS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    EXAMPLE.write_text(
        json.dumps(data, indent=2).replace(str(PUSH), "{{PUSH_LCD}}").replace(py, "python"),
        encoding="utf-8",
    )
    # keep plugin copy of push_lcd in sync
    dst = ROOT / "zcode-plugin" / "taskpad51" / "hooks" / "push_lcd.py"
    shutil.copy2(PUSH, dst)
    print("wrote", PLUGIN_HOOKS)
    print("command:", cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
