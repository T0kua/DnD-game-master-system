"""Плагин «Вики кампании».

- Добавляет вкладку «📖 Вики» в мастер.
- Патчит tk.Toplevel: у каждого Text-виджета появляется кнопка 📖,
  которая вставляет [[статью]] в позицию курсора.
- Данные: app.state.plugins["wiki"] = {"entries": {id: {...}}}.
- Живёт в state.plugins → автоматически попадает в /api/player/<token>.
- Игрок видит вкладку «Вики» (см. static/wiki.js).
"""
import re
import uuid

import tkinter as tk
from tkinter import ttk, messagebox

COL_BG = "#1b1e26"
COL_PANEL = "#232833"
COL_FIELD = "#2a3040"
COL_TEXT = "#e6e8ef"
COL_MUTED = "#9aa3b5"
COL_ACCENT = "#5b8def"
COL_BORDER = "#3a4155"

CATEGORIES = [
    ("item", "Предмет"), ("ability", "Способность"),
    ("spell", "Заклинание"), ("monster", "Монстр"),
    ("npc", "NPC"), ("location", "Локация"),
    ("event", "Событие"), ("lore", "Мир"), ("other", "Другое"),
]
CAT_LABEL = dict(CATEGORIES)
CAT_KEY = {v: k for k, v in CATEGORIES}

LINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")

_GLOBAL_APP = None


# --------------------------------------------------------------------- data
def _slot(state):
    plugins = getattr(state, "plugins", None)
    if plugins is None:
        plugins = {}
        state.plugins = plugins
    slot = plugins.get("wiki")
    if slot is None:
        slot = {"entries": {}}
        plugins["wiki"] = slot
    slot.setdefault("entries", {})
    return slot


def _norm(s):
    return (s or "").strip().casefold()


def _find_by_title(state, title):
    n = _norm(title)
    for e in _slot(state)["entries"].values():
        if _norm(e.get("title")) == n:
            return e
    return None


def _collect_dangling(state):
    """{title: {sources...}} для ссылок без статьи — по всему state."""
    dangling = {}

    def scan(text, source):
        for m in LINK_RE.finditer(text or ""):
            t = m.group(1).strip()
            if t and _find_by_title(state, t) is None:
                dangling.setdefault(t, set()).add(source)

    for e in _slot(state)["entries"].values():
        scan(e.get("body", ""), "вики: " + e.get("title", "?"))
    for c in state.characters:
        for a in c.abilities:
            scan(a.description, "%s → %s" % (c.name, a.name))
        for it in c.inventory:
            scan(it.description, "%s → %s" % (c.name, it.name))
        scan(c.notes, "%s (заметки)" % c.name)
    for m in state.monsters:
        for bucket in (m.traits, m.actions, m.legendary):
            for f in bucket:
                if isinstance(f, dict):
                    scan(f.get("desc", ""), "%s → %s" % (m.name, f.get("name", "")))
        scan(m.notes, "%s (заметки)" % m.name)
    return dangling


# --------------------------------------------------------------- link picker
def _open_link_picker(parent_win, text_widget):
    app = _GLOBAL_APP
    if app is None:
        return
    slot = _slot(app.state)
    d = tk.Toplevel(parent_win)
    d.title("Вставить ссылку на статью")
    d.configure(bg=COL_BG)
    d.geometry("380x420")
    ttk.Label(d, text="Поиск (регистронезависимо)").pack(anchor="w", padx=6, pady=(6, 0))
    search = tk.StringVar()
    ttk.Entry(d, textvariable=search).pack(fill="x", padx=6, pady=2)
    lb = tk.Listbox(d, exportselection=False)
    lb.configure(bg=COL_FIELD, fg=COL_TEXT, selectbackground=COL_ACCENT,
                 selectforeground="#fff")
    lb.pack(fill="both", expand=True, padx=6, pady=4)

    def fill(*_):
        lb.delete(0, "end")
        q = _norm(search.get())
        for e in sorted(slot["entries"].values(), key=lambda x: x.get("title", "")):
            t = e.get("title", "")
            if q and q not in _norm(t):
                continue
            cat = CAT_LABEL.get(e.get("category", ""), "")
            lb.insert("end", "[%s] %s" % (cat, t) if cat else t)

    def pick(*_):
        sel = lb.curselection()
        if not sel:
            return
        title = lb.get(sel[0]).split("] ", 1)[-1]
        try:
            text_widget.insert("insert", "[[%s]]" % title)
            text_widget.focus_set()
        except tk.TclError:
            pass
        d.destroy()

    search.trace_add("write", fill)
    lb.bind("<Double-Button-1>", pick)
    ttk.Button(d, text="Вставить", command=pick).pack(pady=4)
    fill()
    d.grab_set()


# --------------------------------------------------------------- master tab
class WikiTab(ttk.Frame):
    def __init__(self, app):
        super().__init__(app.nb, padding=4)
        self.app = app
        self.current_id = None
        self._ids = []
        self._save_job = None
        self._build()
        self._refresh_list()
        self._update_tab_title()

    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        for text, cmd in [("＋ Новая", self._new),
                          ("💾 Сохранить", self._save),
                          ("🗑 Удалить", self._delete),
                          ("🔗 Висячие", self._show_dangling),
                          ("📖 Ссылка", self._insert_link)]:
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, pady=(6, 0))

        left = ttk.Frame(mid)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Статьи").pack(anchor="w")
        self.listbox = tk.Listbox(left, width=28, exportselection=False)
        self.listbox.configure(bg=COL_FIELD, fg=COL_TEXT,
                               selectbackground=COL_ACCENT,
                               selectforeground="#fff")
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        right = ttk.Frame(mid)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))

        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="Заголовок").pack(side="left")
        self.title_var = tk.StringVar()
        ttk.Entry(head, textvariable=self.title_var).pack(
            side="left", fill="x", expand=True, padx=4)
        ttk.Label(head, text="Категория").pack(side="left", padx=(6, 0))
        self.cat_var = tk.StringVar()
        ttk.Combobox(head, textvariable=self.cat_var,
                     values=[l for _, l in CATEGORIES], state="readonly",
                     width=14).pack(side="left", padx=4)

        ttk.Label(
            right,
            text="[[статья]]  **жирный**  *курсив*  `код`  [текст](url)  "
                 "![alt](/media/x.png)  # H1  - пункт  1. пункт  "
                 "| a | b | + |---|---| для таблицы",
            foreground=COL_MUTED, wraplength=340, justify="left",
        ).pack(anchor="w", pady=(6, 2))

        self.body = tk.Text(right, wrap="word", undo=True)
        self.body.configure(bg=COL_FIELD, fg=COL_TEXT, insertbackground=COL_TEXT,
                            highlightthickness=0, borderwidth=0)
        self.body.pack(fill="both", expand=True, pady=2)

        self.title_var.trace_add("write", lambda *_: self._schedule_save())
        self.cat_var.trace_add("write", lambda *_: self._schedule_save())
        self.body.bind("<KeyRelease>", lambda e: self._schedule_save())

    # ---------------- persistence ----------------
    def _schedule_save(self):
        if self._save_job:
            try:
                self.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = self.after(1000, self._save)

    def _save(self):
        self._save_job = None
        if not self.current_id:
            return
        slot = _slot(self.app.state)
        e = slot["entries"].get(self.current_id)
        if not e:
            return
        title = self.title_var.get().strip()
        if not title:
            return
        if _norm(title) != _norm(e.get("title")):
            for other in slot["entries"].values():
                if other["id"] != self.current_id and _norm(other.get("title")) == _norm(title):
                    messagebox.showwarning("Вики", "Статья с таким заголовком уже есть.")
                    return
        e["title"] = title
        e["category"] = CAT_KEY.get(self.cat_var.get(), "other")
        e["body"] = self.body.get("1.0", "end-1c")
        self.app.state.bump()
        self._refresh_list()
        if self.current_id in self._ids:
            self.listbox.selection_set(self._ids.index(self.current_id))
        self._update_tab_title()

    # ---------------- list / selection ----------------
    def _refresh_list(self):
        self.listbox.delete(0, "end")
        self._ids = []
        for e in sorted(_slot(self.app.state)["entries"].values(),
                        key=lambda x: x.get("title", "")):
            self._ids.append(e["id"])
            cat = CAT_LABEL.get(e.get("category", ""), "")
            self.listbox.insert("end", "[%s] %s" % (cat, e["title"]) if cat else e["title"])

    def _on_select(self, event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._ids):
            return
        self._load(self._ids[idx])

    def _load(self, entry_id):
        e = _slot(self.app.state)["entries"].get(entry_id)
        if not e:
            return
        self.current_id = entry_id
        self.title_var.set(e.get("title", ""))
        self.cat_var.set(CAT_LABEL.get(e.get("category", ""), "Другое"))
        self.body.delete("1.0", "end")
        self.body.insert("1.0", e.get("body", ""))

    # ---------------- actions ----------------
    def _new(self):
        entry_id = "wiki-" + uuid.uuid4().hex[:8]
        _slot(self.app.state)["entries"][entry_id] = {
            "id": entry_id, "title": "Новая статья",
            "category": "other", "body": "",
        }
        self.app.state.bump()
        self._refresh_list()
        if entry_id in self._ids:
            self.listbox.selection_set(self._ids.index(entry_id))
        self._load(entry_id)

    def _delete(self):
        if not self.current_id:
            return
        if not messagebox.askyesno("Вики", "Удалить статью?"):
            return
        _slot(self.app.state)["entries"].pop(self.current_id, None)
        self.current_id = None
        self.title_var.set(""); self.cat_var.set("")
        self.body.delete("1.0", "end")
        self.app.state.bump()
        self._refresh_list()
        self._update_tab_title()

    def _show_dangling(self):
        dangling = _collect_dangling(self.app.state)
        if not dangling:
            messagebox.showinfo("Вики", "Висячих ссылок нет.")
            return
        lines = ["• %s\n    в: %s" % (t, ", ".join(sorted(src)))
                 for t, src in sorted(dangling.items())]
        messagebox.showinfo("Висячие ссылки", "\n\n".join(lines))

    def _insert_link(self):
        _open_link_picker(self.winfo_toplevel(), self.body)

    def _update_tab_title(self):
        try:
            dangling = _collect_dangling(self.app.state)
            self.app.nb.tab(self, text=("📖 Вики ⚠%d" % len(dangling))
                            if dangling else "📖 Вики")
            self.after(5000, self._update_tab_title)
        except tk.TclError:
            pass


# -------------------------------------------------- generic 📖 on Text widgets
def _attach_wiki_buttons(win):
    """Для каждого Text в Toplevel вешает 📖 в правом верхнем углу."""
    try:
        if not win.winfo_exists():
            return
    except tk.TclError:
        return

    found = False

    def walk(w):
        nonlocal found
        try:
            for child in w.winfo_children():
                if isinstance(child, tk.Text):
                    found = True
                    if getattr(child, "_wiki_btn", None) is None:
                        btn = tk.Button(
                            win, text="📖", width=2, relief="flat", bd=0,
                            bg=COL_PANEL, fg=COL_TEXT, cursor="hand2",
                            activebackground=COL_ACCENT, activeforeground="#fff",
                            command=lambda t=child: _open_link_picker(win, t),
                        )
                        try:
                            btn.place(in_=child, relx=1.0, rely=0.0,
                                      x=-4, y=-4, anchor="ne")
                        except tk.TclError:
                            btn.place(relx=1.0, rely=0.0, x=-8, y=8, anchor="ne")
                        child._wiki_btn = btn
                walk(child)
        except tk.TclError:
            pass

    walk(win)

    if not found:
        # диалог мог ещё не дорисовать виджеты — попробуем чуть позже
        try:
            win.after(120, lambda: _attach_wiki_buttons(win))
        except tk.TclError:
            pass


# ------------------------------------------------------------------- register
def register(app):
    global _GLOBAL_APP
    _GLOBAL_APP = app
    _slot(app.state)

    tab = WikiTab(app)
    app.nb.add(tab, text="📖 Вики")
    app._wiki_tab = tab

    # Один глобальный патч: любой Toplevel с Text получает 📖.
    if getattr(tk.Toplevel, "_wiki_patched", False):
        return
    _orig_init = tk.Toplevel.__init__

    def _patched(self, *a, **kw):
        _orig_init(self, *a, **kw)
        try:
            self.after(80, lambda w=self: _attach_wiki_buttons(w))
        except tk.TclError:
            pass

    tk.Toplevel.__init__ = _patched
    tk.Toplevel._wiki_patched = True