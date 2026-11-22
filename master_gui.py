"""Tkinter master window for the D&D table.

The master and the broadcast share one *viewport* (pan + zoom), so the second
screen and the browser projection render exactly the rectangle the master sees.
Render order: map background -> media overlays -> tokens -> drawings.
"""

import colorsys
import math
import json
import os
import shutil
import socket
import uuid
import webbrowser

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser, simpledialog

from PIL import Image, ImageTk, ImageOps, ImageGrab

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    import pygame
    PYGAME_AVAILABLE = True
except Exception:  # noqa: BLE001
    pygame = None
    PYGAME_AVAILABLE = False

from game_state import (
    GameState, uid, Token, Drawing, Overlay, Character, Ability, Monster,
    Combatant, Resource, Item, RESOURCE_PRESETS, Scene, evaluate_dice,
)
from server import UPLOADS_DIR

# Дополнительные поля правил 5e. Расширяем сериализацию модели здесь, чтобы
# старые файлы кампаний продолжали открываться без миграции вручную.
def _install_proficiency_fields(cls, fields):
    original_init = cls.__init__
    original_to_dict = cls.to_dict
    original_from_dict = cls.from_dict
    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        for name, default in fields.items():
            setattr(self, name, list(default) if isinstance(default, list) else (dict(default) if isinstance(default, dict) else default))
    def to_dict(self):
        data = original_to_dict(self)
        for name in fields:
            value = getattr(self, name, fields[name])
            data[name] = list(value) if isinstance(value, list) else (dict(value) if isinstance(value, dict) else value)
        return data
    def from_dict(data):
        obj = original_from_dict(data)
        for name, default in fields.items():
            value = data.get(name, default)
            setattr(obj, name, list(value) if isinstance(default, list) else (dict(value) if isinstance(default, dict) else value))
        return obj
    cls.__init__ = init
    cls.to_dict = to_dict
    cls.from_dict = staticmethod(from_dict)

_install_proficiency_fields(Character, {
    "proficiency_bonus": 2, "skill_proficiencies": [], "skill_expertise": [],
    "save_proficiencies": [], "weapon_proficiencies": [],
    "ability_scores": {"СИЛ": 10, "ЛОВ": 10, "ТЕЛ": 10, "ИНТ": 10, "МДР": 10, "ХАР": 10}})
_install_proficiency_fields(Monster, {
    "proficiency_bonus": 2, "weapon_proficiencies": []})

import ctypes
try:
    # Включаем Per-Monitor DPI Awareness (2). Это решает проблему съезжания окон
    # на мониторах с разным масштабированием (например, 100% и 125%).
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
COL_BG = "#1b1e26"
COL_PANEL = "#232833"
COL_FIELD = "#2a3040"
COL_TEXT = "#e6e8ef"
COL_MUTED = "#9aa3b5"
COL_ACCENT = "#5b8def"
COL_BORDER = "#3a4155"
COL_CANVAS = "#0b0e14"


def apply_ttk_dark(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=COL_BG, foreground=COL_TEXT,
                    fieldbackground=COL_FIELD, bordercolor=COL_BORDER,
                    lightcolor=COL_BORDER, darkcolor=COL_BORDER,
                    troughcolor=COL_PANEL, focuscolor=COL_ACCENT)
    style.configure("TFrame", background=COL_BG)
    style.configure("TLabel", background=COL_BG, foreground=COL_TEXT)
    style.configure("TLabelframe", background=COL_BG, bordercolor=COL_BORDER)
    style.configure("TLabelframe.Label", background=COL_BG,
                    foreground=COL_ACCENT)
    style.configure("TButton", background=COL_PANEL, foreground=COL_TEXT,
                    bordercolor=COL_BORDER, padding=(8, 4))
    style.map("TButton",
              background=[("active", COL_ACCENT), ("pressed", COL_ACCENT)],
              foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])
    style.configure("TEntry", fieldbackground=COL_FIELD, foreground=COL_TEXT,
                    insertcolor=COL_TEXT)
    style.configure("TSpinbox", fieldbackground=COL_FIELD, foreground=COL_TEXT,
                    arrowcolor=COL_TEXT)
    style.configure("TCombobox", fieldbackground=COL_FIELD, foreground=COL_TEXT,
                    arrowcolor=COL_TEXT)
    style.map("TCombobox", fieldbackground=[("readonly", COL_FIELD)],
              foreground=[("readonly", COL_TEXT)])
    style.configure("TNotebook", background=COL_BG, bordercolor=COL_BORDER)
    style.configure("TNotebook.Tab", background=COL_PANEL,
                    foreground=COL_MUTED, padding=(12, 6))
    style.map("TNotebook.Tab",
              background=[("selected", COL_ACCENT)],
              foreground=[("selected", "#ffffff")])
    style.configure("TScale", background=COL_BG, troughcolor=COL_FIELD)
    style.configure("TCheckbutton", background=COL_BG, foreground=COL_TEXT)
    style.map("TCheckbutton", background=[("active", COL_BG)])
    style.configure("TSeparator", background=COL_BORDER)
    style.configure("Vertical.TScrollbar", background=COL_PANEL,
                    troughcolor=COL_BG, arrowcolor=COL_TEXT)
    style.configure("Horizontal.TScrollbar", background=COL_PANEL,
                    troughcolor=COL_BG, arrowcolor=COL_TEXT)


def style_listbox(lb):
    lb.configure(bg=COL_FIELD, fg=COL_TEXT, selectbackground=COL_ACCENT,
                 selectforeground="#ffffff", highlightthickness=1,
                 highlightbackground=COL_BORDER, highlightcolor=COL_ACCENT,
                 borderwidth=0, relief="flat", selectborderwidth=0,
                 activestyle="none", font=("Segoe UI", 10))


def style_text(t):
    t.configure(bg=COL_FIELD, fg=COL_TEXT, insertbackground=COL_TEXT,
                selectbackground=COL_ACCENT, selectforeground="#ffffff",
                highlightthickness=0, borderwidth=0, relief="flat")


def grid_scrollable(widget, parent, row=0, column=0, rowspan=1):
    """Place a list/text widget and its scrollbar in a stable grid row."""
    bar = ttk.Scrollbar(parent, orient="vertical", command=widget.yview)
    widget.configure(yscrollcommand=bar.set)
    widget.grid(row=row, column=column, rowspan=rowspan, sticky="nsew")
    bar.grid(row=row, column=column + 1, rowspan=rowspan, sticky="ns", padx=(3, 0))
    parent.grid_rowconfigure(row, weight=1)
    parent.grid_columnconfigure(column, weight=1)
    return bar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def import_media(src):
    if not src or not os.path.exists(src):
        return None
    base, ext = os.path.splitext(os.path.basename(src))
    name = "%s-%s%s" % (base[:40], uuid.uuid4().hex[:6], ext)
    dest = os.path.join(UPLOADS_DIR, name)
    try:
        shutil.copy2(src, dest)
        return name
    except Exception as e:  # noqa: BLE001
        messagebox.showerror("Ошибка", "Не удалось импортировать файл:\n%s" % e)
        return None


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def make_photo(path, box, rotation=0.0):
    img = Image.open(path).convert("RGBA")
    if rotation:
        img = img.rotate(-rotation, expand=True, resample=Image.BICUBIC)
    img = ImageOps.contain(img, (max(1, int(box)), max(1, int(box))))
    return ImageTk.PhotoImage(img)


def get_monitors():
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        monitors = []
        proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HANDLE,
                                    wintypes.HDC,
                                    ctypes.POINTER(wintypes.RECT),
                                    wintypes.LPARAM)

        def cb(hmon, hdc, lprc, data):
            r = lprc.contents
            monitors.append((r.left, r.top, r.right, r.bottom))
            return 1

        user32.EnumDisplayMonitors(0, 0, proc_t(cb), 0)
        return monitors
    except Exception as e:
        print("Ошибка определения мониторов:", e)
        return []


_color_wheel_cache = {}


def color_wheel_image(size=150):
    if size in _color_wheel_cache:
        return _color_wheel_cache[size]
    img = Image.new("RGB", (size, size), (18, 20, 26))
    px = img.load()
    cx = cy = size / 2.0
    R = size / 2.0 - 1
    for y in range(size):
        for x in range(size):
            dx = x - cx
            dy = y - cy
            r = math.hypot(dx, dy)
            if r <= R:
                h = (math.degrees(math.atan2(dy, dx)) + 360) % 360
                s = r / R
                rr, gg, bb = colorsys.hsv_to_rgb(h / 360.0, s, 1.0)
                px[x, y] = (int(rr * 255), int(gg * 255), int(bb * 255))
    _color_wheel_cache[size] = img
    return img


# ---------------------------------------------------------------------------
# Reusable widgets
# ---------------------------------------------------------------------------
class ScrollFrame(ttk.Frame):
    """Canvas-backed frame with reliable wheel scrolling over every child."""

    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, bg=COL_BG, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical",
                                  command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y", padx=(3, 0))
        self.inner = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.inner,
                                                anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

        # Отдельный bindtag ставится ПЕРЕД классом виджета. Поэтому Listbox,
        # Text и другие элементы не перехватывают колесо раньше контейнера.
        self._wheel_tag = "ScrollWheel_%x" % id(self)
        self.bind_class(self._wheel_tag, "<MouseWheel>", self._on_wheel)
        self.bind_class(self._wheel_tag, "<Button-4>", self._on_wheel_linux)
        self.bind_class(self._wheel_tag, "<Button-5>", self._on_wheel_linux)
        self._install_wheel_tags(self)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _install_wheel_tags(self, widget):
        try:
            tags = widget.bindtags()
            if self._wheel_tag not in tags:
                widget.bindtags((self._wheel_tag,) + tags)
            for child in widget.winfo_children():
                self._install_wheel_tags(child)
        except tk.TclError:
            pass

    def _on_inner(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        # Новые дочерние элементы тоже получают wheel-bindtag.
        self._install_wheel_tags(self.inner)

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _pointer_inside(self):
        try:
            x, y = self.winfo_pointerxy()
            return (self.winfo_rootx() <= x < self.winfo_rootx() + self.winfo_width()
                    and self.winfo_rooty() <= y < self.winfo_rooty() + self.winfo_height())
        except tk.TclError:
            return False

    def _scroll_target(self, event, delta):
        # Nested lists/text (abilities, spell slots, inventory) scroll first;
        # when they reach an edge, the whole sheet continues scrolling.
        target = event.widget
        try:
            view = target.yview()
            if len(view) == 2 and (view[0] > 0.0 or view[1] < 1.0):
                target.yview_scroll(delta, "units")
                return True
        except (tk.TclError, AttributeError):
            pass
        self.canvas.yview_scroll(delta, "units")
        return True

    def _on_wheel(self, event):
        if not self._pointer_inside():
            return
        delta = int(-event.delta / 120) if event.delta else 0
        if delta == 0 and event.delta:
            delta = -1 if event.delta > 0 else 1
        if delta:
            self._scroll_target(event, delta)
        return "break"

    def _on_wheel_linux(self, event):
        if not self._pointer_inside():
            return
        self._scroll_target(event, -1 if event.num == 4 else 1)
        return "break"

    def _on_destroy(self, event):
        if event.widget is self:
            try:
                self.unbind_class(self._wheel_tag, "<MouseWheel>")
                self.unbind_class(self._wheel_tag, "<Button-4>")
                self.unbind_class(self._wheel_tag, "<Button-5>")
            except tk.TclError:
                pass


class ListEditor(ttk.LabelFrame):
    """Modular list of strings / {name,value} pairs / {name,desc} feats."""

    def __init__(self, master, title, items, kind="str", on_edit=None,
                 height=3, preview=False):
        super().__init__(master, text=title)
        self.items = items
        self.kind = kind
        self.on_edit = on_edit
        self.preview = None  # Kept as an attribute for compatibility; preview UI is intentionally omitted.
        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, padx=4, pady=3)
        self.listbox = tk.Listbox(top, height=height, exportselection=False)
        style_listbox(self.listbox)
        grid_scrollable(self.listbox, top, row=0, column=0)
        self.listbox.bind("<Double-Button-1>", lambda e: self.edit())
        btns = ttk.Frame(top)
        btns.grid(row=0, column=2, sticky="ns", padx=(5, 0))
        ttk.Button(btns, text="＋", width=3, command=self.add).pack(pady=1)
        ttk.Button(btns, text="✎", width=3, command=self.edit).pack(pady=1)
        ttk.Button(btns, text="－", width=3, command=self.remove).pack(pady=1)
        top.grid_columnconfigure(0, weight=1)
        top.grid_rowconfigure(0, weight=1)
        self.refresh()

    def _label(self, it):
        if self.kind == "pair":
            return "%s %s" % (it.get("name", ""), it.get("value", ""))
        if self.kind == "feat":
            return it.get("name", "")
        return str(it)

    def _desc(self, it):
        if self.kind == "feat":
            return it.get("desc", "")
        if self.kind == "pair":
            return "%s: %s" % (it.get("name", ""), it.get("value", ""))
        return str(it)

    def refresh(self):
        self.listbox.delete(0, "end")
        for it in self.items:
            self.listbox.insert("end", self._label(it))

    def _update_preview(self):
        return

    def _prompt(self, item):
        if self.on_edit:
            return self.on_edit(item)
        if self.kind == "pair":
            base = item or {}
            name = simpledialog.askstring("Запись", "Название:",
                                          initialvalue=base.get("name", ""),
                                          parent=self)
            if name is None:
                return None
            val = simpledialog.askstring("Значение", "Значение:",
                                         initialvalue=base.get("value", ""),
                                         parent=self)
            return {"name": name, "value": val or ""}
        v = simpledialog.askstring("Запись", "Значение:",
                                   initialvalue=str(item) if item else "",
                                   parent=self)
        return v if v is not None else None

    def add(self):
        new = self._prompt(None)
        if new is not None:
            self.items.append(new)
            self.refresh()
            self.listbox.selection_set("end")
            self._update_preview()

    def edit(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        i = sel[0]
        new = self._prompt(self.items[i])
        if new is not None:
            self.items[i] = new
            self.refresh()
            self.listbox.selection_set(i)
            self._update_preview()

    def remove(self):
        sel = self.listbox.curselection()
        if sel:
            del self.items[sel[0]]
            self.refresh()
            self._update_preview()


# ---------------------------------------------------------------------------
# Character editor
# ---------------------------------------------------------------------------
class CharacterEditor(tk.Toplevel):
    def __init__(self, master, character=None):
        super().__init__(master)
        self.title("Лист персонажа")
        self.geometry("820x860")
        self.configure(bg=COL_BG)
        self.resizable(True, True)
        self.result = None
        self._char = (Character.from_dict(character.to_dict())
                      if character else Character())
        self._build()
        self._load()
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self):
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)
        body = ScrollFrame(outer)
        body.pack(fill="both", expand=True)
        f = body.inner
        pad = {"padx": 4, "pady": 3}

        top = ttk.Frame(f)
        top.pack(fill="x", padx=8, pady=(8, 0))
        self.var_name = tk.StringVar()
        self.var_player = tk.StringVar()
        self.var_class = tk.StringVar()
        self.var_level = tk.IntVar(value=1)
        self.var_race = tk.StringVar()
        self.var_ac = tk.IntVar(value=10)
        self.var_hp = tk.IntVar(value=10)
        self.var_maxhp = tk.IntVar(value=10)
        self.var_temphp = tk.IntVar(value=0)
        self.var_token_image = tk.StringVar()

        for label, var, r in [("Имя", self.var_name, 0),
                              ("Игрок", self.var_player, 1),
                              ("Класс", self.var_class, 2),
                              ("Раса", self.var_race, 3)]:
            ttk.Label(top, text=label).grid(row=r, column=0, sticky="e", **pad)
            ttk.Entry(top, textvariable=var, width=28).grid(row=r, column=1, **pad)

        ttk.Label(top, text="Уровень").grid(row=0, column=2, sticky="e", **pad)
        ttk.Spinbox(top, from_=1, to=20, textvariable=self.var_level,
                    width=5).grid(row=0, column=3, **pad)
        ttk.Label(top, text="КД").grid(row=1, column=2, sticky="e", **pad)
        ttk.Spinbox(top, from_=0, to=40, textvariable=self.var_ac,
                    width=5).grid(row=1, column=3, **pad)
        ttk.Label(top, text="ХП").grid(row=2, column=2, sticky="e", **pad)
        ttk.Spinbox(top, from_=0, to=9999, textvariable=self.var_hp,
                    width=6).grid(row=2, column=3, **pad)
        ttk.Label(top, text="Макс. ХП").grid(row=3, column=2, sticky="e", **pad)
        ttk.Spinbox(top, from_=0, to=9999, textvariable=self.var_maxhp,
                    width=6).grid(row=3, column=3, **pad)
        ttk.Label(top, text="Врем. ХП").grid(row=4, column=2, sticky="e", **pad)
        ttk.Spinbox(top, from_=0, to=999, textvariable=self.var_temphp,
                    width=6).grid(row=4, column=3, **pad)

        ttk.Label(top, text="Токен").grid(row=4, column=0, sticky="e", **pad)
        tf = ttk.Frame(top)
        tf.grid(row=4, column=1, sticky="w", **pad)
        ttk.Entry(tf, textvariable=self.var_token_image, width=18).pack(side="left")
        ttk.Button(tf, text="…", width=2,
                   command=self._pick_token).pack(side="left", padx=2)

        prof = ttk.LabelFrame(f, text="Характеристики, навыки и спасброски")
        prof.pack(fill="x", padx=8, pady=6)
        prow = ttk.Frame(prof); prow.pack(fill="x", padx=5, pady=4)
        self.var_prof_bonus = tk.IntVar(value=2)
        ttk.Label(prow, text="Бонус мастерства (добавляется к навыкам и спасброскам с владением)").pack(side="left")
        ttk.Spinbox(prow, from_=0, to=12, textvariable=self.var_prof_bonus,
                    width=5, command=self._refresh_skill_values).pack(side="left", padx=6)

        self.ability_score_vars = {}
        self.ability_mod_labels = {}
        stat_frame = ttk.LabelFrame(prof, text="Характеристики")
        stat_frame.pack(fill="x", padx=5, pady=3)
        stat_names = [("СИЛ", "Сила"), ("ЛОВ", "Ловкость"), ("ТЕЛ", "Телосложение"),
                      ("ИНТ", "Интеллект"), ("МДР", "Мудрость"), ("ХАР", "Харизма")]
        for i, (key, title) in enumerate(stat_names):
            cell = ttk.Frame(stat_frame)
            cell.grid(row=0, column=i, padx=5, pady=4)
            ttk.Label(cell, text=title).pack()
            var = tk.IntVar(value=10)
            self.ability_score_vars[key] = var
            sp = ttk.Spinbox(cell, from_=1, to=30, textvariable=var, width=4,
                             command=self._refresh_skill_values)
            sp.pack()
            mod = ttk.Label(cell, text="(+0)", foreground=COL_ACCENT)
            mod.pack()
            self.ability_mod_labels[key] = mod
            var.trace_add("write", lambda *_: self._refresh_skill_values())

        skills = [
            ("Акробатика", "ЛОВ"), ("Расследование", "ИНТ"), ("Атлетика", "СИЛ"),
            ("Внимательность", "МДР"), ("Выживание", "МДР"), ("Выступление", "ХАР"),
            ("Запугивание", "ХАР"), ("История", "ИНТ"), ("Ловкость рук", "ЛОВ"),
            ("Магия", "ИНТ"), ("Медицина", "МДР"), ("Обман", "ХАР"),
            ("Природа", "ИНТ"), ("Проницательность", "МДР"), ("Религия", "ИНТ"),
            ("Скрытность", "ЛОВ"), ("Убеждение", "ХАР"), ("Уход за животными", "МДР")]
        self.skill_ability_map = dict(skills)
        sg = ttk.LabelFrame(prof, text="Навыки — итоговый бонус с учётом характеристики и владения")
        sg.pack(fill="x", padx=5, pady=3)
        self.skill_vars = {}
        self.expertise_vars = {}
        self.skill_value_labels = {}
        self.skill_ability_labels = {}
        for i, (skill, ability) in enumerate(skills):
            row, col = divmod(i, 2)
            cell = ttk.Frame(sg)
            cell.grid(row=row, column=col, sticky="ew", padx=5, pady=2)
            cell.columnconfigure(0, weight=1)
            v = tk.BooleanVar(value=False); self.skill_vars[skill] = v
            ttk.Checkbutton(cell, text=skill, variable=v,
                            command=self._refresh_skill_values).grid(
                                row=0, column=0, sticky="w")
            ability_label = ttk.Label(cell, text=ability, width=4,
                                      foreground=COL_MUTED, anchor="center")
            ability_label.grid(row=0, column=1, padx=(3, 2))
            self.skill_ability_labels[skill] = ability_label
            ev = tk.BooleanVar(value=False); self.expertise_vars[skill] = ev
            ttk.Checkbutton(cell, text="Эксп.", variable=ev,
                            command=self._refresh_skill_values).grid(
                                row=0, column=2, padx=(2, 3))
            val = ttk.Label(cell, text="+0", width=5, anchor="e",
                            foreground=COL_ACCENT, font=("Segoe UI", 10, "bold"))
            val.grid(row=0, column=3, sticky="e")
            self.skill_value_labels[skill] = val
        sg.columnconfigure(0, weight=1); sg.columnconfigure(1, weight=1)

        svf = ttk.LabelFrame(prof, text="Спасброски — итоговый бонус")
        svf.pack(fill="x", padx=5, pady=3)
        self.save_vars = {}
        self.save_value_labels = {}
        for ability, title in stat_names:
            cell = ttk.Frame(svf)
            cell.pack(side="left", padx=5, pady=3)
            v = tk.BooleanVar(value=False); self.save_vars[ability] = v
            ttk.Checkbutton(cell, text=ability, variable=v,
                            command=self._refresh_skill_values).pack(side="left")
            val = ttk.Label(cell, text="+0", foreground=COL_ACCENT)
            val.pack(side="left", padx=(2, 0))
            self.save_value_labels[ability] = val

        self.weapon_profs = ListEditor(prof, "Владение оружием и категориями",
                                       self._char.weapon_proficiencies, "str", height=3)
        self.weapon_profs.pack(fill="x", padx=5, pady=3)

        # Abilities
        ab = ttk.LabelFrame(f, text="Способности и заклинания")
        ab.pack(fill="x", padx=8, pady=6)
        abin = ttk.Frame(ab)
        abin.pack(fill="x", padx=4, pady=4)
        self.ability_list = tk.Listbox(abin, height=6, exportselection=False)
        style_listbox(self.ability_list)
        grid_scrollable(self.ability_list, abin)
        self.ability_list.bind("<Double-Button-1>", lambda e: self._edit_ability())
        abtn = ttk.Frame(abin)
        abtn.grid(row=0, column=2, sticky="ns", padx=(5, 0))
        ttk.Button(abtn, text="＋", width=3, command=self._add_ability).pack(pady=1)
        ttk.Button(abtn, text="✎", width=3, command=self._edit_ability).pack(pady=1)
        ttk.Button(abtn, text="－", width=3, command=self._del_ability).pack(pady=1)

        # Resources
        rs = ttk.LabelFrame(f, text="Ресурсы: ячейки, заряды, инфузии…")
        rs.pack(fill="x", padx=8, pady=6)
        rin = ttk.Frame(rs)
        rin.pack(fill="x", padx=4, pady=4)
        self.res_list = tk.Listbox(rin, height=5, exportselection=False)
        style_listbox(self.res_list)
        grid_scrollable(self.res_list, rin)
        self.res_list.bind("<Double-Button-1>", lambda e: self._edit_resource())
        rbtn = ttk.Frame(rin)
        rbtn.grid(row=0, column=2, sticky="ns", padx=(5, 0))
        ttk.Button(rbtn, text="＋", width=3, command=self._add_resource).pack(pady=1)
        ttk.Button(rbtn, text="✎", width=3, command=self._edit_resource).pack(pady=1)
        ttk.Button(rbtn, text="－", width=3, command=self._del_resource).pack(pady=1)

        # Inventory
        inv = ttk.LabelFrame(f, text="Предметы")
        inv.pack(fill="x", padx=8, pady=6)
        iin = ttk.Frame(inv)
        iin.pack(fill="x", padx=4, pady=4)
        self.inv_list = tk.Listbox(iin, height=5, exportselection=False)
        style_listbox(self.inv_list)
        grid_scrollable(self.inv_list, iin)
        self.inv_list.bind("<Double-Button-1>", lambda e: self._edit_item())
        ibtn = ttk.Frame(iin)
        ibtn.grid(row=0, column=2, sticky="ns", padx=(5, 0))
        ttk.Button(ibtn, text="＋", width=3, command=self._add_item).pack(pady=1)
        ttk.Button(ibtn, text="✎", width=3, command=self._edit_item).pack(pady=1)
        ttk.Button(ibtn, text="－", width=3, command=self._del_item).pack(pady=1)

        ttk.Label(f, text="Заметки").pack(anchor="w", padx=8, pady=(6, 0))
        self.notes = tk.Text(f, height=4, wrap="word")
        style_text(self.notes)
        self.notes.pack(fill="x", padx=8, pady=(0, 8))

        btns = ttk.Frame(outer)
        btns.pack(fill="x", padx=8, pady=6)
        ttk.Button(btns, text="Сохранить", command=self._ok).pack(side="left")
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(side="left", padx=6)

    def _preview(self, listbox, preview, items, attr):
        sel = listbox.curselection()
        content = ""
        if sel:
            content = getattr(items[sel[0]], attr, "") or "—"
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        preview.insert("1.0", content)
        preview.configure(state="disabled")

    def _pick_token(self):
        path = filedialog.askopenfilename(
            title="Картинка токена по умолчанию",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp *.gif"),
                       ("Все файлы", "*.*")])
        if path:
            name = import_media(path)
            if name:
                self.var_token_image.set(name)

    @staticmethod
    def _ability_modifier(score):
        return (int(score) - 10) // 2

    @staticmethod
    def _signed(value):
        return "%+d" % int(value)

    def _refresh_skill_values(self):
        try:
            pb = int(self.var_prof_bonus.get())
        except (ValueError, tk.TclError):
            pb = 0
        mods = {}
        for ability, var in self.ability_score_vars.items():
            try:
                mod = self._ability_modifier(var.get())
            except (ValueError, tk.TclError):
                mod = 0
            mods[ability] = mod
            if ability in self.ability_mod_labels:
                self.ability_mod_labels[ability].configure(text="(%s)" % self._signed(mod))
        for skill, ability in self.skill_ability_map.items():
            proficient = self.skill_vars[skill].get()
            expert = self.expertise_vars[skill].get()
            total = mods.get(ability, 0) + pb * (int(proficient or expert) + int(expert))
            self.skill_value_labels[skill].configure(text=self._signed(total))
        for ability, var in self.save_vars.items():
            total = mods.get(ability, 0) + (pb if var.get() else 0)
            self.save_value_labels[ability].configure(text=self._signed(total))

    def _load(self):
        c = self._char
        self.var_name.set(c.name)
        self.var_player.set(c.player)
        self.var_class.set(c.char_class)
        self.var_level.set(c.level)
        self.var_race.set(c.race)
        self.var_ac.set(c.ac)
        self.var_hp.set(c.hp)
        self.var_maxhp.set(c.max_hp)
        self.var_temphp.set(c.temp_hp)
        self.var_token_image.set(c.token_image or "")
        self.var_prof_bonus.set(getattr(c, "proficiency_bonus", 2))
        scores = getattr(c, "ability_scores", {})
        for ability, var in self.ability_score_vars.items():
            var.set(int(scores.get(ability, 10)))
        saved_skill_profs = getattr(c, "skill_proficiencies", [])
        for name, var in self.skill_vars.items():
            # Совместимость со старыми сохранениями, где Investigation
            # ошибочно назывался «Анализ».
            var.set(name in saved_skill_profs or
                    (name == "Расследование" and "Анализ" in saved_skill_profs))
        saved_expertise = getattr(c, "skill_expertise", [])
        for name, var in self.expertise_vars.items():
            var.set(name in saved_expertise or
                    (name == "Расследование" and "Анализ" in saved_expertise))
        for name, var in self.save_vars.items():
            var.set(name in getattr(c, "save_proficiencies", []))
        self.weapon_profs.items[:] = list(getattr(c, "weapon_proficiencies", []))
        self.weapon_profs.refresh()
        self._refresh_skill_values()
        for a in c.abilities:
            self.ability_list.insert("end", "%s — %s" % (a.name, a.source))
        for r in c.resources:
            self.res_list.insert("end", "%s: %d/%d" % (r.label, r.max - r.used, r.max))
        for it in c.inventory:
            self.inv_list.insert("end", "%s ×%d" % (it.name, it.qty))
        self.notes.insert("1.0", c.notes)

    def _save(self):
        c = self._char
        c.name = self.var_name.get() or "Без имени"
        c.player = self.var_player.get()
        c.char_class = self.var_class.get()
        c.level = int(self.var_level.get())
        c.race = self.var_race.get()
        c.ac = int(self.var_ac.get())
        c.hp = int(self.var_hp.get())
        c.max_hp = int(self.var_maxhp.get())
        c.temp_hp = int(self.var_temphp.get())
        c.token_image = self.var_token_image.get() or None
        c.proficiency_bonus = int(self.var_prof_bonus.get())
        c.ability_scores = {k: int(v.get()) for k, v in self.ability_score_vars.items()}
        c.skill_proficiencies = [n for n, v in self.skill_vars.items() if v.get()]
        c.skill_expertise = [n for n, v in self.expertise_vars.items() if v.get()]
        c.save_proficiencies = [n for n, v in self.save_vars.items() if v.get()]
        c.weapon_proficiencies = list(self.weapon_profs.items)
        c.notes = self.notes.get("1.0", "end").strip()

    # abilities
    def _ability_editor(self, ability=None):
        d = tk.Toplevel(self)
        d.title("Способность / заклинание")
        d.configure(bg=COL_BG)
        name = tk.StringVar(value=ability.name if ability else "")
        source = tk.StringVar(value=ability.source if ability else "")
        desc = tk.Text(d, height=8, width=52, wrap="word")
        style_text(desc)
        desc.insert("1.0", ability.description if ability else "")
        ttk.Label(d, text="Название").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(d, textvariable=name, width=40).grid(row=0, column=1)
        ttk.Label(d, text="Источник").grid(row=1, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(d, textvariable=source, width=40).grid(row=1, column=1)
        ttk.Label(d, text="Описание").grid(row=2, column=0, sticky="ne", padx=4, pady=3)
        desc.grid(row=2, column=1, padx=4, pady=3)
        out = {}

        def ok():
            out["v"] = Ability(name.get(), desc.get("1.0", "end").strip(),
                               source.get())
            d.destroy()
        ttk.Button(d, text="ОК", command=ok).grid(row=3, column=1, sticky="e", pady=6)
        d.grab_set()
        self.wait_window(d)
        return out.get("v")

    def _add_ability(self):
        a = self._ability_editor()
        if a:
            self._char.abilities.append(a)
            self.ability_list.insert("end", "%s — %s" % (a.name, a.source))

    def _edit_ability(self):
        sel = self.ability_list.curselection()
        if not sel:
            return
        i = sel[0]
        a = self._ability_editor(self._char.abilities[i])
        if a:
            self._char.abilities[i] = a
            self.ability_list.delete(i)
            self.ability_list.insert(i, "%s — %s" % (a.name, a.source))

    def _del_ability(self):
        sel = self.ability_list.curselection()
        if sel:
            i = sel[0]
            del self._char.abilities[i]
            self.ability_list.delete(i)

    # resources
    def _resource_editor(self, res=None):
        d = tk.Toplevel(self)
        d.title("Ресурс")
        d.configure(bg=COL_BG)
        label = tk.StringVar(value=res.label if res else "")
        mx = tk.IntVar(value=res.max if res else 0)
        used = tk.IntVar(value=res.used if res else 0)
        ttk.Label(d, text="Название").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Combobox(d, textvariable=label, values=RESOURCE_PRESETS,
                     width=28).grid(row=0, column=1, padx=4, pady=3)
        ttk.Label(d, text="Максимум").grid(row=1, column=0, sticky="e", padx=4)
        ttk.Spinbox(d, from_=0, to=99, textvariable=mx,
                    width=5).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(d, text="Потрачено").grid(row=2, column=0, sticky="e", padx=4)
        ttk.Spinbox(d, from_=0, to=99, textvariable=used,
                    width=5).grid(row=2, column=1, sticky="w", padx=4)
        out = {}

        def ok():
            out["v"] = Resource(label.get().strip() or "Ресурс",
                                int(mx.get()), int(used.get()))
            d.destroy()
        ttk.Button(d, text="ОК", command=ok).grid(row=3, column=1, sticky="e", pady=6)
        d.grab_set()
        self.wait_window(d)
        return out.get("v")

    def _add_resource(self):
        r = self._resource_editor()
        if r:
            self._char.resources.append(r)
            self.res_list.insert("end", "%s: %d/%d" % (r.label, r.max - r.used, r.max))

    def _edit_resource(self):
        sel = self.res_list.curselection()
        if not sel:
            return
        i = sel[0]
        r = self._resource_editor(self._char.resources[i])
        if r:
            self._char.resources[i] = r
            self.res_list.delete(i)
            self.res_list.insert(i, "%s: %d/%d" % (r.label, r.max - r.used, r.max))

    def _del_resource(self):
        sel = self.res_list.curselection()
        if sel:
            i = sel[0]
            del self._char.resources[i]
            self.res_list.delete(i)

    # items
    def _item_editor(self, item=None):
        d = tk.Toplevel(self)
        d.title("Предмет")
        d.configure(bg=COL_BG)
        name = tk.StringVar(value=item.name if item else "")
        qty = tk.IntVar(value=item.qty if item else 1)
        desc = tk.Text(d, height=6, width=52, wrap="word")
        style_text(desc)
        desc.insert("1.0", item.description if item else "")
        ttk.Label(d, text="Название").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(d, textvariable=name, width=36).grid(row=0, column=1)
        ttk.Label(d, text="Кол-во").grid(row=1, column=0, sticky="e", padx=4)
        ttk.Spinbox(d, from_=1, to=999, textvariable=qty,
                    width=5).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(d, text="Описание").grid(row=2, column=0, sticky="ne", padx=4, pady=3)
        desc.grid(row=2, column=1, padx=4, pady=3)
        out = {}

        def ok():
            out["v"] = Item(name.get().strip() or "Предмет",
                            desc.get("1.0", "end").strip(), int(qty.get()))
            d.destroy()
        ttk.Button(d, text="ОК", command=ok).grid(row=3, column=1, sticky="e", pady=6)
        d.grab_set()
        self.wait_window(d)
        return out.get("v")

    def _add_item(self):
        it = self._item_editor()
        if it:
            self._char.inventory.append(it)
            self.inv_list.insert("end", "%s ×%d" % (it.name, it.qty))

    def _edit_item(self):
        sel = self.inv_list.curselection()
        if not sel:
            return
        i = sel[0]
        it = self._item_editor(self._char.inventory[i])
        if it:
            self._char.inventory[i] = it
            self.inv_list.delete(i)
            self.inv_list.insert(i, "%s ×%d" % (it.name, it.qty))

    def _del_item(self):
        sel = self.inv_list.curselection()
        if sel:
            i = sel[0]
            del self._char.inventory[i]
            self.inv_list.delete(i)

    def _ok(self):
        self._save()
        self.result = self._char
        self.destroy()


# ---------------------------------------------------------------------------
# Monster editor
# ---------------------------------------------------------------------------
class MonsterEditor(tk.Toplevel):
    def __init__(self, master, monster=None):
        super().__init__(master)
        self.title("Статблок монстра")
        self.geometry("760x820")
        self.configure(bg=COL_BG)
        self.result = None
        self._mon = Monster.from_dict(monster.to_dict()) if monster else Monster()
        self._build()
        self._load()
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self):
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)
        body = ScrollFrame(outer)
        body.pack(fill="both", expand=True)
        f = body.inner
        pad = {"padx": 4, "pady": 3}

        self.v_name = tk.StringVar(); self.v_type = tk.StringVar()
        self.v_size = tk.StringVar(value="Средний"); self.v_align = tk.StringVar()
        self.v_ac = tk.IntVar(value=10); self.v_hp = tk.IntVar(value=10)
        self.v_hpdice = tk.StringVar(); self.v_speed = tk.StringVar()
        self.v_cr = tk.StringVar(); self.v_token_image = tk.StringVar()

        gridf = ttk.Frame(f)
        gridf.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(gridf, text="Имя").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(gridf, textvariable=self.v_name, width=26).grid(row=0, column=1, **pad)
        ttk.Label(gridf, text="Тип").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(gridf, textvariable=self.v_type, width=26).grid(row=1, column=1, **pad)
        ttk.Label(gridf, text="Размер").grid(row=0, column=2, sticky="e", **pad)
        ttk.Entry(gridf, textvariable=self.v_size, width=18).grid(row=0, column=3, **pad)
        ttk.Label(gridf, text="Мировоззрение").grid(row=1, column=2, sticky="e", **pad)
        ttk.Entry(gridf, textvariable=self.v_align, width=18).grid(row=1, column=3, **pad)
        ttk.Label(gridf, text="КД").grid(row=2, column=0, sticky="e", **pad)
        ttk.Spinbox(gridf, from_=0, to=40, textvariable=self.v_ac,
                    width=6).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(gridf, text="ХП").grid(row=2, column=2, sticky="e", **pad)
        ttk.Spinbox(gridf, from_=0, to=9999, textvariable=self.v_hp,
                    width=6).grid(row=2, column=3, sticky="w", **pad)
        ttk.Label(gridf, text="Кости ХП").grid(row=3, column=0, sticky="e", **pad)
        ttk.Entry(gridf, textvariable=self.v_hpdice, width=12).grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(gridf, text="Скорость").grid(row=3, column=2, sticky="e", **pad)
        ttk.Entry(gridf, textvariable=self.v_speed, width=16).grid(row=3, column=3, sticky="w", **pad)
        ttk.Label(gridf, text="Опасность").grid(row=4, column=0, sticky="e", **pad)
        ttk.Entry(gridf, textvariable=self.v_cr, width=12).grid(row=4, column=1, sticky="w", **pad)
        ttk.Label(gridf, text="Бонус мастерства").grid(row=5, column=0, sticky="e", **pad)
        self.v_prof_bonus = tk.IntVar(value=2)
        ttk.Spinbox(gridf, from_=0, to=12, textvariable=self.v_prof_bonus,
                    width=6).grid(row=5, column=1, sticky="w", **pad)
        ttk.Label(gridf, text="Токен").grid(row=4, column=2, sticky="e", **pad)
        tf = ttk.Frame(gridf)
        tf.grid(row=4, column=3, sticky="w", **pad)
        ttk.Entry(tf, textvariable=self.v_token_image, width=12).pack(side="left")
        ttk.Button(tf, text="…", width=2, command=self._pick_token).pack(side="left", padx=2)

        ttk.Label(f, text="Характеристики").pack(anchor="w", padx=8, pady=(8, 2))
        st = ttk.Frame(f)
        st.pack(fill="x", padx=8)
        self.stat_vars = {}
        self.stat_mod_labels = {}
        for i, k in enumerate(Monster.STAT_KEYS):
            ttk.Label(st, text=k).grid(row=0, column=i * 2, padx=4)
            v = tk.IntVar(value=10)
            self.stat_vars[k] = v
            ttk.Spinbox(st, from_=1, to=30, textvariable=v,
                        width=4, command=self._refresh_monster_mods).grid(row=1, column=i * 2, padx=4)
            mod_label = ttk.Label(st, text="(+0)", foreground=COL_ACCENT)
            mod_label.grid(row=1, column=i * 2 + 1, padx=(0, 4))
            self.stat_mod_labels[k] = mod_label
            v.trace_add("write", lambda *_: self._refresh_monster_mods())

        # Modular lists (two columns)
        mg = ttk.Frame(f)
        mg.pack(fill="x", padx=8, pady=6)
        mg.columnconfigure(0, weight=1)
        mg.columnconfigure(1, weight=1)
        self.le_saves = ListEditor(mg, "Спасброски", self._mon.saves, "pair")
        self.le_skills = ListEditor(mg, "Навыки (бонусы)", self._mon.skills, "pair")
        self.le_weapon_profs = ListEditor(f, "Владение оружием", self._mon.weapon_proficiencies,
                                          "str", height=3)
        self.le_weapon_profs.pack(fill="x", padx=8, pady=4)
        self.le_vuln = ListEditor(mg, "Уязвимости", self._mon.damage_vuln)
        self.le_res = ListEditor(mg, "Сопротивления", self._mon.damage_res)
        self.le_imm = ListEditor(mg, "Иммунитет урона", self._mon.damage_imm)
        self.le_cond = ListEditor(mg, "Иммунитет состояний", self._mon.cond_imm)
        self.le_senses = ListEditor(mg, "Чувства", self._mon.senses)
        self.le_lang = ListEditor(mg, "Языки", self._mon.languages)
        for i, le in enumerate([self.le_saves, self.le_skills, self.le_vuln,
                                self.le_res, self.le_imm, self.le_cond,
                                self.le_senses, self.le_lang]):
            le.grid(row=i // 2, column=i % 2, sticky="nsew", padx=2, pady=2)

        # Feats with description preview
        self.le_traits = ListEditor(f, "Особенности", self._mon.traits, "feat",
                                    on_edit=self._feat_editor, height=4)
        self.le_traits.pack(fill="x", padx=8, pady=4)
        self.le_actions = ListEditor(f, "Действия", self._mon.actions, "feat",
                                     on_edit=self._feat_editor, height=4)
        self.le_actions.pack(fill="x", padx=8, pady=4)
        self.le_legendary = ListEditor(f, "Легендарные", self._mon.legendary,
                                       "feat", on_edit=self._feat_editor,
                                       height=4)
        self.le_legendary.pack(fill="x", padx=8, pady=4)

        ttk.Label(f, text="Заметки").pack(anchor="w", padx=8, pady=(6, 0))
        self.notes = tk.Text(f, height=4, wrap="word")
        style_text(self.notes)
        self.notes.pack(fill="x", padx=8, pady=(0, 8))

        btns = ttk.Frame(outer)
        btns.pack(fill="x", padx=8, pady=6)
        ttk.Button(btns, text="Сохранить", command=self._ok).pack(side="left")
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(side="left", padx=6)

    def _pick_token(self):
        path = filedialog.askopenfilename(
            title="Картинка токена по умолчанию",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp *.gif"),
                       ("Все файлы", "*.*")])
        if path:
            name = import_media(path)
            if name:
                self.v_token_image.set(name)

    def _refresh_monster_mods(self):
        for key, var in getattr(self, "stat_vars", {}).items():
            try:
                mod = (int(var.get()) - 10) // 2
            except (ValueError, tk.TclError):
                mod = 0
            self.stat_mod_labels[key].configure(text="(%+d)" % mod)

    def _load(self):
        m = self._mon
        self.v_name.set(m.name); self.v_type.set(m.monster_type)
        self.v_size.set(m.size); self.v_align.set(m.alignment)
        self.v_ac.set(m.ac); self.v_hp.set(m.hp)
        self.v_hpdice.set(m.hp_dice); self.v_speed.set(m.speed); self.v_cr.set(m.cr)
        self.v_token_image.set(m.token_image or "")
        self.v_prof_bonus.set(getattr(m, "proficiency_bonus", 2))
        self.le_weapon_profs.items[:] = list(getattr(m, "weapon_proficiencies", []))
        self.le_weapon_profs.refresh()
        for k in Monster.STAT_KEYS:
            self.stat_vars[k].set(m.stats[k])
        self._refresh_monster_mods()
        self.notes.insert("1.0", m.notes)

    def _feat_editor(self, feat):
        d = tk.Toplevel(self)
        d.title("Особенность / действие")
        d.configure(bg=COL_BG)
        name = tk.StringVar(value=feat.get("name", "") if feat else "")
        desc = tk.Text(d, height=7, width=52, wrap="word")
        style_text(desc)
        desc.insert("1.0", feat.get("desc", "") if feat else "")
        ttk.Label(d, text="Название").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(d, textvariable=name, width=40).grid(row=0, column=1)
        ttk.Label(d, text="Описание").grid(row=1, column=0, sticky="ne", padx=4, pady=3)
        desc.grid(row=1, column=1, padx=4, pady=3)
        out = {}

        def ok():
            out["v"] = {"name": name.get(),
                        "desc": desc.get("1.0", "end").strip()}
            d.destroy()
        ttk.Button(d, text="ОК", command=ok).grid(row=2, column=1, sticky="e", pady=6)
        d.grab_set()
        self.wait_window(d)
        return out.get("v")

    def _ok(self):
        m = self._mon
        m.name = self.v_name.get() or "Монстр"
        m.monster_type = self.v_type.get(); m.size = self.v_size.get()
        m.alignment = self.v_align.get(); m.ac = int(self.v_ac.get())
        m.hp = int(self.v_hp.get()); m.hp_dice = self.v_hpdice.get()
        m.speed = self.v_speed.get(); m.cr = self.v_cr.get()
        m.token_image = self.v_token_image.get() or None
        m.proficiency_bonus = int(self.v_prof_bonus.get())
        m.weapon_proficiencies = list(self.le_weapon_profs.items)
        for k in Monster.STAT_KEYS:
            m.stats[k] = int(self.stat_vars[k].get())
        m.notes = self.notes.get("1.0", "end").strip()
        self.result = m
        self.destroy()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class MasterApp:
    def __init__(self, state: GameState, root: tk.Tk, port: int = 5000):
        self.state = state
        self.root = root
        self.port = port

        self.tool = "select"
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.selected = None
        self._drag_off = (0, 0)
        self._current_shape = None
        self._pending_token_image = None
        self._photo_cache = {}
        self._bg_crop_cache = {}
        self.second_win = None
        self.second_canvas = None
        self._local_track = None
        self._undo_stack = []
        self._redo_stack = []
        self._erasing = False
        self._pan_last = None
        self._token_menu = None
        self.fill_color = "#3a5a9a"
        self.fill_enabled = tk.BooleanVar(value=False)
        self.line_width = tk.IntVar(value=3)
        self._resize_pending = False
        self._second_sync_job = None
        self._second_sync_viewport = None
        self._source_image_cache = {}

        apply_ttk_dark(self.root)
        self.root.configure(bg=COL_BG)
        self._build_ui()
        self._bind_canvas()
        self.canvas.bind("<Control-v>", lambda e: self._paste_image_from_clipboard())
        self.canvas.bind("<Control-V>", lambda e: self._paste_image_from_clipboard())
        self.refresh_lists()
        self.redraw()
        self._update_status()
        self.root.after(120, self.zoom_fit)
        # Загружаем плагины (каждый сам добавляет свой UI).
        try:
            from plugins import load_plugins
            load_plugins(self)
        except Exception as exc:  # noqa: BLE001
            print("[plugins] loader error:", exc)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.root.title("D&D Мастер — %s" % self.state.campaign_name)
        self.root.geometry("1480x900")

        self.toolbar = ttk.Frame(self.root, padding=4)
        self.toolbar.pack(side="top", fill="x")
        tools = [
            ("select", "Выбрать"), ("hand", "Рука"), ("brush", "Кисть"),
            ("erase", "Ластик"), ("line", "Линия"), ("rect", "Прямоуг."),
            ("oval", "Овал"), ("token", "Токен"), ("token_img", "Токен-фото"),
            ("image", "Картинка"), ("video", "Видео"),
        ]
        self._tool_buttons = {}
        for key, label in tools:
            b = tk.Button(self.toolbar, text=label, width=10, relief="flat",
                          bd=0, padx=6, pady=3, cursor="hand2",
                          bg=COL_PANEL, fg=COL_TEXT,
                          activebackground=COL_ACCENT, activeforeground="#fff",
                          command=lambda k=key: self.set_tool(k))
            b.pack(side="left", padx=1)
            self._tool_buttons[key] = b

        ttk.Separator(self.toolbar, orient="vertical").pack(side="left", fill="y", padx=4)
        ttk.Button(self.toolbar, text="Фон", command=self._set_background).pack(side="left", padx=2)
        ttk.Button(self.toolbar, text="Вставить картинку", command=self._paste_image_from_clipboard).pack(side="left", padx=2)
        ttk.Button(self.toolbar, text="Сетка", command=self._toggle_grid).pack(side="left", padx=2)
        ttk.Button(self.toolbar, text="−", width=3,
                   command=lambda: self.zoom_by(1 / 1.2)).pack(side="left", padx=1)
        ttk.Button(self.toolbar, text="＋", width=3,
                   command=lambda: self.zoom_by(1.2)).pack(side="left", padx=1)
        ttk.Button(self.toolbar, text="По размеру", command=self.zoom_fit).pack(side="left", padx=2)

        bar = ttk.Frame(self.root, padding=(4, 0, 4, 4))
        bar.pack(side="top", fill="x")
        ttk.Label(bar, text="Сцена:").pack(side="left")
        self.scene_var = tk.StringVar()
        self.scene_combo = ttk.Combobox(bar, textvariable=self.scene_var,
                                        width=22, state="readonly")
        self.scene_combo.pack(side="left", padx=4)
        self.scene_combo.bind("<<ComboboxSelected>>", self._scene_selected)
        ttk.Button(bar, text="＋", width=3, command=self._scene_add).pack(side="left", padx=1)
        ttk.Button(bar, text="✎", width=3, command=self._scene_rename).pack(side="left", padx=1)
        ttk.Button(bar, text="🗑", width=3, command=self._scene_delete).pack(side="left", padx=1)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="Второй экран", command=self._toggle_second_screen).pack(side="left", padx=2)
        ttk.Button(bar, text="Сайт игроков", command=self._open_player_site).pack(side="left", padx=2)

        main = ttk.Frame(self.root)
        main.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(main, bg=COL_CANVAS, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._build_notebook(main)
        self._build_selection_bar()
        self._build_status_bar()
        self.set_tool("select")

    def _build_notebook(self, parent):
        self.nb = ttk.Notebook(parent, width=410)
        self.nb.pack(side="right", fill="y")

        # Characters
        ch = ttk.Frame(self.nb, padding=4)
        self.nb.add(ch, text="Персонажи")
        char_list_frame = ttk.Frame(ch)
        char_list_frame.pack(fill="x", pady=2)
        self.char_list = tk.Listbox(char_list_frame, height=7)
        style_listbox(self.char_list)
        grid_scrollable(self.char_list, char_list_frame, row=0, column=0)
        self.char_list.bind("<Double-Button-1>", lambda e: self._edit_character())
        self.char_list.bind("<<ListboxSelect>>", lambda e: self._refresh_quick())
        b = ttk.Frame(ch); b.pack(fill="x", pady=2)
        ttk.Button(b, text="Добавить", command=self._add_character).pack(side="left", padx=2)
        ttk.Button(b, text="Изменить", command=self._edit_character).pack(side="left", padx=2)
        ttk.Button(b, text="Удалить", command=self._del_character).pack(side="left", padx=2)
        b2 = ttk.Frame(ch); b2.pack(fill="x", pady=2)
        ttk.Button(b2, text="На карту", command=self._character_to_map).pack(side="left", padx=2)
        ttk.Button(b2, text="В бой", command=self._character_to_combat).pack(side="left", padx=2)
        ttk.Button(b2, text="Ссылка", command=self._player_link).pack(side="left", padx=2)

        self.quick = ttk.LabelFrame(ch, text="Быстрое изменение")
        self.quick.pack(fill="both", expand=True, pady=(6, 0))
        self.quick_body = ttk.Frame(self.quick)
        self.quick_body.pack(fill="both", expand=True, padx=4, pady=4)

        # Bestiary
        be = ttk.Frame(self.nb, padding=4)
        self.nb.add(be, text="Бестиарий")
        search_row = ttk.Frame(be)
        search_row.pack(fill="x", pady=(0, 5))
        ttk.Label(search_row, text="Поиск:").pack(side="left", padx=(0, 5))
        self.monster_search_var = tk.StringVar()
        self.monster_search_entry = ttk.Entry(search_row, textvariable=self.monster_search_var)
        self.monster_search_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(search_row, text="×", width=3,
                   command=lambda: self.monster_search_var.set("")).pack(side="left", padx=(4, 0))
        self.monster_search_var.trace_add("write", lambda *_: self._refresh_bestiary_list())
        self._filtered_monster_indices = []

        mon_list_frame = ttk.Frame(be)
        mon_list_frame.pack(fill="both", expand=True)
        self.mon_list = tk.Listbox(mon_list_frame, height=12)
        style_listbox(self.mon_list)
        grid_scrollable(self.mon_list, mon_list_frame, row=0, column=0)
        self.mon_list.bind("<Double-Button-1>", lambda e: self._edit_monster())
        mb = ttk.Frame(be); mb.pack(fill="x", pady=4)
        ttk.Button(mb, text="Добавить", command=self._add_monster).pack(side="left", padx=2)
        ttk.Button(mb, text="Изменить", command=self._edit_monster).pack(side="left", padx=2)
        ttk.Button(mb, text="Дублировать", command=self._duplicate_monster).pack(side="left", padx=2)
        ttk.Button(mb, text="Удалить", command=self._del_monster).pack(side="left", padx=2)
        mb2 = ttk.Frame(be); mb2.pack(fill="x", pady=2)
        ttk.Button(mb2, text="На карту", command=self._monster_to_map).pack(side="left", padx=2)
        ttk.Button(mb2, text="В бой", command=self._monster_to_combat).pack(side="left", padx=2)

        # Combat
        cb = ttk.Frame(self.nb, padding=4)
        self.nb.add(cb, text="Бой")
        self.combat_list = tk.Listbox(cb, height=12)
        style_listbox(self.combat_list)
        self.combat_list.pack(fill="both", expand=True)
        self.combat_list.bind("<Double-Button-1>", lambda e: self._edit_combatant())
        ct = ttk.Frame(cb); ct.pack(fill="x", pady=4)
        ttk.Button(ct, text="Добавить", command=self._add_combatant).pack(side="left", padx=2)
        ttk.Button(ct, text="Изменить", command=self._edit_combatant).pack(side="left", padx=2)
        ttk.Button(ct, text="Удалить", command=self._del_combatant).pack(side="left", padx=2)
        ct2 = ttk.Frame(cb); ct2.pack(fill="x", pady=2)
        ttk.Button(ct2, text="Инициатива", command=self._roll_initiative).pack(side="left", padx=2)
        ttk.Button(ct2, text="Следующий ход", command=self._next_turn).pack(side="left", padx=2)

        # Dice
        dc = ttk.Frame(self.nb, padding=4)
        self.nb.add(dc, text="Броски")
        drow = ttk.Frame(dc); drow.pack(fill="x")
        self.dice_expr = tk.StringVar(value="1d20")
        ttk.Entry(drow, textvariable=self.dice_expr).pack(side="left", fill="x", expand=True)
        ttk.Button(drow, text="Бросить", command=self._roll_custom).pack(side="left", padx=4)
        dq = ttk.Frame(dc); dq.pack(fill="x", pady=4)
        for die in ["d4", "d6", "d8", "d10", "d12", "d20", "d100"]:
            ttk.Button(dq, text=die, width=5,
                       command=lambda d=die: self._quick_roll(d)).pack(side="left", padx=1)
        dq2 = ttk.Frame(dc); dq2.pack(fill="x", pady=2)
        ttk.Button(dq2, text="d20 (преим.)", command=self._roll_advantage).pack(side="left", padx=2)
        ttk.Button(dq2, text="d20 (помеха)", command=self._roll_disadvantage).pack(side="left", padx=2)
        dice_actions = ttk.Frame(dc)
        dice_actions.pack(fill="x", pady=(2, 4))
        ttk.Button(dice_actions, text="Очистить историю", command=self._clear_dice_log).pack(side="right")
        self.dice_list = tk.Text(dc, height=10, wrap="word", state="disabled",
                                 font=("Segoe UI", 12), padx=8, pady=6, spacing1=4)
        style_text(self.dice_list)
        self.dice_list.tag_configure("roll", font=("Segoe UI", 12))
        self.dice_list.tag_configure("result", font=("Segoe UI", 18, "bold"),
                                     foreground="#7ee787", spacing1=3, spacing3=5)
        self.dice_list.tag_configure("meta", font=("Segoe UI", 9), foreground=COL_MUTED)
        self.dice_list.pack(fill="both", expand=True, pady=4)

        # Music
        mu = ttk.Frame(self.nb, padding=4)
        self.nb.add(mu, text="Музыка")
        self.music_list = tk.Listbox(mu, height=7)
        style_listbox(self.music_list)
        self.music_list.pack(fill="both", expand=True)
        mb3 = ttk.Frame(mu); mb3.pack(fill="x", pady=4)
        ttk.Button(mb3, text="Добавить", command=self._add_track).pack(side="left", padx=2)
        ttk.Button(mb3, text="Удалить", command=self._del_track).pack(side="left", padx=2)
        mctl = ttk.Frame(mu); mctl.pack(fill="x", pady=2)
        ttk.Button(mctl, text="▶", command=self._music_play).pack(side="left", padx=2)
        ttk.Button(mctl, text="⏸", command=self._music_pause).pack(side="left", padx=2)
        ttk.Button(mctl, text="⏭", command=self._music_next).pack(side="left", padx=2)
        ttk.Label(mu, text="Громкость").pack(anchor="w", pady=(8, 0))
        self.music_vol = tk.DoubleVar(value=0.7)
        ttk.Scale(mu, from_=0, to=1, variable=self.music_vol,
                  command=lambda v: self._music_set_volume()).pack(fill="x")
        self.music_now = ttk.Label(mu, text="Сейчас: —", wraplength=360)
        self.music_now.pack(anchor="w", pady=4)

        # Notes: named private notes stored in the existing campaign notes field.
        no = ttk.Frame(self.nb, padding=4)
        self.nb.add(no, text="Заметки")
        note_bar = ttk.Frame(no)
        note_bar.pack(fill="x")
        ttk.Label(note_bar, text="Заметки мастера").pack(side="left")
        ttk.Button(note_bar, text="＋ Новая", command=self._add_note).pack(side="right", padx=2)
        ttk.Button(note_bar, text="Переименовать", command=self._rename_note).pack(side="right", padx=2)
        ttk.Button(note_bar, text="Удалить", command=self._delete_note).pack(side="right", padx=2)
        self.notes_list = tk.Listbox(no, height=5)
        style_listbox(self.notes_list)
        self.notes_list.pack(fill="x", pady=(5, 3))
        self.notes_list.bind("<<ListboxSelect>>", self._note_selected)
        self.notes_text = tk.Text(no, height=18, wrap="word", undo=True)
        style_text(self.notes_text)
        self.notes_text.pack(fill="both", expand=True, pady=4)
        self.notes_text.bind("<KeyRelease>", lambda e: self._save_notes())
        ttk.Button(no, text="Сохранить заметку", command=self._save_notes).pack(anchor="e")
        self._load_notes_data()

    def _build_selection_bar(self):
        self.selbar = ttk.Frame(self.root, padding=4)
        self.selbar.pack(side="bottom", fill="x")
        ttk.Label(self.selbar, text="Выбрано:").pack(side="left")
        self.sel_name = tk.StringVar(value="—")
        self.sel_name_entry = ttk.Entry(self.selbar, textvariable=self.sel_name, width=20)
        self.sel_name_entry.pack(side="left", padx=4)
        self.sel_name.trace_add("write", lambda *a: self._on_sel_rename())
        ttk.Label(self.selbar, text="Размер").pack(side="left", padx=(10, 0))
        self.sel_size = tk.Scale(self.selbar, from_=10, to=400, orient="horizontal",
                                 length=140, bg=COL_BG, fg=COL_TEXT,
                                 troughcolor=COL_FIELD, highlightthickness=0,
                                 command=lambda v: self._on_sel_size())
        self.sel_size.pack(side="left")
        ttk.Label(self.selbar, text="Поворот").pack(side="left", padx=(10, 0))
        self.sel_rot = tk.Scale(self.selbar, from_=0, to=360, orient="horizontal",
                                length=140, bg=COL_BG, fg=COL_TEXT,
                                troughcolor=COL_FIELD, highlightthickness=0,
                                command=lambda v: self._on_sel_rot())
        self.sel_rot.pack(side="left")
        self.sel_hidden = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.selbar, text="Скрыть", variable=self.sel_hidden,
                        command=self._on_sel_hidden).pack(side="left", padx=8)
        ttk.Button(self.selbar, text="Удалить",
                   command=self._delete_selected).pack(side="left", padx=8)
        self.selbar_label = ttk.Label(self.selbar, text="")
        self.selbar_label.pack(side="right", padx=6)

    def _build_status_bar(self):
        self.status = ttk.Label(self.root, anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(side="bottom", fill="x")

    # ------------------------------------------------------------- helpers
    def _url(self):
        return "http://%s:%d" % (lan_ip(), self.port)

    def _ui_alive(self):
        """Return False once Tk has started shutting down."""
        try:
            return bool(self.root.winfo_exists())
        except (tk.TclError, AttributeError):
            return False

    def _update_status(self):
        if not self._ui_alive():
            return
        try:
            self.status.config(
                text="Кампания: %s   |   Сайт игроков: %s   |   Ревизия: %d"
                     "   |   Второй экран — зеркало карты" % (
                         self.state.campaign_name, self._url(), self.state.revision))
            self.root.after(2000, self._update_status)
        except tk.TclError:
            # The main window may be closed while a scheduled callback is pending.
            return

    def _open_player_site(self):
        webbrowser.open("http://localhost:%d" % self.port)

    def changed(self, redraw=True, refresh=True):
        # A modal editor can finish its wait_window() as the main window is
        # already being torn down. Do not touch Tcl widgets after shutdown.
        if not self._ui_alive():
            return
        self.state.bump()
        if redraw:
            self.redraw()
        # Always rebuild the data lists: several actions used to pass
        # refresh=False and the combat/bestiary/character lists went stale.
        try:
            self.refresh_lists()
        except tk.TclError:
            return

    # ----------------------------------------------------------- viewport
    def _viewport(self):
        try:
            cw = max(1, self.canvas.winfo_width())
            ch = max(1, self.canvas.winfo_height())
        except tk.TclError:
            cw = ch = 1
        return {"x": self.pan_x, "y": self.pan_y,
                "w": cw / max(self.zoom, 0.001),
                "h": ch / max(self.zoom, 0.001)}

    def _clamp_pan(self):
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 10 or ch <= 10:
            return
        m = self.state.map
        vw = cw / self.zoom
        vh = ch / self.zoom
        if vw >= m.width:
            self.pan_x = (m.width - vw) / 2.0
        else:
            self.pan_x = max(0.0, min(self.pan_x, m.width - vw))
        if vh >= m.height:
            self.pan_y = (m.height - vh) / 2.0
        else:
            self.pan_y = max(0.0, min(self.pan_y, m.height - vh))

    def _publish_viewport(self, vp):
        key = (round(vp["x"], 1), round(vp["y"], 1),
               round(vp["w"], 1), round(vp["h"], 1))
        if key == getattr(self, "_vp_key", None):
            return
        self._vp_key = key
        with self.state.lock:
            self.state.viewport = dict(vp)
        self.state.bump()

    # ----------------------------------------------------------- rendering
    def redraw(self):
        if not self._ui_alive():
            return
        try:
            vp = self._viewport()
            self._render_viewport(self.canvas, vp, self.zoom, True, 0, 0)
            self._publish_viewport(vp)
            self._schedule_second_screen_sync(vp)
        except tk.TclError:
            # Ignore a redraw that races with closing the root window.
            return

    def _render_viewport(self, c, vp, scale, draw_ui, off_x, off_y):
        cw = max(1, c.winfo_width())
        ch = max(1, c.winfo_height())
        c.delete("all")
        c.create_rectangle(0, 0, cw, ch, fill=COL_CANVAS, outline="")
        m = self.state.map
        ix0 = max(0.0, vp["x"])
        iy0 = max(0.0, vp["y"])
        ix1 = min(float(m.width), vp["x"] + vp["w"])
        iy1 = min(float(m.height), vp["y"] + vp["h"])
        has_map = ix1 > ix0 and iy1 > iy0
        if has_map:
            sx = off_x + (ix0 - vp["x"]) * scale
            sy = off_y + (iy0 - vp["y"]) * scale
            sw = (ix1 - ix0) * scale
            sh = (iy1 - iy0) * scale
            if m.background:
                ph = self._bg_crop(m.background, ix0, iy0, ix1, iy1, scale)
                if ph:
                    c.create_image(sx, sy, anchor="nw", image=ph)
            else:
                c.create_rectangle(sx, sy, sx + sw, sy + sh, fill="#1b2027",
                                   outline="")
            if m.grid:
                gs = m.grid_size
                startx = math.floor(ix0 / gs) * gs
                for gx in range(int(startx), int(ix1) + 1, gs):
                    cx = off_x + (gx - vp["x"]) * scale
                    c.create_line(cx, sy, cx, sy + sh, fill="#2b3442")
                starty = math.floor(iy0 / gs) * gs
                for gy in range(int(starty), int(iy1) + 1, gs):
                    cy = off_y + (gy - vp["y"]) * scale
                    c.create_line(sx, cy, sx + sw, cy, fill="#2b3442")

        ox = off_x - vp["x"] * scale
        oy = off_y - vp["y"] * scale

        # layer 1: media / images
        for o in self.state.overlays:
            x = o.x * scale + ox
            y = o.y * scale + oy
            ww = o.w * scale
            hh = o.h * scale
            if o.kind == "image" and o.path:
                ph = self._photo(o.path, int(max(ww, hh)), rotation=o.rotation)
                if ph:
                    c.create_image(x + ww / 2, y + hh / 2, image=ph)
                    c.create_rectangle(x, y, x + ww, y + hh, outline="#556",
                                       dash=(4, 4))
                else:
                    self._placeholder(c, x, y, ww, hh, "IMG")
            else:
                self._placeholder(c, x, y, ww, hh,
                                  "▶ видео" if o.kind == "video" else "?")

        # layer 2: tokens
        for t in self.state.tokens:
            if t.hidden:
                continue
            cx = t.x * scale + ox
            cy = t.y * scale + oy
            s = t.size * scale
            if t.image:
                ph = self._photo(t.image, int(s), rotation=t.rotation)
                if ph:
                    c.create_image(cx, cy, image=ph)
                else:
                    self._draw_token_circle(c, cx, cy, s, t)
            else:
                self._draw_token_circle(c, cx, cy, s, t)
            if t.show_label and t.name:
                c.create_text(cx, cy + s / 2 + 9 * max(scale, 0.5), text=t.name,
                              fill="#e6e8ef",
                              font=("TkDefaultFont", int(10 * max(scale, 0.5))))

        # layer 3: drawings
        for d in self.state.drawings:
            pts = [(p[0] * scale + ox, p[1] * scale + oy) for p in d.points]
            if d.kind in ("freehand", "line"):
                if len(pts) >= 2:
                    c.create_line(*[v for p in pts for v in p], fill=d.color,
                                  width=max(1, int(d.width * scale)),
                                  capstyle="round", joinstyle="round")
            elif d.kind == "rect" and len(pts) >= 2:
                c.create_rectangle(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1],
                                   outline=d.color,
                                   width=max(1, int(d.width * scale)),
                                   fill=d.fill or "")
            elif d.kind == "oval" and len(pts) >= 2:
                c.create_oval(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1],
                              outline=d.color,
                              width=max(1, int(d.width * scale)),
                              fill=d.fill or "")

        if not draw_ui:
            return

        if self._current_shape:
            kind, pts = self._current_shape
            p = [(a * scale + ox, b * scale + oy) for a, b in pts]
            if kind in ("freehand", "line") and len(p) >= 2:
                c.create_line(*[v for q in p for v in q], fill=self.draw_color,
                              width=max(1, int(self.line_width.get() * scale)))
            elif kind == "rect" and len(p) >= 2:
                c.create_rectangle(p[0][0], p[0][1], p[-1][0], p[-1][1],
                                   outline=self.draw_color,
                                   width=max(1, int(self.line_width.get() * scale)),
                                   fill=(self.fill_color
                                         if self.fill_enabled.get() else ""))
            elif kind == "oval" and len(p) >= 2:
                c.create_oval(p[0][0], p[0][1], p[-1][0], p[-1][1],
                              outline=self.draw_color,
                              width=max(1, int(self.line_width.get() * scale)),
                              fill=(self.fill_color
                                    if self.fill_enabled.get() else ""))

        if self.selected is not None:
            self._draw_selection(c, scale, ox, oy)

    def _bg_crop(self, filename, ix0, iy0, ix1, iy1, scale):
        key = (filename, round(ix0, 1), round(iy0, 1),
               round(ix1, 1), round(iy1, 1), round(scale, 3))
        if key in self._bg_crop_cache:
            return self._bg_crop_cache[key]
        if len(self._bg_crop_cache) > 8:
            self._bg_crop_cache.clear()
        m = self.state.map
        img = self._source_image_cache.get(filename)
        if img is None:
            path = os.path.join(UPLOADS_DIR, filename)
            try:
                with Image.open(path) as source:
                    img = source.convert("RGB")
                if len(self._source_image_cache) >= 4:
                    self._source_image_cache.pop(next(iter(self._source_image_cache)))
                self._source_image_cache[filename] = img
            except Exception:  # noqa: BLE001
                return None
        iw, ih = img.size
        sx0 = int(ix0 / m.width * iw)
        sy0 = int(iy0 / m.height * ih)
        sx1 = max(sx0 + 1, int(ix1 / m.width * iw))
        sy1 = max(sy0 + 1, int(iy1 / m.height * ih))
        crop = img.crop((sx0, sy0, sx1, sy1))
        out_w = max(1, int(round((ix1 - ix0) * scale)))
        out_h = max(1, int(round((iy1 - iy0) * scale)))
        resampling = getattr(Image, "Resampling", Image)
        crop = crop.resize((out_w, out_h), resampling.BILINEAR)
        try:
            ph = ImageTk.PhotoImage(crop)
        except Exception:  # noqa: BLE001
            return None
        self._bg_crop_cache[key] = ph
        return ph

    def _placeholder(self, c, x, y, w, h, label):
        c.create_rectangle(x, y, x + w, y + h, fill="#26303b",
                           outline="#7fb3ff", width=2, dash=(4, 4))
        c.create_text(x + w / 2, y + h / 2, text=label, fill="#9db4d0")

    def _draw_token_circle(self, c, cx, cy, s, t):
        r = s / 2
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=t.color,
                      outline="#ffffff", width=2)
        c.create_text(cx, cy, text=t.name[:1].upper() if t.name else "?",
                      fill="#fff", font=("TkDefaultFont", int(max(1, r * 0.8))))

    def _draw_selection(self, c, scale, ox, oy):
        o = self.selected
        if isinstance(o, Token):
            x, y = o.x * scale + ox, o.y * scale + oy
            s = o.size * scale
            c.create_rectangle(x - s / 2, y - s / 2, x + s / 2, y + s / 2,
                               outline="#ffd34d", width=2, dash=(4, 3))
        elif isinstance(o, Overlay):
            x, y = o.x * scale + ox, o.y * scale + oy
            w, h = o.w * scale, o.h * scale
            c.create_rectangle(x, y, x + w, y + h, outline="#ffd34d",
                               width=2, dash=(4, 3))
        elif isinstance(o, Drawing) and o.points:
            xs = [p[0] * scale + ox for p in o.points]
            ys = [p[1] * scale + oy for p in o.points]
            c.create_rectangle(min(xs), min(ys), max(xs), max(ys),
                               outline="#ffd34d", width=1, dash=(2, 2))

    def _photo(self, filename, box, rotation=0.0):
        key = (filename, int(box), round(rotation))
        if key not in self._photo_cache:
            if len(self._photo_cache) > 300:
                self._photo_cache.clear()
            path = os.path.join(UPLOADS_DIR, filename)
            try:
                self._photo_cache[key] = make_photo(path, box, rotation)
            except Exception:  # noqa: BLE001
                return None
        return self._photo_cache[key]

    # ------------------------------------------------------------ tools
    def set_tool(self, key):
        self.tool = key
        for k, b in self._tool_buttons.items():
            b.config(bg=COL_ACCENT if k == key else COL_PANEL,
                     fg="#ffffff" if k == key else COL_TEXT)
        if key in ("token", "token_img", "image", "video"):
            self.selected = None
            self._sync_selection_bar()

    def zoom_by(self, factor, cx=None, cy=None):
        if cx is None:
            cx = self.canvas.winfo_width() / 2
        if cy is None:
            cy = self.canvas.winfo_height() / 2
        mx = self.pan_x + cx / self.zoom
        my = self.pan_y + cy / self.zoom
        new = max(0.05, min(8.0, self.zoom * factor))
        self.zoom = new
        self.pan_x = mx - cx / new
        self.pan_y = my - cy / new
        self._clamp_pan()
        self.redraw()

    def zoom_fit(self):
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        m = self.state.map
        self.zoom = max(0.02, min(cw / m.width, ch / m.height))
        self.pan_x = (m.width - cw / self.zoom) / 2.0
        self.pan_y = (m.height - ch / self.zoom) / 2.0
        self.redraw()

    def _toggle_grid(self):
        with self.state.lock:
            self.state.map.grid = not self.state.map.grid
        self.changed(refresh=False)

    def _set_background(self):
        path = filedialog.askopenfilename(
            title="Фон карты",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"),
                       ("Все файлы", "*.*")])
        if not path:
            return
        name = import_media(path)
        if not name:
            return
        with self.state.lock:
            img = Image.open(path)
            w, h = img.size
            self._push_undo()
            self.state.map.background = name
            self.state.map.width = max(200, w)
            self.state.map.height = max(200, h)
        self._bg_crop_cache.clear()
        self._source_image_cache.clear()
        self.changed(refresh=False)

    # ------------------------------------------------------------ scenes
    def _scene_selected(self, event=None):
        idx = self.scene_combo.current()
        if idx >= 0:
            with self.state.lock:
                self.state.set_active_scene(idx)
            self.selected = None
            self._sync_selection_bar()
            self.zoom_fit()
            self.refresh_lists()

    def _scene_add(self):
        name = simpledialog.askstring("Новая сцена", "Название сцены:",
                                      parent=self.root)
        if not name:
            return
        with self.state.lock:
            self.state.add_scene(name)
        self.zoom_fit()
        self.refresh_lists()

    def _scene_rename(self):
        sc = self.state.scenes[self.state.active_scene_index]
        name = simpledialog.askstring("Переименовать", "Название сцены:",
                                      initialvalue=sc.name, parent=self.root)
        if name:
            with self.state.lock:
                sc.name = name
            self.changed(refresh=False)

    def _scene_delete(self):
        if len(self.state.scenes) <= 1:
            messagebox.showinfo("Сцены", "Должна остаться хотя бы одна сцена.",
                                parent=self.root)
            return
        if not messagebox.askyesno("Удалить сцену",
                                   "Удалить сцену «%s»?" % self.state.scene.name,
                                   parent=self.root):
            return
        with self.state.lock:
            self.state.delete_scene(self.state.active_scene_index)
        self.selected = None
        self._sync_selection_bar()
        self.zoom_fit()
        self.refresh_lists()

    def refresh_scenes(self):
        names = [s.name for s in self.state.scenes]
        self.scene_combo["values"] = names
        if names:
            idx = max(0, min(self.state.active_scene_index, len(names) - 1))
            self.scene_combo.current(idx)

    # ------------------------------------------------------- canvas events
    def _bind_canvas(self):
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-2>", self._pan_press)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Control-MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Delete>", lambda e: self._delete_selected())
        self.canvas.bind("<Control-z>", lambda e: self._undo())
        self.canvas.bind("<Control-y>", lambda e: self._redo())
        self.canvas.bind("<Control-Z>", lambda e: self._redo())

    def _on_configure(self, event):
        if self._resize_pending:
            return
        self._resize_pending = True
        self.root.after(60, self._do_resize)

    def _do_resize(self):
        self._resize_pending = False
        self._clamp_pan()
        self.redraw()

    def _on_wheel(self, event):
        self.zoom_by(1.1 if event.delta > 0 else 1 / 1.1, event.x, event.y)
        return "break"

    def _lxy(self, event):
        return (self.pan_x + event.x / self.zoom,
                self.pan_y + event.y / self.zoom)

    def _pan_press(self, event):
        self._pan_last = (event.x, event.y)

    def _pan_move(self, event):
        if self._pan_last is None:
            return
        dx = event.x - self._pan_last[0]
        dy = event.y - self._pan_last[1]
        self._pan_last = (event.x, event.y)
        self.pan_x -= dx / self.zoom
        self.pan_y -= dy / self.zoom
        self._clamp_pan()
        self.redraw()

    def _on_press(self, event):
        self.canvas.focus_set()
        if self.tool == "hand":
            self._pan_last = (event.x, event.y)
            return
        x, y = self._lxy(event)
        if self.tool == "select":
            obj = self._hit_test(x, y)
            self.selected = obj
            self._sync_selection_bar()
            if isinstance(obj, Token):
                self._select_character_for_token(obj)
            if isinstance(obj, (Token, Overlay)):
                self._push_undo()
                self._drag_off = (x - obj.x, y - obj.y)
            self.redraw()
        elif self.tool == "brush":
            self._push_undo()
            self._current_shape = ("freehand", [[x, y]])
        elif self.tool in ("line", "rect", "oval"):
            self._push_undo()
            self._current_shape = (self.tool, [[x, y], [x, y]])
        elif self.tool == "erase":
            self._push_undo()
            self._erasing = True
            self._erase_at(x, y)
        elif self.tool == "token":
            self._push_undo()
            with self.state.lock:
                t = Token("Токен", x, y, 60, self.draw_color)
                if self._pending_token_image:
                    t.image = self._pending_token_image
                    t.name = os.path.splitext(self._pending_token_image)[0]
                self.state.tokens.append(t)
            self.changed(refresh=False)
        elif self.tool == "token_img":
            self._choose_token_image()
        elif self.tool == "image":
            self._choose_overlay("image")
        elif self.tool == "video":
            self._choose_overlay("video")

    def _on_drag(self, event):
        if self.tool == "hand" and self._pan_last is not None:
            self._pan_move(event)
            return
        x, y = self._lxy(event)
        if self._erasing:
            self._erase_at(x, y)
            return
        if self.tool == "select" and isinstance(self.selected, (Token, Overlay)):
            self.selected.x = x - self._drag_off[0]
            self.selected.y = y - self._drag_off[1]
            self.state.bump()
            self.redraw()
        elif self._current_shape:
            kind, pts = self._current_shape
            if kind == "freehand":
                pts.append([x, y])
            else:
                pts[1] = [x, y]
            self.redraw()

    def _on_release(self, event):
        if self.tool == "hand":
            self._pan_last = None
            return
        if self._erasing:
            self._erasing = False
            self.state.bump()
            self.redraw()
            return
        if self._current_shape:
            kind, pts = self._current_shape
            fill = ""
            if kind in ("rect", "oval") and self.fill_enabled.get():
                fill = self.fill_color
            with self.state.lock:
                d = Drawing(kind, self.draw_color, self.line_width.get(), fill)
                d.points = pts
                self.state.drawings.append(d)
            self._current_shape = None
            self.changed(refresh=False)

    def _on_right_click(self, event):
        if self.tool in ("brush", "rect", "oval"):
            self._open_brush_panel(event)
            return
        if self.tool in ("token", "token_img", "image", "video"):
            self.set_tool("select")
            return
        x, y = self._lxy(event)
        obj = self._hit_test(x, y)
        if isinstance(obj, Token):
            self._select_character_for_token(obj)
            self._open_token_menu(event, obj)
        else:
            self.selected = obj
            self._sync_selection_bar()
            self.redraw()

    def _hit_test(self, x, y):
        for d in reversed(self.state.drawings):
            if self._drawing_near(d, x, y, max(8, d.width)):
                return d
        for t in reversed(self.state.tokens):
            r = t.size / 2
            if not t.hidden and abs(x - t.x) <= r and abs(y - t.y) <= r:
                return t
        for o in reversed(self.state.overlays):
            if o.x <= x <= o.x + o.w and o.y <= y <= o.y + o.h:
                return o
        return None

    # ------------------------------------------------------------ eraser
    def _drawing_near(self, d, x, y, r):
        if d.kind in ("freehand", "line"):
            for p in d.points:
                if (p[0] - x) ** 2 + (p[1] - y) ** 2 <= r * r:
                    return True
            return False
        if len(d.points) >= 2:
            x0, y0 = d.points[0]
            x1, y1 = d.points[-1]
            return (min(x0, x1) - r <= x <= max(x0, x1) + r and
                    min(y0, y1) - r <= y <= max(y0, y1) + r)
        return False

    def _erase_at(self, x, y):
        r = 16
        removed = False
        with self.state.lock:
            for d in list(self.state.drawings):
                if self._drawing_near(d, x, y, r):
                    self.state.drawings.remove(d)
                    removed = True
            for o in list(self.state.overlays):
                if o.x - r <= x <= o.x + o.w + r and o.y - r <= y <= o.y + o.h + r:
                    self.state.overlays.remove(o)
                    removed = True
        if removed:
            self.redraw()

    # ------------------------------------------------------ brush panel
    def _open_brush_panel(self, event):
        if self._token_menu is not None and self._token_menu.winfo_exists():
            self._token_menu.destroy()
        panel = tk.Toplevel(self.root)
        self._token_menu = panel
        panel.overrideredirect(True)
        panel.geometry("+%d+%d" % (event.x_root, event.y_root))
        panel.configure(bg=COL_BORDER)
        frame = tk.Frame(panel, bg=COL_PANEL, padx=8, pady=8)
        frame.pack(padx=1, pady=1)
        tk.Label(frame, text="Цвет", bg=COL_PANEL, fg=COL_TEXT).pack(anchor="w")
        img = color_wheel_image(150)
        photo = ImageTk.PhotoImage(img)
        wheel = tk.Canvas(frame, width=150, height=150, bg=COL_PANEL,
                          highlightthickness=0)
        wheel.pack()
        wheel.create_image(0, 0, anchor="nw", image=photo)
        wheel.image = photo

        def pick(ev):
            dx = ev.x - 75
            dy = ev.y - 75
            r = math.hypot(dx, dy)
            if r > 74:
                return
            h = (math.degrees(math.atan2(dy, dx)) + 360) % 360
            s = r / 74.0
            rr, gg, bb = colorsys.hsv_to_rgb(h / 360.0, s, 1.0)
            col = "#%02x%02x%02x" % (int(rr * 255), int(gg * 255), int(bb * 255))
            self.draw_color = col
            wheel.delete("mark")
            wheel.create_oval(ev.x - 6, ev.y - 6, ev.x + 6, ev.y + 6,
                              outline="#ffffff", width=2, tags="mark")

        wheel.bind("<Button-1>", pick)
        wheel.create_oval(68, 68, 82, 82, outline="#ffffff", width=1)
        wheel.create_oval(60, 60, 90, 90, outline="#ffffff", width=1, tags="mark")

        tk.Label(frame, text="Толщина", bg=COL_PANEL, fg=COL_TEXT).pack(anchor="w", pady=(6, 0))
        size = tk.Scale(frame, from_=1, to=40, orient="horizontal", length=210,
                        bg=COL_PANEL, fg=COL_TEXT, troughcolor=COL_FIELD,
                        highlightthickness=0,
                        command=lambda v: self.line_width.set(int(float(v))))
        size.set(self.line_width.get())
        size.pack()

        tk.Label(frame, text="Заливка (прямоугольник/овал)",
                 bg=COL_PANEL, fg=COL_TEXT).pack(anchor="w", pady=(6, 0))
        fill_row = tk.Frame(frame, bg=COL_PANEL)
        fill_row.pack(anchor="w")
        swatch = tk.Canvas(fill_row, width=22, height=22,
                           bg=self.fill_color, highlightthickness=1)
        swatch.pack(side="left")

        def pick_fill():
            c = colorchooser.askcolor(color=self.fill_color)
            if c and c[1]:
                self.fill_color = c[1]
                swatch.config(bg=c[1])
        tk.Button(fill_row, text="Цвет…", command=pick_fill,
                  bg=COL_PANEL, fg=COL_TEXT, relief="flat").pack(side="left", padx=4)
        tk.Checkbutton(fill_row, text="Заливать", variable=self.fill_enabled,
                       bg=COL_PANEL, fg=COL_TEXT, selectcolor=COL_FIELD,
                       activebackground=COL_PANEL,
                       activeforeground=COL_TEXT).pack(side="left", padx=4)

        row = tk.Frame(frame, bg=COL_PANEL)
        row.pack(fill="x", pady=(8, 0))
        tk.Button(row, text="Ластик", command=lambda: (panel.destroy(),
                                                       self.set_tool("erase")),
                  bg=COL_PANEL, fg=COL_TEXT, relief="flat").pack(side="left")
        tk.Button(row, text="Закрыть", command=panel.destroy,
                  bg=COL_PANEL, fg=COL_TEXT, relief="flat").pack(side="right")
        panel.bind("<Escape>", lambda e: panel.destroy())
        panel.focus_set()

    # ------------------------------------------------------ token/overlay add
    def _choose_token_image(self):
        path = filedialog.askopenfilename(
            title="Изображение токена",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp *.gif"),
                       ("Все файлы", "*.*")])
        if path:
            self._pending_token_image = import_media(path)
            if self._pending_token_image:
                self.set_tool("token")

    def _choose_overlay(self, kind):
        if kind == "image":
            path = filedialog.askopenfilename(
                title="Картинка",
                filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp *.gif"),
                           ("Все файлы", "*.*")])
            name = import_media(path) if path else None
        else:
            name = simpledialog.askstring("Видео",
                                          "URL видео или путь к файлу (mp4/webm):",
                                          parent=self.root)
            if name and os.path.exists(name):
                name = import_media(name)
        if not name:
            return
        self._push_undo()
        m = self.state.map
        o = Overlay(kind, name, m.width / 2 - 100, m.height / 2 - 75, 200, 150)
        with self.state.lock:
            self.state.overlays.append(o)
        self.selected = o
        self._sync_selection_bar()
        self.set_tool("select")
        self.changed(refresh=False)

    # ------------------------------------------------- selection bar sync
    def _sync_selection_bar(self):
        o = self.selected
        if o is None:
            self.sel_name.set("—")
            self.sel_size.set(60)
            self.sel_rot.set(0)
            self.sel_hidden.set(False)
            self.selbar_label.config(text="")
            self._selbar_enable(False)
            return
        if isinstance(o, Drawing):
            self.sel_name.set("")
            self.sel_size.set(0)
            self.sel_rot.set(0)
            self.sel_hidden.set(False)
            self.selbar_label.config(text="Рисунок")
            self._selbar_enable(False)
            return
        self._selbar_enable(True)
        self.sel_name.set(getattr(o, "name", ""))
        size = o.size if isinstance(o, Token) else o.w
        self.sel_size.set(int(size))
        self.sel_rot.set(int(o.rotation))
        self.sel_hidden.set(bool(getattr(o, "hidden", False)))
        self.selbar_label.config(
            text="Токен" if isinstance(o, Token) else
            ("Картинка" if getattr(o, "kind", "") == "image" else "Видео"))

    def _selbar_enable(self, on):
        st = "normal" if on else "disabled"
        self.sel_name_entry.config(state=st)
        self.sel_size.config(state=st)
        self.sel_rot.config(state=st)

    def _on_sel_rename(self):
        o = self.selected
        if o is not None and not isinstance(o, Drawing):
            o.name = self.sel_name.get()
            self.state.bump()
            self.redraw()

    def _on_sel_size(self):
        o = self.selected
        if o is not None and not isinstance(o, Drawing):
            v = int(float(self.sel_size.get()))
            if isinstance(o, Token):
                o.size = v
            else:
                o.w = v
                o.h = v * 0.75
            self.state.bump()
            self.redraw()

    def _on_sel_rot(self):
        o = self.selected
        if o is not None and not isinstance(o, Drawing):
            o.rotation = float(self.sel_rot.get())
            self.state.bump()
            self.redraw()

    def _on_sel_hidden(self):
        o = self.selected
        if isinstance(o, Token):
            o.hidden = self.sel_hidden.get()
            self.state.bump()
            self.redraw()

    def _delete_selected(self):
        o = self.selected
        if o is None:
            return
        self._push_undo()
        with self.state.lock:
            if isinstance(o, Token) and o in self.state.tokens:
                self.state.tokens.remove(o)
            elif isinstance(o, Overlay) and o in self.state.overlays:
                self.state.overlays.remove(o)
            elif isinstance(o, Drawing) and o in self.state.drawings:
                self.state.drawings.remove(o)
        self.selected = None
        self._sync_selection_bar()
        self.changed(refresh=False)

    # ------------------------------------------------------------ undo/redo
    def _push_undo(self):
        self._undo_stack.append(self.state.scene.to_dict())
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _restore_scene(self, data):
        with self.state.lock:
            sc = Scene.from_dict(data)
            self.state.scenes[self.state.active_scene_index] = sc
        self.selected = None
        self._sync_selection_bar()
        self.state.bump()
        self.redraw()

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self.state.scene.to_dict())
        self._restore_scene(self._undo_stack.pop())

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self.state.scene.to_dict())
        self._restore_scene(self._redo_stack.pop())

    # ------------------------------------------------------- token menu
    def _open_token_menu(self, event, token):
        self.selected = token
        self._sync_selection_bar()
        self.redraw()
        if self._token_menu is not None and self._token_menu.winfo_exists():
            self._token_menu.destroy()
        menu = tk.Toplevel(self.root)
        self._token_menu = menu
        menu.overrideredirect(True)
        menu.geometry("+%d+%d" % (event.x_root, event.y_root))
        menu.configure(bg=COL_BORDER)
        frame = tk.Frame(menu, bg=COL_PANEL, padx=8, pady=8)
        frame.pack(padx=1, pady=1)
        tk.Label(frame, text=token.name or "Токен", bg=COL_PANEL, fg=COL_TEXT,
                 font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        tk.Label(frame, text="Угол", bg=COL_PANEL, fg=COL_MUTED).pack(anchor="w")
        rot = tk.Scale(frame, from_=0, to=360, orient="horizontal", length=230,
                       bg=COL_PANEL, fg=COL_TEXT, troughcolor=COL_FIELD,
                       highlightthickness=0,
                       command=lambda v: self._menu_rot(token, float(v)))
        rot.set(token.rotation)
        rot.pack()
        tk.Label(frame, text="Размер", bg=COL_PANEL, fg=COL_MUTED).pack(anchor="w")
        size = tk.Scale(frame, from_=10, to=400, orient="horizontal", length=230,
                        bg=COL_PANEL, fg=COL_TEXT, troughcolor=COL_FIELD,
                        highlightthickness=0,
                        command=lambda v: self._menu_size(token, float(v)))
        size.set(token.size)
        size.pack()

        row = tk.Frame(frame, bg=COL_PANEL)
        row.pack(fill="x", pady=(6, 0))
        for text, cmd in [("Статблок", lambda: self._linked_statblock(token)),
                          ("Свойства", lambda: self._token_properties(token)),
                          ("Дублировать", lambda: self._duplicate_token(token))]:
            tk.Button(row, text=text, command=cmd, bg=COL_PANEL, fg=COL_TEXT,
                      relief="flat").pack(side="left", padx=1)
        row2 = tk.Frame(frame, bg=COL_PANEL)
        row2.pack(fill="x", pady=(4, 0))
        for text, cmd in [("Скрыть/Показать", lambda: self._toggle_token_hidden(token)),
                          ("Удалить", lambda: self._delete_token(token)),
                          ("Закрыть", lambda: menu.destroy())]:
            tk.Button(row2, text=text, command=cmd, bg=COL_PANEL, fg=COL_TEXT,
                      relief="flat").pack(side="left", padx=1)
        menu.bind("<Escape>", lambda e: menu.destroy())
        menu.focus_set()

    def _menu_rot(self, token, v):
        token.rotation = v
        self.state.bump()
        self.redraw()

    def _menu_size(self, token, v):
        token.size = v
        self.state.bump()
        self.redraw()

    def _linked_statblock(self, token):
        if token.linked_kind == "monster":
            idx = next((i for i, x in enumerate(self.state.monsters)
                        if x.id == token.linked_id), None)
            if idx is not None:
                e = MonsterEditor(self.root, self.state.monsters[idx])
                self.root.wait_window(e)
                if e.result:
                    self.state.monsters[idx] = e.result
                self.changed(refresh=False)
                return
        elif token.linked_kind == "character":
            idx = next((i for i, x in enumerate(self.state.characters)
                        if x.id == token.linked_id), None)
            if idx is not None:
                e = CharacterEditor(self.root, self.state.characters[idx])
                self.root.wait_window(e)
                if e.result:
                    self.state.characters[idx] = e.result
                self.changed(refresh=False)
                return
        messagebox.showinfo("Статблок", "У токена нет связанного статблока.",
                            parent=self.root)

    def _token_properties(self, token):
        d = tk.Toplevel(self.root)
        d.title("Свойства токена")
        d.configure(bg=COL_BG)
        name = tk.StringVar(value=token.name)
        hp = tk.IntVar(value=token.hp if token.hp is not None else 0)
        mx = tk.IntVar(value=token.max_hp if token.max_hp is not None else 0)
        img = tk.StringVar(value=token.image or "")
        color = tk.StringVar(value=token.color)
        ttk.Label(d, text="Имя").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(d, textvariable=name, width=28).grid(row=0, column=1)
        ttk.Label(d, text="ХП").grid(row=1, column=0, sticky="e", padx=4)
        ttk.Spinbox(d, from_=0, to=9999, textvariable=hp, width=6).grid(row=1, column=1, sticky="w")
        ttk.Label(d, text="Макс. ХП").grid(row=2, column=0, sticky="e", padx=4)
        ttk.Spinbox(d, from_=0, to=9999, textvariable=mx, width=6).grid(row=2, column=1, sticky="w")
        ttk.Label(d, text="Картинка").grid(row=3, column=0, sticky="e", padx=4)
        ttk.Entry(d, textvariable=img, width=22).grid(row=3, column=1, sticky="w")
        ttk.Label(d, text="Цвет").grid(row=4, column=0, sticky="e", padx=4)
        ttk.Entry(d, textvariable=color, width=12).grid(row=4, column=1, sticky="w")

        def pick():
            path = filedialog.askopenfilename(
                title="Картинка токена",
                filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp *.gif"),
                           ("Все файлы", "*.*")])
            if path:
                n = import_media(path)
                if n:
                    img.set(n)
        ttk.Button(d, text="Выбрать…", command=pick).grid(row=3, column=2, padx=2)

        def ok():
            token.name = name.get()
            token.hp = int(hp.get()) or None
            token.max_hp = int(mx.get()) or None
            token.image = img.get() or None
            token.color = color.get() or token.color
            self.state.bump()
            self.redraw()
            self.refresh_lists()
            d.destroy()
        ttk.Button(d, text="ОК", command=ok).grid(row=5, column=1, sticky="e", pady=6)
        d.grab_set()

    def _duplicate_token(self, token):
        self._push_undo()
        with self.state.lock:
            t = Token.from_dict(token.to_dict())
            t.id = uid("tok")
            t.x += 30
            t.y += 30
            self.state.tokens.append(t)
        self.changed(refresh=False)

    def _toggle_token_hidden(self, token):
        token.hidden = not token.hidden
        self.state.bump()
        self.redraw()

    def _delete_token(self, token):
        self._push_undo()
        with self.state.lock:
            if token in self.state.tokens:
                self.state.tokens.remove(token)
        self.selected = None
        self._sync_selection_bar()
        self.changed(refresh=False)

    # ------------------------------------------------------------ characters
    def refresh_lists(self):
        char_sel = self.char_list.curselection()
        combat_sel = self.combat_list.curselection()
        music_sel = self.music_list.curselection()
        self.refresh_scenes()
        self.char_list.delete(0, "end")
        for c in self.state.characters:
            self.char_list.insert("end", "%s (%s, %s) — %d/%d ХП" % (
                c.name, c.char_class or "—", c.level, c.hp, c.max_hp))
        if char_sel and char_sel[0] < len(self.state.characters):
            self.char_list.selection_set(char_sel[0])
        self._refresh_bestiary_list()
        self.combat_list.delete(0, "end")
        for c in self.state.ordered_combatants():
            mark = "▶ " if c.active else "  "
            self.combat_list.insert("end", "%s%s — иниц. %d (%d/%d)" % (
                mark, c.name, c.initiative, c.hp, c.max_hp))
        if combat_sel and combat_sel[0] < len(self.state.combatants):
            self.combat_list.selection_set(combat_sel[0])
        self.dice_list.configure(state="normal")
        self.dice_list.delete("1.0", "end")
        for d in self.state.dice_log:
            self.dice_list.insert("end", "%s  ·  %s\n" % (d.get("expression", "Бросок"), d.get("detail", "")), "roll")
            self.dice_list.insert("end", "  %s\n" % d.get("total", "—"), "result")
            self.dice_list.insert("end", "  %s\n\n" % d.get("ts", ""), "meta")
        self.dice_list.configure(state="disabled")
        self.music_list.delete(0, "end")
        for i, t in enumerate(self.state.music["tracks"]):
            cur = "▶ " if self.state.music["current"] == i else "  "
            self.music_list.insert("end", "%s%s" % (cur, t["name"]))
        if music_sel and music_sel[0] < len(self.state.music["tracks"]):
            self.music_list.selection_set(music_sel[0])
        cur = self.state.music["current"]
        if cur is not None and 0 <= cur < len(self.state.music["tracks"]):
            self.music_now.config(text="Сейчас: %s" % self.state.music["tracks"][cur]["name"])
        else:
            self.music_now.config(text="Сейчас: —")
        self._refresh_quick()

    def _refresh_quick(self):
        for w in self.quick_body.winfo_children():
            w.destroy()
        sel = self.char_list.curselection()
        if not sel or sel[0] >= len(self.state.characters):
            ttk.Label(self.quick_body, text="Выберите персонажа").pack(anchor="w")
            return
        c = self.state.characters[sel[0]]
        hp_row = ttk.Frame(self.quick_body)
        hp_row.pack(fill="x", pady=2)
        ttk.Label(hp_row, text="%s: %d/%d ХП" % (c.name, c.hp, c.max_hp)).pack(side="left")
        ttk.Button(hp_row, text="−", width=3,
                   command=lambda: self._quick_hp(c, -1)).pack(side="right", padx=1)
        ttk.Button(hp_row, text="+", width=3,
                   command=lambda: self._quick_hp(c, 1)).pack(side="right", padx=1)
        for r in c.resources:
            row = ttk.Frame(self.quick_body)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=r.label).pack(side="left")
            ttk.Label(row, text="%d/%d" % (r.max - r.used, r.max)).pack(side="right")
            ttk.Button(row, text="−", width=2,
                       command=lambda rr=r: self._quick_res(rr, 1)).pack(side="right", padx=1)
            ttk.Button(row, text="+", width=2,
                       command=lambda rr=r: self._quick_res(rr, -1)).pack(side="right", padx=1)

    def _quick_hp(self, c, delta):
        c.hp = max(0, min(c.max_hp if c.max_hp else 9999, c.hp + delta))
        for cb in self.state.combatants:
            if cb.linked_kind == "character" and cb.linked_id == c.id:
                cb.hp = c.hp
        self.state.bump()
        self.refresh_lists()

    def _quick_res(self, r, delta):
        r.used = max(0, min(r.max, r.used + delta))
        self.state.bump()
        self.refresh_lists()

    def _select_character_for_token(self, token):
        """Clicking a player's token selects that character in quick edit."""
        if not isinstance(token, Token):
            return
        idx = None
        if token.linked_kind == "character" and token.linked_id:
            for i, c in enumerate(self.state.characters):
                if c.id == token.linked_id:
                    idx = i
                    break
        if idx is None:  # fallback: match by token name
            for i, c in enumerate(self.state.characters):
                if c.name == token.name:
                    idx = i
                    break
        if idx is None:
            return
        self.nb.select(0)  # вкладка «Персонажи»
        self.char_list.selection_clear(0, "end")
        self.char_list.selection_set(idx)
        self.char_list.activate(idx)
        self.char_list.see(idx)
        self._refresh_quick()

    def _add_character(self):
        e = CharacterEditor(self.root)
        self.root.wait_window(e)
        if e.result:
            with self.state.lock:
                self.state.characters.append(e.result)
            self.changed(refresh=False)

    def _edit_character(self):
        sel = self.char_list.curselection()
        if not sel:
            return
        idx = sel[0]
        e = CharacterEditor(self.root, self.state.characters[idx])
        self.root.wait_window(e)
        if e.result:
            self.state.characters[idx] = e.result
        self.changed(refresh=False)

    def _del_character(self):
        sel = self.char_list.curselection()
        if not sel:
            return
        with self.state.lock:
            del self.state.characters[sel[0]]
        self.changed(refresh=False)

    def _add_to_map(self, name, token_image, hp, max_hp, linked_id, kind):
        cx = self.pan_x + (self.canvas.winfo_width() / 2) / self.zoom
        cy = self.pan_y + (self.canvas.winfo_height() / 2) / self.zoom
        self._push_undo()
        t = Token(name, cx, cy, 60, "#4f8fda")
        t.image = token_image
        t.hp = hp
        t.max_hp = max_hp
        t.linked_id = linked_id
        t.linked_kind = kind
        with self.state.lock:
            self.state.tokens.append(t)
        self.selected = t
        self._sync_selection_bar()
        self.set_tool("select")
        self.changed(refresh=False)

    def _character_to_map(self):
        sel = self.char_list.curselection()
        if not sel:
            return
        c = self.state.characters[sel[0]]
        self._add_to_map(c.name, c.token_image, c.hp, c.max_hp, c.id, "character")

    def _character_to_combat(self):
        sel = self.char_list.curselection()
        if not sel:
            return
        c = self.state.characters[sel[0]]
        init = simpledialog.askinteger("Инициатива", "Инициатива для %s:" % c.name,
                                       parent=self.root, initialvalue=0)
        if init is None:
            return
        with self.state.lock:
            cb = Combatant(c.name, init, c.hp, c.max_hp, "player")
            cb.linked_id, cb.linked_kind = c.id, "character"
            self.state.combatants.append(cb)
        self.changed(refresh=False)

    def _player_link(self):
        sel = self.char_list.curselection()
        if not sel:
            return
        c = self.state.characters[sel[0]]
        url = "http://%s:%d/p/%s" % (lan_ip(), self.port, c.player_token)
        d = tk.Toplevel(self.root)
        d.title("Ссылка игрока")
        d.configure(bg=COL_BG)
        ttk.Label(d, text="Отправьте эту ссылку игроку «%s»:" % c.name).pack(padx=10, pady=(10, 4))
        var = tk.StringVar(value=url)
        ttk.Entry(d, textvariable=var, width=52).pack(padx=10, pady=4)
        row = ttk.Frame(d)
        row.pack(pady=8)

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
        ttk.Button(row, text="Копировать", command=copy).pack(side="left", padx=4)
        ttk.Button(row, text="Открыть",
                   command=lambda: webbrowser.open(url)).pack(side="left", padx=4)
        ttk.Button(row, text="Закрыть", command=d.destroy).pack(side="left", padx=4)
        d.grab_set()

    # ------------------------------------------------------------- bestiary
    def _refresh_bestiary_list(self):
        """Rebuild the visible monster list according to the search query."""
        if not hasattr(self, "mon_list"):
            return
        try:
            selected = self.mon_list.curselection()
            selected_real = (self._filtered_monster_indices[selected[0]]
                             if selected and selected[0] < len(self._filtered_monster_indices)
                             else None)
            query = (self.monster_search_var.get().strip().casefold()
                     if hasattr(self, "monster_search_var") else "")
            self.mon_list.delete(0, "end")
            self._filtered_monster_indices = []
            for real_idx, monster in enumerate(self.state.monsters):
                searchable = " ".join(str(value or "") for value in (
                    monster.name, monster.monster_type, monster.size,
                    monster.alignment, monster.cr, monster.ac, monster.hp,
                    monster.speed)).casefold()
                if query and query not in searchable:
                    continue
                self._filtered_monster_indices.append(real_idx)
                self.mon_list.insert("end", "%s (КД %s, %d ХП)" % (
                    monster.name, monster.ac, monster.hp))
            if selected_real in self._filtered_monster_indices:
                self.mon_list.selection_set(self._filtered_monster_indices.index(selected_real))
        except tk.TclError:
            return

    def _selected_monster_index(self):
        selected = self.mon_list.curselection()
        if not selected:
            return None
        visible_idx = selected[0]
        if visible_idx >= len(self._filtered_monster_indices):
            return None
        return self._filtered_monster_indices[visible_idx]

    def _duplicate_monster(self):
        idx = self._selected_monster_index()
        if idx is None:
            messagebox.showinfo("Бестиарий", "Сначала выберите монстра для дублирования.", parent=self.root)
            return
        # Round-trip through the model serializer to make a fully independent copy
        # while preserving every statblock field; assign a fresh identity.
        original = self.state.monsters[idx]
        duplicate = Monster.from_dict(original.to_dict())
        duplicate.id = uid("mon")
        duplicate.name = (original.name or "Монстр") + " (копия)"
        with self.state.lock:
            self.state.monsters.insert(idx + 1, duplicate)
        self.changed(refresh=False)
        self._refresh_bestiary_list()
        if idx + 1 in self._filtered_monster_indices:
            visible_idx = self._filtered_monster_indices.index(idx + 1)
            self.mon_list.selection_clear(0, "end")
            self.mon_list.selection_set(visible_idx)
            self.mon_list.see(visible_idx)

    def _add_monster(self):
        e = MonsterEditor(self.root)
        self.root.wait_window(e)
        if e.result:
            with self.state.lock:
                self.state.monsters.append(e.result)
            self.changed(refresh=False)

    def _edit_monster(self):
        idx = self._selected_monster_index()
        if idx is None:
            return
        e = MonsterEditor(self.root, self.state.monsters[idx])
        self.root.wait_window(e)
        if e.result:
            self.state.monsters[idx] = e.result
        self.changed(refresh=False)

    def _del_monster(self):
        idx = self._selected_monster_index()
        if idx is None:
            return
        with self.state.lock:
            del self.state.monsters[idx]
        self.changed(refresh=False)

    def _monster_to_map(self):
        idx = self._selected_monster_index()
        if idx is None:
            return
        m = self.state.monsters[idx]
        self._add_to_map(m.name, m.token_image, m.hp, m.hp, m.id, "monster")

    def _monster_to_combat(self):
        idx = self._selected_monster_index()
        if idx is None:
            return
        m = self.state.monsters[idx]
        init = simpledialog.askinteger("Инициатива", "Инициатива для %s:" % m.name,
                                       parent=self.root, initialvalue=0)
        if init is None:
            return
        with self.state.lock:
            self.state.combatants.append(Combatant(m.name, init, m.hp, m.hp, "monster"))
        self.changed(refresh=False)

    # --------------------------------------------------------------- combat
    def _add_combatant(self):
        d = tk.Toplevel(self.root)
        d.title("Участник боя")
        d.configure(bg=COL_BG)
        name = tk.StringVar(); ini = tk.IntVar(value=0)
        hp = tk.IntVar(value=10); mx = tk.IntVar(value=10)
        kind = tk.StringVar(value="monster")
        ttk.Label(d, text="Имя").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(d, textvariable=name, width=24).grid(row=0, column=1)
        ttk.Label(d, text="Инициатива").grid(row=1, column=0, sticky="e", padx=4)
        ttk.Spinbox(d, from_=-99, to=99, textvariable=ini, width=6).grid(row=1, column=1)
        ttk.Label(d, text="ХП").grid(row=2, column=0, sticky="e", padx=4)
        ttk.Spinbox(d, from_=0, to=9999, textvariable=hp, width=6).grid(row=2, column=1)
        ttk.Label(d, text="Макс. ХП").grid(row=3, column=0, sticky="e", padx=4)
        ttk.Spinbox(d, from_=0, to=9999, textvariable=mx, width=6).grid(row=3, column=1)
        ttk.Label(d, text="Тип").grid(row=4, column=0, sticky="e", padx=4)
        ttk.Combobox(d, textvariable=kind, width=12,
                     values=["monster", "player", "npc"]).grid(row=4, column=1)
        out = {}

        def ok():
            if name.get():
                out["c"] = Combatant(name.get(), int(ini.get()), int(hp.get()),
                                     int(mx.get()), kind.get())
            d.destroy()
        ttk.Button(d, text="ОК", command=ok).grid(row=5, column=1, sticky="e", pady=6)
        d.grab_set()
        self.root.wait_window(d)
        if out.get("c"):
            with self.state.lock:
                self.state.combatants.append(out["c"])
            self.changed(refresh=False)

    def _edit_combatant(self):
        sel = self.combat_list.curselection()
        if not sel:
            return
        c = self.state.ordered_combatants()[sel[0]]
        hp = simpledialog.askinteger("ХП", "Текущее ХП для %s:" % c.name,
                                     parent=self.root, initialvalue=c.hp)
        if hp is not None:
            with self.state.lock:
                c.hp = hp
                c.max_hp = max(c.max_hp, hp)
                if c.linked_kind == "character":
                    ch = next((x for x in self.state.characters
                               if x.id == c.linked_id), None)
                    if ch is not None:
                        ch.hp = hp
                        ch.max_hp = max(ch.max_hp, hp)
            self.changed(refresh=False)

    def _del_combatant(self):
        sel = self.combat_list.curselection()
        if not sel:
            return
        c = self.state.ordered_combatants()[sel[0]]
        with self.state.lock:
            if c in self.state.combatants:
                self.state.combatants.remove(c)
        self.changed(refresh=False)

    def _roll_initiative(self):
        with self.state.lock:
            for c in self.state.combatants:
                c.initiative = evaluate_dice("1d20")[0]
        self.changed(refresh=False)

    def _next_turn(self):
        with self.state.lock:
            self.state.next_turn()
        self.changed(refresh=False)

    # ----------------------------------------------------------------- dice
    def _quick_roll(self, die):
        self.state.roll(die)
        self.refresh_lists()

    def _roll_custom(self):
        expr = self.dice_expr.get().strip()
        if expr:
            try:
                self.state.roll(expr)
            except ValueError as e:
                messagebox.showerror("Ошибка", str(e))
            self.refresh_lists()

    def _roll_advantage(self):
        import random
        a, b = random.randint(1, 20), random.randint(1, 20)
        entry = {"id": uid("roll"), "expression": "d20 (преим.)", "total": max(a, b),
                 "detail": "(%d, %d)" % (a, b), "roller": "Мастер",
                 "ts": __import__("game_state").now_iso()}
        with self.state.lock:
            self.state.dice_log.insert(0, entry)
            self.state.dice_log = self.state.dice_log[:100]
        self.changed(refresh=False)

    def _roll_disadvantage(self):
        import random
        a, b = random.randint(1, 20), random.randint(1, 20)
        entry = {"id": uid("roll"), "expression": "d20 (помеха)", "total": min(a, b),
                 "detail": "(%d, %d)" % (a, b), "roller": "Мастер",
                 "ts": __import__("game_state").now_iso()}
        with self.state.lock:
            self.state.dice_log.insert(0, entry)
            self.state.dice_log = self.state.dice_log[:100]
        self.changed(refresh=False)

    # ---------------------------------------------------------------- music
    def _add_track(self):
        path = filedialog.askopenfilename(
            title="Аудиофайл",
            filetypes=[("Аудио", "*.mp3 *.ogg *.wav *.m4a *.aac *.flac"),
                       ("Все файлы", "*.*")])
        if not path:
            return
        name = import_media(path)
        if not name:
            return
        with self.state.lock:
            self.state.music["tracks"].append(
                {"name": os.path.basename(path), "path": name, "volume": 1.0})
            if self.state.music["current"] is None:
                self.state.music["current"] = 0
        self._local_music_apply()
        self.changed(refresh=False)

    def _del_track(self):
        sel = self.music_list.curselection()
        if not sel:
            return
        i = sel[0]
        with self.state.lock:
            del self.state.music["tracks"][i]
            cur = self.state.music["current"]
            if cur is not None:
                if not self.state.music["tracks"]:
                    self.state.music["current"] = None
                elif cur >= len(self.state.music["tracks"]):
                    self.state.music["current"] = len(self.state.music["tracks"]) - 1
        self._local_music_apply()
        self.changed(refresh=False)

    def _music_play(self):
        with self.state.lock:
            if self.state.music["current"] is None and self.state.music["tracks"]:
                self.state.music["current"] = 0
            self.state.music["playing"] = True
        self._local_music_apply()
        self.changed(refresh=False)

    def _music_pause(self):
        with self.state.lock:
            self.state.music["playing"] = False
        self._local_music_apply()
        self.changed(refresh=False)

    def _music_next(self):
        with self.state.lock:
            tracks = self.state.music["tracks"]
            if tracks:
                cur = self.state.music["current"]
                self.state.music["current"] = (0 if cur is None else (cur + 1) % len(tracks))
                self.state.music["playing"] = True
        self._local_music_apply()
        self.changed(refresh=False)

    def _music_set_volume(self):
        with self.state.lock:
            self.state.music["volume"] = float(self.music_vol.get())
        self._local_music_apply()

    def _local_music_apply(self):
        if not PYGAME_AVAILABLE:
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            m = self.state.music
            tracks = m["tracks"]
            cur = m["current"]
            if cur is not None and 0 <= cur < len(tracks):
                path = os.path.join(UPLOADS_DIR, tracks[cur]["path"])
                if not os.path.exists(path):
                    return
                if getattr(self, "_local_track", None) != path:
                    pygame.mixer.music.load(path)
                    self._local_track = path
                vol = max(0.0, min(1.0, (m.get("volume", 0.7) or 0.7)
                                   * (tracks[cur].get("volume", 1.0) or 1.0)))
                pygame.mixer.music.set_volume(vol)
                if m.get("playing"):
                    if not pygame.mixer.music.get_busy():
                        pygame.mixer.music.play(-1)
                else:
                    pygame.mixer.music.pause()
            else:
                pygame.mixer.music.stop()
                self._local_track = None
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- notes
    def _load_notes_data(self):
        """Читает новые именованные заметки и поддерживает старый формат одной заметки."""
        raw = self.state.notes or ""
        self._notes_data = []
        if raw.startswith("__DND_NOTES_V2__"):
            try:
                data = json.loads(raw[len("__DND_NOTES_V2__"):])
                self._notes_data = data if isinstance(data, list) else []
            except (TypeError, ValueError):
                self._notes_data = []
        elif raw.strip():
            self._notes_data = [{"title": "Основная заметка", "text": raw}]
        if not self._notes_data:
            self._notes_data = [{"title": "Основная заметка", "text": ""}]
        self._active_note = 0
        self._refresh_notes_list()
        self._show_active_note()

    def _persist_notes_data(self):
        self.state.notes = "__DND_NOTES_V2__" + json.dumps(self._notes_data, ensure_ascii=False)

    def _refresh_notes_list(self):
        self.notes_list.delete(0, "end")
        for note in self._notes_data:
            self.notes_list.insert("end", note.get("title") or "Без названия")
        if self._notes_data:
            self.notes_list.selection_clear(0, "end")
            self.notes_list.selection_set(self._active_note)
            self.notes_list.activate(self._active_note)

    def _show_active_note(self):
        self.notes_text.delete("1.0", "end")
        if self._notes_data:
            self.notes_text.insert("1.0", self._notes_data[self._active_note].get("text", ""))

    def _note_selected(self, event=None):
        sel = self.notes_list.curselection()
        if not sel or sel[0] == self._active_note:
            return
        self._save_notes()
        self._active_note = sel[0]
        self._show_active_note()

    def _save_notes(self):
        try:
            if self._notes_data:
                self._notes_data[self._active_note]["text"] = self.notes_text.get("1.0", "end-1c")
                self._persist_notes_data()
        except Exception:  # noqa: BLE001
            pass

    def _add_note(self):
        title = simpledialog.askstring("Новая заметка", "Название заметки:", parent=self.root)
        if not title or not title.strip():
            return
        self._save_notes()
        self._notes_data.append({"title": title.strip(), "text": ""})
        self._active_note = len(self._notes_data) - 1
        self._persist_notes_data()
        self._refresh_notes_list()
        self._show_active_note()

    def _rename_note(self):
        if not self._notes_data:
            return
        old = self._notes_data[self._active_note].get("title", "Заметка")
        title = simpledialog.askstring("Переименовать заметку", "Название:", initialvalue=old, parent=self.root)
        if title and title.strip():
            self._notes_data[self._active_note]["title"] = title.strip()
            self._persist_notes_data()
            self._refresh_notes_list()

    def _delete_note(self):
        if len(self._notes_data) <= 1:
            messagebox.showinfo("Заметки", "Должна остаться хотя бы одна заметка.")
            return
        title = self._notes_data[self._active_note].get("title", "эту заметку")
        if not messagebox.askyesno("Удалить заметку", "Удалить «%s»?" % title, parent=self.root):
            return
        self._notes_data.pop(self._active_note)
        self._active_note = min(self._active_note, len(self._notes_data) - 1)
        self._persist_notes_data()
        self._refresh_notes_list()
        self._show_active_note()

    def _clear_dice_log(self):
        if not self.state.dice_log:
            return
        if not messagebox.askyesno("Очистить броски", "Удалить всю историю бросков?", parent=self.root):
            return
        with self.state.lock:
            self.state.dice_log.clear()
        self.state.bump()
        self.refresh_lists()

    def _paste_image_from_clipboard(self):
        """Добавляет изображение из буфера обмена как обычный объект сцены."""
        try:
            clip = ImageGrab.grabclipboard()
            if isinstance(clip, list):
                paths = [x for x in clip if isinstance(x, str) and os.path.isfile(x)]
                if paths:
                    name = import_media(paths[0])
                    if not name:
                        return
                    with Image.open(os.path.join(UPLOADS_DIR, name)) as im:
                        width, height = im.size
                else:
                    raise ValueError("В буфере нет изображения")
            elif isinstance(clip, Image.Image):
                image = clip.convert("RGBA")
                image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                name = "clipboard-%s.png" % uuid.uuid4().hex[:10]
                os.makedirs(UPLOADS_DIR, exist_ok=True)
                image.save(os.path.join(UPLOADS_DIR, name), "PNG")
                width, height = image.size
            else:
                raise ValueError("В буфере обмена нет изображения. Скопируй картинку и повтори.")
        except Exception as e:  # noqa: BLE001
            messagebox.showwarning("Буфер обмена", str(e), parent=self.root)
            return

        # Ограничиваем стартовый размер, но сохраняем пропорции оригинала.
        factor = min(1.0, 600.0 / max(width, height))
        draw_w, draw_h = max(40, width * factor), max(40, height * factor)
        self._push_undo()
        m = self.state.map
        view = self._viewport()
        center_x = view["x"] + view["w"] / 2
        center_y = view["y"] + view["h"] / 2
        o = Overlay("image", name, center_x - draw_w / 2,
                    center_y - draw_h / 2, draw_w, draw_h)
        with self.state.lock:
            self.state.overlays.append(o)
        self.selected = o
        self._sync_selection_bar()
        self.set_tool("select")
        self.changed(refresh=False)

    # --------------------------------------------------- second screen
    def _toggle_second_screen(self):
        if self.second_win is not None and self.second_win.winfo_exists():
            self._close_second_screen()
        else:
            self._build_second_screen()

    def _build_second_screen(self):
        win = tk.Toplevel(self.root)
        win.title("Трансляция карты")
        win.configure(bg=COL_CANVAS)
        self.second_win = win
        self.second_canvas = tk.Canvas(win, bg=COL_CANVAS, highlightthickness=0)
        self.second_canvas.pack(fill="both", expand=True)

        mons = get_monitors()
        if len(mons) >= 2:
            mons.sort(key=lambda m: m[0])
            primary = next((m for m in mons if m[0] == 0 and m[1] == 0), mons[0])
            secondary = next((m for m in mons if m != primary), mons[-1])
            l, t, r, b = secondary
            w, h = r - l, b - t
        else:
            l, t = 0, 0
            w, h = win.winfo_screenwidth(), win.winfo_screenheight()

        # 1. Сначала стандартно задаем координаты и размер
        geom_x = f"+{l}" if l >= 0 else str(l)
        geom_y = f"+{t}" if t >= 0 else str(t)
        win.geometry(f"{w}x{h}{geom_x}{geom_y}")
        win.update()

        # 2. Принудительно применяем стиль без рамок через WinAPI
        try:
            # Получаем корневой HWND окна (GA_ROOT = 2)
            hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)
            
            GWL_STYLE = -16
            WS_POPUP = 0x80000000
            WS_VISIBLE = 0x10000000
            
            # Получаем текущий стиль и модифицируем его
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            style = (style & ~0x00C00000) & ~0x00040000  # Убираем рамки и заголовок
            style = style | WS_POPUP | WS_VISIBLE
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
            
            # Обновляем окно с новыми координатами и стилем
            SWP_FRAMECHANGED = 0x0020
            SWP_SHOWWINDOW = 0x0040
            ctypes.windll.user32.SetWindowPos(hwnd, 0, l, t, w, h, SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        except Exception as e:
            print("Не удалось применить WinAPI стиль:", e)
            win.overrideredirect(True)
            win.geometry(f"{w}x{h}+{l}+{t}")

        # Закрытие по Escape и двойному клику убрано по запросу пользователя.
        
        self.second_canvas.bind("<Configure>", lambda e: self._sync_second_screen())
        win.focus_force()
        self._schedule_second_sync()

    def _schedule_second_sync(self, attempts=15):
        if (self.second_canvas is None or self.second_win is None
                or not self.second_win.winfo_exists()):
            return
        if (self.second_canvas.winfo_width() > 10
                and self.second_canvas.winfo_height() > 10):
            self._sync_second_screen()
        elif attempts > 0:
            self.root.after(100, lambda: self._schedule_second_sync(attempts - 1))

    def _close_second_screen(self):
        if self._second_sync_job is not None:
            try:
                self.root.after_cancel(self._second_sync_job)
            except Exception:  # noqa: BLE001
                pass
            self._second_sync_job = None
        self._second_sync_viewport = None
        if self.second_win is not None:
            try:
                self.second_win.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.second_win = None
        self.second_canvas = None

    def _schedule_second_screen_sync(self, vp=None):
        """Объединяет частые перерисовки в один кадр примерно каждые 33 мс."""
        if self.second_canvas is None or self.second_win is None:
            return
        if not self.second_win.winfo_exists():
            return
        self._second_sync_viewport = dict(vp or self._viewport())
        if self._second_sync_job is None:
            self._second_sync_job = self.root.after(33, self._run_second_screen_sync)

    def _run_second_screen_sync(self):
        self._second_sync_job = None
        vp = self._second_sync_viewport
        self._second_sync_viewport = None
        if vp is not None:
            self._sync_second_screen(vp)

    def _sync_second_screen(self, vp=None):
        """Отображает тот же участок карты, который сейчас видит мастер."""
        if self.second_canvas is None or self.second_win is None:
            return
        if not self.second_win.winfo_exists():
            return
        if vp is None:
            vp = self._viewport()
        sc = self.second_canvas
        bw = sc.winfo_width()
        bh = sc.winfo_height()
        if bw <= 1 or bh <= 1:
            return
        scale = min(bw / max(vp["w"], 1e-6), bh / max(vp["h"], 1e-6))
        off_x = (bw - vp["w"] * scale) / 2.0
        off_y = (bh - vp["h"] * scale) / 2.0
        self._render_viewport(sc, vp, scale, False, off_x, off_y)
