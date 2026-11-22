"""Entry point: choose a campaign, start the Flask server, run the master GUI.

Usage:
    python app.py [--port 5000] [--host 0.0.0.0] [--campaign "Имя"] [--no-save]

Without --campaign a campaign picker is shown before the game starts.  Every
campaign is stored as its own JSON file under ``data/campaigns/``.
"""

import argparse
import os
import re
import sys
import threading
import traceback

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from game_state import GameState, Character, Ability, Item, Resource, Monster, Token, Combatant
from server import start_server
from master_gui import MasterApp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CAMPAIGNS_DIR = os.path.join(DATA_DIR, "campaigns")
LEGACY_SAVE = os.path.join(DATA_DIR, "state.json")


# ---------------------------------------------------------------------------
# Campaign storage helpers
# ---------------------------------------------------------------------------
def slugify(name):
    s = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE).strip()
    s = s.replace(" ", "_")
    return s or "campaign"


def campaign_path(name):
    return os.path.join(CAMPAIGNS_DIR, slugify(name) + ".json")


def list_campaigns():
    """Return [(display_name, path), ...] sorted by name."""
    out = []
    if not os.path.isdir(CAMPAIGNS_DIR):
        return out
    for fn in sorted(os.listdir(CAMPAIGNS_DIR)):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(CAMPAIGNS_DIR, fn)
        name = os.path.splitext(fn)[0]
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            name = data.get("campaign_name") or data.get("session_name") or name
        except Exception:  # noqa: BLE001
            pass
        out.append((name, path))
    return out


def migrate_legacy():
    """One-time import of the old single-file save into a campaign."""
    if not os.path.exists(LEGACY_SAVE) or list_campaigns():
        return
    try:
        st = GameState.load(LEGACY_SAVE)
        path = campaign_path(st.campaign_name)
        st.save(path)
        os.replace(LEGACY_SAVE, LEGACY_SAVE + ".migrated")
        print("Старое сохранение перенесено в кампанию:", path)
    except Exception as e:  # noqa: BLE001
        print("Не удалось перенести старое сохранение:", e)


def seed_demo(state):
    pc = Character("Арабелла", "Игрок 1", "Волшебница", 3, "Человек",
                   hp=24, max_hp=24, ac=13)
    pc.abilities = [
        Ability("Огненный шар", "Дальность 45 м, 8d6 урона огнём, "
                "спасбросок ЛОВ.", "Заклинание 3 круга"),
        Ability("Лечение ран", "Восстанавливает 1d8 + мод. заклинателя.",
                "Заклинание 1 круга"),
    ]
    pc.resources = [Resource("Ячейки 1 ур.", 4, 1),
                    Resource("Ячейки 2 ур.", 2, 0),
                    Resource("Инфузии", 2, 0)]
    pc.inventory = [Item("Кинжал", "1d4 колющего урона, лёгкое оружие.", 1),
                    Item("Зелье лечения", "Восстанавливает 2d4+2 ХП.", 2)]
    state.characters.append(pc)

    gob = Monster("Гоблин", "Гуманоид (гоблиноид)", "Маленький",
                  "Нейтрально-злой", ac=15, hp=7, hp_dice="2d6",
                  speed="30 фт.")
    gob.stats = {"СИЛ": 8, "ЛОВ": 14, "ТЕЛ": 10, "ИНТ": 10, "МДР": 8, "ХАР": 8}
    gob.skills = {"Скрытность": "+6"}
    gob.senses = "тёмное зрение 60 фт."
    gob.cr = "1/4"
    gob.traits = [{"name": "Ловкий побег",
                   "desc": "Отход или Засада бонусным действием."}]
    gob.actions = [{"name": "Ятаган",
                    "desc": "Ближняя атака: +4, 1d6+2 рубящего урона."}]
    state.monsters.append(gob)

    state.combatants = [Combatant("Арабелла", 15, 24, 24, "player"),
                        Combatant("Гоблин", 8, 7, 7, "monster")]
    state.combatants[0].active = True

    tok = Token("Арабелла", 420, 340, 60, "#4f8fda")
    tok.hp, tok.max_hp = 24, 24
    tok.linked_id, tok.linked_kind = pc.id, "character"
    state.tokens.append(tok)
    tok2 = Token("Гоблин", 700, 480, 50, "#c0392b")
    tok2.hp, tok2.max_hp = 7, 7
    tok2.linked_id, tok2.linked_kind = gob.id, "monster"
    state.tokens.append(tok2)
    state.bump()


def load_or_create(name):
    path = campaign_path(name)
    if os.path.exists(path):
        return GameState.load(path), path
    state = GameState(name)
    seed_demo(state)
    state.save(path)
    return state, path


# ---------------------------------------------------------------------------
# Campaign picker dialog
# ---------------------------------------------------------------------------
class CampaignDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Выбор кампании — с кем играем?")
        self.geometry("460x380")
        self.result = None
        self._items = []
        self._build()
        self._refresh()
        self.bind("<Escape>", lambda e: self.destroy())
        # NB: do NOT make this window transient to a withdrawn root — on
        # Windows such a Toplevel never becomes mapped, wait_window() blocks
        # forever and Ctrl-C cannot interrupt Tk's wait loop.
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            self.attributes("-topmost", True)
            self.after(400, lambda: self._safe_topmost(False))
        except tk.TclError:
            pass
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _safe_topmost(self, on):
        try:
            if self.winfo_exists():
                self.attributes("-topmost", on)
        except tk.TclError:
            pass

    def _build(self):
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="С кем играем? Выберите кампанию:",
                  font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(f, height=12)
        self.listbox.pack(fill="both", expand=True, pady=6)
        self.listbox.bind("<Double-Button-1>", lambda e: self._open())
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Открыть", command=self._open).pack(side="left")
        ttk.Button(btns, text="Новая", command=self._new).pack(side="left", padx=6)
        ttk.Button(btns, text="Удалить", command=self._delete).pack(side="left")
        ttk.Button(btns, text="Выход",
                   command=self.destroy).pack(side="right")

    def _refresh(self):
        self._items = list_campaigns()
        self.listbox.delete(0, "end")
        for name, _path in self._items:
            self.listbox.insert("end", name)
        if not self._items:
            self.listbox.insert("end", "(нет кампаний — нажмите «Новая»)")

    def _selected(self):
        sel = self.listbox.curselection()
        if not sel or not self._items:
            return None
        idx = sel[0]
        if idx >= len(self._items):
            return None
        return self._items[idx]

    def _open(self):
        item = self._selected()
        if not item:
            return
        name, path = item
        try:
            state = GameState.load(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка", "Не удалось открыть кампанию:\n%s" % e,
                                 parent=self)
            return
        self.result = (state, path)
        self.destroy()

    def _new(self):
        name = simpledialog.askstring("Новая кампания", "Название кампании:",
                                      parent=self)
        if not name:
            return
        path = campaign_path(name)
        if os.path.exists(path):
            if not messagebox.askyesno("Кампания существует",
                                       "Перезаписать кампанию «%s»?" % name,
                                       parent=self):
                return
        state = GameState(name)
        seed_demo(state)
        state.save(path)
        self._refresh()
        try:
            self.result = (state, path)
            self.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _delete(self):
        item = self._selected()
        if not item:
            return
        name, path = item
        if messagebox.askyesno("Удалить", "Удалить кампанию «%s»?" % name,
                               parent=self):
            try:
                os.remove(path)
            except OSError as e:
                messagebox.showerror("Ошибка", str(e), parent=self)
            self._refresh()


def choose_campaign(root):
    print("Открываю выбор кампании…", flush=True)
    dlg = CampaignDialog(root)
    root.update_idletasks()
    if not dlg.winfo_viewable():
        # last-resort fallback: make sure the picker is on screen
        dlg.deiconify()
        dlg.lift()
        root.update()
    root.wait_window(dlg)
    return dlg.result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _main():
    parser = argparse.ArgumentParser(description="D&D table: master + players")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--campaign", default=None,
                        help="Имя кампании (без диалога выбора)")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    print("D&D Table — запуск…", flush=True)
    os.makedirs(CAMPAIGNS_DIR, exist_ok=True)
    migrate_legacy()

    root = tk.Tk()
    root.withdraw()

    if args.campaign:
        print("Загружаю кампанию: %s" % args.campaign, flush=True)
        state, save_path = load_or_create(args.campaign)
    else:
        res = choose_campaign(root)
        if not res:
            print("Выбор кампании отменён — выход.", flush=True)
            root.destroy()
            return
        state, save_path = res
        print("Выбрана кампания: %s" % state.campaign_name, flush=True)

    root.deiconify()

    print("Запускаю сервер: http://localhost:%d" % args.port, flush=True)
    server_thread = threading.Thread(
        target=start_server, args=(state, args.host, args.port), daemon=True)
    server_thread.start()

    print("Открываю окно мастера…", flush=True)
    app = MasterApp(state, root, args.port)

    def on_close():
        if not args.no_save:
            try:
                state.save(save_path)
                print("Сохранено в", save_path, flush=True)
            except Exception as e:  # noqa: BLE001
                print("Ошибка сохранения:", e, flush=True)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def main():
    try:
        _main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(os.path.join(DATA_DIR, "error.log"), "a",
                      encoding="utf-8") as f:
                f.write(tb + "\n")
        except Exception:  # noqa: BLE001
            pass
        raise


if __name__ == "__main__":
    main()
