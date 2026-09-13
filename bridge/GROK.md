## Grok Bot（推荐当前用法）

ZCode 本地 stdio MCP 挂了时，用桥接 + Aster webhook：

1. 在 Grok Bot 打开 [Webhook URL](grokbot://app/v1/sidebar?target=webhook-url&automation=taskpad51-webhook)，复制完整 URL。
2. 写入 `bridge/.env`：`GROK_WEBHOOK_URL=...`
3. `python bridge.py`（占 COM3，并开 `http://127.0.0.1:8765`）
4. LCD 目标切到 `grok`，K3 START → 叫醒 Aster；工作时 LCD 会跳进度（并短鸣），聊天里也会有节拍更新。
5. 完成后蜂鸣催 ACK；K2 短按确认。

进度命令（给 Aster / 本机调试）：

```bat
python taskpad_ctl.py progress "thinking" "read files"
python taskpad_ctl.py done "ok"
```
