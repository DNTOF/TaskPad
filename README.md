# TaskPad

Physical task desk for **STC89C52RC** (普中实验板): keys + **LCD1602** progress, optional **ESP8266 NodeMCU** Wi-Fi UART tunnel + **SSD1306 OLED** detail panel, and a PC hub that can talk to ZCode hooks / Grok Bot / Cursor adapters.

> **Status / 现状**
>
> - **本仓库核心开源范围：** 单片机固件 `firmware/`、PC 桥接 `bridge/`、ESP8266 Wi-Fi+OLED `esp8266/`。
> - **ZCode hooks**（`hooks/`、`zcode-plugin/`）是目前最完整的 LCD/OLED 实时进度路径。
> - **Cursor / Grok Bot 功能尚不完善：** 适配器代码在仓库里，但缺打磨；START 开工较脆，端到端覆盖有限。日常请优先用 ZCode hooks + hub；Cursor / Grok 路径仅作实验。
> - 建议 `launch_on_start: false`，除非你明确要用 K3 拉起 agent 任务。
>
> This release is sanitized for open source. Copy example configs; never commit secrets.

## Architecture

```
STC89C52  --UART 9600-->  ESP8266 (optional Wi-Fi tunnel)  --TCP :8870-->  bridge.py hub (:8765)
                \-- USB CH340 COM (optional, conflicts with K1/K2) --/
LCD1602  short progress (STAT|...)
OLED     dir / model / ctx (used/total) / status
ZCode    hooks -> push_lcd.py -> POST /oled + /progress
Grok     webhook routine + taskpad_ctl.py
```

## Repo layout

| Path | What |
|------|------|
| `firmware/` | STC89C52 Keil C51 sources + `taskpad51.hex` |
| `esp8266/taskpad_wifi_bridge/` | NodeMCU sketch (UART-TCP + OLED intercept) |
| `bridge/` | Python hub: serial/Wi-Fi mux, HTTP API, MCP |
| `zcode-plugin/` | Local marketplace plugin (hooks + MCP client) |
| `hooks/` | Standalone `push_lcd.py` (same as plugin hooks) |
| `buzzer_music/` | Optional buzzer demo |
| `PROTOCOL.md` | UART line protocol |

## Hardware (defaults)

| Role | Wiring notes |
|------|----------------|
| LCD1602 | P0 data; RS/RW/EN = P2.6 / P2.5 / P2.7 |
| Buzzer | P1.5 (active low) |
| K3 START | P3.2 |
| K4 CYCLE | P3.3 |
| K2 ACK | P3.0 (RXD) — only while DONE; USB UART uses same pins |
| ESP TX to STC P30, ESP RX from P31 | Remove P30-URX / P31-UTX shorting caps; level-shift STC to ESP; shared GND |
| OLED SSD1306 I2C | NodeMCU G / 3V3 / D1 / D2 = GND / VDD / SCK / SDA |
| Flash ESP | Unplug UART Duponts; use NodeMCU USB |

## Quick start

### 1. Firmware (STC)

1. Keil C51, chip `STC89C52`, add `firmware/*.c`, crystal **11.0592 MHz**
2. Or flash prebuilt `firmware/taskpad51.hex` with STC-ISP

### 2. Bridge (PC)

```bat
cd bridge
python -m pip install -r requirements.txt
copy .env.example .env
copy config\config.example.json config\config.json
notepad .env
python bridge.py
```

Hub: `http://127.0.0.1:8765`  
Wi-Fi tunnel listen: `:8870` (`transport: auto`)

Set `launch_on_start` to `false` if you only want LCD/OLED progress from ZCode hooks (no K3 job launch).

### 3. ESP8266 Wi-Fi bridge (+ OLED)

```bat
cd esp8266\taskpad_wifi_bridge
copy secrets.h.example secrets.h
notepad secrets.h
```

Flash with Arduino IDE / arduino-cli (ESP8266 board). Keep SSID/password and `BRIDGE_HOST` only in `secrets.h`.

### 4. ZCode hooks (recommended for live LCD/OLED)

1. Add marketplace folder `zcode-plugin/`
2. Install plugin **taskpad51**
3. Run `python hooks/install_hooks_paths.py` **or** edit hooks so each command points at your Python + this repo's `hooks/push_lcd.py` (ASCII path recommended)
4. Keep bridge running; optional MCP `http://127.0.0.1:8765/mcp`

OLED ctx is read from ZCode `~/.zcode/cli/rollout/model-io-<session>.jsonl` when hook temp transcripts disappear. Optional `hooks/oled_prefs.example.json` to `oled_prefs.json` for model label / context total.

### 5. Grok Bot (experimental)

1. Create a webhook routine; put URL in `bridge/.env` as `GROK_WEBHOOK_URL=`
2. `python taskpad_ctl.py progress "thinking" "plan"` / `done "ok"` / `idle`

**Note:** Grok Bot and Cursor adapters are incomplete — see Status above.

## Security

- Do **not** commit `.env`, `secrets.h`, redeem codes, or `bridge/inbox/*`
- Rotate Wi-Fi / API keys if they ever lived in a shared folder
- Hook debug logs may contain workspace paths — keep local

## License

[GPLv3](LICENSE) — Copyright (C) 2026 DNTOF / Cache Allow
