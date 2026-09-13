# TaskPad51 COM Hub

Only `bridge.py` opens **COM3**. Clients use HTTP.

```
Board --COM3--> bridge.py (:8765)
                  |-- REST: /health /events /progress /done ...
                  |-- MCP Streamable HTTP: POST /mcp   (ZCode type:http)
                  |-- Grok: taskpad_ctl.py
```

## Start
```bat
cd /d %USERPROFILE%\Desktop\开发\TaskPad51\bridge
python bridge.py
```

## ZCode (preferred)
```json
{
  "type": "http",
  "url": "http://127.0.0.1:8765/mcp",
  "timeoutMs": 120000
}
```
No Python spawn. Hub must be running.

## K4 target
- `grok` — clipboard webhook
- `zcode` — MCP wait_event + done
