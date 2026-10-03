/* CortHeXis dashboard — vanilla + d3-force on canvas, no build step.
   Reads: graph, notes, recall, health, bench. Writes (token required): review run,
   repair proposals and their approval, bench questions and runs. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const COLORS = {
    project: "#c8a35a", feedback: "#9b7bff", reference: "#3ecfb2", user: "#ffffff",
    unknown: "#6b7280", memory: "#ff8ad6", ghost: "#ff5c6c", cyan: "#5ee1ff", red: "#ff5c6c",
    orange: "#ffb454", teal: "#3ecfb2",
  };
  const params = new URLSearchParams(location.search);
  const I = window.CX_I18N;
  let LANG = I.pick(params.get("lang"));
  const t = (k, v) => I.t(LANG, k, v);
  const LOCALE = () => (LANG === "fr" ? "fr-CH" : "en-GB");

  // ------------------------------------------------------------ state
  const S = {
    info: {}, graph: null, nodes: [], links: [], byId: new Map(), version: null,
    mode: "synapse", labels: false, hidden: new Set(), hover: null, selected: null,
    recall: new Map(), flash: new Map(), review: null, history: [], summary: {},
    lastGraphAt: 0, offline: false, transform: d3.zoomIdentity, particles: [],
    critIds: new Set(), warnIds: new Set(), proposals: [], status: null,
  };

  // ------------------------------------------------------------ API (token in localStorage)
  const TOKEN_KEY = "corthexis_token";
  const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } };
  const setToken = v => { try { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } };
  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.body ? { "content-type": "application/json" } : {}, opts.headers || {});
    const tok = getToken();
    if (tok) headers.authorization = `Bearer ${tok}`;
    const r = await fetch(path, { method: opts.method || "GET", headers, cache: "no-store",
      body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (r.status === 401) { showLogin(); throw new Error("401"); }
    if (!r.ok) {
      let msg = String(r.status);
      try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
      throw new Error(msg);
    }
    return r.json();
  }
  const canWrite = () => !!S.info.writable && !S.info.demo;

  function showLogin() {
    if (S.info.public_read && !S.info.writable) return;
    const box = $("#login"); if (!box.hidden) return;
    box.hidden = false; $("#tokenInput").focus();
  }
  $("#login form").addEventListener("submit", async e => {
    e.preventDefault();
    setToken($("#tokenInput").value.trim());
    try {
      await api("/api/status");
      $("#login").hidden = true; $("#loginErr").textContent = "";
      boot();
    } catch { $("#loginErr").textContent = t("login_bad"); setToken(""); }
  });

  // ------------------------------------------------------------ canvases
  const cv = $("#graph"), ctx = cv.getContext("2d");
  const sc = $("#stars"), sctx = sc.getContext("2d");
  let W = 0, H = 0, DPR = 1, stars = [];
  function resize() {
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = innerWidth; H = innerHeight;
    for (const c of [cv, sc]) { c.width = W * DPR; c.height = H * DPR; c.style.width = W + "px"; c.style.height = H + "px"; }
    stars = d3.range(Math.round((W * H) / 9000)).map(() => ({
      x: Math.random() * W, y: Math.random() * H, r: Math.random() * 1.1 + .2, a: Math.random() * .5 + .15, p: Math.random() * Math.PI * 2,
    }));
    if (sim) sim.force("center", d3.forceCenter(W / 2, H / 2)).alpha(.3).restart();
  }
  addEventListener("resize", resize);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function drawStars(tm) {
    sctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    sctx.clearRect(0, 0, W, H);
    const g = sctx.createRadialGradient(W * .5, H * .45, 0, W * .5, H * .45, Math.max(W, H) * .7);
    g.addColorStop(0, "#0b1120"); g.addColorStop(.55, "#070a12"); g.addColorStop(1, "#04060a");
    sctx.fillStyle = g; sctx.fillRect(0, 0, W, H);
    sctx.strokeStyle = "rgba(200,163,90,.035)"; sctx.lineWidth = 1;
    const step = 80, ox = reduced ? 0 : (tm / 200) % step;
    sctx.beginPath();
    for (let x = -step + ox; x < W + step; x += step) { sctx.moveTo(x, 0); sctx.lineTo(x, H); }
    for (let y = 0; y < H; y += step) { sctx.moveTo(0, y); sctx.lineTo(W, y); }
    sctx.stroke();
    for (const s of stars) {
      const tw = reduced ? 1 : .6 + .4 * Math.sin(tm / 900 + s.p);
      sctx.fillStyle = `rgba(233,230,225,${s.a * tw})`;
      sctx.beginPath(); sctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); sctx.fill();
    }
  }

  // ------------------------------------------------------------ simulation
  let sim = null;
  function radius(n) {
    if (n.ghost) return 3.2;
    return 3 + Math.min(9, Math.sqrt(n.words || 50) / 6) + Math.min(6, (n.in || 0) * .6);
  }
  function color(n) {
    if (n.ghost) return COLORS.ghost;
    if (S.mode === "age") {
      if (n.age == null) return "#4b5563";
      return d3.interpolateRgb(COLORS.cyan, "#5a3a2a")(Math.min(1, n.age / 120));
    }
    if (S.mode === "health") {
      const f = n.flags || [];
      if (!f.length) return "#2b3140";
      return f.some(id => S.critIds.has(id)) ? COLORS.red : f.some(id => S.warnIds.has(id)) ? COLORS.orange : "#6b7280";
    }
    return COLORS[n.type] || COLORS.unknown;
  }
  function buildSim() {
    if (sim) sim.stop();
    sim = d3.forceSimulation(S.nodes)
      .force("link", d3.forceLink(S.links).id(d => d.id).distance(l => l.broken ? 40 : 46 + 22 * Math.random()).strength(l => l.broken ? .25 : .55))
      .force("charge", d3.forceManyBody().strength(d => d.ghost ? -30 : -95).distanceMax(420))
      .force("collide", d3.forceCollide().radius(d => radius(d) + 4).iterations(2))
      .force("center", d3.forceCenter(W / 2, H / 2))
      .force("x", d3.forceX(W / 2).strength(.02)).force("y", d3.forceY(H / 2).strength(.03))
      .alphaDecay(.02).velocityDecay(.35);
    applyMode();
  }
  function applyMode() {
    if (!sim) return;
    const semantic = S.mode === "semantic";
    const R = Math.min(W, H) * .42;
    sim.force("x", d3.forceX(d => semantic && d.sx != null ? W / 2 + d.sx * R : W / 2).strength(semantic ? .28 : .02));
    sim.force("y", d3.forceY(d => semantic && d.sy != null ? H / 2 + d.sy * R * .85 : H / 2).strength(semantic ? .28 : .03));
    sim.force("link").strength(l => semantic ? .05 : l.broken ? .25 : .55);
    sim.force("charge").strength(d => semantic ? -25 : d.ghost ? -30 : -95);
    sim.alpha(.6).restart();
  }

  // ------------------------------------------------------------ data
  function ingest(g, first) {
    const prev = S.byId;
    const nodes = g.nodes.map(n => Object.assign(prev.get(n.id) || { x: W / 2 + (Math.random() - .5) * 200, y: H / 2 + (Math.random() - .5) * 200 }, n));
    for (const gh of g.ghosts) nodes.push(Object.assign(prev.get(gh.id) || { x: W / 2 + (Math.random() - .5) * 600, y: H / 2 + (Math.random() - .5) * 600 }, gh, { type: "ghost" }));
    const byId = new Map(nodes.map(n => [n.id, n]));
    const links = g.edges.filter(e => byId.has(e.s) && byId.has(e.t)).map(e => ({ source: e.s, target: e.t, broken: !!e.broken }));
    if (!first) {
      for (const n of g.nodes) {
        const old = prev.get(n.id);
        if (!old) { S.flash.set(n.id, performance.now()); toast(t("toast_new", { n: n.id })); }
        else if (old.words !== n.words || old.modified !== n.modified) { S.flash.set(n.id, performance.now()); toast(t("toast_upd", { n: n.id })); }
      }
    }
    S.graph = g; S.nodes = nodes; S.links = links; S.byId = byId; S.version = g.version; S.lastGraphAt = Date.now();
    for (const n of nodes) n.neighbors = new Set();
    for (const l of links) { byId.get(l.source)?.neighbors.add(l.target); byId.get(l.target)?.neighbors.add(l.source); }
    buildSim();
    S.particles = links.filter(l => !l.broken).filter(() => Math.random() < .55).map(l => ({ l, t: Math.random(), v: .002 + Math.random() * .004 }));
    counters(g.stats);
    if (g.error) setLive("down", t("index_unreadable"));
  }
  function counters(st) {
    const map = { notes: st.notes, words: st.words, links: st.links, chunks: st.chunks ?? 0 };
    for (const [k, v] of Object.entries(map)) {
      const el = $(`[data-count=${k}]`), from = +el.dataset.v || 0;
      el.dataset.v = v;
      d3.select(el).transition().duration(1200).ease(d3.easeCubicOut).tween("t", () => x => { el.textContent = fmt(Math.round(from + (v - from) * x)); });
    }
  }
  const fmt = n => Number(n).toLocaleString(LOCALE()).replace(/ /g, " ");
  const when = s => s ? new Date(s).toLocaleString(LOCALE(), { dateStyle: "short", timeStyle: "short" }) : "—";

  async function loadGraph(first = false) {
    try {
      const g = await api(`/api/graph${S.version ? `?since=${S.version}` : ""}`);
      S.offline = false;
      if (g.unchanged) { S.lastGraphAt = Date.now(); return; }
      ingest(g, first);
      if (first) {
        const nm = params.get("note"); if (nm) openNote(nm);
        const tab = params.get("tab"); if (tab) { openPanel(); showTab(tab); }
      }
    } catch (e) { if (e.message !== "401") { S.offline = true; setLive("down", t("api_down")); } }
  }
  async function loadReview() {
    try {
      const d = await api("/api/review");
      S.review = d.report; S.history = d.history || []; S.summary = d.summary || {}; S.digestAt = d.digest_at;
      S.pending = d.pending || 0; S.notify = d.notify;
      S.critIds = new Set(d.report.findings.filter(f => f.severity === "crit").map(f => f.id));
      S.warnIds = new Set(d.report.findings.filter(f => f.severity === "warn").map(f => f.id));
      renderHealth(); renderReview(); repairBadge();
      if (S.graph) { S.version = null; loadGraph(); }
    } catch { /* the graph stays */ }
  }

  function setLive(cls, text) { const el = $("#live"); el.className = "live " + (cls || ""); $("#liveText").textContent = text; }
  function liveTick() {
    if (S.offline) return;
    const s = Math.round((Date.now() - S.lastGraphAt) / 1000);
    setLive(s > 90 ? "stale" : "", s < 3 ? t("live_now") : t("live_ago", { s }));
  }

  // ------------------------------------------------------------ render loop
  function render(tm) {
    drawStars(tm);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const tr = S.transform;
    ctx.translate(tr.x, tr.y); ctx.scale(tr.k, tr.k);
    const focus = S.hover || S.selected;
    const recallOn = S.recall.size > 0;
    const vis = n => !S.hidden.has(n.ghost ? "ghost" : n.type);
    for (const l of S.links) {
      const a = l.source, b = l.target;
      if (!vis(a) || !vis(b)) continue;
      let alpha = .10, w = .8, col = "200,163,90";
      if (focus) { const on = focus === a || focus === b; alpha = on ? .75 : .03; w = on ? 1.6 : .6; if (on) col = "94,225,255"; }
      if (recallOn) { const m = Math.max(S.recall.get(a.id) || 0, S.recall.get(b.id) || 0); alpha = m ? .12 + m * .6 : .03; if (m) col = "94,225,255"; }
      if (l.broken) { col = "255,92,108"; ctx.setLineDash([3, 4]); alpha = Math.max(alpha, .25); } else ctx.setLineDash([]);
      ctx.strokeStyle = `rgba(${col},${alpha})`; ctx.lineWidth = w / tr.k;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    ctx.setLineDash([]);
    if (!reduced) {
      for (const p of S.particles) {
        const a = p.l.source, b = p.l.target;
        if (!vis(a) || !vis(b)) continue;
        p.t += p.v * (recallOn ? 2.2 : 1); if (p.t > 1) p.t = 0;
        const x = a.x + (b.x - a.x) * p.t, y = a.y + (b.y - a.y) * p.t;
        const hot = focus ? (focus === a || focus === b) : recallOn ? (S.recall.has(a.id) || S.recall.has(b.id)) : true;
        if (!hot && (focus || recallOn)) continue;
        ctx.fillStyle = hot && (focus || recallOn) ? "rgba(94,225,255,.95)" : "rgba(240,211,138,.55)";
        ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 8;
        ctx.beginPath(); ctx.arc(x, y, 1.3 / Math.sqrt(tr.k), 0, Math.PI * 2); ctx.fill();
      }
      ctx.shadowBlur = 0;
    }
    for (const n of S.nodes) {
      if (!vis(n)) continue;
      const r = radius(n), c = color(n);
      let dim = 1;
      if (focus) dim = (focus === n || focus.neighbors?.has(n.id)) ? 1 : .18;
      if (recallOn) dim = S.recall.has(n.id) ? 1 : .12;
      if (S.mode === "health" && !(n.flags || []).length && !n.ghost) dim = Math.min(dim, .35);
      ctx.globalAlpha = dim;
      const rs = S.recall.get(n.id) || 0;
      const fl = S.flash.get(n.id); let burst = 0;
      if (fl != null) { const d = (tm - fl) / 1800; if (d > 1) S.flash.delete(n.id); else burst = Math.max(0, 1 - d); }
      ctx.shadowColor = rs || burst ? COLORS.cyan : c; ctx.shadowBlur = 6 + r * 1.2 + rs * 26 + burst * 40 + (focus === n ? 14 : 0);
      if (n.ghost) {
        ctx.strokeStyle = c; ctx.lineWidth = 1 / tr.k; ctx.setLineDash([2, 2]);
        ctx.beginPath(); ctx.arc(n.x, n.y, r + 1.5, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
      } else {
        const g = ctx.createRadialGradient(n.x - r * .3, n.y - r * .3, r * .1, n.x, n.y, r);
        g.addColorStop(0, "#fff"); g.addColorStop(.25, c); g.addColorStop(1, d3.color(c).darker(1.1).formatHex());
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill();
        if (rs || burst) { ctx.strokeStyle = `rgba(94,225,255,${.5 + .5 * Math.max(rs, burst)})`; ctx.lineWidth = 1.2 / tr.k; ctx.beginPath(); ctx.arc(n.x, n.y, r + 3 + burst * 18 + Math.sin(tm / 250) * 1.5, 0, Math.PI * 2); ctx.stroke(); }
        if (S.mode !== "health" && (n.flags || []).some(id => S.critIds.has(id))) { ctx.strokeStyle = COLORS.red; ctx.lineWidth = 1 / tr.k; ctx.beginPath(); ctx.arc(n.x, n.y, r + 2.5, 0, Math.PI * 2); ctx.stroke(); }
      }
      ctx.shadowBlur = 0;
      const showLabel = S.labels || focus === n || (focus && focus.neighbors?.has(n.id)) || rs > 0 || tr.k > 2.2 || (n.in || 0) > 9;
      if ((showLabel && !n.ghost) || (n.ghost && (focus === n || tr.k > 1.8))) {
        ctx.font = `${Math.max(10, 11 / Math.sqrt(tr.k))}px ui-monospace, SFMono-Regular, Menlo, monospace`;
        ctx.fillStyle = focus === n || rs ? "#ffffff" : `rgba(233,230,225,${n.ghost ? .6 : .78})`;
        ctx.shadowColor = "rgba(0,0,0,.9)"; ctx.shadowBlur = 6; ctx.textAlign = "center";
        ctx.fillText(n.ghost ? `[[${n.label}]]` : n.id, n.x, n.y + r + 12 / Math.sqrt(tr.k));
        ctx.shadowBlur = 0;
      }
      ctx.globalAlpha = 1;
    }
    requestAnimationFrame(render);
  }

  // ------------------------------------------------------------ interaction
  function pick(px, py) {
    const [x, y] = S.transform.invert([px, py]);
    let best = null, bd = 1e9;
    for (const n of S.nodes) {
      if (S.hidden.has(n.ghost ? "ghost" : n.type)) continue;
      const d = Math.hypot(n.x - x, n.y - y); const r = radius(n) + 6 / S.transform.k;
      if (d < r && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  const zoom = d3.zoom().scaleExtent([.25, 8]).on("zoom", e => { S.transform = e.transform; });
  d3.select(cv).call(zoom).on("dblclick.zoom", null);
  d3.select(cv).call(d3.drag().container(cv)
    .subject(e => pick(e.x, e.y) || null)
    .on("start", e => { if (!e.subject) return; cv.classList.add("grabbing"); sim.alphaTarget(.25).restart(); e.subject.fx = e.subject.x; e.subject.fy = e.subject.y; })
    .on("drag", e => { if (!e.subject) return; const [x, y] = S.transform.invert([e.x, e.y]); e.subject.fx = x; e.subject.fy = y; })
    .on("end", e => { if (!e.subject) return; cv.classList.remove("grabbing"); sim.alphaTarget(0); e.subject.fx = e.subject.fy = null; })
    .filter(e => !e.button && pick(e.offsetX ?? e.x, e.offsetY ?? e.y)));
  const tip = $("#tip"), inp = $("#search"), res = $("#results");
  let lastQ = "";
  cv.addEventListener("mousemove", e => {
    const n = pick(e.offsetX, e.offsetY);
    S.hover = n;
    if (n) {
      tip.hidden = false;
      tip.style.left = Math.min(W - 340, e.clientX + 16) + "px"; tip.style.top = Math.min(H - 120, e.clientY + 16) + "px";
      tip.innerHTML = n.ghost
        ? `<b>[[${esc(n.label)}]]</b><div class="d">${t("ghost_tip", { n: n.refs })}</div>`
        : `<b>${esc(n.id)}</b><div class="d">${esc(n.desc || "").slice(0, 180)}</div><div class="m">${esc(t("t_" + n.type) || n.type)} · ${fmt(n.words)} ${t("words")} · ${n.in}↙ ${n.out}↗${n.age != null ? ` · ${t("days", { n: n.age })}` : ` · ${t("undated")}`}${(n.flags || []).length ? ` · ⚑ ${n.flags.length}` : ""}</div>`;
    } else tip.hidden = true;
  });
  cv.addEventListener("mouseleave", () => { S.hover = null; tip.hidden = true; });
  cv.addEventListener("click", e => {
    const n = pick(e.offsetX, e.offsetY);
    if (n) { if (n.ghost) openGhost(n); else openNote(n.id); return; }
    if (panel.classList.contains("open")) closePanel();
    if (S.recall.size) { S.recall.clear(); res.hidden = true; inp.value = ""; lastQ = ""; }
  });
  cv.addEventListener("dblclick", e => { const n = pick(e.offsetX, e.offsetY); if (n) focusNode(n); });
  function focusNode(n, k = 2.2) {
    d3.select(cv).transition().duration(reduced ? 0 : 900).ease(d3.easeCubicInOut)
      .call(zoom.transform, d3.zoomIdentity.translate(W / 2 - n.x * k, H / 2 - n.y * k).scale(k));
  }

  // ------------------------------------------------------------ panel
  const panel = $("#panel");
  function openPanel() { panel.classList.add("open"); panel.classList.remove("closed"); }
  function closePanel() { panel.classList.remove("open"); S.selected = null; history.replaceState(null, "", location.pathname + (params.get("lang") ? `?lang=${params.get("lang")}` : "")); }
  function showTab(id) {
    $$("#panel .tabs button[data-tab]").forEach(b => b.classList.toggle("on", b.dataset.tab === id));
    $$("#panel .tab").forEach(s => s.classList.toggle("on", s.id === `tab-${id}`));
    if (id === "pulse") renderPulse();
    if (id === "repairs") loadProposals();
    if (id === "bench") renderBench();
  }
  $$("#panel .tabs button[data-tab]").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("#closePanel").addEventListener("click", closePanel);
  $("#closeFab").addEventListener("click", closePanel);
  function bannerOffset() { const b = $("#demoBanner"); document.documentElement.style.setProperty("--banner-h", (b ? b.offsetHeight : 0) + "px"); }
  addEventListener("resize", bannerOffset);

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  marked.setOptions({ gfm: true, breaks: false });
  function renderMd(md) {
    const html = marked.parse(md.replace(/\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]/g, (_m, x) => `<a class="wl" data-note="${esc(x.trim())}">[[${esc(x.trim())}]]</a>`));
    const doc = new DOMParser().parseFromString(html, "text/html");
    doc.querySelectorAll("script,iframe,object,embed,style,link,meta,form").forEach(e => e.remove());
    doc.querySelectorAll("*").forEach(e => [...e.attributes].forEach(a => { if (/^on/i.test(a.name) || ((a.name === "href" || a.name === "src") && /^\s*(javascript|data):/i.test(a.value))) e.removeAttribute(a.name); }));
    return doc.body.innerHTML;
  }

  function itemText(f, it) {
    if (f.id === "secrets") return t("secret_item", { kind: it.kind, line: it.line });
    const parts = Object.entries(it).filter(([k, v]) => k !== "note" && v !== null && v !== "" && typeof v !== "object").map(([k, v]) => `${k}: ${v}`);
    return parts.join(" · ");
  }
  function itemAction(f, it) {
    if (!canWrite()) return "";
    const b = (label, p) => `<button class="btn mini" data-propose='${esc(JSON.stringify(p))}'>${label}</button>`;
    if (f.action === "relink" && it.suggestion) return b(t("act_relink", { to: esc(it.suggestion) }), { kind: "relink", target: String(it.target), new_target: String(it.suggestion), note: String(it.note || "") });
    if (f.action === "merge" && it.other) return b(t("act_merge"), { kind: "merge", keep: String(it.note), drop: String(it.other) });
    if (f.action === "rename") return b(t("act_rename", { to: esc(it.name || "") }), { kind: "rename", note: String(it.note), new_name: String(it.name || "") });
    if (f.action === "close") return b(t("act_close"), { kind: "close", note: String(it.note) });
    return "";
  }
  function findingItems(f, only) {
    const items = (f.items || []).filter(it => !only || it.note === only || it.other === only).slice(0, 30);
    if (!items.length) return "";
    return `<ul class="items">${items.map(it => `<li><span>${it.note ? `<a data-note="${esc(it.note)}">${esc(it.note)}</a>` : ""} <span class="small">${esc(itemText(f, it))}</span></span>${itemAction(f, it)}</li>`).join("")}</ul>`;
  }

  async function openNote(id) {
    const n = S.byId.get(id);
    S.selected = n || null; S.recall.clear(); res.hidden = true;
    openPanel(); showTab("note");
    const box = $("#tab-note"); box.innerHTML = `<div class="empty">${t("loading")}</div>`;
    history.replaceState(null, "", `?note=${encodeURIComponent(id)}${params.get("lang") ? `&lang=${params.get("lang")}` : ""}`);
    try {
      const d = await api(`/api/note/${encodeURIComponent(id)}`);
      if (n) focusNode(n, Math.max(1.4, S.transform.k));
      const approx = d.date_source && !["frontmatter", "indexed", "transcript"].includes(d.date_source);
      box.innerHTML = `
        <article class="note">
          <h1 class="nm">${esc(d.id)}</h1>
          <div class="meta">
            <span class="chip t-${esc(d.type)}">${esc(t("t_" + d.type) || d.type)}</span>
            <span class="chip">${fmt(d.words)} ${t("words")}</span>
            ${d.chunks != null ? `<span class="chip">${t("passages", { n: d.chunks })}</span>` : `<span class="chip approx">${t("not_indexed")}</span>`}
            ${d.modified ? `<span class="chip ${approx ? "approx" : ""}" title="${esc(t("date_source"))}: ${esc(d.date_source)}">${esc(d.modified)} · ${t("days", { n: d.age })}${approx ? " ≈" : ""}</span>` : `<span class="chip approx">${t("undated")}</span>`}
            <span class="chip">${esc(d.file)}</span>
          </div>
          ${d.desc ? `<div class="desc">${esc(d.desc)}</div>` : `<div class="flag crit"><b>${t("no_desc")}</b>${t("no_desc_text")}</div>`}
          ${d.warnings && d.warnings.length ? `<div class="flags"><div class="flag warn"><b>${t("header")}</b>${esc(d.warnings.join(" · "))}</div></div>` : ""}
          ${d.flags.length ? `<div class="flags">${d.flags.map(f => `<div class="flag ${f.severity}"><b>${esc(f.title)}</b>${esc(f.remedy)}${findingItems(f, d.id)}</div>`).join("")}</div>` : ""}
          <div class="links">
            <div><h4>${t("inbound", { n: d.in.length })}</h4><ul>${d.in.map(x => `<li><a data-note="${esc(x)}">${esc(x)}</a></li>`).join("") || "<li class='small'>—</li>"}</ul></div>
            <div><h4>${t("outbound", { n: d.out.length })}</h4><ul>${d.out.map(x => x.resolved ? `<li><a data-note="${esc(x.resolved)}">${esc(x.target)}</a></li>` : `<li><a class="broken" title="${esc(t("t_ghost"))}">${esc(x.target)}</a></li>`).join("") || "<li class='small'>—</li>"}</ul></div>
          </div>
          <div class="md">${renderMd(d.body)}</div>
        </article>`;
      box.scrollTop = 0;
    } catch (e) { box.innerHTML = `<div class="empty">${t("note_missing")} (${esc(e.message)})</div>`; }
  }
  function openGhost(n) {
    S.selected = n; openPanel(); showTab("note");
    const srcs = S.links.filter(l => l.target === n).map(l => l.source.id);
    $("#tab-note").innerHTML = `<article class="note"><h1 class="nm">[[${esc(n.label)}]]</h1>
      <div class="flags"><div class="flag warn"><b>${t("t_ghost")}</b>${t("ghost_text")}</div></div>
      <div class="links"><div><h4>${t("cited_by")}</h4><ul>${srcs.map(s => `<li><a data-note="${esc(s)}">${esc(s)}</a></li>`).join("")}</ul></div></div></article>`;
  }
  panel.addEventListener("click", async e => {
    const p = e.target.closest("[data-propose]");
    if (p) { e.preventDefault(); propose(JSON.parse(p.dataset.propose), p); return; }
    const a = e.target.closest("[data-note]");
    if (a) { e.preventDefault(); openNote(a.dataset.note); }
  });

  // ------------------------------------------------------------ health
  function renderHealth() {
    const r = S.review; if (!r) return;
    const arc = $("#healthArc"), s = r.score;
    arc.style.strokeDashoffset = 170 - 170 * s / 100;
    arc.style.stroke = s >= 80 ? COLORS.teal : s >= 55 ? COLORS.orange : COLORS.red;
    $("#healthScore").textContent = s;
    const b = $("#reviewBadge"), c = r.counts;
    if (c.crit) { b.hidden = false; b.className = "badge"; b.textContent = c.crit; }
    else if (c.warn) { b.hidden = false; b.className = "badge warn"; b.textContent = c.warn; }
    else b.hidden = true;
  }
  function spark(hist) {
    if (hist.length < 2) return `<div class="small">${t("history_short", { n: hist.length })}</div>`;
    const w = 480, h = 56, xs = d3.scaleLinear([0, hist.length - 1], [2, w - 2]), ys = d3.scaleLinear([0, 100], [h - 3, 3]);
    const line = d3.line().x((d, i) => xs(i)).y(d => ys(d.score)).curve(d3.curveMonotoneX);
    const area = d3.area().x((d, i) => xs(i)).y0(h).y1(d => ys(d.score)).curve(d3.curveMonotoneX);
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#c8a35a" stop-opacity=".45"/><stop offset="1" stop-color="#c8a35a" stop-opacity="0"/></linearGradient></defs>
      <path d="${area(hist)}" fill="url(#sg)"/><path d="${line(hist)}" fill="none" stroke="#f0d38a" stroke-width="1.5"/>
      <circle cx="${xs(hist.length - 1)}" cy="${ys(hist.at(-1).score)}" r="3" fill="#fff"/></svg>`;
  }
  function renderReview() {
    const r = S.review, box = $("#tab-review"); if (!r) return;
    const cls = r.score >= 80 ? "" : r.score >= 55 ? "mid" : "bad";
    const dotc = { crit: COLORS.red, warn: COLORS.orange, info: "#6b7280" };
    const sm = S.summary || {};
    let html = `<div class="score"><span class="n ${cls}">${r.score}</span><span class="t">${t("score_line", { n: r.notes_total })}<br>${t("reviewed", { at: when(r.at), ms: r.duration_ms })}${S.digestAt ? `<br>${t("last_digest", { at: when(S.digestAt) })}` : ""}</span></div>
      ${spark(S.history)}
      ${sm.open != null ? `<div class="kv four"><div><b>${sm.open}</b><span>${t("open_problems")}</span></div><div><b>${sm.open_over_7d ?? 0}</b><span>${t("open_7d")}</span></div><div><b>${sm.fixed ?? 0}</b><span>${t("fixed")}</span></div><div><b>${sm.mean_fix_hours != null ? sm.mean_fix_hours + " h" : "—"}</b><span>${t("mean_fix")}</span></div></div>` : ""}
      ${canWrite() ? `<button class="btn" id="rerun">${t("rerun")}</button> ` : ""}<span class="small">${S.notify ? t("review_note_notify") : t("review_note")}</span>`;
    for (const sev of ["crit", "warn", "info"]) {
      const fs = r.findings.filter(f => f.severity === sev); if (!fs.length) continue;
      html += `<div class="sev"><i style="background:${dotc[sev]};box-shadow:0 0 8px ${dotc[sev]}"></i>${fs.length} ${t("sev_" + sev)}</div>`;
      for (const f of fs) html += `<details class="f ${sev}" ${sev === "crit" ? "open" : ""}><summary><span>${esc(f.title)}</span><span class="cnt">${f.count || ""}</span></summary>
        <div class="body">${esc(f.detail)}${f.remedy ? `<div class="rem" data-label="${esc(t("remedy"))}">${esc(f.remedy)}</div>` : ""}
        ${findingItems(f) || (f.notes?.length ? `<div class="nl">${f.notes.slice(0, 40).map(n => `<a data-note="${esc(n)}">${esc(n)}</a>`).join("")}${f.notes.length > 40 ? `<span class="small">… +${f.notes.length - 40}</span>` : ""}</div>` : "")}</div></details>`;
    }
    if (!r.findings.length) html += `<div class="empty">${t("all_clean")}</div>`;
    box.innerHTML = html;
    const rr = $("#rerun");
    if (rr) rr.addEventListener("click", async e => {
      e.target.disabled = true; e.target.textContent = t("running");
      try { await api("/api/review/run", { method: "POST" }); await loadReview(); toast(t("review_done")); }
      catch (err) { toast(`${t("failed")}: ${err.message}`); }
      finally { e.target.disabled = false; e.target.textContent = t("rerun"); }
    });
  }

  // ------------------------------------------------------------ repairs
  function repairBadge() { const b = $("#repairBadge"); b.hidden = !S.pending; b.textContent = S.pending || ""; }
  async function propose(p, btn) {
    if (btn) btn.disabled = true;
    try {
      const rec = await api("/api/proposals", { method: "POST", body: p });
      toast(t("proposed", { title: rec.title }));
      S.pending = (S.pending || 0) + 1; repairBadge();
      openPanel(); showTab("repairs");
    } catch (e) { toast(`${t("failed")}: ${e.message}`); }
    finally { if (btn) btn.disabled = false; }
  }
  async function loadProposals() {
    const box = $("#tab-repairs"); box.innerHTML = `<div class="empty">${t("loading")}</div>`;
    try { S.proposals = await api("/api/proposals"); } catch { box.innerHTML = `<div class="empty">${t("unavailable")}</div>`; return; }
    S.pending = S.proposals.filter(p => p.status === "pending").length; repairBadge();
    const focusId = params.get("proposal");
    let html = `<p class="small">${t("repairs_intro")}</p>`;
    if (!canWrite()) html += `<div class="flag info">${S.info.demo ? t("demo_readonly") : t("writes_off")}</div>`;
    if (!S.proposals.length) html += `<div class="empty">${t("no_proposals")}</div>`;
    for (const p of S.proposals) {
      const diff = (p.changes || []).map(c => `<div class="small">${esc(c.path)}</div><pre class="diff">${(c.diff || "").split("\n").map(l => `<span class="${l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : ""}">${esc(l)}</span>`).join("\n")}</pre>`).join("");
      html += `<details class="f prop ${p.status}" ${p.status === "pending" || p.id === focusId ? "open" : ""}><summary><span>${esc(p.title)}</span><span class="cnt">${t("st_" + p.status)}</span></summary>
        <div class="body"><div>${esc(p.summary || "")}</div><div class="small">${t("proposal_meta", { by: esc(p.created_by || "?"), at: when((p.created_at || 0) * 1000), n: p.files || (p.changes || []).length })}</div>
        ${p.error ? `<div class="flag warn">${esc(p.error)}</div>` : ""}
        ${diff}
        ${p.status === "pending" && canWrite() ? `<div class="acts"><button class="btn" data-decide="approve" data-id="${esc(p.id)}">${t("approve")}</button> <button class="btn ghost" data-decide="refuse" data-id="${esc(p.id)}">${t("refuse")}</button></div>` : ""}
        </div></details>`;
    }
    box.innerHTML = html;
    $$("[data-decide]", box).forEach(b => b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        const r = await api(`/api/proposals/${b.dataset.id}/${b.dataset.decide}`, { method: "POST" });
        toast(t("decided_" + r.status, { title: r.title }));
        await loadProposals();
        setTimeout(() => { loadReview(); S.version = null; loadGraph(); }, 2500);
      } catch (e) { toast(`${t("failed")}: ${e.message}`); b.disabled = false; }
    }));
  }

  // ------------------------------------------------------------ bench
  const pct = x => x == null ? "—" : (x * 100).toFixed(1) + " %";
  const f3 = x => x == null ? "—" : Number(x).toFixed(3);
  async function renderBench() {
    const box = $("#tab-bench"); box.innerHTML = `<div class="empty">${t("loading")}</div>`;
    let d;
    try { d = await api("/api/bench"); } catch (e) { box.innerHTML = `<div class="empty">${t("unavailable")} (${esc(e.message)})</div>`; return; }
    const q = d.questions || {}, last = d.last, m = last?.metrics?.overall;
    let html = `<p class="small">${t("bench_intro")}</p>
      <div class="kv four"><div><b>${q.total_active ?? 0}</b><span>${t("questions")}</span></div>
      <div><b>${f3(m?.mrr)}</b><span>MRR</span></div><div><b>${pct(m?.h1)}</b><span>hit@1</span></div><div><b>${pct(m?.h5)}</b><span>hit@5</span></div></div>`;
    if (last) html += `<div class="small">${t("bench_last", { id: last.id, at: when(last.finished_at || last.started_at), gen: last.generation_id, p50: last.metrics?.search_ms_p50 ?? "—" })}</div>`;
    for (const f of d.findings || []) html += `<div class="flag ${f.severity}"><b>${esc(f.title)}</b>${esc(f.detail || "")}</div>`;
    if (canWrite()) html += `<div class="acts"><button class="btn" id="benchRun">${t("bench_run")}</button></div>
      <h4 class="sev">${t("bench_add")}</h4>
      <form id="qForm" class="qform"><input name="q" placeholder="${esc(t("bench_q"))}" required minlength="8"><input name="n" placeholder="${esc(t("bench_n"))}" required><button class="btn">${t("add")}</button></form>`;
    const runs = (d.runs || []).slice(0, 12);
    if (runs.length) html += `<h4 class="sev">${t("bench_runs")}</h4><table class="runs"><tr><th>#</th><th>${t("when")}</th><th>gen</th><th>MRR</th><th>hit@1</th><th>n</th></tr>${runs.map(r => `<tr class="${r.regression ? "reg" : ""}"><td>${r.id}</td><td>${when(r.started_at)}</td><td>${r.generation_id}</td><td>${f3(r.metrics?.overall?.mrr)}</td><td>${pct(r.metrics?.overall?.h1)}</td><td>${r.metrics?.overall?.n ?? r.n_questions ?? "—"}</td></tr>`).join("")}</table>`;
    box.innerHTML = html;
    const br = $("#benchRun");
    if (br) br.addEventListener("click", async () => {
      br.disabled = true; br.textContent = t("running");
      try { const r = await api("/api/bench/run", { method: "POST" }); toast(t("bench_done", { mrr: f3(r.metrics?.overall?.mrr) })); renderBench(); }
      catch (e) { toast(`${t("failed")}: ${e.message}`); br.disabled = false; br.textContent = t("bench_run"); }
    });
    const qf = $("#qForm");
    if (qf) qf.addEventListener("submit", async e => {
      e.preventDefault();
      const notes = qf.n.value.split(",").map(s => s.trim()).filter(Boolean);
      try { await api("/api/bench/questions", { method: "POST", body: { question: qf.q.value.trim(), notes } }); toast(t("added")); renderBench(); }
      catch (err) { toast(`${t("failed")}: ${err.message}`); }
    });
  }

  // ------------------------------------------------------------ pulse
  async function renderPulse() {
    const box = $("#tab-pulse"); box.innerHTML = `<div class="empty">…</div>`;
    try {
      const [d, st] = await Promise.all([api("/api/stats"), api("/api/status").catch(() => null)]);
      const weeks = Object.entries(d.weekly).slice(-26), mx = Math.max(1, ...weeks.map(w => w[1]));
      const types = Object.entries(d.stats.types).sort((a, b) => b[1] - a[1]);
      const e = st?.embed || {}, lic = st?.models?.licence || {}, act = st?.models?.active || {};
      box.innerHTML = `<h4 class="sev">${t("pulse")}</h4>
        <div class="kv">
          <div><b>${d.age.fresh_7d}</b><span>${t("touched", { n: 7 })}</span></div>
          <div><b>${d.age.fresh_30d}</b><span>${t("touched", { n: 30 })}</span></div>
          <div><b>${d.age.median != null ? Math.round(d.age.median) + " " + t("d") : "—"}</b><span>${t("median_age")}</span></div>
          <div><b>${d.stats.broken}</b><span>${t("broken")}</span></div>
          <div><b>${d.stats.families}</b><span>${t("families")}</span></div>
          <div><b>${d.stats.chunks ?? "—"}</b><span>${t("chunks")}</span></div>
        </div>
        <h4 class="sev">${t("per_week")}</h4>
        <div class="bars">${weeks.map(([k, v]) => `<div style="height:${Math.max(2, v / mx * 100)}%" data-l="${k} · ${v}"></div>`).join("")}</div>
        <div class="small">${weeks[0]?.[0] || ""} → ${weeks.at(-1)?.[0] || ""}</div>
        <h4 class="sev">${t("types")}</h4>
        <div class="kv">${types.map(([ty, n]) => `<div><b style="color:${COLORS[ty] || COLORS.unknown}">${n}</b><span>${esc(t("t_" + ty) || ty)}</span></div>`).join("")}</div>
        ${st ? `<h4 class="sev">${t("engine")}</h4><div class="small engine">
          ${t("model")}: ${esc(e.label || e.model || "—")} (${esc(e.profile || "—")})<br>
          ${t("index")}: ${st.generation ? `${t("generation")} ${st.generation.id} · ${esc(st.generation.embed_identity)}` : "—"}${(st.building || []).length ? ` · ${t("building")}` : ""}<br>
          ${t("servers")}: ${(e.health?.embed || []).map(h => `${h.ok ? "●" : "○"} ${esc(h.url)}`).join(" ") || "—"}${e.health?.rerank ? ` · rerank ${e.health.rerank.ok ? "●" : "○"}` : ""}<br>
          ${t("licence")}: ${lic.decision === "accepted" ? t("gemma_ok") : act.embed ? t("gemma_no", { m: esc(act.embed) }) : "—"}<br>
          ${t("indexer")}: ${st.indexer ? (st.indexer.last_error ? `⚠ ${esc(st.indexer.last_error)}` : t("indexer_ok", { n: st.indexer.runs, at: st.indexer.last_run ? when(st.indexer.last_run * 1000) : "—" })) : "—"}
        </div>` : ""}
        <p class="small">${t("pulse_note", { v: esc(S.info.version || "") })}</p>`;
    } catch { box.innerHTML = `<div class="empty">${t("unavailable")}</div>`; }
  }

  // ------------------------------------------------------------ recall
  let tmr = null;
  inp.addEventListener("input", () => { clearTimeout(tmr); const q = inp.value.trim(); if (q.length < 2) { S.recall.clear(); res.hidden = true; return; } tmr = setTimeout(() => recall(q), 320); });
  inp.addEventListener("keydown", e => {
    if (e.key === "Escape") { const had = inp.value || S.recall.size; inp.value = ""; S.recall.clear(); res.hidden = true; lastQ = ""; inp.blur(); if (!had) closePanel(); }
    if (e.key === "Enter") { const sel = $("#results .r.sel") || $("#results .r"); if (sel) openNote(sel.dataset.note); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const rows = $$("#results .r"); if (!rows.length) return; let i = rows.findIndex(r => r.classList.contains("sel")); i = e.key === "ArrowDown" ? Math.min(rows.length - 1, i + 1) : Math.max(0, i - 1); rows.forEach(r => r.classList.remove("sel")); rows[i].classList.add("sel"); S.selected = S.byId.get(rows[i].dataset.note) || S.selected; }
  });
  async function recall(q) {
    if (q === lastQ) return; lastQ = q;
    try {
      const d = await api(`/api/search?q=${encodeURIComponent(q)}&k=12`);
      S.recall.clear();
      const top = d.results[0]?.score || 1;
      d.results.forEach((x, i) => { S.recall.set(x.note, Math.max(.15, x.score / top) * (1 - i * .04)); if (i < 3) S.flash.set(x.note, performance.now() + i * 120); });
      res.hidden = false;
      res.innerHTML = (d.degraded ? `<div class="deg">⚠ ${esc(d.degraded)}</div>` : "") + d.results.map((x, i) => `<div class="r ${i === 0 ? "sel" : ""}" data-note="${esc(x.note)}"><div class="sc">${x.score.toFixed(2)}</div><div><div class="nm">${esc(x.note)}${x.age_days != null ? ` <span class="small">· ${t("days", { n: x.age_days })}</span>` : ""}</div><div class="sn">${esc(x.snippet)}</div></div></div>`).join("") || `<div class="deg">${t("no_memory")}</div>`;
      $$("#results .r").forEach(el => { el.addEventListener("click", () => openNote(el.dataset.note)); el.addEventListener("mouseenter", () => { S.hover = S.byId.get(el.dataset.note) || null; }); el.addEventListener("mouseleave", () => { S.hover = null; }); });
    } catch { res.hidden = false; res.innerHTML = `<div class="deg">${t("search_down")}</div>`; }
  }
  addEventListener("keydown", e => {
    if (e.key === "/" && document.activeElement !== inp && !e.metaKey && !e.ctrlKey && !/INPUT|TEXTAREA/.test(document.activeElement?.tagName || "")) { e.preventDefault(); inp.focus(); }
    if (e.key === "Escape" && document.activeElement !== inp) { if (S.recall.size) { S.recall.clear(); res.hidden = true; inp.value = ""; } else closePanel(); }
  });

  // ------------------------------------------------------------ modes / legend
  $$("#modes button[data-mode]").forEach(b => b.addEventListener("click", () => { $$("#modes button[data-mode]").forEach(x => x.classList.remove("on")); b.classList.add("on"); S.mode = b.dataset.mode; applyMode(); if (S.mode === "health") { openPanel(); showTab("review"); } }));
  $("#labels").addEventListener("change", e => { S.labels = e.target.checked; });
  $$("#legend .lg").forEach(l => l.addEventListener("click", () => { const k = l.classList.contains("ghost") ? "ghost" : l.dataset.type; l.classList.toggle("off"); S.hidden.has(k) ? S.hidden.delete(k) : S.hidden.add(k); }));

  function toast(msg) { const el = document.createElement("div"); el.className = "toast"; el.textContent = msg; $("#toasts").appendChild(el); setTimeout(() => el.remove(), 6200); }

  function translatePage() {
    document.documentElement.lang = LANG;
    $$("[data-i18n]").forEach(el => { const v = t(el.dataset.i18n); if (v) el.textContent = v; });
    inp.placeholder = t("search_ph");
    $("#closePanel").title = t("close");
    $("#health").title = t("health_tip");
  }

  // ------------------------------------------------------------ go
  let booted = false, timers = [];
  async function boot() {
    if (booted) return; booted = true;
    await loadGraph(true); await loadReview();
    timers.push(setInterval(() => loadGraph(false), 20000), setInterval(loadReview, 5 * 60000), setInterval(liveTick, 1000));
  }
  (async () => {
    try { S.info = await (await fetch("/api/info", { cache: "no-store" })).json(); } catch { S.info = {}; }
    if (!params.get("lang") && S.info.lang) LANG = I.pick(S.info.lang);
    translatePage();
    if (S.info.title) document.title = S.info.title;
    if (S.info.demo) {
      document.body.dataset.demo = "1";
      const b = document.createElement("div"); b.id = "demoBanner";
      b.innerHTML = S.info.demo_text ? esc(S.info.demo_text) : t("demo_banner");
      document.body.prepend(b);
      $(".brand .sub").textContent = t("demo_sub");
    }
    const lic = S.info.licence;
    if (!S.info.demo && lic && lic.embed && lic.decision !== "accepted" && lic.reason && lic.reason !== "gemma-terms-accepted") {
      toast(t("fallback_toast", { m: lic.embed }));
    }
    bannerOffset();
    resize();
    requestAnimationFrame(render);
    if (!S.info.public_read && !getToken()) { showLogin(); return; }
    boot();
  })();
})();
