/* Fullscreen map projection. Renders exactly the master's viewport. */
"use strict";

let state = null;
let lastRevision = null;
let currentVp = null;
let currentScale = 1;
let renderToken = 0;

const canvas = document.getElementById("map-canvas");
const ctx = canvas.getContext("2d");

function mediaUrl(path) {
  if (!path) return null;
  if (/^https?:\/\//i.test(path) || path.startsWith("/")) return path;
  return "/media/" + encodeURI(path);
}

const imgCache = {};
function loadImage(url) {
  if (!url) return Promise.resolve(null);
  if (imgCache[url]) return imgCache[url];
  imgCache[url] = new Promise((res) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = () => { imgCache[url] = null; res(null); };
    img.src = url;
  });
  return imgCache[url];
}

const videoCache = {};
function getVideo(url) {
  if (!url) return null;
  if (videoCache[url]) return videoCache[url];
  const v = document.createElement("video");
  v.src = url;
  v.loop = true;
  v.muted = true;
  v.playsInline = true;
  v.load();
  v.play().catch(() => {});
  videoCache[url] = v;
  return v;
}

function viewportOf(sc) {
  const vp = state.viewport;
  const m = sc.map;
  if (vp && vp.w > 1 && vp.h > 1) return vp;
  return { x: 0, y: 0, w: m.width, h: m.height };
}

async function fetchState() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    const data = await r.json();
    if (data.revision === lastRevision && state) return;
    state = data;
    lastRevision = data.revision;
    await render();
  } catch (e) { /* keep last frame */ }
}

async function render() {
  if (!state || !state.scene) return;
  const sc = state.scene;
  const vp = viewportOf(sc);
  const wrap = document.getElementById("map-wrap");
  const aw = Math.max(40, wrap.clientWidth);
  const ah = Math.max(40, wrap.clientHeight);
  const s = Math.min(aw / vp.w, ah / vp.h);
  const pw = Math.max(1, Math.round(vp.w * s));
  const ph = Math.max(1, Math.round(vp.h * s));
  canvas.width = pw;
  canvas.height = ph;
  canvas.style.width = pw + "px";
  canvas.style.height = ph + "px";
  currentVp = vp;
  currentScale = s;

  const token = ++renderToken;
  const urls = [];
  if (sc.map.background) urls.push(mediaUrl(sc.map.background));
  for (const o of sc.overlays) if (o.kind === "image" && o.path) urls.push(mediaUrl(o.path));
  for (const t of sc.tokens) if (t.image) urls.push(mediaUrl(t.image));
  await Promise.all(urls.map(loadImage));
  if (token !== renderToken) return;
  draw();
}

function draw() {
  if (!state || !state.scene || !currentVp) return;
  const sc = state.scene;
  const m = sc.map;
  const vp = currentVp;
  const s = currentScale;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#0b0e14";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.save();
  ctx.scale(s, s);
  ctx.translate(-vp.x, -vp.y);

  // --- 0. map background
  if (m.background) {
    const img = imgCache[mediaUrl(m.background)];
    if (img) ctx.drawImage(img, 0, 0, m.width, m.height);
  } else {
    ctx.fillStyle = "#1b2027";
    ctx.fillRect(0, 0, m.width, m.height);
  }

  // grid (part of the map)
  if (m.grid) {
    ctx.strokeStyle = "#2b3442";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= m.width; x += m.grid_size) {
      ctx.moveTo(x, 0); ctx.lineTo(x, m.height);
    }
    for (let y = 0; y <= m.height; y += m.grid_size) {
      ctx.moveTo(0, y); ctx.lineTo(m.width, y);
    }
    ctx.stroke();
  }

  // --- 1. media / images / video
  for (const o of sc.overlays) {
    const cx = o.x + o.w / 2;
    const cy = o.y + o.h / 2;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((o.rotation || 0) * Math.PI / 180);
    if (o.kind === "image") {
      const img = imgCache[mediaUrl(o.path)];
      if (img) ctx.drawImage(img, -o.w / 2, -o.h / 2, o.w, o.h);
      else {
        ctx.fillStyle = "#26303b";
        ctx.strokeStyle = "#7fb3ff";
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 4]);
        ctx.fillRect(-o.w / 2, -o.h / 2, o.w, o.h);
        ctx.strokeRect(-o.w / 2, -o.h / 2, o.w, o.h);
        ctx.setLineDash([]);
      }
    } else if (o.kind === "video") {
      const v = getVideo(mediaUrl(o.path));
      if (v && v.readyState >= 2) {
        ctx.drawImage(v, -o.w / 2, -o.h / 2, o.w, o.h);
      } else {
        ctx.fillStyle = "#101820";
        ctx.strokeStyle = "#7fb3ff";
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 4]);
        ctx.fillRect(-o.w / 2, -o.h / 2, o.w, o.h);
        ctx.strokeRect(-o.w / 2, -o.h / 2, o.w, o.h);
        ctx.setLineDash([]);
      }
    }
    ctx.restore();
  }

  // --- 2. tokens
  for (const t of sc.tokens) {
    if (t.hidden) continue;
    ctx.save();
    ctx.translate(t.x, t.y);
    ctx.rotate((t.rotation || 0) * Math.PI / 180);
    const r = t.size / 2;
    const img = t.image ? imgCache[mediaUrl(t.image)] : null;
    if (img) {
      // preserve aspect like the master's PIL "contain"
      const ir = img.width / img.height;
      let dw = t.size;
      let dh = t.size;
      if (ir > 1) dh = t.size / ir;
      else dw = t.size * ir;
      ctx.drawImage(img, -dw / 2, -dh / 2, dw, dh);
    } else {
      ctx.fillStyle = t.color || "#cc3333";
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = Math.max(1, t.size / 24);
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.font = `${Math.round(r * 0.8)}px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText((t.name || "?").charAt(0).toUpperCase(), 0, 1);
    }
    ctx.restore();

    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillStyle = "#e6ecf3";
    ctx.font = "11px system-ui, sans-serif";
    if (t.show_label && t.name) ctx.fillText(t.name, t.x, t.y + r + 4);
  }

  // --- 3. drawings
  for (const d of sc.drawings) {
    if (!d.points || d.points.length < 2) continue;
    ctx.strokeStyle = d.color;
    ctx.fillStyle = d.fill || "transparent";
    ctx.lineWidth = d.width;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    if (d.kind === "freehand" || d.kind === "line") {
      ctx.beginPath();
      ctx.moveTo(d.points[0][0], d.points[0][1]);
      for (const p of d.points) ctx.lineTo(p[0], p[1]);
      ctx.stroke();
    } else if (d.kind === "rect") {
      const a = d.points[0], b = d.points[d.points.length - 1];
      ctx.beginPath();
      ctx.rect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      if (d.fill) ctx.fill();
      ctx.stroke();
    } else if (d.kind === "oval") {
      const a = d.points[0], b = d.points[d.points.length - 1];
      ctx.beginPath();
      ctx.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2,
                  Math.abs(b[0] - a[0]) / 2, Math.abs(b[1] - a[1]) / 2,
                  0, 0, Math.PI * 2);
      if (d.fill) ctx.fill();
      ctx.stroke();
    }
  }

  ctx.restore();
}

let lastFrame = 0;
function loop(ts) {
  if (state && state.scene &&
      state.scene.overlays.some((o) => o.kind === "video")) {
    if (ts - lastFrame > 33) {
      lastFrame = ts;
      draw();
    }
  }
  requestAnimationFrame(loop);
}

function connect() {
  try {
    const es = new EventSource("/api/events");
    es.onmessage = () => fetchState();
  } catch (e) { /* polling fallback */ }
  setInterval(fetchState, 3000);
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("btn-fullscreen");
  if (btn) {
    btn.addEventListener("click", () => {
      const el = document.documentElement;
      if (!document.fullscreenElement) {
        el.requestFullscreen && el.requestFullscreen().catch(() => {});
      } else {
        document.exitFullscreen && document.exitFullscreen();
      }
    });
  }
  window.addEventListener("resize", () => render());
  fetchState();
  connect();
  requestAnimationFrame(loop);
});
