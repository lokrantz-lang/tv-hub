/* TV Hub i webbläsaren. Samma data och regler som appen (/device/...), inloggad som en egen enhet.
   Strömmen går via tillägget (/web/stream/...), så webbläsaren ser aldrig Toucans adress eller lösenord. */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get token() { try { return localStorage.getItem("tvhub_token"); } catch { return null; } },
  set token(v) { try { v ? localStorage.setItem("tvhub_token", v) : localStorage.removeItem("tvhub_token"); } catch {} },
  get profile() { try { return localStorage.getItem("tvhub_profile") || ""; } catch { return ""; } },
  set profile(v) { try { localStorage.setItem("tvhub_profile", String(v)); } catch {} },
};
// Hallplattan: ?wall=1 (från Smart). Tillbaka-knapp, stora knappar, Spela på TV:n först och återgång till Smart.
const WALL = new URLSearchParams(location.search).get("wall") === "1";
const SMART_URL = `http://${location.hostname}:8101/?wall=1`;
const WALL_IDLE_MS = 5 * 60 * 1000;
let HOME = null;
let PROFILE = null;
let PAGE = "home";

// ------------------------------------------------------------------ API
class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }

async function api(path, opts = {}) {
  const headers = { "Authorization": "Bearer " + store.token };
  if (store.profile) headers["X-TVHub-Profile"] = store.profile;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  } catch {
    throw new ApiError(0, "TV Hub svarar inte. Är du hemma eller ansluten med Tailscale?");
  }
  let data = null;
  try { data = await res.json(); } catch {}
  if (res.status === 401) { store.token = null; showLogin("Datorn är inte längre inloggad. Logga in igen."); throw new ApiError(401, "Inte inloggad"); }
  if (!res.ok) throw new ApiError(res.status, (data && data.error) || `Fel ${res.status}`);
  return data;
}

function toast(msg, ms = 3200) {
  const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

const fail = (e) => { if (e && e.status !== 401) toast(e.message || String(e), 5000); };

// ------------------------------------------------------------------ inloggning
function showLogin(msg) {
  $("app").classList.add("hidden"); $("login").classList.remove("hidden");
  $("lg-err").textContent = msg || ""; $("lg-code").focus();
}

async function login() {
  const code = $("lg-code").value.replace(/\D/g, "");
  const name = $("lg-name").value.trim() || "Laptop";
  if (code.length < 6) { $("lg-err").textContent = "Skriv koden (6 eller 8 siffror)."; return; }
  $("lg-go").disabled = true;
  try {
    const res = await fetch("/device/pair", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name, model: "Webbläsare", app_version: "web" }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.token) throw new Error(data.error || "Fel kod, eller koden har gått ut.");
    store.token = data.token;
    start();
  } catch (e) { $("lg-err").textContent = e.message; }
  finally { $("lg-go").disabled = false; }
}
$("lg-go").addEventListener("click", login);
$("lg-code").addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });

// ------------------------------------------------------------------ start, profil, navigering
function setupWall() {
  if (!WALL || setupWall.done) return;
  setupWall.done = true;
  document.body.classList.add("wall");
  const back = document.createElement("a");
  back.className = "btn back-smart"; back.href = SMART_URL; back.textContent = "◀ Tillbaka till Smart";
  document.querySelector(".top").prepend(back);
  const loginBack = back.cloneNode(true); loginBack.style.marginTop = "6px";
  document.querySelector(".login-box").append(loginBack);
  $("lg-name").value = "Hallplatta";
  // Ingen rör skärmen på fem minuter och inget spelas här: tillbaka till Smart.
  let timer;
  const arm = () => { clearTimeout(timer); timer = setTimeout(() => { if (!P.session) location.href = SMART_URL; else arm(); }, WALL_IDLE_MS); };
  ["pointerdown", "keydown", "wheel", "touchstart", "scroll"].forEach((ev) => window.addEventListener(ev, arm, { passive: true, capture: true }));
  arm();
}
setupWall();

async function start() {
  if (!store.token) return showLogin();
  $("login").classList.add("hidden"); $("app").classList.remove("hidden");
  try {
    const p = await api("/device/profiles");
    PROFILE = p.active;
    if (p.locked && PROFILE) store.profile = PROFILE.id;
    paintProfile(p);
  } catch (e) { fail(e); }
  route();
}

function paintProfile(p) {
  const b = $("profile-btn");
  b.textContent = PROFILE ? (PROFILE.emoji || PROFILE.name.slice(0, 1)) : "?";
  b.style.background = PROFILE ? PROFILE.color : "#444";
  b.title = PROFILE ? `${PROFILE.name}${p.locked ? "" : " – byt profil"}` : "";
  b.onclick = () => chooseProfile(p.locked ? [] : p.profiles);
  $("side-foot").innerHTML = PROFILE ? `Tittar som <b>${esc(PROFILE.name)}</b><br><a href="#" id="logout">Logga ut datorn</a>` : "";
  const lo = $("logout");
  if (lo) lo.onclick = (e) => { e.preventDefault(); if (confirm("Logga ut den här datorn?")) { store.token = null; location.reload(); } };
}

function chooseProfile(list) {
  modal(`<div style="padding:24px">${list.length ? `<h2 style="margin-top:0">Vem tittar?</h2><div class="chips">${list.map((p) =>
    `<button class="chip" data-p="${p.id}" style="font-size:15px;padding:10px 18px">${esc(p.emoji || "")} ${esc(p.name)}${p.has_pin ? " 🔒" : ""}</button>`).join("")}</div>`
    : `<h2 style="margin-top:0">${esc(PROFILE ? PROFILE.name : "")}</h2>`}
    <div class="acts" style="margin-top:18px"><button class="btn" id="m-logout">Logga ut den här datorn</button><button class="btn" onclick="closeModal()">Stäng</button></div></div>`);
  $("m-logout").onclick = () => { if (confirm("Logga ut den här datorn?")) { store.token = null; location.reload(); } };
  $("modal-box").querySelectorAll("[data-p]").forEach((el) => el.onclick = async () => {
    const p = list.find((x) => String(x.id) === el.dataset.p);
    if (p.has_pin) {
      const pin = prompt(`PIN för ${p.name}`);
      if (pin === null) return;
      try { await api("/device/profile-check", { method: "POST", body: { profile_id: p.id, pin } }); } catch (e) { return fail(e); }
    }
    store.profile = p.id; closeModal(); HOME = null; start();
  });
}

const PAGES = { home: pageHome, live: pageLive, vod: (a) => pageBrowse("vod", a), series: (a) => pageBrowse("series", a),
  sport: pageSport, worldcup: pageWorldcup, recordings: pageRecordings, search: pageSearch };

function route() {
  const [page, ...rest] = (location.hash.slice(1) || "home").split("/");
  PAGE = PAGES[page] ? page : "home";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === PAGE));
  $("page").innerHTML = '<div class="spin"></div>';
  window.scrollTo(0, 0);
  Promise.resolve(PAGES[PAGE](rest.map(decodeURIComponent))).catch(fail);
}
window.addEventListener("hashchange", route);
document.querySelectorAll("#nav a").forEach((a) => a.addEventListener("click", () => { location.hash = a.dataset.page; }));
$("search-form").addEventListener("submit", (e) => { e.preventDefault(); const q = $("search").value.trim(); if (q) location.hash = "search/" + encodeURIComponent(q); });

async function home() {
  if (!HOME) HOME = await api("/device/home?app_version=web");
  return HOME;
}

// ------------------------------------------------------------------ kort
// Via https (Tailscale Funnel) stoppar webbläsaren bilder på http. Då hämtas de via TV Hub.
const imgUrl = (url) => (location.protocol === "https:" && /^http:\/\//.test(url)) ? "/web/img?u=" + encodeURIComponent(url) : url;
function img(url) { return url ? `background-image:url('${esc(imgUrl(url))}')` : ""; }

function posterCard(item, sub) {
  const prog = item.duration_ms ? Math.min(100, Math.round(100 * (item.position_ms || 0) / item.duration_ms)) : 0;
  const name = item.series_name ? `${item.series_name}` : item.name;
  return `<div class="card" data-k="${esc(item.type)}:${esc(item.id)}">
    <div class="img" style="${img(item.icon)}">${item.icon ? "" : `<div class="noimg">${esc(name)}</div>`}${prog ? `<div class="bar"><i style="width:${prog}%"></i></div>` : ""}</div>
    <div class="t">${esc(name)}</div><div class="s">${esc(sub ?? (item.series_name ? item.name : (item.year || "")))}</div></div>`;
}

function channelCard(item, now, progress) {
  return `<div class="card ch" data-k="live:${esc(item.id)}">
    <div class="img" style="${img(item.icon)}">${item.icon ? "" : `<div class="noimg">${esc(item.name)}</div>`}${progress >= 0 ? `<div class="bar"><i style="width:${Math.round(progress / 10)}%"></i></div>` : ""}</div>
    <div class="t">${esc(now || item.name)}</div><div class="s">${esc(now ? item.name : "")}</div></div>`;
}

/** Klick på kort: allt med data-k i elementet. items: uppslag nyckel -> objekt. */
function bindCards(root, items) {
  root.querySelectorAll("[data-k]").forEach((el) => el.addEventListener("click", () => {
    const it = items.get(el.dataset.k); if (it) openItem(it);
  }));
}

function shelf(title, items, kind = "poster") {
  if (!items || !items.length) return "";
  return `<h2>${esc(title)}</h2><div class="row">${items.map((i) => kind === "channel" ? channelCard(i, i.now_title, i.now_progress ?? -1) : posterCard(i)).join("")}</div>`;
}

function keyed(...lists) {
  const m = new Map();
  for (const l of lists) for (const i of l || []) m.set(`${i.type}:${i.id}`, i);
  return m;
}

function openItem(it) {
  if (it.type === "live") return play({ type: "live", id: it.id, name: it.name, icon: it.icon }, it.name);
  if (it.type === "vod") return openVod(it.id);
  if (it.type === "series") return openSeries(it.id);
  if (it.type === "episode") return play({ type: "episode", id: it.id, ext: it.ext, series_id: it.series_id, name: it.name, icon: it.icon }, it.series_name || it.name, it.name);
}

// ------------------------------------------------------------------ Hem
async function pageHome() {
  const h = await home();
  let sport = null;
  try { sport = await api("/device/sports"); } catch {}
  const soon = sport ? [...sport.now, ...sport.upcoming.filter((e) => e.start * 1000 < Date.now() + 86400000)].slice(0, 16) : [];
  const recentLive = h.recent_live || [];
  $("page").innerHTML = `<h1>Hej${PROFILE ? " " + esc(PROFILE.name) : ""}</h1>
    ${shelf("Fortsätt titta", h.continue)}
    ${soon.length ? `<h2>Sport: nu och snart</h2><div class="grid wide" id="sport-soon"></div>` : ""}
    ${shelf("Favoriter", h.favorites)}
    ${recentLive.length ? `<h2>Senast sedda kanaler</h2><div class="row">${recentLive.map((c) => channelCard(c)).join("")}</div>` : ""}
    ${shelf("Nya filmer", h.recent_vod)}
    ${shelf("Nya serier", h.recent_series)}
    ${shelf("Högt betyg", h.top_vod)}`;
  bindCards($("page"), keyed(h.continue, h.favorites, recentLive, h.recent_vod, h.recent_series, h.top_vod));
  if (soon.length) renderEvents($("sport-soon"), soon);
}

// ------------------------------------------------------------------ Live-TV
async function pageLive(args) {
  const h = await home();
  const cats = (h.categories.live || []).filter((c) => !c.hidden && c.count > 0);
  const cid = args[0] || (h.start_categories || {}).live || (cats[0] && cats[0].category_id);
  $("page").innerHTML = `<h1>Live-TV</h1><div class="split"><div class="cats" id="cats">${cats.map((c) =>
    `<a data-c="${esc(c.category_id)}" class="${c.category_id === cid ? "on" : ""}"><span>${esc(c.name)}</span><span>${c.count}</span></a>`).join("")}</div>
    <div><div class="toolbar"><input id="ch-filter" placeholder="Filtrera kanaler"></div><div class="chlist" id="chlist"><div class="spin"></div></div></div></div>`;
  $("cats").querySelectorAll("[data-c]").forEach((a) => a.onclick = () => { location.hash = "live/" + encodeURIComponent(a.dataset.c); });
  $("cats").querySelector(".on")?.scrollIntoView({ block: "nearest" });
  if (!cid) { $("chlist").innerHTML = '<div class="empty">Inga kanaler.</div>'; return; }
  const data = await api(`/device/items?type=live&category_id=${encodeURIComponent(cid)}&limit=1000`);
  const items = Array.isArray(data) ? data : (data.items || []);
  const draw = (list) => {
    $("chlist").innerHTML = list.map((c) => `<div class="chrow" data-id="${esc(c.id)}">
      <div class="num">${esc(c.num ?? "")}</div><div class="logo-s" style="${img(c.icon)}"></div>
      <div><div class="nm">${esc(c.name)}${c.archive_days > 0 ? ' <span class="small muted">⟲ arkiv</span>' : ""}</div><div class="np" data-np="${esc(c.id)}"></div></div>
      <div class="acts"><button class="btn small" data-g="${esc(c.id)}">Programguide</button></div></div>`).join("") || '<div class="empty">Inga kanaler.</div>';
    $("chlist").querySelectorAll(".chrow").forEach((r) => r.onclick = (e) => {
      const c = items.find((x) => String(x.id) === r.dataset.id);
      if (e.target.dataset.g) return openGuide(c);
      play({ type: "live", id: c.id, name: c.name, icon: c.icon }, c.name);
    });
    loadNowNext(list.slice(0, 120).map((c) => c.id));
  };
  draw(items);
  $("ch-filter").oninput = (e) => { const q = e.target.value.toLowerCase(); draw(items.filter((c) => c.name.toLowerCase().includes(q))); };
}

async function loadNowNext(ids) {
  for (let i = 0; i < ids.length; i += 40) {
    const part = ids.slice(i, i + 40);
    try {
      const res = await api("/device/epg-now?ids=" + part.join(","));
      for (const [id, v] of Object.entries(res)) {
        const el = document.querySelector(`[data-np="${CSS.escape(id)}"]`);
        if (el && v.now) el.textContent = `${clock(v.now.start)} ${v.now.title}` + (v.next ? `  ·  ${clock(v.next.start)} ${v.next.title}` : "");
      }
    } catch { return; }
  }
}

const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" });
const day = (ts) => {
  const d = new Date(ts * 1000), t = new Date(); t.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(d).setHours(0, 0, 0, 0) - t) / 86400000);
  return diff === 0 ? "Idag" : diff === -1 ? "Igår" : diff === 1 ? "I morgon" : d.toLocaleDateString("sv-SE", { weekday: "short", day: "numeric", month: "numeric" });
};

async function openGuide(c) {
  modal(`<div style="padding:24px"><h2 style="margin-top:0">${esc(c.name)}</h2><div id="guide"><div class="spin"></div></div></div>`);
  try {
    const g = await api("/device/guide/" + encodeURIComponent(c.id));
    const now = Date.now() / 1000;
    const progs = g.programs.filter((p) => p.end > now - (g.archive_days || 0) * 86400);
    if (!progs.length) { $("guide").innerHTML = '<div class="empty">Ingen programguide för kanalen.</div>'; return; }
    $("guide").innerHTML = progs.map((p, i) => `<div class="prog ${p.now ? "now" : ""}" ${p.now ? 'id="gnow"' : ""}>
      <div><div>${clock(p.start)}–${clock(p.end)}</div><div class="d">${day(p.start)}</div></div>
      <div><b>${esc(p.title)}</b>${p.description ? `<div class="d">${esc(p.description).slice(0, 160)}</div>` : ""}</div>
      <div class="acts">${p.now ? `<button class="btn small primary" data-a="live" data-i="${i}">▶ Titta</button>` : ""}
        ${p.playable ? `<button class="btn small" data-a="arch" data-i="${i}">▶ Se i efterhand</button>` : ""}
        ${p.end > now && !p.now ? `<button class="btn small" data-a="rec" data-i="${i}">● Spela in</button>` : ""}</div></div>`).join("");
    $("gnow")?.scrollIntoView({ block: "center" });
    $("guide").querySelectorAll("button").forEach((b) => b.onclick = () => {
      const p = progs[+b.dataset.i];
      if (b.dataset.a === "live") { closeModal(); play({ type: "live", id: c.id, name: c.name, icon: c.icon }, c.name); }
      if (b.dataset.a === "arch") { closeModal(); play({ type: "catchup", id: c.id, start: p.start, duration: Math.max(1, Math.round((p.end - p.start) / 60)), name: `${c.name}: ${p.title}`, icon: c.icon }, p.title, `${c.name} · ${day(p.start)} ${clock(p.start)}`); }
      if (b.dataset.a === "rec") record({ stream_id: c.id, title: p.title, start: p.start - 120, end: p.end + 600 });
    });
  } catch (e) { $("guide").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

// ------------------------------------------------------------------ Filmer och serier
async function pageBrowse(kind, args) {
  const h = await home();
  const cats = (h.categories[kind] || []).filter((c) => !c.hidden && c.count > 0);
  const label = kind === "vod" ? "Filmer" : "Serier";
  const st = pageBrowse.state[kind] || (pageBrowse.state[kind] = { cat: "", sort: "added", q: "" });
  if (args[0] !== undefined) st.cat = args[0];
  $("page").innerHTML = `<h1>${label}</h1><div class="toolbar">
    <select id="b-cat"><option value="">Alla kategorier</option>${cats.map((c) => `<option value="${esc(c.category_id)}" ${c.category_id === st.cat ? "selected" : ""}>${esc(c.name)} (${c.count})</option>`).join("")}</select>
    <select id="b-sort">${[["added", "Senast tillagda"], ["rating", "Betyg"], ["year", "År"], ["name", "Namn"]].map(([v, t]) => `<option value="${v}" ${v === st.sort ? "selected" : ""}>${t}</option>`).join("")}</select>
    <input id="b-q" placeholder="Sök i ${label.toLowerCase()}" value="${esc(st.q)}"><span class="muted small" id="b-count"></span></div>
    <div class="grid" id="b-grid"></div><div style="text-align:center;margin-top:20px"><button class="btn hidden" id="b-more">Visa fler</button></div>`;
  let offset = 0;
  const items = new Map();
  const load = async (reset) => {
    if (reset) { offset = 0; items.clear(); $("b-grid").innerHTML = '<div class="spin"></div>'; }
    const qs = new URLSearchParams({ type: kind, sort: st.sort, offset, limit: 60 });
    if (st.cat) qs.set("category_id", st.cat);
    if (st.q) qs.set("q", st.q);
    const res = await api("/device/browse?" + qs);
    if (reset) $("b-grid").innerHTML = "";
    res.items.forEach((i) => items.set(`${i.type}:${i.id}`, i));
    const tmp = document.createElement("div");
    tmp.innerHTML = res.items.map((i) => posterCard(i)).join("");
    bindCards(tmp, items);
    $("b-grid").append(...tmp.children);
    offset += res.items.length;
    $("b-count").textContent = `${res.total} titlar`;
    $("b-more").classList.toggle("hidden", offset >= res.total);
    if (!res.total) $("b-grid").innerHTML = '<div class="empty">Inget hittades.</div>';
  };
  $("b-cat").onchange = (e) => { st.cat = e.target.value; load(true).catch(fail); };
  $("b-sort").onchange = (e) => { st.sort = e.target.value; load(true).catch(fail); };
  let t;
  $("b-q").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { st.q = e.target.value.trim(); load(true).catch(fail); }, 350); };
  $("b-more").onclick = () => load(false).catch(fail);
  await load(true);
}
pageBrowse.state = {};

async function openVod(id) {
  modal('<div class="spin"></div>');
  try {
    const d = await api("/device/vod/" + encodeURIComponent(id));
    const resume = d.resume && d.resume.position_ms > 60000 ? d.resume.position_ms : 0;
    const facts = [d.year, d.rating_num ? "★ " + String(d.rating_num).replace(".", ",") : null, d.duration_min ? `${d.duration_min} min` : null, d.genre].filter(Boolean).join(" · ");
    $("modal-box").innerHTML = `<button class="btn close-x" onclick="closeModal()">✕</button>
      <div class="det-hero" style="${img(d.backdrop || d.poster || d.icon)}"></div>
      <div class="det-body"><h1>${esc(d.name)}</h1><div class="muted">${esc(facts)}</div>
        <div class="acts" style="margin:14px 0">
          ${resume ? `<button class="btn primary" id="d-resume">▶ Fortsätt (${Math.round(resume / 60000)} min)</button><button class="btn" id="d-play">Från början</button>`
                   : `<button class="btn primary" id="d-play">▶ Spela</button>`}
          <button class="btn" id="d-fav">${d.favorite ? "★ Favorit" : "☆ Favorit"}</button>
          <button class="btn" id="d-tv">📺 Spela på TV:n</button></div>
        <p>${esc(d.plot || "")}</p>${d.actors ? `<p class="small muted">Medverkande: ${esc(d.actors)}</p>` : ""}</div>`;
    const req = { type: "vod", id: d.id || id, ext: d.ext, name: d.name, icon: d.icon };
    $("d-play").onclick = () => { closeModal(); play(req, d.name, facts, 0); };
    if ($("d-resume")) $("d-resume").onclick = () => { closeModal(); play(req, d.name, facts, resume); };
    $("d-fav").onclick = () => favorite("vod", d.id || id, !d.favorite).then((on) => { d.favorite = on; $("d-fav").textContent = on ? "★ Favorit" : "☆ Favorit"; });
    $("d-tv").onclick = () => sendToTv({ type: "vod", id: d.id || id, name: d.name });
  } catch (e) { $("modal-box").innerHTML = `<div style="padding:24px">${esc(e.message)}</div>`; }
}

async function openSeries(id) {
  modal('<div class="spin"></div>');
  try {
    const d = await api("/device/series/" + encodeURIComponent(id));
    const seasons = d.seasons || [];
    const facts = [d.year, d.rating_num ? "★ " + String(d.rating_num).replace(".", ",") : null, d.genre, `${seasons.length} säsong${seasons.length === 1 ? "" : "er"}`].filter(Boolean).join(" · ");
    $("modal-box").innerHTML = `<button class="btn close-x" onclick="closeModal()">✕</button>
      <div class="det-hero" style="${img(d.backdrop || d.cover)}"></div>
      <div class="det-body"><h1>${esc(d.name)}</h1><div class="muted">${esc(facts)}</div>
        <div class="acts" style="margin:14px 0"><button class="btn" id="d-fav">${d.favorite ? "★ Favorit" : "☆ Favorit"}</button></div>
        <p>${esc(d.plot || "")}</p>
        <div class="chips" id="seasons">${seasons.map((s, i) => `<button class="chip" data-s="${i}">Säsong ${esc(s.season)}</button>`).join("")}</div>
        <div class="eps" id="eps"></div></div>`;
    const watched = new Set((d.watched || []).map(String));
    const showSeason = (i) => {
      $("seasons").querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", +c.dataset.s === i));
      const eps = seasons[i].episodes;
      $("eps").innerHTML = eps.map((e, j) => {
        const pr = (d.progress || {})[e.id];
        const pct = pr && pr.duration_ms ? Math.round(100 * pr.position_ms / pr.duration_ms) : 0;
        return `<div class="ep" data-j="${j}"><b>${esc(e.num ?? j + 1)}</b><div><div>${esc(e.title)}</div>
          <div class="p">${watched.has(String(e.id)) ? "✓ Sedd" : pct ? `${pct} % sett` : esc(e.duration || "")}</div></div><span>▶</span></div>`;
      }).join("");
      $("eps").querySelectorAll(".ep").forEach((el) => el.onclick = () => {
        const e = eps[+el.dataset.j];
        const pr = (d.progress || {})[e.id];
        closeModal();
        play({ type: "episode", id: e.id, ext: e.ext, series_id: d.id || id, name: `${d.name} – ${e.title}`, icon: d.cover },
          d.name, `Säsong ${seasons[i].season} · ${e.title}`, pr && !watched.has(String(e.id)) ? pr.position_ms : 0);
      });
    };
    $("seasons").querySelectorAll(".chip").forEach((c) => c.onclick = () => showSeason(+c.dataset.s));
    if (seasons.length) showSeason(0); else $("eps").innerHTML = '<div class="empty">Inga avsnitt.</div>';
    $("d-fav").onclick = () => favorite("series", d.id || id, !d.favorite).then((on) => { d.favorite = on; $("d-fav").textContent = on ? "★ Favorit" : "☆ Favorit"; });
  } catch (e) { $("modal-box").innerHTML = `<div style="padding:24px">${esc(e.message)}</div>`; }
}

async function favorite(type, id, on) {
  try { const r = await api("/device/favorites", { method: "POST", body: { type, id, on } }); HOME = null; toast(r.favorite ? "Tillagd i favoriter" : "Borttagen ur favoriter"); return r.favorite; }
  catch (e) { fail(e); return !on; }
}

// ------------------------------------------------------------------ Sport
function eventCmd(e) {
  const c = e.command;
  if (!c) return null;
  if (c.type === "catchup") return { type: "catchup", id: c.id, start: c.catchup_start, duration: c.catchup_minutes, name: c.name, icon: c.icon };
  return { type: c.type, id: c.id, name: c.name, icon: c.icon };
}

function renderEvents(root, events) {
  root.innerHTML = events.map((e, i) => {
    const b = e.state === "now" ? '<span class="badge live">● NU</span>' : e.state === "upcoming" ? '<span class="badge up">KOMMANDE</span>' : '<span class="badge">ARKIV</span>';
    return `<div class="ev">${b}<div class="t">${esc(e.title)}</div><div class="small muted">${esc(e.when)} · ${esc(e.channel)}${e.also && e.also.length ? " · även " + esc(e.also.join(", ")) : ""}</div>
      <div class="acts">${e.command ? `<button class="btn small primary" data-i="${i}" data-a="play">▶ ${e.state === "past" ? "Se i efterhand" : "Titta"}</button>` : ""}
      ${e.state === "upcoming" ? `<button class="btn small" data-i="${i}" data-a="rec">● Spela in</button>` : ""}</div></div>`;
  }).join("");
  root.querySelectorAll("button").forEach((b) => b.onclick = () => {
    const e = events[+b.dataset.i];
    if (b.dataset.a === "play") play(eventCmd(e), e.title, `${e.when} · ${e.channel}`);
    else record((e.record && e.record.body) || { stream_id: e.channel_id, title: e.title, start: e.start - 120, end: e.end + 900 });
  });
}

async function pageSport(args) {
  const iid = args[0] || "";
  const all = await api("/device/sports");
  const data = iid ? await api("/device/sports?interest=" + encodeURIComponent(iid)) : all;
  $("page").innerHTML = `<h1>Sport</h1><div class="chips"><button class="chip ${iid ? "" : "on"}" data-i="">Allt</button>${all.interests.map((i) =>
    `<button class="chip ${String(i.id) === iid ? "on" : ""}" data-i="${i.id}">${esc(i.name)}${i.count ? ` (${i.count})` : ""}</button>`).join("")}</div><div id="sp"></div>`;
  $("page").querySelectorAll(".chips .chip").forEach((c) => c.onclick = () => { location.hash = "sport" + (c.dataset.i ? "/" + c.dataset.i : ""); });
  const sp = $("sp");
  const sec = (title, evs, id) => evs.length ? `<h2>${title}</h2><div class="grid wide" id="${id}"></div>` : "";
  sp.innerHTML = sec("Just nu", data.now, "s-now") + sec("Kommande", data.upcoming, "s-up") + sec("I efterhand", data.past, "s-past");
  if (data.now.length) renderEvents($("s-now"), data.now);
  if (data.upcoming.length) renderEvents($("s-up"), data.upcoming);
  if (data.past.length) renderEvents($("s-past"), data.past);
  const lists = [];
  if (data.channels && data.channels.length) {
    sp.insertAdjacentHTML("beforeend", `<h2>Alla kanaler (${data.channels.length})</h2><div class="row">${data.channels.map((c) => channelCard(c, c.now_title, c.now_progress ?? -1)).join("")}</div>`);
    lists.push(data.channels);
  }
  for (const t of data.library || []) {
    sp.insertAdjacentHTML("beforeend", shelf(`${t.name} · ${t.count} i biblioteket`, t.items));
    lists.push(t.items);
  }
  bindCards(sp, keyed(...lists));
  if (!sp.innerHTML.trim()) sp.innerHTML = '<div class="empty">Inget i programguiden just nu.</div>';
}

// ------------------------------------------------------------------ Världscupen
async function pageWorldcup(args) {
  const d = await api("/device/worldcup");
  const legs = d.legs || [];
  const hero = d.hero;
  let sel = args[0] || (hero && hero.leg) || d.next || (legs[0] && legs[0].id);
  const flag = (c) => ({ ESP: "🇪🇸", ITA: "🇮🇹", GER: "🇩🇪", FRA: "🇫🇷", NOR: "🇳🇴", SWE: "🇸🇪", SUI: "🇨🇭", GBR: "🇬🇧", NED: "🇳🇱", BEL: "🇧🇪", AUT: "🇦🇹", USA: "🇺🇸", IRL: "🇮🇪", POL: "🇵🇱", CZE: "🇨🇿", DEN: "🇩🇰", FIN: "🇫🇮", POR: "🇵🇹" }[String(c).toUpperCase()] || "");
  let heroHtml = '<div class="hero"><div class="h">Säsongen är slut</div></div>';
  if (hero) {
    const e = hero.event;
    heroHtml = `<div class="hero"><div class="k ${hero.live ? "live" : ""}">${hero.live ? "● SÄNDS NU" : esc(hero.countdown || "NÄSTA").toUpperCase()}</div>
      <div class="h">${esc(hero.leg_name)} ${flag(hero.country)}</div>
      ${e ? `<div>${esc(e.title)}</div><div class="muted">${esc(e.when)} · ${esc(e.channel)}${e.unconfirmed ? " · enligt TV4:s tablå" : ""}</div><div class="acts" id="hero-acts" style="margin-top:12px"></div>`
          : '<div class="muted">Tiderna kommer när TV4 publicerar dem.</div>'}
      ${(d.channels || []).length ? `<div class="acts" style="margin-top:14px"><span class="muted small" style="align-self:center">Kanaler</span>${d.channels.map((c, i) => `<button class="btn small" data-ch="${i}">▶ ${esc(c.label)}</button>`).join("")}</div>` : ""}</div>`;
  }
  $("page").innerHTML = `<h1>🏇 Världscupen i hoppning <span class="muted small">Säsongen ${esc(d.season)}</span></h1>
    ${(d.hints || []).map((h) => `<p class="err">⚠ ${esc(h)}</p>`).join("")}${heroHtml}
    <div class="muted small" style="font-weight:700;letter-spacing:1px">SÄSONGEN</div><div class="tiles" id="tiles"></div><div id="leg"></div>
    ${d.status_line ? `<p class="small muted" style="margin-top:24px">ⓘ ${esc(d.status_line)}</p>` : ""}`;
  $("page").querySelectorAll("[data-ch]").forEach((b) => b.onclick = () => { const c = d.channels[+b.dataset.ch].command; play({ type: "live", id: c.id, name: c.name, icon: c.icon }, c.name); });
  if (hero && hero.event) eventActions($("hero-acts"), hero.event);
  const tiles = $("tiles");
  tiles.innerHTML = legs.map((l) => {
    const lab = l.kind === "final" && l.state !== "done" ? ["FINAL", "var(--star)"] : l.state === "done" && l.winner ? ["🏆 " + l.winner, "var(--star)"]
      : l.state === "done" ? ["Avgjord", "var(--muted)"] : l.state === "ongoing" ? ["● Pågår", "var(--live)"] : l.id === d.next ? ["Nästa", "var(--accent)"]
      : [l.kind === "other" ? "Ingen etapp" : "Kommande", "var(--muted)"];
    return `<div class="tile" data-l="${esc(l.id)}"><div class="n">${flag(l.country)} ${esc(l.name)}</div><div class="small muted">${esc(l.dates)}</div>
      <div class="l" style="color:${lab[1]}">${esc(lab[0])}</div></div>`;
  }).join("");
  const show = (id) => {
    sel = id;
    tiles.querySelectorAll(".tile").forEach((t) => t.classList.toggle("on", t.dataset.l === id));
    const l = legs.find((x) => x.id === id);
    if (!l) return;
    $("leg").innerHTML = `<div class="ev" style="padding:20px 22px"><h2 style="margin:0">${flag(l.country)} ${esc(l.name)}${l.kind === "final" ? " · FINAL" : ""}</h2>
      <div class="muted">${esc(l.dates)} · Världscupklassen ${esc(l.wc_label)}${l.tv ? " · TV: " + esc(l.tv) : ""}</div>
      ${(l.podium || []).length ? `<div class="podium">${l.podium.map((p, i) => `<div class="pod p${i + 1}"><span class="m">${["🥇", "🥈", "🥉"][i]}</span><div><b>${esc(p.rider)}</b>${p.horse ? `<div class="small muted">${esc(p.horse)}</div>` : ""}</div></div>`).join("")}</div>` : ""}
      ${l.events.length ? "" : `<div class="muted" style="margin-top:6px">${esc(l.headline)}</div>`}<div class="grid wide" id="leg-ev" style="margin-top:10px"></div>
      <div id="leg-more"></div>
      ${(l.seen || []).length ? `<div class="small muted" style="margin-top:8px">Sändes, finns inte längre i arkivet: ${l.seen.map((s) => esc(s.when + " · " + s.channel)).join(", ")}</div>` : ""}</div>`;
    const card = (e, i) => {
      const badge = e.kind === "live" ? `<span class="badge live">${e.state === "now" ? "● " : ""}DIREKT</span>`
        : e.kind === "replay" ? '<span class="badge">REPRIS</span>' : e.kind === "highlights" ? '<span class="badge">SAMMANDRAG</span>'
        : '<span class="badge up">FÖRSTA SÄNDNING</span>';
      const st = e.state === "now" ? "Sänds nu · " : e.state === "past" ? "I arkivet · " : "";
      return `<div class="ev" style="background:var(--s2)">${badge}<div class="t">${esc(e.title)}${e.main ? " ★" : ""}</div>
        <div class="small muted">${st}${esc(e.when)} · ${esc(e.channel)}${e.unconfirmed ? " · enligt TV4:s tablå" : ""}</div><div class="acts" data-e="${i}"></div></div>`;
    };
    const prim = [], sec = [];
    l.events.forEach((e, i) => ((e.kind === "replay" || e.kind === "highlights") ? sec : prim).push(i));
    $("leg-ev").innerHTML = prim.map((i) => card(l.events[i], i)).join("");
    if (sec.length) {
      $("leg-more").innerHTML = `<details style="margin-top:12px"><summary class="btn small" style="display:inline-block">Repriser och sammandrag (${sec.length})</summary>
        <div class="grid wide" style="margin-top:10px">${sec.map((i) => card(l.events[i], i)).join("")}</div></details>`;
    }
    $("leg").querySelectorAll("[data-e]").forEach((a) => eventActions(a, l.events[+a.dataset.e]));
  };
  tiles.querySelectorAll(".tile").forEach((t) => t.onclick = () => show(t.dataset.l));
  show(sel);
  tiles.querySelector(".tile.on")?.scrollIntoView({ inline: "center", block: "nearest" });
}

function eventActions(root, e) {
  const parts = [];
  if (e.command) parts.push(`<button class="btn small primary" data-a="play">▶ ${esc(e.play_label || "Titta")}</button>`);
  else if (e.state === "upcoming" && e.open_command) parts.push(`<button class="btn small" data-a="open">▶ Öppna ${esc(e.channel)}</button>`);
  if (e.recording) parts.push(`<span class="small" style="color:var(--accent);align-self:center">● ${esc({ scheduled: "Inspelning planerad", recording: "Spelas in just nu", done: "Inspelad", failed: "Inspelningen misslyckades" }[e.recording.status] || e.recording.status)}</span>`);
  else if (e.record && e.record.action === "record") parts.push('<button class="btn small" data-a="rec">● Spela in</button>');
  else if (e.record && e.record.action === "later") parts.push(`<span class="small muted" style="align-self:center">${esc(e.record.label)}</span>`);
  root.innerHTML = parts.join("");
  root.querySelectorAll("button").forEach((b) => b.onclick = () => {
    if (b.dataset.a === "play") play(eventCmd(e), e.title, `${e.when} · ${e.channel}`);
    if (b.dataset.a === "open") { const c = e.open_command; play({ type: "live", id: c.id, name: c.name, icon: c.icon }, c.name); }
    if (b.dataset.a === "rec") record(e.record.body);
  });
}

// ------------------------------------------------------------------ Sök
async function pageSearch(args) {
  const q = args[0] || "";
  $("search").value = q;
  const [items, when] = await Promise.all([api("/device/search?limit=120&q=" + encodeURIComponent(q)), api("/device/when?q=" + encodeURIComponent(q)).catch(() => null)]);
  const by = (t) => items.filter((i) => i.type === t);
  const evs = when ? [...when.now, ...when.upcoming, ...when.past] : [];
  $("page").innerHTML = `<h1>Sök: ${esc(q)}</h1>${evs.length ? `<h2>Sändningar</h2><div class="grid wide" id="q-ev"></div>` : ""}
    ${by("live").length ? `<h2>Kanaler</h2><div class="row">${by("live").map((c) => channelCard(c)).join("")}</div>` : ""}
    ${shelf("Filmer", by("vod"))}${shelf("Serier", by("series"))}${!items.length && !evs.length ? '<div class="empty">Inget hittades.</div>' : ""}`;
  bindCards($("page"), keyed(items));
  if (evs.length) renderEvents($("q-ev"), evs.slice(0, 30));
}

// ------------------------------------------------------------------ Inspelningar
async function pageRecordings() {
  const list = await api("/device/web-recordings");
  const st = { scheduled: "Planerad", recording: "● Spelas in", done: "✔ Klar", failed: "✖ Misslyckades" };
  $("page").innerHTML = `<h1>Inspelningar</h1><p class="muted small">Inspelningar görs och sparas på boxarna. Här ser du dem och kan planera nya från programguiden, Sport och Världscupen.</p>
    ${list.length ? `<div class="chlist">${list.map((r) => `<div class="chrow" style="grid-template-columns:120px 1fr auto;cursor:default">
      <div class="small"><div>${day(r.start)} ${clock(r.start)}</div><div class="muted">${esc(r.device_name || "")}</div></div>
      <div><div class="nm">${esc(r.title)}</div><div class="np">${esc(r.channel || "")}</div></div><div class="small">${esc(st[r.status] || r.status)}</div></div>`).join("")}</div>`
      : '<div class="empty">Inga inspelningar.</div>'}`;
}

async function record(body) {
  if (!body) return;
  let boxes = [];
  try { boxes = (await api("/device/boxes")).filter((b) => b); } catch (e) { return fail(e); }
  if (!boxes.length) return toast("Ingen box att spela in på.");
  const go = async (box) => {
    try { await api("/device/recordings", { method: "POST", body: { ...body, box_id: box.id } }); closeModal(); toast(`● Planerad på ${box.name}`); }
    catch (e) { fail(e); }
  };
  if (boxes.length === 1) return go(boxes[0]);
  modal(`<div style="padding:24px"><h2 style="margin-top:0">Spela in på vilken box?</h2><p class="muted small">${esc(body.title)}</p><div class="acts">${boxes.map((b, i) =>
    `<button class="btn" data-b="${i}">${esc(b.name)}${b.online ? "" : " (avstängd)"}</button>`).join("")}</div></div>`);
  $("modal-box").querySelectorAll("[data-b]").forEach((el) => el.onclick = () => go(boxes[+el.dataset.b]));
}

async function sendToTv(item) {
  let boxes = [];
  try { boxes = (await api("/device/boxes")).filter((b) => b.online); } catch (e) { return fail(e); }
  if (!boxes.length) return toast("Ingen box är på just nu.");
  const go = async (b) => {
    try { await api("/device/send", { method: "POST", body: { device_id: b.id, ...item } }); closeModal(); toast(`📺 Startar på ${b.name}`); } catch (e) { fail(e); }
  };
  if (boxes.length === 1) return go(boxes[0]);
  modal(`<div style="padding:24px"><h2 style="margin-top:0">Spela på vilken TV?</h2><div class="acts">${boxes.map((b, i) => `<button class="btn" data-b="${i}">${esc(b.name)}</button>`).join("")}</div></div>`);
  $("modal-box").querySelectorAll("[data-b]").forEach((el) => el.onclick = () => go(boxes[+el.dataset.b]));
}

// ------------------------------------------------------------------ modal
function modal(html) { $("modal-box").innerHTML = html; $("modal").classList.remove("hidden"); }
function closeModal() { $("modal").classList.add("hidden"); $("modal-box").innerHTML = ""; }
window.closeModal = closeModal;
$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });

// ------------------------------------------------------------------ spelaren
const P = { session: null, hb: null, ts: null, req: null, idle: null };

/** Hallplattan: välj TV (huvudval) eller spela här. Annars spelas det direkt här. */
async function play(req, title, sub = "", resumeMs = 0, force = false) {
  if (!req) return;
  if (WALL && !force) return chooseWhere(req, title, sub, resumeMs);
  return playHere(req, title, sub, resumeMs, force);
}

async function chooseWhere(req, title, sub, resumeMs) {
  let boxes = [];
  try { boxes = (await api("/device/boxes")).filter((b) => b.online); } catch (_) {}
  // Vardagsrummets box först.
  boxes.sort((a, b) => (/(z10|vardagsrum)/i.test(b.name) ? 1 : 0) - (/(z10|vardagsrum)/i.test(a.name) ? 1 : 0));
  modal(`<div style="padding:26px"><h2 style="margin-top:0">${esc(title || req.name || "")}</h2>${sub ? `<p class="muted" style="margin-top:-6px">${esc(sub)}</p>` : ""}
    <div class="where">${boxes.map((b, i) => `<button class="btn ${i === 0 ? "primary" : ""} big" data-b="${i}">📺 Spela på ${esc(b.name)}</button>`).join("")}
    ${boxes.length ? "" : '<p class="muted">Ingen TV-box är på just nu.</p>'}
    <button class="btn big" id="w-here">▶ Spela här på plattan</button><button class="btn" onclick="closeModal()">Avbryt</button></div></div>`);
  $("modal-box").querySelectorAll("[data-b]").forEach((el) => el.onclick = async () => {
    const b = boxes[+el.dataset.b];
    const body = { device_id: b.id, type: req.type, id: req.id, name: req.name, series_id: req.series_id, start: req.start, duration: req.duration };
    try { await api("/device/send", { method: "POST", body }); closeModal(); toast(`📺 Startar på ${b.name}`); } catch (e) { fail(e); }
  });
  $("w-here").onclick = () => { closeModal(); playHere(req, title, sub, resumeMs, false); };
}

async function playHere(req, title, sub = "", resumeMs = 0, force = false) {
  if (!req) return;
  await stopPlayback("replaced");
  $("player").classList.remove("hidden");
  $("pl-title").textContent = title || req.name || "";
  $("pl-sub").textContent = sub || "";
  msg("Startar…");
  P.req = { req, title, sub, resumeMs };
  let res;
  for (let tries = 0; tries < 40; tries++) {
    try { res = await api("/device/web-play", { method: "POST", body: { ...req, force } }); } catch (e) { return msg(esc(e.message), true); }
    if (res.status !== "waiting") break;
    msg(esc(res.message || "Väntar…"));
    await new Promise((r) => setTimeout(r, res.retry_ms || 1000));
  }
  if (res.status === "conflict") {
    return msg(`${esc(res.message)}<div class="acts" style="justify-content:center"><button class="btn primary" id="pl-take">Ta över</button><button class="btn" id="pl-no">Avbryt</button></div>`, false, () => {
      $("pl-take").onclick = () => playHere(req, title, sub, resumeMs, true);
      $("pl-no").onclick = closePlayer;
    });
  }
  if (res.status !== "ok") return msg(esc(res.error || res.message || "Det gick inte att spela."), true);
  P.session = res.session_id;
  const live = req.type === "live" || req.type === "catchup";
  const v = $("video");
  const resume = resumeMs || (res.resume && res.resume.position_ms) || 0;
  if (live) {
    if (!window.mpegts || !mpegts.isSupported()) return msg("Webbläsaren kan inte spela TV-kanaler (MPEG-TS). Använd Chrome eller Edge.", true);
    P.ts = mpegts.createPlayer({ type: "mpegts", isLive: req.type === "live", url: new URL(res.stream, location.href).href }, { enableWorker: true, liveBufferLatencyChasing: false, lazyLoad: false });
    P.ts.attachMediaElement(v);
    P.ts.on(mpegts.Events.ERROR, (type, detail, info) => onPlayError(`${type} ${detail} ${(info && info.msg) || ""}`));
    P.ts.load();
    v.play().catch(() => {});
  } else {
    v.src = new URL(res.stream, location.href).href;
    v.addEventListener("loadedmetadata", function once() { v.removeEventListener("loadedmetadata", once); if (resume > 60000) v.currentTime = resume / 1000; }, { once: true });
    v.play().catch(() => {});
  }
  v.onplaying = () => { hideMsg(); checkAudio(v); };
  v.onerror = () => onPlayError(v.error ? `kod ${v.error.code}` : "okänt");
  P.hb = setInterval(heartbeat, res.heartbeat_ms || 10000);
}

function checkAudio(v) {
  // Chrome och Edge spelar inte AC3/E-AC3-ljud. Ingen ljudspår-avkodning syns som 0 avkodade ljudbytes.
  setTimeout(() => {
    if (v.webkitAudioDecodedByteCount === 0 && !v.muted && v.currentTime > 2) toast("Inget ljud? Kanalen har troligen AC3-ljud, som webbläsaren inte kan spela. Spela på TV:n i stället.", 8000);
  }, 5000);
}

function onPlayError(detail) {
  msg(`Webbläsaren kunde inte spela det här.<div class="small muted">Vissa kanaler använder format som webbläsaren inte kan spela (till exempel HEVC eller AC3-ljud). Det fungerar på TV-boxen.</div>
    <div class="small muted">(${esc(detail)})</div><div class="acts" style="justify-content:center"><button class="btn" id="pl-retry">Försök igen</button><button class="btn" id="pl-c2">Stäng</button></div>`, false, () => {
    $("pl-retry").onclick = () => { const r = P.req; playHere(r.req, r.title, r.sub, r.resumeMs); };
    $("pl-c2").onclick = closePlayer;
  });
}

async function heartbeat() {
  if (!P.session) return;
  const v = $("video");
  const metrics = isFinite(v.duration) && v.duration > 0 ? { position_ms: Math.round(v.currentTime * 1000), duration_ms: Math.round(v.duration * 1000) } : {};
  try {
    const r = await api("/device/heartbeat", { method: "POST", body: { session_id: P.session, metrics } });
    if (r.stop) {
      const why = r.message || (r.reason === "takeover" ? "En annan box tog över." : "Uppspelningen stoppades.");
      await stopPlayback(null);
      msg(`${esc(why)}<div class="acts" style="justify-content:center"><button class="btn" id="pl-c3">Stäng</button></div>`, false, () => { $("pl-c3").onclick = closePlayer; });
    }
  } catch {}
}

async function stopPlayback(reason) {
  clearInterval(P.hb); P.hb = null;
  const v = $("video");
  const metrics = isFinite(v.duration) && v.duration > 0 ? { position_ms: Math.round(v.currentTime * 1000), duration_ms: Math.round(v.duration * 1000) } : {};
  if (P.ts) { try { P.ts.pause(); P.ts.unload(); P.ts.detachMediaElement(); P.ts.destroy(); } catch {} P.ts = null; }
  v.onerror = null; v.onplaying = null;
  v.removeAttribute("src"); try { v.load(); } catch {}
  const sid = P.session; P.session = null;
  if (sid && reason) { try { await api("/device/stop", { method: "POST", body: { session_id: sid, reason, metrics } }); } catch {} }
}

function closePlayer() {
  stopPlayback("stopped");
  $("player").classList.add("hidden"); hideMsg();
  HOME = null;
}
$("pl-close").onclick = closePlayer;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { if (!$("player").classList.contains("hidden")) closePlayer(); else closeModal(); }
});
window.addEventListener("pagehide", () => {
  if (P.session) fetch("/device/stop", { method: "POST", keepalive: true,
    headers: { "Authorization": "Bearer " + store.token, "Content-Type": "application/json" }, body: JSON.stringify({ session_id: P.session, reason: "closed" }) });
});
$("player").addEventListener("mousemove", () => {
  $("player").classList.remove("idle"); clearTimeout(P.idle); P.idle = setTimeout(() => $("player").classList.add("idle"), 3000);
});

function msg(html, isError, bind) {
  const m = $("pl-msg"); m.innerHTML = html + (isError ? '<div class="acts" style="justify-content:center"><button class="btn" id="pl-c4">Stäng</button></div>' : "");
  m.classList.remove("hidden");
  if ($("pl-c4")) $("pl-c4").onclick = closePlayer;
  if (bind) bind();
}
function hideMsg() { $("pl-msg").classList.add("hidden"); }

start();
