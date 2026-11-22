"""Плагин «Внутриигровые часы».

Добавляет кнопку в тулбар мастера. По клику открывается окно с 24-секторным
циферблатом и стрелкой. Клик по сектору задаёт час (округление до 30 минут).

Время хранится в ``app.state.plugins["clock"] = {"hour": 14.5}`` и попадает
в снапшот для игроков — его видно в вебе (см. map.js / player.js).
"""
import math
import tkinter as tk
from tkinter import ttk

# Палитра совпадает с master_gui.py.
COL_BG = "#1b1e26"
COL_PANEL = "#232833"
COL_FIELD = "#2a3040"
COL_TEXT = "#e6e8ef"
COL_MUTED = "#9aa3b5"
COL_ACCENT = "#5b8def"
COL_BORDER = "#3a4155"


def _slot(app):
    """Возвращает (и при необходимости создаёт) словарь плагина в GameState."""
    plugins = getattr(app.state, "plugins", None)
    if plugins is None:
        plugins = {}
        app.state.plugins = plugins
    slot = plugins.get("clock")
    if slot is None:
        slot = {"hour": 12.0}  # по умолчанию — полдень
        plugins["clock"] = slot
    return slot


def _fmt(hour):
    h = int(math.floor(hour)) % 24
    m = int(round((hour - math.floor(hour)) * 60)) % 60
    return f"{h:02d}:{m:02d}"


class ClockWindow(tk.Toplevel):
    """24-секторный циферблат с перетаскиваемой стрелкой."""

    R = 120
    PAD = 30

    def __init__(self, master, app, slot):
        super().__init__(master)
        self.app = app
        self.slot = slot
        self.title("Внутриигровые часы")
        self.configure(bg=COL_BG)
        self.resizable(False, False)

        size = self.R * 2 + self.PAD * 2
        self.canvas = tk.Canvas(
            self, width=size, height=size,
            bg=COL_PANEL, highlightthickness=0, cursor="hand2",
        )
        self.canvas.pack(padx=10, pady=(10, 4))

        self.info = ttk.Label(
            self, text="", anchor="center",
            font=("Segoe UI", 18, "bold"), foreground=COL_ACCENT,
        )
        self.info.pack(fill="x", padx=10)

        hint = ttk.Label(
            self,
            text="Кликни по сектору, чтобы задать час. "
                 "Цифры по кругу — часы 0–23.",
            wraplength=size, justify="center", foreground=COL_MUTED,
        )
        hint.pack(fill="x", padx=10, pady=(0, 10))

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_click)

        self._redraw()
        self._update_label()

    # ---------- геометрия ----------
    def _center(self):
        return self.PAD + self.R, self.PAD + self.R

    def _hour_to_angle(self, hour):
        # 0:00 — сверху, дальше по часовой стрелке.
        return (hour / 24.0) * 2 * math.pi - math.pi / 2

    # ---------- события ----------
    def _on_click(self, event):
        cx, cy = self._center()
        dx = event.x - cx
        dy = event.y - cy
        if math.hypot(dx, dy) > self.R + 6:
            return
        ang = math.atan2(dy, dx) + math.pi / 2
        if ang < 0:
            ang += 2 * math.pi
        hour = (ang / (2 * math.pi)) * 24.0
        hour = (round(hour * 2) / 2.0) % 24.0   # шаг 30 минут
        if hour == self.slot["hour"]:
            return
        self.slot["hour"] = hour
        self._redraw()
        self._update_label()
        _refresh_button(self.app)
        # Только bump — перерисовка карты не нужна.
        self.app.state.bump()

    def _update_label(self):
        self.info.config(text=f"Сейчас примерно {_fmt(self.slot['hour'])}")

    # ---------- рендер ----------
    def _redraw(self):
        c = self.canvas
        c.delete("all")
        cx, cy = self._center()
        r = self.R

        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill=COL_FIELD, outline=COL_BORDER, width=2)

        current = self.slot["hour"]
        cur_floor = int(math.floor(current)) % 24

        for h in range(24):
            a0 = self._hour_to_angle(h)
            a1 = self._hour_to_angle(h + 1)
            x0 = cx + r * math.cos(a0)
            y0 = cy + r * math.sin(a0)
            x1 = cx + r * math.cos(a1)
            y1 = cy + r * math.sin(a1)
            fill = "#3a5a9a" if h == cur_floor else COL_PANEL
            c.create_polygon(cx, cy, x0, y0, x1, y1,
                             fill=fill, outline=COL_BORDER, width=1)
            am = (a0 + a1) / 2
            tx = cx + (r - 18) * math.cos(am)
            ty = cy + (r - 18) * math.sin(am)
            c.create_text(tx, ty, text=str(h), fill=COL_TEXT,
                          font=("Segoe UI", 10, "bold"))

        c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6,
                      fill=COL_ACCENT, outline="#ffffff", width=1)

        a = self._hour_to_angle(current)
        ex = cx + (r - 24) * math.cos(a)
        ey = cy + (r - 24) * math.sin(a)
        c.create_line(cx, cy, ex, ey, fill="#ffd34d", width=4,
                      arrow="last", arrowshape=(14, 18, 6))


def _refresh_button(app):
    slot = _slot(app)
    try:
        app._clock_button.config(text="🕒 " + _fmt(slot["hour"]))
    except Exception:  # noqa: BLE001
        pass


def _open_clock(app):
    slot = _slot(app)
    win = getattr(app, "_clock_window", None)
    if win is not None and win.winfo_exists():
        win.lift()
        win.focus_force()
        return
    app._clock_window = ClockWindow(app.root, app, slot)


def register(app):
    """Точка входа плагина: добавляет кнопку в тулбар мастера."""
    btn = tk.Button(
        app.toolbar, text="🕒 " + _fmt(_slot(app)["hour"]), width=10,
        relief="flat", bd=0, padx=6, pady=3, cursor="hand2",
        bg=COL_PANEL, fg=COL_TEXT,
        activebackground=COL_ACCENT, activeforeground="#ffffff",
        command=lambda: _open_clock(app),
    )
    btn.pack(side="left", padx=(8, 1))
    app._clock_button = btn
    app._clock_window = None