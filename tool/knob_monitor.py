"""
ESP32S AS5600 Knob Monitor
串口格式：Raw=XXXX | XXX.XX deg | X.XXXX rad | PW=XXXX.XX us
"""

import tkinter as tk
from tkinter import ttk, font
import serial
import serial.tools.list_ports
import threading
import math
import re
import queue
import time

# ── 設定 ─────────────────────────────────────────────────────
BAUD      = 115200
BG        = "#0d0d0d"
BG2       = "#1a1a1a"
BG3       = "#111111"
BORDER    = "#2a2a2a"
CYAN      = "#00d4ff"
DIM       = "#555555"
WARN      = "#aaaa00"
ERR       = "#aa3333"
WHITE     = "#ffffff"
KNOB_R    = 110      # 旋鈕半徑 px
CX = CY   = 140      # canvas 中心

# ── 解析一行 ─────────────────────────────────────────────────
RE_DEG = re.compile(r"([\d.]+)\s*deg")
RE_RAD = re.compile(r"([\d.]+)\s*rad")
RE_RAW = re.compile(r"Raw=\s*(\d+)")
RE_PW  = re.compile(r"PW=([\d.]+)\s*us")

def parse_line(line):
    m_deg = RE_DEG.search(line)
    m_rad = RE_RAD.search(line)
    if not (m_deg and m_rad):
        return None
    deg = float(m_deg.group(1))
    rad = float(m_rad.group(1))
    raw = int(RE_RAW.search(line).group(1)) if RE_RAW.search(line) else None
    m_pw = RE_PW.search(line)
    pw  = float(m_pw.group(1)) if m_pw else 1000 + (deg / 360) * 1000
    return dict(deg=deg, rad=rad, raw=raw, pw=pw)

# ── 主視窗 ────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ESP32S · AS5600 Knob Monitor")
        self.configure(bg=BG)
        self.resizable(False, False)

        self._serial  = None
        self._thread  = None
        self._running = False
        self._queue   = queue.Queue()
        self._deg     = 0.0

        self._build_ui()
        self._refresh_ports()
        self.after(50, self._poll_queue)

    # ── UI ────────────────────────────────────────────────────
    def _build_ui(self):
        pad = dict(padx=16, pady=0)

        # title
        tk.Label(self, text="ESP32S · AS5600 Knob Monitor",
                 bg=BG, fg=DIM, font=("Consolas", 10)).pack(pady=(18, 12))

        # knob canvas
        self._canvas = tk.Canvas(self, width=280, height=280,
                                 bg=BG, highlightthickness=0)
        self._canvas.pack()
        self._draw_knob(0)

        # stats row
        stats = tk.Frame(self, bg=BG)
        stats.pack(fill="x", padx=16, pady=(18, 0))

        self._vdeg = self._stat_box(stats, "ANGLE",  "---", "deg")
        self._vrad = self._stat_box(stats, "RADIAN", "---", "rad")
        self._vraw = self._stat_box(stats, "RAW",    "---", "/ 4095")
        for w in (self._vdeg, self._vrad, self._vraw):
            w.pack(side="left", expand=True, fill="x", padx=4)

        # PWM bar
        pw_frame = tk.Frame(self, bg=BG)
        pw_frame.pack(fill="x", padx=16, pady=(16, 0))

        top = tk.Frame(pw_frame, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text="PWM Pulse Width", bg=BG, fg=DIM,
                 font=("Consolas", 8)).pack(side="left")
        self._lbl_pw = tk.Label(top, text="--- us", bg=BG, fg=CYAN,
                                font=("Consolas", 8))
        self._lbl_pw.pack(side="right")

        bar_bg = tk.Frame(pw_frame, bg=BG2, height=10,
                          highlightbackground=BORDER, highlightthickness=1)
        bar_bg.pack(fill="x", pady=(4, 0))
        bar_bg.pack_propagate(False)
        self._bar = tk.Frame(bar_bg, bg=CYAN, height=10)
        self._bar.place(x=0, y=0, relheight=1, relwidth=0)

        bot = tk.Frame(pw_frame, bg=BG)
        bot.pack(fill="x", pady=(3, 0))
        tk.Label(bot, text="1000 us (0°)",   bg=BG, fg=DIM, font=("Consolas", 7)).pack(side="left")
        tk.Label(bot, text="2000 us (360°)", bg=BG, fg=DIM, font=("Consolas", 7)).pack(side="right")

        # log
        log_frame = tk.Frame(self, bg=BG)
        log_frame.pack(fill="x", padx=16, pady=(16, 0))
        self._log = tk.Text(log_frame, height=6, bg=BG3, fg=DIM,
                            font=("Consolas", 8), relief="flat",
                            highlightbackground=BORDER, highlightthickness=1,
                            state="disabled", wrap="none")
        sb = tk.Scrollbar(log_frame, command=self._log.yview, bg=BG2)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True)
        self._log.tag_config("ok",   foreground="#3a7a3a")
        self._log.tag_config("warn", foreground=WARN)
        self._log.tag_config("err",  foreground=ERR)

        # controls
        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(pady=16)

        tk.Label(ctrl, text="Port:", bg=BG, fg=DIM,
                 font=("Consolas", 9)).pack(side="left", padx=(0, 4))
        self._port_var = tk.StringVar()
        self._port_cb  = ttk.Combobox(ctrl, textvariable=self._port_var,
                                      width=10, state="readonly",
                                      font=("Consolas", 9))
        self._port_cb.pack(side="left")

        self._btn_refresh = tk.Button(ctrl, text="↺", bg=BG2, fg=DIM,
                                      font=("Consolas", 10), relief="flat",
                                      padx=6, command=self._refresh_ports)
        self._btn_refresh.pack(side="left", padx=(4, 12))

        self._btn_conn = tk.Button(ctrl, text="Connect", bg=BG2, fg=CYAN,
                                   font=("Consolas", 9), relief="flat",
                                   padx=16, pady=6, command=self._toggle)
        self._btn_conn.pack(side="left", padx=4)

        tk.Button(ctrl, text="Clear", bg=BG2, fg=DIM,
                  font=("Consolas", 9), relief="flat",
                  padx=12, pady=6, command=self._clear_log).pack(side="left", padx=4)

    def _stat_box(self, parent, label, val, unit):
        f = tk.Frame(parent, bg=BG2,
                     highlightbackground=BORDER, highlightthickness=1)
        tk.Label(f, text=label, bg=BG2, fg=DIM,
                 font=("Consolas", 7)).pack(pady=(8, 0))
        lbl = tk.Label(f, text=val, bg=BG2, fg=CYAN,
                       font=("Consolas", 18, "bold"))
        lbl.pack()
        tk.Label(f, text=unit, bg=BG2, fg=DIM,
                 font=("Consolas", 7)).pack(pady=(0, 8))
        return lbl

    # ── Knob canvas ───────────────────────────────────────────
    def _draw_knob(self, deg):
        c = self._canvas
        c.delete("all")
        cx, cy, r = CX, CY, KNOB_R

        # tick marks
        for i in range(36):
            a = math.radians(i * 10 - 90)
            r1, r2 = r + 12, r + 19
            c.create_line(
                cx + math.cos(a) * r1, cy + math.sin(a) * r1,
                cx + math.cos(a) * r2, cy + math.sin(a) * r2,
                fill="#252525", width=1)

        # outer track arc (full circle, dim)
        self._arc(c, cx, cy, r, -90, 360, width=16, color="#1e1e1e")

        # progress arc
        if deg > 0.1:
            self._arc(c, cx, cy, r, -90, deg, width=16, color=CYAN)

        # knob body
        c.create_oval(cx - r + 14, cy - r + 14,
                      cx + r - 14, cy + r - 14,
                      fill="#181818", outline="#222222", width=2)

        # pointer
        pa = math.radians(deg - 90)
        px = cx + math.cos(pa) * (r - 26)
        py = cy + math.sin(pa) * (r - 26)
        c.create_line(cx, cy, px, py, fill=CYAN, width=3,
                      capstyle="round")

        # center dot
        c.create_oval(cx - 7, cy - 7, cx + 7, cy + 7,
                      fill=CYAN, outline="")

        # degree text
        c.create_text(cx, cy + 2,
                      text=f"{deg:.1f}°",
                      fill=WHITE, font=("Consolas", 22, "bold"))

    def _arc(self, c, cx, cy, r, start, extent, width, color):
        """tkinter arc helper"""
        x0, y0 = cx - r, cy - r
        x1, y1 = cx + r, cy + r
        c.create_arc(x0, y0, x1, y1,
                     start=-start - extent, extent=extent,
                     style="arc", outline=color, width=width)

    # ── Serial ────────────────────────────────────────────────
    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self._port_cb["values"] = ports
        if ports and not self._port_var.get():
            self._port_var.set(ports[0])

    def _toggle(self):
        if self._running:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self._port_var.get()
        if not port:
            self._log_msg("No port selected", "warn")
            return
        try:
            self._serial = serial.Serial(port, BAUD, timeout=1)
            self._running = True
            self._thread  = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            self._btn_conn.config(text="Disconnect", fg=ERR)
            self._log_msg(f"Connected {port} @ {BAUD}", "ok")
        except Exception as e:
            self._log_msg(f"Connect failed: {e}", "err")

    def _disconnect(self):
        self._running = False
        try:
            if self._serial:
                self._serial.close()
        except Exception:
            pass
        self._btn_conn.config(text="Connect", fg=CYAN)
        self._log_msg("Disconnected", "warn")

    def _read_loop(self):
        buf = ""
        while self._running:
            try:
                raw = self._serial.readline()
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._queue.put(line)
            except Exception as e:
                if self._running:
                    self._queue.put(f"__ERR__{e}")
                break

    # ── Poll queue (main thread) ──────────────────────────────
    def _poll_queue(self):
        try:
            while True:
                line = self._queue.get_nowait()
                if line.startswith("__ERR__"):
                    self._log_msg(line[7:], "err")
                    self._disconnect()
                else:
                    data = parse_line(line)
                    if data:
                        self._update_data(data)
                    else:
                        tag = "warn" if ("WARN" in line or "warn" in line) else \
                              "err"  if ("ERROR" in line or "error" in line) else "ok"
                        self._log_msg(line, tag)
        except queue.Empty:
            pass
        self.after(30, self._poll_queue)

    def _update_data(self, d):
        self._draw_knob(d["deg"])
        self._vdeg.config(text=f"{d['deg']:.2f}")
        self._vrad.config(text=f"{d['rad']:.4f}")
        self._vraw.config(text=str(d["raw"]) if d["raw"] is not None else "---")
        self._lbl_pw.config(text=f"{d['pw']:.1f} us")
        ratio = (d["pw"] - 1000) / 1000
        ratio = max(0, min(1, ratio))
        self._bar.place(relwidth=ratio)

    # ── Log ───────────────────────────────────────────────────
    def _log_msg(self, msg, tag="ok"):
        ts = time.strftime("%H:%M:%S")
        self._log.config(state="normal")
        self._log.insert("end", f"{ts}  {msg}\n", tag)
        self._log.see("end")
        # 最多 300 行
        lines = int(self._log.index("end-1c").split(".")[0])
        if lines > 300:
            self._log.delete("1.0", "2.0")
        self._log.config(state="disabled")

    def _clear_log(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")

    def on_close(self):
        self._disconnect()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
