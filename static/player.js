/* Лист игрока — рендерит данные из /api/player/<token> */
"use strict";

const TOKEN = window.PLAYER_TOKEN || "";

const ABILITY_ORDER = [
  { key: "СИЛ", label: "СИЛ", full: "Сила" },
  { key: "ЛОВ", label: "ЛОВ", full: "Ловкость" },
  { key: "ТЕЛ", label: "ТЕЛ", full: "Телосложение" },
  { key: "ИНТ", label: "ИНТ", full: "Интеллект" },
  { key: "МДР", label: "МДР", full: "Мудрость" },
  { key: "ХАР", label: "ХАР", full: "Харизма" }
];

const SKILL_MAP = {
  "Акробатика": "ЛОВ", "Расследование": "ИНТ", "Атлетика": "СИЛ",
  "Внимательность": "МДР", "Выживание": "МДР", "Выступление": "ХАР",
  "Запугивание": "ХАР", "История": "ИНТ", "Ловкость рук": "ЛОВ",
  "Магия": "ИНТ", "Медицина": "МДР", "Обман": "ХАР",
  "Природа": "ИНТ", "Проницательность": "МДР", "Религия": "ИНТ",
  "Скрытность": "ЛОВ", "Убеждение": "ХАР", "Уход за животными": "МДР"
};

let lastRevision = null;

function $(id) { return document.getElementById(id); }
function mod(score) { return Math.floor((Number(score) - 10) / 2); }
function signed(n) { return (n >= 0 ? "+" : "") + n; }
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

/* URL медиафайла — так же, как в map.js */
function mediaUrl(path) {
  if (!path) return null;
  if (/^https?:\/\//i.test(path) || path.startsWith("/")) return path;
  return "/media/" + encodeURI(path);
}

function hpClass(ratio) {
  if (ratio <= 0) return "bad";
  if (ratio > 0.5) return "";
  if (ratio > 0.25) return "warn";
  return "bad";
}

/* Аватар: картинка token_image либо буква */
function renderAvatar(c) {
  const avatar = $("avatar");
  if (!avatar) return;
  const url = mediaUrl(c.token_image);
  avatar.innerHTML = "";
  if (url) {
    const img = document.createElement("img");
    img.src = url;
    img.alt = "";
    img.onerror = () => {
      // если картинка не загрузилась — откатываемся к букве
      avatar.innerHTML = "";
      avatar.textContent = (c.name || "?").charAt(0).toUpperCase();
      avatar.classList.remove("has-image");
    };
    avatar.appendChild(img);
    avatar.classList.add("has-image");
  } else {
    avatar.textContent = (c.name || "?").charAt(0).toUpperCase();
    avatar.classList.remove("has-image");
  }
}

function render(c) {
  // --- Header
  $("char-name").textContent = c.name || "Безымянный";
  renderAvatar(c);
  $("char-sub").textContent = [c.class, c.level ? c.level + " ур." : "",
                                c.race, c.player].filter(Boolean).join(" · ");

  // --- HP
  const ratio = c.max_hp ? c.hp / c.max_hp : 0;
  $("hp-text").textContent = `${c.hp}/${c.max_hp} ХП`;
  const bar = $("hp-bar");
  bar.style.width = Math.max(0, Math.min(100, ratio * 100)) + "%";
  bar.className = "progress-fill " + hpClass(ratio);
  $("temp-hp").textContent = c.temp_hp || 0;

  // --- Base stats
  $("stat-ac").textContent = (c.ac ?? "—");
  $("stat-level").textContent = (c.level ?? "—");
  $("stat-prof").textContent = "+" + (c.proficiency_bonus ?? 2);

  const scores = c.ability_scores || {};
  const pb = Number(c.proficiency_bonus ?? 2);

  // --- Abilities (6 scores)
  $("abilities-grid").innerHTML = ABILITY_ORDER.map(a => {
    const score = Number(scores[a.key] ?? 10);
    return `<div class="ability" title="${esc(a.full)}">
      <span>${a.label}</span>
      <strong>${score}</strong>
      <small>${signed(mod(score))}</small>
    </div>`;
  }).join("");

  // --- Saves
  const saveProfs = new Set(c.save_proficiencies || []);
  $("saves-grid").innerHTML = ABILITY_ORDER.map(a => {
    const score = Number(scores[a.key] ?? 10);
    const prof = saveProfs.has(a.key);
    const total = mod(score) + (prof ? pb : 0);
    return `<div class="save ${prof ? "proficient" : ""}">
      <span class="save-label">${a.label}</span>
      <strong class="save-value">${signed(total)}</strong>
      <div class="save-dot"></div>
    </div>`;
  }).join("");

  // --- Skills
  const skillProfs = new Set(c.skill_proficiencies || []);
  const skillExpertise = new Set(c.skill_expertise || []);
  if (skillProfs.has("Анализ")) { skillProfs.delete("Анализ"); skillProfs.add("Расследование"); }
  if (skillExpertise.has("Анализ")) { skillExpertise.delete("Анализ"); skillExpertise.add("Расследование"); }

  const skillNames = Object.keys(SKILL_MAP).sort((a, b) => a.localeCompare(b, "ru"));
  $("skills-list").innerHTML = skillNames.map(name => {
    const ab = SKILL_MAP[name];
    const score = Number(scores[ab] ?? 10);
    const prof = skillProfs.has(name);
    const exp = skillExpertise.has(name);
    const bonus = (prof || exp ? pb : 0) + (exp ? pb : 0);
    const total = mod(score) + bonus;
    const cls = exp ? "skill expertise" : prof ? "skill proficient" : "skill";
    return `<div class="${cls}" title="${esc(name)} (${ab})">
      <span class="skill-mark"></span>
      <span class="skill-name">${esc(name)}</span>
      <span class="skill-value">${signed(total)}</span>
    </div>`;
  }).join("");

  // --- Weapons
  const weapons = c.weapon_proficiencies || [];
  if (weapons.length) {
    $("weapons-section").classList.remove("hidden");
    $("weapons-list").innerHTML = weapons.map(w =>
      `<span class="tag">${esc(w)}</span>`).join("");
  } else {
    $("weapons-section").classList.add("hidden");
  }

  // --- Spells / abilities
  const abilities = c.abilities || [];
  if (abilities.length) {
    $("spells-section").classList.remove("hidden");
    $("spells-list").innerHTML = abilities.map((a, i) => `
      <div class="spell-card" data-spell="${i}">
        <div class="spell-main">
          <div class="spell-level">${esc((a.source || "").includes("круг") ? "✦" : "★")}</div>
          <div class="spell-title">
            <strong>${esc(a.name || "Без названия")}</strong>
            ${a.source ? `<span class="src">${esc(a.source)}</span>` : ""}
          </div>
          <div class="spell-toggle">⌄</div>
        </div>
        <div class="spell-desc">${window.Wiki ? Wiki.renderText(a.description || "Нет описания.") : esc(a.description || "Нет описания.")}</div>
      </div>
    `).join("");
    document.querySelectorAll(".spell-card").forEach(card => {
      card.querySelector(".spell-main").addEventListener("click", () =>
        card.classList.toggle("open"));
    });
  } else {
    $("spells-section").classList.add("hidden");
  }

  // --- Resources
  const resources = (c.resources || []).filter(r => r.max > 0);
  if (resources.length) {
    $("resources-section").classList.remove("hidden");
    $("resources-list").innerHTML = resources.map(r => {
      const dots = [];
      for (let i = 0; i < r.max; i++) {
        const available = i < (r.max - r.used);
        dots.push(`<span class="resource-dot ${available ? "on" : ""}"></span>`);
      }
      return `<div class="resource-row">
        <div class="resource-label">${esc(r.label || "Ресурс")}</div>
        <div class="resource-dots">${dots.join("")}</div>
        <div class="resource-count">${r.max - r.used}/${r.max}</div>
      </div>`;
    }).join("");
  } else {
    $("resources-section").classList.add("hidden");
  }

  // --- Inventory
  const inventory = c.inventory || [];
  if (inventory.length) {
    $("inventory-section").classList.remove("hidden");
    $("inventory-list").innerHTML = inventory.map((it, i) => `
      <div class="item-row" data-item="${i}">
        <div class="item-main">
          <strong>${esc(it.name || "Предмет")}</strong>
          <span class="item-qty">×${Number(it.qty) || 1}</span>
        </div>
        ${it.description ? `<div class="item-desc">${window.Wiki ? Wiki.renderText(it.description) : esc(it.description)}</div>` : ""}
      </div>
    `).join("");
    document.querySelectorAll(".item-row").forEach(row => {
      if (row.querySelector(".item-desc")) {
        row.querySelector(".item-main").addEventListener("click", () =>
          row.classList.toggle("open"));
      }
    });
  } else {
    $("inventory-section").classList.add("hidden");
  }

  // --- Notes
  if (c.notes && c.notes.trim()) {
    $("notes-section").classList.remove("hidden");
    $("notes-text").innerHTML = window.Wiki ? Wiki.renderText(c.notes) : esc(c.notes);
  } else {
    $("notes-section").classList.add("hidden");
  }
  if (window.Wiki) window.Wiki.installTabs();
  $("sheet").classList.remove("hidden");
  $("invalid").classList.add("hidden");
}

/* Сворачиваемые секции */
function setupCollapsibles() {
  document.querySelectorAll("[data-collapse]").forEach(btn => {
    btn.addEventListener("click", () => {
      const section = btn.closest("[data-collapse-section]");
      if (section) section.classList.toggle("collapsed");
    });
  });
}

/* Загрузка с сервера */
async function load() {
  try {
    const r = await fetch("/api/player/" + encodeURIComponent(TOKEN),
                          { cache: "no-store" });
    if (!r.ok) {
      $("sheet").classList.add("hidden");
      $("invalid").classList.remove("hidden");
      return;
    }
    const data = await r.json();
    if (data.revision === lastRevision) return;
    lastRevision = data.revision;

    if (data.campaign) $("campaign-name").textContent = data.campaign;
    if (window.Wiki) window.Wiki.setWikiData(data.plugins);
    if (data.character) render(data.character);
  } catch (e) {
    /* тихо игнорируем — попробуем в следующий раз */
  }
}

/* SSE + polling fallback */
function connect() {
  const dot = $("conn-dot");
  try {
    const es = new EventSource("/api/events");
    es.onopen = () => dot.classList.add("on");
    es.onerror = () => dot.classList.remove("on");
    es.onmessage = () => load();
  } catch (e) { /* polling ниже */ }
  setInterval(load, 5000);
}

document.addEventListener("DOMContentLoaded", () => {
  setupCollapsibles();
  load();
  connect();
});