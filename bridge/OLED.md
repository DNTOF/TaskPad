# TaskPad OLED detail (ESP)

Push via hub: `POST /oled` or `python taskpad_ctl.py oled --dir ... --model ... --ctx ... --status ...`

| Field | Meaning | Example |
|-------|---------|---------|
| dir | work folder (short) | `TaskPad51/esp8266` |
| model | model name | `Aster` / `GLM-5.3` |
| ctx | **used/total** context | `1.1k/1m` (not percent) |
| status | short phase | `thinking` / `ok` |

Only ASCII on the wire. Requires WiFi tunnel (`wifi_tunnel: true`).
