/* Вики игрока. Парсер [[..]] + markdown-lite + вкладка. */
"use strict";

(function () {
  const CAT_LABEL = {
    item: "Предмет", ability: "Способность", spell: "Заклинание",
    monster: "Монстр", npc: "NPC", location: "Локация",
    event: "Событие", lore: "Мир", other: "Другое"
  };
  const CAT_ORDER = ["ability", "spell", "item", "monster", "npc",
                     "location", "event", "lore", "other"];

  let WIKI = { entries: {} };
  let viewStack = [];

  const norm = s => String(s || "").trim().toLocaleLowerCase();
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

  function findEntry(title) {
    const n = norm(title);
    for (const id in WIKI.entries) {
      const e = WIKI.entries[id];
      if (norm(e.title) === n) return e;
    }
    return null;
  }

  /* ---------- inline ---------- */
  function renderInline(text) {
    let s = esc(text);
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g,
      (_, a, u) => `<img class="wiki-img" src="${esc(u)}" alt="${esc(a)}">`);
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
      (_, t, u) => `<a class="wiki-ext" href="${esc(u)}" target="_blank" rel="noopener">${t}</a>`);
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    s = s.replace(/\[\[([^\[\]|\n]+?)(?:\|([^\[\]\n]+))?\]\]/g, (_, title, alias) => {
      const t = title.trim(), label = (alias || t).trim();
      if (findEntry(t)) {
        return `<a href="#" class="wiki-link" data-title="${esc(t)}">${esc(label)}</a>`;
      }
      return `<span class="wiki-missing" title="Ссылка не найдена">${esc(label)} (ссылка не найдена)</span>`;
    });
    return s;
  }

  /* ---------- block ---------- */
  function splitRow(line) {
    return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "")
      .split("|").map(x => x.trim());
  }

  function renderMarkdown(src) {
    const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0, listType = null, listBuf = null;
    const flushList = () => {
      if (listBuf) { out.push(`<${listType}>${listBuf.join("")}</${listType}>`); }
      listType = null; listBuf = null;
    };

    while (i < lines.length) {
      const line = lines[i];

      if (line.startsWith("```")) {
        flushList();
        const buf = [];
        i++;
        while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]);
        i++;
        out.push(`<pre class="wiki-pre"><code>${esc(buf.join("\n"))}</code></pre>`);
        continue;
      }

      const hm = /^(#{1,6})\s+(.*)$/.exec(line);
      if (hm) {
        flushList();
        out.push(`<h${hm[1].length}>${renderInline(hm[2])}</h${hm[1].length}>`);
        i++; continue;
      }

      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length &&
          /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        flushList();
        const head = splitRow(line);
        i += 2;
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          rows.push(splitRow(lines[i++])); 
        }
        let t = '<table class="wiki-table"><thead><tr>';
        for (const c of head) t += `<th>${renderInline(c)}</th>`;
        t += "</tr></thead><tbody>";
        for (const r of rows) {
          t += "<tr>";
          for (const c of r) t += `<td>${renderInline(c)}</td>`;
          t += "</tr>";
        }
        out.push(t + "</tbody></table>");
        continue;
      }

      let m = /^\s*[-*+]\s+(.*)$/.exec(line);
      if (m) {
        if (listType !== "ul") { flushList(); listType = "ul"; listBuf = []; }
        listBuf.push(`<li>${renderInline(m[1])}</li>`);
        i++; continue;
      }
      m = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (m) {
        if (listType !== "ol") { flushList(); listType = "ol"; listBuf = []; }
        listBuf.push(`<li>${renderInline(m[1])}</li>`);
        i++; continue;
      }

      if (!line.trim()) { flushList(); i++; continue; }

      flushList();
      const buf = [line]; i++;
      while (i < lines.length && lines[i].trim() &&
             !/^(#{1,6})\s+/.test(lines[i]) &&
             !/^\s*[-*+]\s+/.test(lines[i]) &&
             !/^\s*\d+[.)]\s+/.test(lines[i]) &&
             !/^```/.test(lines[i]) &&
             !/^\s*\|.*\|\s*$/.test(lines[i])) {
        buf.push(lines[i++]);
      }
      out.push(`<p>${renderInline(buf.join(" "))}</p>`);
    }
    flushList();
    return out.join("\n");
  }

  /* ---------- public API ---------- */
  function setWikiData(plugins) {
    WIKI = (plugins && plugins.wiki) || { entries: {} };
    if (!WIKI.entries) WIKI.entries = {};
    // Перерисовываем открытую статью, если её правят.
    if (viewStack.length && document.body.classList.contains("wiki-mode")) {
      const cur = viewStack[viewStack.length - 1];
      const fresh = findEntry(cur.title);
      if (fresh) openArticle(fresh.title, true);
    }
  }

  function renderText(text) { return renderInline(text || ""); }

  /* ---------- UI ---------- */
  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function showList() {
    const root = document.getElementById("wiki-view");
    if (!root) return;
    viewStack = [];
    root.innerHTML = "";

    const entries = Object.values(WIKI.entries)
      .sort((a, b) => (a.title || "").localeCompare(b.title || "", "ru"));
    if (!entries.length) {
      root.innerHTML = '<div class="wiki-empty">Мастер ещё не создал статей.</div>';
      return;
    }
    const groups = {};
    for (const e of entries) (groups[e.category || "other"] ||= []).push(e);
    for (const cat of CAT_ORDER) {
      if (!groups[cat]) continue;
      const g = el("div", "wiki-group");
      g.appendChild(el("h2", "wiki-group-title", CAT_LABEL[cat] || cat));
      const list = el("div", "wiki-list");
      for (const e of groups[cat]) {
        const btn = el("button", "wiki-item", esc(e.title));
        btn.addEventListener("click", () => openArticleByEntry(e));
        list.appendChild(btn);
      }
      g.appendChild(list);
      root.appendChild(g);
    }
  }

  function openArticleByEntry(entry, replaceTop) {
    if (!entry) { showList(); return; }
    if (replaceTop && viewStack.length) viewStack[viewStack.length - 1] = entry;
    else viewStack.push(entry);
    openArticle(entry.title, true);
  }

  function openArticle(title, keepStack) {
    const root = document.getElementById("wiki-view");
    if (!root) return;
    if (!keepStack) viewStack = [findEntry(title) || { title, id: null }];

    root.innerHTML = "";
    const back = el("button", "wiki-back", "← Назад");
    back.addEventListener("click", () => {
      viewStack.pop();
      if (viewStack.length) openArticle(viewStack[viewStack.length - 1].title, true);
      else showList();
    });
    root.appendChild(back);

    const entry = findEntry(title);
    if (!entry) {
      root.appendChild(el("div", "wiki-missing-big",
        "Статья «" + esc(title) + "» не найдена."));
      return;
    }
    const head = el("div", "wiki-head");
    head.innerHTML =
      `<div class="wiki-eyebrow">${esc(CAT_LABEL[entry.category] || "")}</div>` +
      `<h1>${esc(entry.title)}</h1>`;
    root.appendChild(head);
    const body = el("div", "wiki-body");
    body.innerHTML = renderMarkdown(entry.body || "");
    root.appendChild(body);
  }

  function showTab() {
    document.body.classList.add("wiki-mode");
    document.getElementById("invalid")?.classList.add("hidden");
    document.getElementById("wiki-view") &&
      (document.getElementById("wiki-view").innerHTML = "");
    showList();
    document.getElementById("tab-sheet")?.classList.remove("active");
    document.getElementById("tab-wiki")?.classList.add("active");
  }

  function hideTab() {
    document.body.classList.remove("wiki-mode");
    document.getElementById("tab-sheet")?.classList.add("active");
    document.getElementById("tab-wiki")?.classList.remove("active");
  }

  function installTabs() {
    const topbar = document.querySelector(".topbar");
    if (!topbar || document.getElementById("tab-wiki")) return;
    const nav = el("div", "sheet-tabs");
    nav.innerHTML = `
      <button id="tab-sheet" class="sheet-tab active" type="button">📋 Лист персонажа</button>
      <button id="tab-wiki"  class="sheet-tab" type="button">📖 Вики</button>`;
    topbar.appendChild(nav);
    document.getElementById("tab-sheet").addEventListener("click", hideTab);
    document.getElementById("tab-wiki").addEventListener("click", showTab);
  }

  // Глобальный делегированный обработчик: любой [[..]] в любом описании.
  document.addEventListener("click", ev => {
    const a = ev.target.closest(".wiki-link");
    if (!a || !a.dataset.title) return;
    ev.preventDefault();
    ev.stopPropagation();
    showTab();
    openArticle(a.dataset.title);
  });

  window.Wiki = { setWikiData, renderText, installTabs,
                  showTab, hideTab, showList, openArticle };
})();