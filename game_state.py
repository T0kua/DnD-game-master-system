"""Shared game state model for the D&D table.

Imported by the Flask server (background thread) and the tkinter master window
(main thread).  A single :class:`GameState` is shared; the server only reads
``snapshot()`` while the GUI mutates state under ``state.lock``.

Model highlights:
  * a campaign contains several **scenes** (independent maps with their own
    tokens / drawings / overlays);
  * a character has **modular resources** (arbitrary named pools such as
    "Ячейки 1 ур." or "Инфузии", each with max/used) instead of fixed slot types;
  * items are objects with a description;
  * every character has a unique ``player_token`` used for the personal link.
"""

import json
import random
import re
import threading
import time
import uuid

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def uid(prefix="id"):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:8])


def new_player_token():
    return uuid.uuid4().hex[:10]


def now_iso():
    return time.strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Tolerant converters (old saves stored dicts / comma-separated strings;
# the new stat block uses modular lists).
# ---------------------------------------------------------------------------
def _as_pair_list(val):
    """Normalise to [{"name": str, "value": str}, ...]."""
    if isinstance(val, dict):
        return [{"name": str(k), "value": str(v)} for k, v in val.items()]
    if isinstance(val, list):
        out = []
        for x in val:
            if isinstance(x, dict):
                out.append({"name": str(x.get("name", "")),
                            "value": str(x.get("value", ""))})
            else:
                out.append({"name": str(x), "value": ""})
        return out
    if isinstance(val, str) and val.strip():
        out = []
        for part in val.split(","):
            part = part.strip()
            if not part:
                continue
            bits = part.split()
            if len(bits) >= 2 and bits[-1].lstrip("+-").isdigit():
                out.append({"name": " ".join(bits[:-1]), "value": bits[-1]})
            else:
                out.append({"name": part, "value": ""})
        return out
    return []


def _as_str_list(val):
    if isinstance(val, list):
        return [str(x) for x in val if str(x).strip()]
    if isinstance(val, str):
        return [s.strip() for s in val.split(",") if s.strip()]
    return []


# Quick-pick labels offered by the master when adding a resource pool.
RESOURCE_PRESETS = [
    "Заговоры", "Ячейки 1 ур.", "Ячейки 2 ур.", "Ячейки 3 ур.",
    "Ячейки 4 ур.", "Ячейки 5 ур.", "Ячейки 6 ур.", "Ячейки 7 ур.",
    "Ячейки 8 ур.", "Ячейки 9 ур.", "Инфузии", "Заряды артефакта",
    "Заряды способности", "Ячейки колдуна", "Ячейки паладина",
    "Вдохновение", "Кости превосходства",
]


# ---------------------------------------------------------------------------
# Leaf model objects
# ---------------------------------------------------------------------------
class Ability:
    def __init__(self, name="", description="", source=""):
        self.name = name
        self.description = description
        self.source = source

    def to_dict(self):
        return {"name": self.name, "description": self.description,
                "source": self.source}

    @staticmethod
    def from_dict(d):
        return Ability(d.get("name", ""), d.get("description", ""),
                       d.get("source", ""))


class Item:
    def __init__(self, name="", description="", qty=1):
        self.id = uid("itm")
        self.name = name
        self.description = description
        self.qty = int(qty)

    def to_dict(self):
        return {"id": self.id, "name": self.name,
                "description": self.description, "qty": self.qty}

    @staticmethod
    def from_dict(d):
        if isinstance(d, str):           # legacy inventory entries
            return Item(d)
        it = Item(d.get("name", ""), d.get("description", ""), d.get("qty", 1))
        it.id = d.get("id", it.id)
        return it


class Resource:
    """A named expendable pool, e.g. spell slots or charges."""

    def __init__(self, label="", max=0, used=0):
        self.id = uid("res")
        self.label = label
        self.max = int(max)
        self.used = int(used)

    def to_dict(self):
        return {"id": self.id, "label": self.label,
                "max": self.max, "used": self.used}

    @staticmethod
    def from_dict(d):
        r = Resource(d.get("label", ""), d.get("max", 0), d.get("used", 0))
        r.id = d.get("id", r.id)
        return r


class Character:
    def __init__(self, name="", player="", char_class="", level=1,
                 race="", hp=10, max_hp=10, ac=10):
        self.id = uid("pc")
        self.name = name
        self.player = player
        self.char_class = char_class
        self.level = level
        self.race = race
        self.hp = hp
        self.max_hp = max_hp
        self.temp_hp = 0
        self.ac = ac
        self.abilities = []
        self.resources = []
        self.inventory = []
        self.notes = ""
        self.token_image = None
        self.player_token = new_player_token()

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "player": self.player,
            "class": self.char_class, "level": self.level, "race": self.race,
            "hp": self.hp, "max_hp": self.max_hp, "temp_hp": self.temp_hp,
            "ac": self.ac,
            "abilities": [a.to_dict() for a in self.abilities],
            "resources": [r.to_dict() for r in self.resources],
            "inventory": [i.to_dict() for i in self.inventory],
            "notes": self.notes, "token_image": self.token_image,
            "player_token": self.player_token,
        }

    @staticmethod
    def from_dict(d):
        c = Character(d.get("name", ""), d.get("player", ""),
                      d.get("class", ""), d.get("level", 1),
                      d.get("race", ""), d.get("hp", 10),
                      d.get("max_hp", 10), d.get("ac", 10))
        c.id = d.get("id", c.id)
        c.temp_hp = d.get("temp_hp", 0)
        c.abilities = [Ability.from_dict(a) for a in d.get("abilities", [])]
        c.inventory = [Item.from_dict(i) for i in d.get("inventory", [])]
        c.notes = d.get("notes", "")
        c.token_image = d.get("token_image")
        c.player_token = d.get("player_token") or new_player_token()
        if "resources" in d:
            c.resources = [Resource.from_dict(r) for r in d["resources"]]
        else:
            c.resources = []
            for s in d.get("spell_slots", []):
                if s.get("max", 0) > 0 or s.get("used", 0) > 0:
                    c.resources.append(Resource(s.get("label", "Ресурс"),
                                                s.get("max", 0), s.get("used", 0)))
        return c


class Monster:
    STAT_KEYS = ["СИЛ", "ЛОВ", "ТЕЛ", "ИНТ", "МДР", "ХАР"]

    def __init__(self, name="", monster_type="", size="Средний", alignment="",
                 ac=10, hp=10, hp_dice="", speed=""):
        self.id = uid("mon")
        self.name = name
        self.monster_type = monster_type
        self.size = size
        self.alignment = alignment
        self.ac = ac
        self.hp = hp
        self.hp_dice = hp_dice
        self.speed = speed
        self.stats = {k: 10 for k in self.STAT_KEYS}
        self.saves = []
        self.skills = []
        self.damage_vuln = []
        self.damage_res = []
        self.damage_imm = []
        self.cond_imm = []
        self.senses = []
        self.languages = []
        self.cr = ""
        self.traits = []
        self.actions = []
        self.legendary = []
        self.notes = ""
        self.token_image = None

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "type": self.monster_type,
            "size": self.size, "alignment": self.alignment, "ac": self.ac,
            "hp": self.hp, "hp_dice": self.hp_dice, "speed": self.speed,
            "stats": dict(self.stats), "saves": list(self.saves),
            "skills": list(self.skills),
            "damage_vuln": list(self.damage_vuln),
            "damage_res": list(self.damage_res),
            "damage_imm": list(self.damage_imm),
            "cond_imm": list(self.cond_imm),
            "senses": list(self.senses), "languages": list(self.languages),
            "cr": self.cr,
            "traits": list(self.traits), "actions": list(self.actions),
            "legendary": list(self.legendary), "notes": self.notes,
            "token_image": self.token_image,
        }

    @staticmethod
    def from_dict(d):
        m = Monster(d.get("name", ""), d.get("type", ""), d.get("size", "Средний"),
                    d.get("alignment", ""), d.get("ac", 10), d.get("hp", 10),
                    d.get("hp_dice", ""), d.get("speed", ""))
        m.id = d.get("id", m.id)
        for k in m.STAT_KEYS:
            m.stats[k] = d.get("stats", {}).get(k, 10)
        m.saves = _as_pair_list(d.get("saves", []))
        m.skills = _as_pair_list(d.get("skills", []))
        m.damage_vuln = _as_str_list(d.get("damage_vuln", []))
        m.damage_res = _as_str_list(d.get("damage_res", []))
        m.damage_imm = _as_str_list(d.get("damage_imm", []))
        m.cond_imm = _as_str_list(d.get("cond_imm", []))
        m.senses = _as_str_list(d.get("senses", []))
        m.languages = _as_str_list(d.get("languages", []))
        m.cr = d.get("cr", "")
        m.traits = list(d.get("traits", []))
        m.actions = list(d.get("actions", []))
        m.legendary = list(d.get("legendary", []))
        m.notes = d.get("notes", "")
        m.token_image = d.get("token_image")
        return m


class Combatant:
    def __init__(self, name="", initiative=0, hp=0, max_hp=0, kind="monster"):
        self.id = uid("cb")
        self.name = name
        self.initiative = initiative
        self.hp = hp
        self.max_hp = max_hp
        self.kind = kind
        self.active = False
        self.conditions = []
        self.linked_id = None
        self.linked_kind = None

    def to_dict(self):
        return {"id": self.id, "name": self.name,
                "initiative": self.initiative, "hp": self.hp,
                "max_hp": self.max_hp, "kind": self.kind,
                "active": self.active, "conditions": list(self.conditions),
                "linked_id": self.linked_id, "linked_kind": self.linked_kind}

    @staticmethod
    def from_dict(d):
        c = Combatant(d.get("name", ""), d.get("initiative", 0),
                      d.get("hp", 0), d.get("max_hp", 0), d.get("kind", "monster"))
        c.id = d.get("id", c.id)
        c.active = d.get("active", False)
        c.conditions = list(d.get("conditions", []))
        c.linked_id = d.get("linked_id")
        c.linked_kind = d.get("linked_kind")
        return c


class Token:
    def __init__(self, name="", x=0, y=0, size=60, color="#cc3333"):
        self.id = uid("tok")
        self.name = name
        self.x = float(x)
        self.y = float(y)
        self.size = float(size)
        self.rotation = 0.0
        self.image = None
        self.color = color
        self.hp = None
        self.max_hp = None
        self.hidden = False
        self.visible = True
        self.show_label = True
        self.linked_id = None
        self.linked_kind = None

    def to_dict(self):
        return {"id": self.id, "name": self.name, "x": self.x, "y": self.y,
                "size": self.size, "rotation": self.rotation,
                "image": self.image, "color": self.color,
                "hp": self.hp, "max_hp": self.max_hp,
                "hidden": self.hidden, "visible": self.visible,
                "show_label": self.show_label,
                "linked_id": self.linked_id, "linked_kind": self.linked_kind}

    @staticmethod
    def from_dict(d):
        t = Token(d.get("name", ""), d.get("x", 0), d.get("y", 0),
                  d.get("size", 60), d.get("color", "#cc3333"))
        t.id = d.get("id", t.id)
        t.rotation = d.get("rotation", 0.0)
        t.image = d.get("image")
        t.hp = d.get("hp")
        t.max_hp = d.get("max_hp")
        t.hidden = d.get("hidden", False)
        t.visible = d.get("visible", True)
        t.show_label = d.get("show_label", True)
        t.linked_id = d.get("linked_id")
        t.linked_kind = d.get("linked_kind")
        return t


class Drawing:
    def __init__(self, kind="freehand", color="#ffffff", width=3, fill=""):
        self.id = uid("drw")
        self.kind = kind
        self.points = []
        self.color = color
        self.width = width
        self.fill = fill

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "points": self.points,
                "color": self.color, "width": self.width, "fill": self.fill}

    @staticmethod
    def from_dict(d):
        dr = Drawing(d.get("kind", "freehand"), d.get("color", "#ffffff"),
                     d.get("width", 3), d.get("fill", ""))
        dr.id = d.get("id", dr.id)
        dr.points = [list(p) for p in d.get("points", [])]
        return dr


class Overlay:
    def __init__(self, kind="image", path="", x=0, y=0, w=200, h=150,
                 rotation=0.0):
        self.id = uid("ovl")
        self.kind = kind
        self.path = path
        self.x = float(x)
        self.y = float(y)
        self.w = float(w)
        self.h = float(h)
        self.rotation = rotation

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "path": self.path,
                "x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "rotation": self.rotation}

    @staticmethod
    def from_dict(d):
        o = Overlay(d.get("kind", "image"), d.get("path", ""),
                    d.get("x", 0), d.get("y", 0), d.get("w", 200),
                    d.get("h", 150), d.get("rotation", 0.0))
        o.id = d.get("id", o.id)
        return o


class GameMap:
    def __init__(self):
        self.width = 1600
        self.height = 1000
        self.background = None
        self.grid = True
        self.grid_size = 40

    def to_dict(self):
        return {"width": self.width, "height": self.height,
                "background": self.background, "grid": self.grid,
                "grid_size": self.grid_size}

    @staticmethod
    def from_dict(d):
        g = GameMap()
        g.width = d.get("width", 1600)
        g.height = d.get("height", 1000)
        g.background = d.get("background")
        g.grid = d.get("grid", True)
        g.grid_size = d.get("grid_size", 40)
        return g


class Scene:
    def __init__(self, name="Сцена 1", map_=None):
        self.id = uid("scn")
        self.name = name
        self.map = map_ or GameMap()
        self.tokens = []
        self.drawings = []
        self.overlays = []

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "map": self.map.to_dict(),
            "tokens": [t.to_dict() for t in self.tokens],
            "drawings": [d.to_dict() for d in self.drawings],
            "overlays": [o.to_dict() for o in self.overlays],
        }

    @staticmethod
    def from_dict(d):
        s = Scene(d.get("name", "Сцена"), GameMap.from_dict(d.get("map", {})))
        s.id = d.get("id", s.id)
        s.tokens = [Token.from_dict(x) for x in d.get("tokens", [])]
        s.drawings = [Drawing.from_dict(x) for x in d.get("drawings", [])]
        s.overlays = [Overlay.from_dict(x) for x in d.get("overlays", [])]
        return s


# ---------------------------------------------------------------------------
# Central state
# ---------------------------------------------------------------------------
class GameState:
    def __init__(self, campaign_name="Новая кампания"):
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.revision = 0
        self.campaign_name = campaign_name
        self.scenes = [Scene("Сцена 1")]
        self.active_scene_index = 0
        self.characters = []
        self.monsters = []
        self.combatants = []
        self.dice_log = []
        self.music = {"tracks": [], "current": None, "volume": 0.7,
                      "playing": False}
        self.notes = ""
        self.viewport = None
        self.plugins = {}          # {plugin_name: data}

    # -- active scene accessors --------------------------------------------
    @property
    def scene(self):
        if not self.scenes:
            self.scenes = [Scene("Сцена 1")]
            self.active_scene_index = 0
        idx = max(0, min(int(self.active_scene_index), len(self.scenes) - 1))
        self.active_scene_index = idx
        return self.scenes[idx]

    @property
    def map(self):
        return self.scene.map

    @property
    def tokens(self):
        return self.scene.tokens

    @property
    def drawings(self):
        return self.scene.drawings

    @property
    def overlays(self):
        return self.scene.overlays

    # -- scene management ---------------------------------------------------
    def add_scene(self, name=None):
        name = name or ("Сцена %d" % (len(self.scenes) + 1))
        self.scenes.append(Scene(name))
        self.active_scene_index = len(self.scenes) - 1
        self.bump()
        return self.scenes[-1]

    def delete_scene(self, index):
        if len(self.scenes) <= 1:
            return False
        if 0 <= index < len(self.scenes):
            del self.scenes[index]
            self.active_scene_index = min(self.active_scene_index,
                                          len(self.scenes) - 1)
            self.bump()
            return True
        return False

    def set_active_scene(self, index):
        if 0 <= index < len(self.scenes):
            self.active_scene_index = index
            self.bump()

    # -- mutation helpers --------------------------------------------------
    def bump(self):
        with self.cond:
            self.revision += 1
            self.cond.notify_all()

    def roll(self, expression, roller="Мастер"):
        total, detail = evaluate_dice(expression)
        entry = {"id": uid("roll"), "expression": expression,
                 "total": total, "detail": detail, "roller": roller,
                 "ts": now_iso()}
        self.dice_log.insert(0, entry)
        self.dice_log = self.dice_log[:100]
        self.bump()
        return entry

    def next_turn(self):
        ordered = self.ordered_combatants()
        if not ordered:
            return None
        active = self.active_combatant()
        idx = ordered.index(active) if active in ordered else -1
        nxt = ordered[(idx + 1) % len(ordered)]
        for c in self.combatants:
            c.active = (c.id == nxt.id)
        self.bump()
        return nxt

    def active_combatant(self):
        for c in self.combatants:
            if c.active:
                return c
        return None

    def ordered_combatants(self):
        return sorted(self.combatants, key=lambda c: c.initiative, reverse=True)

    def find_character_by_token(self, token):
        for c in self.characters:
            if c.player_token == token:
                return c
        return None

    # -- persistence --------------------------------------------------------
    def to_state(self):
        return {
            "campaign_name": self.campaign_name,
            "scenes": [s.to_dict() for s in self.scenes],
            "active_scene": self.active_scene_index,
            "characters": [c.to_dict() for c in self.characters],
            "monsters": [m.to_dict() for m in self.monsters],
            "combatants": [c.to_dict() for c in self.combatants],
            "dice_log": self.dice_log,
            "music": self.music,
            "notes": self.notes,
            "viewport": self.viewport,
            "plugins": self.plugins,
        }

    @classmethod
    def from_state(cls, data):
        st = cls(data.get("campaign_name") or data.get("session_name")
                 or "Кампания")
        st.scenes = [Scene.from_dict(s) for s in data.get("scenes", [])]
        if not st.scenes:
            st.scenes = [Scene("Сцена 1")]
        st.active_scene_index = int(data.get("active_scene", 0))
        st.characters = [Character.from_dict(d) for d in data.get("characters", [])]
        st.monsters = [Monster.from_dict(d) for d in data.get("monsters", [])]
        st.combatants = [Combatant.from_dict(d) for d in data.get("combatants", [])]
        st.dice_log = list(data.get("dice_log", []))
        st.music = data.get("music") or st.music
        st.notes = data.get("notes", "")
        st.viewport = data.get("viewport")
        st.plugins = data.get("plugins") or {}
        return st

    @classmethod
    def from_legacy(cls, data):
        """Migrate the pre-scenes single-map format."""
        st = cls(data.get("session_name") or "Моя кампания")
        scene = Scene("Сцена 1", GameMap.from_dict(data.get("map", {})))
        scene.tokens = [Token.from_dict(d) for d in data.get("tokens", [])]
        scene.drawings = [Drawing.from_dict(d) for d in data.get("drawings", [])]
        scene.overlays = [Overlay.from_dict(d) for d in data.get("overlays", [])]
        st.scenes = [scene]
        st.active_scene_index = 0
        st.characters = [Character.from_dict(d) for d in data.get("characters", [])]
        st.monsters = [Monster.from_dict(d) for d in data.get("monsters", [])]
        st.combatants = [Combatant.from_dict(d) for d in data.get("combatants", [])]
        st.dice_log = list(data.get("dice_log", []))
        st.music = data.get("music") or st.music
        st.notes = data.get("notes", "")
        st.plugins = data.get("plugins") or {}
        return st

    def save(self, path):
        with self.lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_state(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "scenes" in data:
            return cls.from_state(data)
        return cls.from_legacy(data)

    # -- snapshot for the web client ----------------------------------------
    def snapshot(self):
        with self.lock:
            sc = self.scene
            return {
                "revision": self.revision,
                "campaign": self.campaign_name,
                "scene": sc.to_dict(),
                "characters": [c.to_dict() for c in self.characters],
                "monsters": [m.to_dict() for m in self.monsters],
                "combatants": [c.to_dict() for c in sorted(
                    self.combatants, key=lambda c: c.initiative, reverse=True)],
                "dice_log": self.dice_log,
                "music": self.music,
                "viewport": self.viewport,
                "plugins": self.plugins,
            }

    def player_payload(self, token):
        with self.lock:
            c = self.find_character_by_token(token)
            if c is None:
                return None
            return {"revision": self.revision,
                    "campaign": self.campaign_name,
                    "character": c.to_dict(),
                    "plugins": self.plugins}


# ---------------------------------------------------------------------------
# Dice expression evaluator
# ---------------------------------------------------------------------------
def _die(sides):
    return random.randint(1, sides)


def evaluate_dice(expression):
    """Evaluate a simple dice expression, returning (total, detail_string)."""
    expr = expression.replace(" ", "").lower()
    if not expr:
        return 0, ""
    total = 0
    detail_parts = []
    tokens = re.findall(r'([+-]?)(\d*d\d+|\d+)', expr)
    if not tokens:
        raise ValueError("Не удалось разобрать выражение: %s" % expression)
    for sign, term in tokens:
        sign = -1 if sign == '-' else 1
        if 'd' in term:
            n_str, s_str = term.split('d')
            n = int(n_str) if n_str else 1
            s = int(s_str)
            if not (1 <= s <= 1000) or not (0 <= n <= 1000):
                raise ValueError("Недопустимые параметры кубика: %s" % term)
            rolls = [_die(s) for _ in range(n)]
            subtotal = sum(rolls)
            total += sign * subtotal
            detail_parts.append("%s(%s)" % (("" if sign > 0 else "-") + term,
                                            "+".join(str(r) for r in rolls)))
        else:
            v = int(term)
            total += sign * v
            detail_parts.append("%s%d" % ("+" if sign > 0 else "-", v))
    detail = " ".join(detail_parts).lstrip("+")
    return total, detail