# TaskPad51 串口协议 (9600 8N1)

行协议，`\n` 结尾，ASCII。MCU ↔ PC Bridge。

## MCU → PC
| 帧 | 含义 |
|---|---|
| `HELLO` | 上电自报 |
| `BTN:START` | 独立按键 K1：开始/派发任务 |
| `BTN:ACK` | 独立按键 K2：确认完成（停蜂鸣） |
| `BTN:CYCLE` | 独立按键 K3：切换目标后端 cursor/zcode/grok |
| `PONG` | 心跳应答 |

## PC → MCU
| 帧 | 含义 |
|---|---|
| `PING` | 心跳 |
| `LCD|L0|<16chars>` | 写 LCD 第 0 行（自动截断/垫空格到 16） |
| `LCD|L1|<16chars>` | 写 LCD 第 1 行 |
| `STAT|idle` | 空闲 |
| `STAT|run|<tag>` | 任务运行中，tag=cursor/zcode/grok |
| `STAT|done|<tag>` | 任务完成 → MCU 进入 C4 式催促蜂鸣直到 ACK |
| `STAT|fail|<tag>` | 失败（短促报警后回 idle，可再 START） |
| `BUZZ|off` | 强制停蜂鸣 |
| `TARGET|<tag>` | 同步当前目标到 LCD 提示 |

## 普中默认引脚（可按你板子改 firmware/config.h）
- LCD1602: 数据 `P0`，RS=`P2.6`，RW=`P2.5`，EN=`P2.7`
- 蜂鸣器: `P1.5`（有源，低电平响——按普中常见接法，可在 config.h 改极性）
- 独立键: 板丝印 K3=`P3.2` START，K4=`P3.3` idle时CYCLE / done时ACK（K1/K2与UART冲突）（按下为低）
- 串口: 板载 CH340，`COM3`，定时器1 9600@11.0592MHz
