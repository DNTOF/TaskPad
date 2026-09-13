#!/usr/bin/env python3
"""TaskPad51 COM hub: sole owner of COM3; Grok/ZCode clients use HTTP."""
from __future__ import annotations
import json
import os
import sys
import threading
import queue
import time
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    import serial
except ImportError:
    print("pip install -r requirements.txt")
    raise

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from adapters.cursor_adapter import CursorAdapter
from adapters.zcode_adapter import ZCodeAdapter
from adapters.grok_adapter import GrokAdapter
import mcp_http


def read_windows_clipboard() -> str:
    """Best-effort clipboard text on the user's Windows PC."""
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        try:
            text = r.clipboard_get()
        finally:
            r.destroy()
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:
        pass
    try:
        import subprocess
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        if p.returncode == 0 and p.stdout and p.stdout.strip():
            return p.stdout.strip()
    except Exception:
        pass
    return ""




def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Bridge:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.target = cfg.get("default_target", "grok")
        self.prompt = cfg.get("task_prompt", "ping")
        self._busy = False
        self._lock = threading.Lock()
        self._async_event = threading.Event()
        self._async_result = None
        ad = cfg.get("adapters", {})
        c = ad.get("cursor", {})
        z = ad.get("zcode", {})
        g = ad.get("grok", {})
        grok_url = (os.environ.get("GROK_WEBHOOK_URL") or g.get("webhook_url") or "").strip()
        zcode_url = (os.environ.get("ZCODE_WEBHOOK_URL") or z.get("webhook_url") or "").strip()
        self.adapters = {
            "cursor": CursorAdapter(
                c.get("api_base", "https://api.cursor.com"),
                c.get("poll_seconds", 5),
                c.get("timeout_seconds", 600),
            ),
            "zcode": ZCodeAdapter(zcode_url, z.get("mode", "webhook")),
            "grok": GrokAdapter(grok_url, g.get("mode", "webhook")),
        }
        self.async_timeout = int(g.get("wait_seconds") or cfg.get("async_wait_seconds") or 1800)
        self._ser_lock = threading.Lock()
        self._tcp_client = None
        self._tcp_addr = None
        self._tcp_lock = threading.Lock()
        self._tcp_stop = threading.Event()
        self._tcp_thread = None
        self._tcp_server = None
        self._wifi_out_q = __import__("queue").Queue(maxsize=32)
        self._try_open_serial()
        self._start_wifi_tunnel()
        self.control_host = cfg.get("control_host", "127.0.0.1")
        self.control_port = int(cfg.get("control_port", 8765))
        # Fan-out board events to HTTP waiters (ZCode MCP, etc.)
        self._event_q: queue.Queue[str] = queue.Queue(maxsize=64)
        self._last_event: str | None = None
        self._last_start_ts: float = 0.0
        self._session = {
            "state": "idle",  # idle|run|done|fail
            "target": None,
            "started_at": None,
            "summary": "",
            "joinable": False,
        }
        # LCD effects (thinking timer + L1 marquee); generation cancels prior threads
        self._lcd_fx_gen = 0
        self._lcd_fx_stop = threading.Event()
        self._lcd_fx_thread: threading.Thread | None = None
        # Board RUN arm + last L0 (firmware StepChirp = each LCD|L0| while RUN)
        self._board_run = False
        self._last_l0 = None

    def _transport_mode(self) -> str:
        # auto | wifi | com
        return str(self.cfg.get("transport") or "auto").strip().lower()

    def _open_serial(self):
        port = self.cfg.get("port")
        if not port:
            return None
        baud = self.cfg.get("baud", 9600)
        return serial.Serial(port, baud, timeout=0.05, write_timeout=2)

    def _try_open_serial(self) -> None:
        mode = self._transport_mode()
        if mode == "wifi":
            self.ser = None
            print("[serial] skipped (transport=wifi)")
            return
        port = self.cfg.get("port")
        if not port:
            self.ser = None
            print("[serial] no port configured")
            return
        try:
            self.ser = self._open_serial()
            print(f"[serial] open {port} @ {self.cfg.get('baud', 9600)}")
        except Exception as e:
            self.ser = None
            if mode == "com":
                raise
            print(f"[serial] open fail (will use WiFi if ESP connects): {e}")

    def _reopen_serial(self) -> None:
        if self._transport_mode() == "wifi" or not self.cfg.get("port"):
            return
        try:
            if self.ser and getattr(self.ser, "is_open", False):
                self.ser.close()
        except Exception:
            pass
        time.sleep(0.3)
        self.ser = self._open_serial()
        print(f"[serial] reopened {self.cfg.get('port')}")

    def _tcp_accept_loop(self) -> None:
        host = self.cfg.get("wifi_tunnel_host", "0.0.0.0")
        port = int(self.cfg.get("wifi_tunnel_port", 8870))
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(1)
        srv.settimeout(1.0)
        self._tcp_server = srv
        print(f"[wifi] tunnel listen {host}:{port}")
        while not self._tcp_stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(0.05)
            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            with self._tcp_lock:
                old = self._tcp_client
                self._tcp_client = conn
                self._tcp_addr = addr
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            print(f"[wifi] ESP connected from {addr[0]}:{addr[1]}")

    def _start_wifi_tunnel(self) -> None:
        self._tcp_client = None
        self._tcp_addr = None
        self._tcp_server = None
        self._tcp_lock = threading.Lock()
        self._tcp_stop = threading.Event()
        if int(self.cfg.get("wifi_tunnel_port", 0) or 0) <= 0:
            print("[wifi] tunnel disabled")
            return
        th = threading.Thread(target=self._tcp_accept_loop, name="wifi-tunnel", daemon=True)
        th.start()
        self._tcp_thread = th

    def _wifi_connected(self) -> bool:
        c = self._tcp_client
        return c is not None

    def _write_transport(self, data: bytes) -> None:
        # Prefer live ESP TCP; else COM.
        with self._tcp_lock:
            sock = self._tcp_client
        if sock is not None:
            try:
                sock.sendall(data)
                return
            except OSError as e:
                print(f"[wifi] write fail: {e}")
                with self._tcp_lock:
                    if self._tcp_client is sock:
                        self._tcp_client = None
                try:
                    sock.close()
                except Exception:
                    pass
                raise
        if self.ser is None:
            raise OSError("no transport (WiFi ESP not connected and COM closed)")
        self.ser.write(data)

    def _read_transport(self, n: int = 128) -> bytes:
        with self._tcp_lock:
            sock = self._tcp_client
        if sock is not None:
            try:
                chunk = sock.recv(n)
            except socket.timeout:
                return b""
            except OSError as e:
                print(f"[wifi] read fail: {e}")
                with self._tcp_lock:
                    if self._tcp_client is sock:
                        self._tcp_client = None
                try:
                    sock.close()
                except Exception:
                    pass
                return b""
            if chunk == b"":
                print("[wifi] ESP disconnected")
                with self._tcp_lock:
                    if self._tcp_client is sock:
                        self._tcp_client = None
                try:
                    sock.close()
                except Exception:
                    pass
                return b""
            return chunk
        if self.ser is None:
            time.sleep(0.05)
            return b""
        return self.ser.read(n)

    def send(self, line: str) -> None:
        # Do NOT strip trailing spaces — LCD lines are padded to 16 chars.
        line = line.rstrip("\r\n")
        data = (line + "\n").encode("ascii", errors="ignore")
        with self._ser_lock:
            for attempt in range(2):
                try:
                    self._write_transport(data)
                    print(f">>> {line}")
                    return
                except (serial.SerialException, OSError) as e:
                    print(f"[tx] write fail: {e}")
                    if self._wifi_connected():
                        if attempt == 1:
                            raise
                        continue
                    try:
                        self._reopen_serial()
                    except Exception as e2:
                        print(f"[serial] reopen fail: {e2}")
                        if attempt == 1:
                            raise


    def send_wifi_only(self, line: str) -> bool:
        """Queue a line for ESP TCP (flushed in loop; never COM/STC)."""
        if not self._wifi_connected():
            return False
        line = line.rstrip("\r\n")
        data = (line + "\n").encode("ascii", errors="ignore")
        try:
            self._wifi_out_q.put_nowait((line, data))
        except queue.Full:
            try:
                self._wifi_out_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._wifi_out_q.put_nowait((line, data))
            except queue.Full:
                return False
        return True

    def _flush_wifi_out(self) -> None:
        """Send queued WiFi-only lines from the bridge loop thread."""
        while True:
            try:
                line, data = self._wifi_out_q.get_nowait()
            except queue.Empty:
                return
            with self._tcp_lock:
                sock = self._tcp_client
            if sock is None:
                return
            try:
                sock.sendall(data)
                print(f">>>wifi {line}")
            except OSError as e:
                print(f"[wifi] oled/tx fail: {e}")
                with self._tcp_lock:
                    if self._tcp_client is sock:
                        self._tcp_client = None
                try:
                    sock.close()
                except Exception:
                    pass
                return

    def set_oled(self, *, directory: str = "", model: str = "", ctx: str = "", status: str = "") -> dict:
        """Push task-detail fields to ESP OLED. Empty fields are skipped."""
        sent = []
        mapping = (
            ("dir", directory),
            ("mdl", model),
            ("ctx", ctx),
            ("st", status),
        )
        live = self._wifi_connected()
        for key, val in mapping:
            if val is None:
                continue
            s = str(val).strip()
            if not s:
                continue
            s = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in s)[:48]
            ok = live and self.send_wifi_only(f"OLED|{key}|{s}")
            sent.append({"field": key, "ok": ok, "text": s})
        return {"ok": any(x["ok"] for x in sent) if sent else False, "sent": sent, "wifi": live}

    def lcd(self, row: int, text: str, *, settle: bool = False) -> None:
        # Always send exactly 16 chars so the LCD never keeps stale glyphs.
        # Firmware Beep()/StepChirp busy-waits ~55ms and drops UART RX — pace after L0.
        t = ((text or "")[:16]).ljust(16)
        self.send(f"LCD|L{row}|{t}")
        if settle or row == 0:
            time.sleep(0.08)

    def resolve_prompt(self) -> tuple[str, str]:
        """Return (prompt, source). Prefer clipboard, then task.txt, then config."""
        clip = read_windows_clipboard()
        if clip:
            # Ignore huge accidental pastes
            if len(clip) > 4000:
                clip = clip[:4000]
            return clip, "clipboard"
        task_file = ROOT / "task.txt"
        if task_file.exists():
            text = task_file.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text, "task.txt"
        return (self.prompt or "ping"), "config"

    def on_progress(self, msg: str) -> None:
        self.lcd(0, "RUNNING...")
        self.lcd(1, msg[:16])

    @staticmethod
    def _lcd_ascii(s: str) -> str:
        """Keep printable ASCII only — drop non-ASCII (no ???? spam)."""
        return "".join(ch for ch in (s or "") if 32 <= ord(ch) < 127)

    def _stop_lcd_effects(self) -> None:
        self._lcd_fx_gen += 1
        self._lcd_fx_stop.set()
        th = self._lcd_fx_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=0.4)
        self._lcd_fx_thread = None
        self._lcd_fx_stop = threading.Event()

    def _arm_run_for_progress(self) -> None:
        """Enter board ST_RUN once. STAT|run itself Beeps and blocks UART ~55ms+."""
        try:
            if self._session.get("state") == "done":
                return
            if self._board_run and self._session.get("state") == "run":
                return
            tag = (self.target or "hub")[:12]
            self.send(f"STAT|run|{tag}")
            # Firmware HandleRx STAT|run -> Beep(55ms) busy-wait; do not TX yet.
            time.sleep(0.09)
            self._board_run = True
            self.mark_session("run", "progress")
            self._last_l0 = "RUNNING..."
        except Exception:
            pass

    def _start_think_timer(self) -> None:
        """L1 shows elapsed MM:SS while L0 stays put (no extra L0 chirps)."""
        gen = self._lcd_fx_gen
        stop = self._lcd_fx_stop
        t0 = time.time()

        def tick() -> None:
            while not stop.is_set() and gen == self._lcd_fx_gen:
                elapsed = int(time.time() - t0)
                m, s = divmod(elapsed, 60)
                label = f"{m:02d}:{s:02d}"
                try:
                    self.lcd(1, label)  # L1 only — firmware does not chirp on L1
                except Exception as e:
                    print(f"[lcd-timer] {e}")
                    break
                if stop.wait(1.0):
                    break

        self._lcd_fx_thread = threading.Thread(target=tick, name="lcd-think-timer", daemon=True)
        self._lcd_fx_thread.start()

    def _start_scroll_l1(self, text: str, interval: float = 0.35) -> None:
        """Marquee L1 when text is longer than 16 chars (L1 = no chirp)."""
        plain = self._lcd_ascii(text).strip()
        if len(plain) <= 16:
            self.lcd(1, plain)
            return
        gen = self._lcd_fx_gen
        stop = self._lcd_fx_stop
        buf = plain + "   "
        n = len(buf)

        def tick() -> None:
            i = 0
            while not stop.is_set() and gen == self._lcd_fx_gen:
                window = (buf + buf)[i : i + 16]
                try:
                    self.lcd(1, window)
                except Exception as e:
                    print(f"[lcd-scroll] {e}")
                    break
                i = (i + 1) % n
                if stop.wait(interval):
                    break

        self._lcd_fx_thread = threading.Thread(target=tick, name="lcd-scroll-l1", daemon=True)
        self._lcd_fx_thread.start()

    def _push_l0_phase(self, phase: str) -> None:
        """L0 phase change -> firmware StepChirp; also send BEEP|step backup."""
        phase = (phase or "")[:16]
        if not phase:
            return
        if self._last_l0 == phase:
            return
        self.lcd(0, phase, settle=True)
        self._last_l0 = phase

    def set_ready(self, line0: str = "ready", line1: str = "await next") -> None:
        """End ZCode turn without STAT|idle — next prompt needs no K3."""
        self._stop_lcd_effects()
        self._arm_run_for_progress()
        l0 = self._lcd_ascii(line0).strip() or "ready"
        l1 = self._lcd_ascii(line1).strip() or "await next"
        self._push_l0_phase(l0)
        self.lcd(1, l1)
        if self._session.get("state") != "done":
            self.mark_session("run", "ready")

    def set_progress(
        self,
        line0: str = "",
        line1: str = "",
        *,
        timer: bool = False,
        scroll: bool = True,
        chirp: bool = False,
    ) -> None:
        """Classic pad progress: STAT|run once, then L0 phase changes chirp.

        Firmware: StepChirp on each LCD|L0| while ST_RUN. L1 never chirps.
        Must pace serial after STAT|run / L0 because Beep() blocks UART RX.
        """
        self._arm_run_for_progress()
        l0 = self._lcd_ascii(line0).strip()
        l1_raw = line1 or ""
        l1 = self._lcd_ascii(l1_raw).strip()

        use_timer = timer or (
            l0.lower() in ("thinking", "working") and (not l1 or set(l1) <= set("?"))
        ) or (timer and not l1)

        # chirp=True forces L0 resend even if same text (explicit step)
        if chirp and l0 and self._last_l0 == l0:
            self._last_l0 = None

        self._stop_lcd_effects()

        if use_timer:
            phase = l0 or "thinking"
            self._push_l0_phase(phase)
            self.lcd(1, "00:00")
            self._start_think_timer()
            return

        if l0:
            self._push_l0_phase(l0)
        if scroll and len(l1) > 16:
            self._start_scroll_l1(l1)
        elif l1 or l1_raw:
            self.lcd(1, l1[:16] if l1 else "")

    def mark_session(self, state: str, summary: str = "") -> None:
        self._session["state"] = state
        self._session["target"] = self.target
        self._session["summary"] = summary or ""
        if state == "run":
            self._session["started_at"] = time.time()
            self._session["joinable"] = True
        elif state == "idle":
            self._session["joinable"] = False
        elif state in ("done", "fail"):
            self._session["joinable"] = False

    def session_snapshot(self) -> dict:
        started = self._session.get("started_at")
        age = (time.time() - started) if started else None
        return {
            "state": self._session.get("state"),
            "target": self._session.get("target") or self.target,
            "started_at": started,
            "summary": self._session.get("summary") or "",
            "joinable": bool(self._session.get("joinable")),
            "busy": bool(self._busy),
            "last_event": self._last_event,
            "last_start_age_sec": round(age, 1) if age is not None else None,
            "port": self.cfg.get("port"),
        }

    def join_session(self) -> dict:
        """Late joiner: attach to in-flight run without a new K3."""
        snap = self.session_snapshot()
        if self._busy or self._session.get("state") == "run":
            try:
                self.send(f"STAT|run|{self.target}")
                self.lcd(0, "joined hub")
                self.lcd(1, (self.target or "run")[:16])
            except Exception as e:
                return {"ok": False, "error": str(e), "session": snap}
            if self._last_event == "BTN:START" or self._last_start_ts:
                try:
                    self._event_q.put_nowait("BTN:START")
                except queue.Full:
                    pass
            return {"ok": True, "joined": True, "session": self.session_snapshot()}
        hint = "Idle — call taskpad_wait_event then press K3"
        if not bool(self.cfg.get("launch_on_start", False)):
            hint = "Detect mode (no START jobs); hooks push LCD"
        snap["hint"] = hint
        return {"ok": True, "joined": False, "session": snap}

    def wait_event(
        self,
        timeout_sec: float = 30.0,
        match_prefix: str | None = None,
        replay_last: bool = True,
    ) -> dict:
        if replay_last and match_prefix and str(match_prefix).startswith("BTN:START"):
            if self._busy or self._session.get("state") == "run" or self._last_event == "BTN:START":
                return {
                    "ok": True,
                    "event": "BTN:START",
                    "replayed": True,
                    "session": self.session_snapshot(),
                }
        deadline = time.time() + max(0.1, float(timeout_sec))
        while time.time() < deadline:
            try:
                remaining = max(0.05, deadline - time.time())
                ev = self._event_q.get(timeout=min(0.5, remaining))
            except queue.Empty:
                continue
            if match_prefix and not str(ev).startswith(str(match_prefix)):
                continue
            return {"ok": True, "event": ev, "replayed": False, "session": self.session_snapshot()}
        return {"ok": False, "error": "timeout", "session": self.session_snapshot()}

    def signal_done(self, ok: bool, summary: str = "") -> None:
        summary = (summary or ("done" if ok else "fail"))[:16]
        with self._lock:
            self._async_result = {"ok": ok, "summary": summary}
            self._async_event.set()
        try:
            self._stop_lcd_effects()
            if ok:
                self.send(f"STAT|done|{self.target}")
                self.lcd(0, "DONE! press ACK")
                self.lcd(1, summary)
                self.mark_session("done", summary)
            else:
                self.send(f"STAT|fail|{self.target}")
                self.lcd(0, "FAIL")
                self.lcd(1, summary)
                self.mark_session("fail", summary)
        except Exception as e:
            print(f"[hub] signal_done ui fail: {e}")

    def run_task(self) -> None:
        with self._lock:
            if self._busy:
                return
            self._busy = True
            self._async_event.clear()
            self._async_result = None
        tag = (self.target or "hub")[:12]
        try:
            prompt, source = self.resolve_prompt()
            print(f"[hub] run_task target={tag} source={source}")
            adapter = self.adapters.get(tag)
            if adapter is None:
                self.signal_done(False, "bad target")
                return
            # ZCode: wait for MCP client /done instead of webhook-only.
            if tag == "zcode":
                self.lcd(0, "ZCode via hub")
                self.lcd(1, "await /done")
                print("HUB zcode: waiting for /done from MCP client")
                self._async_event.wait(timeout=self.async_timeout)
                result = self._async_result or {"ok": False, "summary": "timeout"}
                if not result.get("ok"):
                    self.lcd(0, "FAIL")
                    self.lcd(1, str(result.get("summary") or "timeout")[:16])
                return
            def on_progress(msg: str) -> None:
                self.on_progress(msg)

            result = adapter.start(prompt, on_progress=on_progress)
            if result.ok and tag in ("grok", "zcode") and getattr(adapter, "mode", "") == "webhook":
                # Async webhook: wait for /done from client (Grok taskpad_ctl done)
                self.lcd(0, "WAITING...")
                self.lcd(1, (tag)[:16])
                self._async_event.wait(timeout=self.async_timeout)
                done = self._async_result
                if done is None:
                    self.signal_done(False, "timeout")
                return
            if result.ok:
                self.signal_done(True, (result.summary or "ok")[:16])
            else:
                self.lcd(0, "FAIL")
                self.lcd(1, (result.summary or "error")[:16])
                self.mark_session("fail", result.summary or "error")
        finally:
            with self._lock:
                self._busy = False

    def publish_event(self, line: str) -> None:
        self._last_event = line
        if line == "BTN:START":
            self._last_start_ts = time.time()
        try:
            self._event_q.put_nowait(line)
        except queue.Full:
            try:
                self._event_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._event_q.put_nowait(line)
            except queue.Full:
                pass

    def handle(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        print(f"<<< {line}")
        if line == "HELLO":
            self.publish_event(line)
            self.send(f"TARGET|{self.target}")
            self.send("STAT|idle")
            return
        if line == "PONG":
            return
        if line == "BTN:START":
            # Detect-only by default: hooks push LCD; no Grok/ZCode job launch.
            if not bool(self.cfg.get("launch_on_start", False)):
                print("[hub] START ignored (launch_on_start=false); hooks/detect only")
                try:
                    self.lcd(0, "detect mode")
                    self.lcd(1, "no START job")
                    self.send("STAT|idle")
                except Exception as e:
                    print(f"[serial] START ignore fail: {e}")
                return
            self.publish_event(line)
            try:
                self.send(f"STAT|run|{self.target}")
            except Exception as e:
                print(f"[serial] START ack fail: {e}")
            self.mark_session("run")
            if self._busy:
                print("[hub] preempt busy waiter")
                self.signal_done(False, "preempt")
                time.sleep(0.05)
            threading.Thread(target=self.run_task, daemon=True).start()
            return
        if line == "BTN:ACK":
            self.publish_event(line)
            self.send("BUZZ|off")
            self.send("STAT|idle")
            self.mark_session("idle")
            return
        if line.startswith("TARGET:"):
            t = line.split(":", 1)[1].strip()
            if t in self.adapters:
                self.target = t
                self.send(f"TARGET|{t}")
            return
        if line == "BTN:CYCLE":
            order = ["grok", "cursor", "zcode"]
            try:
                i = order.index(self.target)
            except ValueError:
                i = 0
            self.target = order[(i + 1) % len(order)]
            self.send(f"TARGET|{self.target}")
            return

    def loop(self) -> None:
        buf = b""
        last_ping = 0.0
        port = self.cfg.get("port") or "(none)"
        print(f"TaskPad51 bridge COM={port} wifi=:{self.cfg.get('wifi_tunnel_port', 8870)} target={self.target}")
        print(f"HUB HTTP http://{self.control_host}:{self.control_port}/")
        while True:
            try:
                with self._ser_lock:
                    chunk = self._read_transport(128)
            except (serial.SerialException, OSError) as e:
                print(f"[rx] read fail: {e}")
                if not self._wifi_connected():
                    try:
                        with self._ser_lock:
                            self._reopen_serial()
                    except Exception as e2:
                        print(f"[serial] reopen fail: {e2}")
                        time.sleep(1)
                else:
                    time.sleep(0.2)
                continue
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    try:
                        line = raw.decode("ascii", errors="ignore").strip("\r")
                    except Exception:
                        continue
                    # ESP keepalive / status — log only
                    if line.startswith("WIFI|"):
                        print(f"[wifi] {line}")
                        continue
                    self.handle(line)
            now = time.time()
            if now - last_ping > 10:
                # Only ping when a board path exists
                if self._wifi_connected() or self.ser is not None:
                    try:
                        self.send("PING")
                    except Exception as e:
                        print(f"[tx] ping fail: {e}")
                last_ping = now
            self._flush_wifi_out()
            time.sleep(0.01)



def make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print("[http]", fmt % args)

        def _json(self, code: int, obj: dict):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return {}

        def _read_json_any(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {"_parse_error": True}

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            from urllib.parse import parse_qs
            qs = parse_qs(parsed.query or "")
            if path in ("/", "/health"):
                self._json(
                    200,
                    {
                        "ok": True,
                        "hub": True,
                        "wifi_tunnel": bool(bridge._wifi_connected()),
                        "wifi_peer": (bridge._tcp_addr[0] if bridge._tcp_addr else None),
                        "busy": bridge._busy,
                        "target": bridge.target,
                        "port": bridge.cfg.get("port"),
                        "clients": ["grok", "zcode", "http"],
                        "session": bridge.session_snapshot(),
                    },
                )
                return
            if path == "/events":
                timeout = float((qs.get("timeout") or ["30"])[0])
                match = (qs.get("match") or [None])[0]
                replay = (qs.get("replay") or ["1"])[0] not in ("0", "false", "no")
                self._json(200, bridge.wait_event(timeout, match, replay_last=replay))
                return
            if path == "/target":
                self._json(200, {"ok": True, "target": bridge.target})
                return
            if path == "/session":
                self._json(200, {"ok": True, **bridge.session_snapshot()})
                return
            if path == "/join":
                self._json(200, bridge.join_session())
                return
            if path == "/mcp":
                # Streamable HTTP: optional GET SSE not required for tools-only clients
                self.send_response(405)
                self.send_header("Allow", "POST, DELETE")
                self.end_headers()
                return
            self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            # MCP must parse list-or-object; read once for all POST routes.
            if path == "/mcp":
                body = self._read_json_any()
                if isinstance(body, dict) and body.get("_parse_error"):
                    self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                    return
                resp, sid, code = mcp_http.dispatch(bridge, body)
                if code == 202 and resp is None:
                    self.send_response(202)
                    if sid:
                        self.send_header("Mcp-Session-Id", sid)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload = b"" if resp is None else json.dumps(resp, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                if sid:
                    self.send_header("Mcp-Session-Id", sid)
                self.send_header("Mcp-Protocol-Version", mcp_http.PROTOCOL)
                self.end_headers()
                if payload:
                    self.wfile.write(payload)
                return

            data = self._read_json()
            if path == "/oled":
                out = bridge.set_oled(
                    directory=str(data.get("dir") or data.get("directory") or ""),
                    model=str(data.get("model") or data.get("mdl") or ""),
                    ctx=str(data.get("ctx") or data.get("context") or ""),
                    status=str(data.get("status") or data.get("st") or ""),
                )
                self._json(200 if out.get("ok") else 503, out)
                return
            if path == "/progress":
                l0 = str(data.get("line0") or data.get("l0") or "")
                l1 = str(data.get("line1") or data.get("l1") or "")
                if not l0 and data.get("msg"):
                    l0 = "RUNNING..."
                    l1 = str(data.get("msg"))
                timer = bool(data.get("timer"))
                scroll = True if data.get("scroll") is None else bool(data.get("scroll"))
                chirp = bool(data.get("chirp"))
                bridge.set_progress(l0, l1, timer=timer, scroll=scroll, chirp=chirp)
                self._json(200, {"ok": True})
                return
            if path == "/ready":
                # Soft end-of-turn: keep board RUN so next ZCode turn needs no K3
                l0 = str(data.get("line0") or "ready")
                l1 = str(data.get("line1") or "await next")
                try:
                    bridge.set_ready(l0, l1)
                except Exception as e:
                    self._json(500, {"ok": False, "error": str(e)})
                    return
                self._json(200, {"ok": True})
                return
            if path == "/done":
                summary = str(data.get("summary") or data.get("msg") or "done")
                bridge.signal_done(True, summary)
                self._json(200, {"ok": True})
                return
            if path == "/fail":
                summary = str(data.get("summary") or data.get("msg") or "fail")
                bridge.signal_done(False, summary)
                self._json(200, {"ok": True})
                return
            if path == "/idle":
                # Soft clear for normal chat — no DONE beep
                try:
                    bridge._stop_lcd_effects()
                    bridge.send("STAT|idle")
                    bridge._board_run = False
                    bridge._last_l0 = None
                    bridge.mark_session("idle")
                except Exception as e:
                    self._json(500, {"ok": False, "error": str(e)})
                    return
                self._json(200, {"ok": True})
                return
            if path == "/lcd":
                row = data.get("row")
                if row is not None:
                    bridge.lcd(int(row), str(data.get("text") or data.get("line0") or ""))
                else:
                    bridge.set_progress(str(data.get("line0") or ""), str(data.get("line1") or ""))
                self._json(200, {"ok": True})
                return
            if path == "/send":
                line = str(data.get("line") or "").strip()
                if not line:
                    self._json(400, {"ok": False, "error": "line required"})
                    return
                try:
                    bridge.send(line)
                except Exception as e:
                    self._json(500, {"ok": False, "error": str(e)})
                    return
                self._json(200, {"ok": True})
                return
            if path == "/status":
                st = str(data.get("state") or "idle")
                tag = str(data.get("tag") or bridge.target)
                summary = str(data.get("summary") or "")[:16]
                try:
                    if st == "idle":
                        bridge.send("BUZZ|off")
                        bridge.send("STAT|idle")
                        bridge.mark_session("idle")
                    elif st in ("run", "done", "fail"):
                        bridge.send(f"STAT|{st}|{tag}")
                        if st == "run":
                            bridge.mark_session("run")
                            # Treat as a soft START so late wait_event can replay
                            bridge._last_event = "BTN:START"
                            bridge._last_start_ts = time.time()
                        if st == "done":
                            bridge.lcd(0, "DONE! press ACK")
                            if summary:
                                bridge.lcd(1, summary)
                            bridge.signal_done(True, summary or "done")
                        elif st == "fail":
                            bridge.lcd(0, "FAIL")
                            if summary:
                                bridge.lcd(1, summary)
                            bridge.signal_done(False, summary or "fail")
                    else:
                        self._json(400, {"ok": False, "error": "bad state"})
                        return
                except Exception as e:
                    self._json(500, {"ok": False, "error": str(e)})
                    return
                self._json(200, {"ok": True})
                return
            if path == "/join":
                self._json(200, bridge.join_session())
                return
            if path == "/target":
                tag = str(data.get("target") or data.get("tag") or "").strip()
                if tag not in bridge.adapters:
                    self._json(400, {"ok": False, "error": "bad target"})
                    return
                bridge.target = tag
                try:
                    bridge.send(f"TARGET|{tag}")
                except Exception:
                    pass
                self._json(200, {"ok": True, "target": tag})
                return
            self._json(404, {"ok": False, "error": "not found"})

        def do_DELETE(self):
            path = urlparse(self.path).path
            if path == "/mcp":
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

    return Handler


def main() -> None:
    load_dotenv(ROOT / ".env")
    cfg_path = ROOT / "config" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    bridge = Bridge(cfg)
    handler = make_handler(bridge)
    httpd = ThreadingHTTPServer((bridge.control_host, bridge.control_port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        bridge.loop()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
