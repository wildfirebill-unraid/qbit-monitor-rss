"use strict";

/* --------------------------------------------------------------- state */
const S = {
  state: null,        // /api/state
  torrents: [],       // /api/torrents?instance=all
  rss: null,          // /api/rss
  activeTab: "all",
  search: "",
  sortKey: "added_on",
  sortDir: -1,
  showCompleted: true,
};

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------- helpers */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function trackerLabel(t) {
  if (!t) return "";
  return (t.abbr || t.url || t.name || "").trim() || `Tracker ${t.id}`;
}

function trackerOptions() {
  const trackers = S.state?.trackers || [];
  const opts = [`<option value="">No tracker</option>`];
  for (const t of trackers) {
    const label = t.public ? `${trackerLabel(t)} (public)` : trackerLabel(t);
    opts.push(`<option value="${t.id}">${esc(label)}</option>`);
  }
  return opts.join("");
}

function fmtBytes(v) {
  if (!v && v !== 0) return "-";
  v = Number(v);
  const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

function fmtSpeed(v) {
  if (!v && v !== 0) return "-";
  return `${fmtBytes(v)}/s`;
}

function fmtRatio(v) {
  if (v === undefined || v === null) return "-";
  return Number(v).toFixed(2);
}

function fmtDuration(s) {
  s = Math.max(0, Math.floor(Number(s) || 0));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtDate(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtETA(s) {
  if (!s || s < 0) return "-";
  return fmtDuration(s);
}

const STATE_LABEL = {
  downloading: ["Downloading", "st-down"],
  forcedDL: ["Forced download", "st-down"],
  metaDL: ["Fetching metadata", "st-down"],
  stalledDL: ["Stalled", "st-down"],
  queuedDL: ["Queued", "st-queued"],
  allocating: ["Allocating", "st-down"],
  checkingDL: ["Checking", "st-down"],
  uploading: ["Seeding", "st-up"],
  forcedUP: ["Forced seeding", "st-up"],
  stalledUP: ["Stalled seeding", "st-up"],
  queuedUP: ["Queued", "st-queued"],
  checkingUP: ["Checking", "st-up"],
  pausedUP: ["Paused", "st-pause"],
  pausedDL: ["Paused", "st-pause"],
  errored: ["Error", "st-error"],
  missingFiles: ["Missing files", "st-error"],
  moving: ["Moving", "st-queued"],
  unknown: ["Unknown", "st-queued"],
};

function stateBadge(st) {
  const [label, cls] = STATE_LABEL[st] || STATE_LABEL.unknown;
  return `<span class="st-badge ${cls}">${label}</span>`;
}

/* --------------------------------------------------------------- api */
async function api(path, opts) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  return r.json();
}

/* ------------------------------------------------------------ polling */
function startPolling() {
  pollState();
  setInterval(pollState, 4000);
  setInterval(pollTorrents, 5000);
  setInterval(() => { if (S.activeTab === "rss") pollRss(); }, 20000);
}

async function pollState() {
  try {
    const data = await api("/api/state");
    if (data && data.instances) { S.state = data; renderHeader(); renderTabs(); }
    if (S.activeTab === "settings") renderSettings();
    $("foot-status").textContent =
      `Last refresh ${new Date().toLocaleTimeString()} ` +
      (S.state && S.state.instances.some((i) => !i.connected) ? "| some instances unreachable" : "");
  } catch (e) {
    $("foot-status").textContent = `API error: ${e}`;
  }
}

async function pollTorrents() {
  try {
    const data = await api("/api/torrents?instance=all");
    if (data && Array.isArray(data.torrents)) S.torrents = data.torrents;
    if (["all"].concat((S.state?.instances || []).map((i) => String(i.id))).includes(S.activeTab)) {
      renderTorrents();
    }
  } catch (e) { /* ignore */ }
}

async function pollRss() {
  try {
    const data = await api("/api/rss");
    if (data && Array.isArray(data.feeds)) { S.rss = data; renderRss(); }
  } catch (e) { /* ignore */ }
}

/* --------------------------------------------------------- header/tabs */
function renderHeader() {
  const s = S.state;
  if (!s) return;
  const dots = s.instances
    .map(
      (i) => `<span class="inst-dot" title="${esc(i.url)}${i.error ? " \u2014 " + esc(i.error) : ""}">
        <i class="${i.connected ? "ok" : "bad"}"></i>${esc(i.name)}
        <span class="spd">${i.connected ? "&#8595;" + fmtSpeed(i.download_speed) + " &#8593;" + fmtSpeed(i.upload_speed) : ""}</span>
      </span>`
    )
    .join("");
  $("instance-dots").innerHTML = dots || `<span class="inst-dot"><i class="bad"></i>no instances</span>`;
}

function renderTabs() {
  const s = S.state;
  if (!s) return;
  const instTabs = s.instances
    .map((i) => {
      const active = S.activeTab === String(i.id);
      return `<button class="tab ${active ? "active" : ""}" data-tab="${i.id}">
        ${esc(i.name)}<span class="count">${i.torrent_count}</span>
      </button>`;
    })
    .join("");
  const allActive = S.activeTab === "all";
  const rssActive = S.activeTab === "rss";
  const rssCount = S.rss ? S.rss.feeds.reduce((n, f) => n + f.items.filter((it) => it.state === "pending").length, 0) : 0;
  $("tabs").innerHTML = `
    <button class="tab ${allActive ? "active" : ""}" data-tab="all">All<span class="count">${S.torrents.length}</span></button>
    ${instTabs}
    <button class="tab ${rssActive ? "active" : ""}" data-tab="rss">RSS<span class="count">${rssCount}</span></button>
    <button class="tab ${S.activeTab === "settings" ? "active" : ""}" data-tab="settings">Settings</button>
    <button class="tab ${S.activeTab === "about" ? "active" : ""}" data-tab="about">About</button>
  `;
  $("tabs").querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => { switchTab(t.dataset.tab); })
  );
}

function switchTab(tab) {
  S.activeTab = tab;
  renderTabs();
  const view = $("view");
  if (tab === "all" || (S.state?.instances || []).some((i) => String(i.id) === tab)) {
    renderTorrents();
  } else if (tab === "rss") {
    pollRss();
    view.innerHTML = `<div class="empty">Loading RSS\u2026</div>`;
  } else if (tab === "settings") {
    renderSettings();
  } else if (tab === "about") {
    renderAbout();
  }
}

/* ------------------------------------------------------------- torrents */
function activeTorrents() {
  if (S.activeTab === "all") return S.torrents;
  return S.torrents.filter((t) => String(t.instance_id) === S.activeTab);
}

function renderTorrents() {
  let list = activeTorrents();
  const q = S.search.trim().toLowerCase();
  if (q) list = list.filter((t) => (t.name || "").toLowerCase().includes(q));
  if (!S.showCompleted) list = list.filter((t) => (t.progress || 0) < 1);

  list.sort((a, b) => {
    const val = (t) => t[S.sortKey];
    const av = val(a), bv = val(b);
    if (typeof av === "string" && typeof bv === "string") return av.localeCompare(bv) * S.sortDir;
    return ((av ?? 0) - (bv ?? 0)) * S.sortDir;
  });

  const allTab = S.activeTab === "all";
  const multiInst = (S.state?.instances || []).length > 1;
  const arrow = (k) => (S.sortKey === k ? (S.sortDir === 1 ? " \u25b2" : " \u25bc") : "");
  const cols = allTab
    ? `<th data-k="instance_name">Instance${arrow("instance_name")}</th>` : "";
  const actionTh = multiInst ? "<th>Actions</th>" : "";
  const colspan = (allTab ? 7 : 6) + (multiInst ? 1 : 0);

  let rows;
  if (!list.length) {
    rows = `<tr><td colspan="${colspan}" class="empty">No torrents${q ? " matching \u201c" + esc(q) + "\u201d" : ""}</td></tr>`;
  } else {
    rows = list.map((t) => {
      const name = t.name || t.hash || "";
      const prog = (t.progress || 0) * 100;
      const cat = t.category ? `<span class="tag cat">${esc(t.category)}</span>` : "";
      const inst = allTab
        ? `<td><span class="inst-badge"><i style="background:${t.instance_connected ? "var(--green)" : "var(--red)"}"></i>${esc(t.instance_name)}</span></td>`
        : "";
      const move = multiInst
        ? `<td><button class="btn sm ghost move-btn" data-move-hash="${esc(t.hash || "")}" data-move-from="${t.instance_id}">Move</button></td>`
        : "";
      return `<tr title="${esc(t.hash || "")}">
        ${inst}
        <td class="name">${esc(name)}${cat}</td>
        <td class="num">${fmtBytes(t.size)}</td>
        <td><span class="prog"><span class="bar"><i style="width:${prog.toFixed(1)}%"></i></span><span>${prog.toFixed(1)}%</span></span></td>
        <td>${t.tracker ? esc(t.tracker) : "-"}</td>
        <td class="num">${fmtDuration(t.seeding_time)}</td>
        <td class="num">${fmtDate(t.added_on)}</td>
        ${move}
      </tr>`;
    }).join("");
  }

  $("view").innerHTML = `
    <div class="toolbar">
      <input class="search" placeholder="Search torrents\u2026" value="${esc(S.search)}" id="search-box" />
      <span class="btn ghost" id="toggle-completed" title="Show/hide 100% completed torrents" style="opacity:.85">&#128065;</span>
      <span style="flex:1"></span>
      <button class="btn" id="magnet-add">+ Add magnet</button>
      <button class="btn primary" id="torrent-add">+ Add torrent</button>
    </div>
    <div class="torrent-table-wrap">
      <table class="torrents">
        <thead><tr>
          ${cols}
          <th data-k="name">Name${arrow("name")}</th>
          <th data-k="size">Size${arrow("size")}</th>
          <th data-k="progress">Progress${arrow("progress")}</th>
          <th data-k="tracker">Tracker${arrow("tracker")}</th>
          <th data-k="seeding_time">Seed time${arrow("seeding_time")}</th>
          <th data-k="added_on">Added${arrow("added_on")}</th>
          ${actionTh}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  wireTorrentEvents();
}

function wireTorrentEvents() {
  const search = $("search-box");
  if (search) search.addEventListener("input", () => { S.search = search.value; renderTorrents(); });
  const toggler = $("toggle-completed");
  if (toggler) toggler.addEventListener("click", () => {
    S.showCompleted = !S.showCompleted;
    renderTorrents();
  });
  document.querySelectorAll(".torrents th[data-k]").forEach((th) =>
    th.addEventListener("click", () => {
      const k = th.dataset.k;
      if (S.sortKey === k) S.sortDir *= -1;
      else { S.sortKey = k; S.sortDir = k === "name" ? 1 : -1; }
      renderTorrents();
    })
  );
  const magnetBtn = $("magnet-add");
  if (magnetBtn) magnetBtn.addEventListener("click", () => openAddDialog());
  const addBtn = $("torrent-add");
  if (addBtn) addBtn.addEventListener("click", () => openUploadDialog());
  document.querySelectorAll(".move-btn").forEach((b) =>
    b.addEventListener("click", () =>
      openMoveDialog(b.dataset.moveHash, Number(b.dataset.moveFrom))
    )
  );
}

/* ------------------------------------------------------------------ rss */
function renderRss() {
  if (!S.rss) return;
  const feeds = S.rss.feeds;
  const body = feeds.length
    ? feeds.map(feedCard).join("")
    : `<div class="empty">No feeds yet \u2014 add an RSS feed to auto-download.</div>`;
  $("view").innerHTML = `
    <div class="toolbar">
      <button class="btn primary" id="feed-add">+ Add RSS feed</button>
      <span style="color:var(--muted);font-size:12px">Feeds are scanned on a schedule; new items join the slot queue and download to the chosen instance.</span>
    </div>
    <div class="cards">${body}</div>
  `;
  $("feed-add").addEventListener("click", () => openFeedDialog());
  document.querySelectorAll("[data-feed-edit]").forEach((b) =>
    b.addEventListener("click", () => openFeedDialog(Number(b.dataset.feedEdit)))
  );
  document.querySelectorAll("[data-feed-scan]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      await api(`/api/feeds/${b.dataset.feedScan}/scan`, { method: "POST" });
      await Promise.all([pollRss(), pollState()]);
    })
  );
  document.querySelectorAll("[data-feed-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Delete this feed? Its queued items are removed too.")) return;
      await api(`/api/feeds/${b.dataset.feedDel}/delete`, { method: "POST" });
      await Promise.all([pollRss(), pollState()]);
    })
  );
  document.querySelectorAll("[data-feed-toggle]").forEach((t) =>
    t.addEventListener("change", async () => {
      await api(`/api/feeds/${t.dataset.feedToggle}/toggle`, { method: "POST" });
      await Promise.all([pollRss(), pollState()]);
    })
  );
  document.querySelectorAll("[data-item-action]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api(`/api/rss/${encodeURIComponent(b.dataset.guid)}/action?action=${b.dataset.itemAction}`, { method: "POST" });
      await Promise.all([pollRss(), pollState()]);
    })
  );
}

function feedCard(f) {
  const counts = { pending: 0, added: 0, duplicate: 0, error: 0, ignored: 0 };
  f.items.forEach((it) => { counts[it.state] = (counts[it.state] || 0) + 1; });
  const tr = f.tracker;
  const slotsInfo = tr && tr.public
    ? `<span>Tracker: <b>public</b></span>`
    : `<span>Tracker: <b>${tr ? esc(trackerLabel(tr)) : "default"}</b></span>
       <span>Slots: <b>${tr && tr.max_slots ? tr.max_slots : 50}</b></span>
       <span>Seed: <b>${tr && tr.seed_hours ? tr.seed_hours + "h" : "72.5h"}</b></span>`;
  const meta =
    `<div class="feed-meta">
      <span>Instance: <b>${esc(f.instance_name)}</b></span>
      ${slotsInfo}
      <span>Pending: <b>${counts.pending}</b></span>
      <span>Added: <b>${counts.added}</b></span>
      <span>Duplicate: <b>${counts.duplicate}</b></span>
      <span>Errors: <b>${counts.error}</b></span>
      <span>Last scan: <b>${f.last_scan ? fmtDate(f.last_scan) : "never"}</b></span>
    </div>`;

  let rows;
  if (!f.items.length) {
    rows = `<tr><td class="nofeed" colspan="4">No items seen yet.</td></tr>`;
  } else {
    rows = f.items.map((it) => {
      const stBadge = `<span class="st-badge state-${it.state}">${esc(it.state)}</span>`;
      const err = it.error ? ` <span title="${esc(it.error)}" style="color:var(--red)">&#9888;</span>` : "";
      let actions = "";
      if (it.state === "pending") {
        actions = `<button class="btn sm primary" data-item-action="add-now" data-guid="${esc(it.guid)}">Add now</button>
                   <button class="btn sm" data-item-action="ignore" data-guid="${esc(it.guid)}">Ignore</button>`;
      } else if (it.state === "duplicate" || it.state === "error") {
        actions = `<button class="btn sm" data-item-action="retry" data-guid="${esc(it.guid)}">Retry</button>
                   <button class="btn sm" data-item-action="ignore" data-guid="${esc(it.guid)}">Ignore</button>`;
      } else if (it.state === "ignored") {
        actions = `<button class="btn sm" data-item-action="retry" data-guid="${esc(it.guid)}">Restore</button>`;
      }
      return `<tr title="${esc(it.link || "")}">
        <td class="ititle">${esc(it.title)}${err}</td>
        <td>${stBadge}</td>
        <td class="num">${fmtDate(it.matched_at)}</td>
        <td style="white-space:nowrap">${actions}</td>
      </tr>`;
    }).join("");
  }

  return `<div class="card">
    <div class="feed-head">
      <h3>${esc(f.name)}</h3>
      <label class="switch" title="Enabled"><input type="checkbox" data-feed-toggle="${f.id}" ${f.enabled ? "checked" : ""} /><i></i></label>
    </div>
    <div class="sub">${esc(f.url)}</div>
    ${meta}
    <div class="card-actions">
      <button class="btn sm" data-feed-edit="${f.id}">Edit</button>
      <button class="btn sm" data-feed-scan="${f.id}">Scan now</button>
      <button class="btn sm danger" data-feed-del="${f.id}">Delete</button>
    </div>
    <table class="item-table">
      <thead><tr><th>Title</th><th>Status</th><th>Matched</th><th>Actions</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

/* ------------------------------------------------------------- settings */
function renderSettings() {
  const s = S.state;
  if (!s) return;
  const set = s.settings;
  const instCards = s.instances.length
    ? s.instances.map((i) => `
      <div class="card">
        <h3>${esc(i.name)}</h3>
        <div class="sub">${esc(i.url)}</div>
        <div class="status-line"><i class="${i.connected ? "ok" : "bad"}"></i>
          ${i.connected ? `connected \u00b7 v${esc(i.version || "")} \u00b7 ${i.torrent_count} torrents` : esc(i.error || "not connected")}
        </div>
        <div class="card-actions">
          <button class="btn sm" data-inst-edit="${i.id}">Edit</button>
          <button class="btn sm danger" data-inst-del="${i.id}">Delete</button>
        </div>
      </div>`).join("")
    : `<div class="card"><div class="empty" style="padding:12px">No instances configured.</div></div>`;

  const trackerCards = (s.trackers || []).length
    ? s.trackers.map((t) => `
      <div class="card">
        <h3>${esc(trackerLabel(t))}</h3>
        ${t.url ? `<div class="status-line">${esc(t.url)}</div>` : ""}
        <div class="status-line">${t.public
          ? "public \u00b7 no slot or seed limits"
          : `${t.max_slots ? t.max_slots : 50} max slots \u00b7 seed ${t.seed_hours ? t.seed_hours + "h" : "72.5h"}`}
        </div>
        <div class="card-actions">
          <button class="btn sm" data-tracker-edit="${t.id}">Edit</button>
          <button class="btn sm danger" data-tracker-del="${t.id}">Delete</button>
        </div>
      </div>`).join("")
    : `<div class="card"><div class="empty" style="padding:12px">No trackers configured \u2014 feeds use default limits (50 slots / 72.5h) until assigned to a tracker.</div></div>`;

  $("view").innerHTML = `
    <div class="set-grid">
      <div class="set-item">
        <h3 style="margin:0 0 12px">Polling &amp; downloading</h3>
        <label>Poll interval (s)
          <input type="number" id="sl-poll" min="5" step="5" value="${set.poll_interval_seconds}" />
        </label>
        <label>RSS scan interval (min)
          <input type="number" id="sl-rss" min="1" step="1" value="${set.rss_scan_interval_minutes}" />
        </label>
        <label>Default save folder
          <input type="text" id="sl-data" value="${esc(set.data_folder || "/data/torrents")}" placeholder="/data/torrents" />
        </label>
        <button class="btn primary" id="sl-save">Save settings</button>
      </div>
      <div class="set-item">
        <h3 style="margin:0 0 12px">Tracker limits</h3>
        <p style="color:var(--muted);font-size:12px;margin:0">Slot caps and seed hours belong to trackers, not feeds \u2014 all feeds of one tracker share its slot budget (e.g. 200 slots max across every feed from that tracker). A torrent occupies a slot from when it is added until it has seeded for that tracker&rsquo;s seed hours. Public trackers have no limits.</p>
      </div>
    </div>
    <h3 style="margin:4px 0 10px;font-size:14px">Trackers</h3>
    <div style="margin-bottom:14px"><button class="btn primary" id="tracker-add">+ Add tracker</button></div>
    <div class="cards">${trackerCards}</div>
    <h3 style="margin:18px 0 10px;font-size:14px">qBittorrent instances</h3>
    <div style="margin-bottom:14px"><button class="btn primary" id="inst-add">+ Add instance</button></div>
    <div class="cards">${instCards}</div>
  `;

  $("tracker-add").addEventListener("click", () => openTrackerDialog());
  document.querySelectorAll("[data-tracker-edit]").forEach((b) =>
    b.addEventListener("click", () => openTrackerDialog(Number(b.dataset.trackerEdit)))
  );
  document.querySelectorAll("[data-tracker-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Delete this tracker? Feeds that use it fall back to default limits.")) return;
      await api(`/api/trackers/${b.dataset.trackerDel}/delete`, { method: "POST" });
      await pollState();
    })
  );
  $("inst-add").addEventListener("click", () => openInstanceDialog());
  document.querySelectorAll("[data-inst-edit]").forEach((b) =>
    b.addEventListener("click", () => openInstanceDialog(Number(b.dataset.instEdit)))
  );
  document.querySelectorAll("[data-inst-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Delete this instance?")) return;
      await api(`/api/instances/${b.dataset.instDel}/delete`, { method: "POST" });
      await pollState();
    })
  );
  $("sl-save").addEventListener("click", async () => {
    const body = {
      poll_interval_seconds: Math.max(5, Number($("sl-poll").value) || 30),
      rss_scan_interval_minutes: Math.max(1, Number($("sl-rss").value) || 15),
      data_folder: $("sl-data").value.trim() || "/data/torrents",
    };
    await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
    await pollState();
  });
}

/* ------------------------------------------------------------------ about */
async function renderAbout() {
  $("view").innerHTML = `<div class="empty">Loading\u2026</div>`;
  let info;
  try {
    info = await api("/api/about");
  } catch (e) {
    $("view").innerHTML = `<div class="empty">Could not load about info.</div>`;
    return;
  }
  $("view").innerHTML = `
    <div class="about-card">
      <h1>${esc(info.name)}</h1>
      <div class="sub">v${esc(info.version)}</div>
      <div class="about-update" id="about-update">
        <button class="btn sm ghost" id="update-check" onclick="checkUpdate()">Check for updates</button>
      </div>
      <div class="about-row"><span>Creator</span><b>${esc(info.creator)}</b></div>
      <div class="about-row"><span>Homepage</span><a href="${esc(info.repo_url)}" target="_blank" rel="noopener">${esc(info.repo_url)}</a></div>
      <div class="about-row"><span>Issues</span><a href="${esc(info.issues_url)}" target="_blank" rel="noopener">${esc(info.issues_url)}</a></div>
      <div class="about-row"><span>License</span><a href="LICENSE" target="_blank" rel="noopener">MIT</a></div>
      <div class="about-copy">${esc(info.copyright)}</div>
    </div>
  `;
  checkUpdate();
}

async function checkUpdate() {
  const box = $("about-update");
  if (!box) return;
  box.innerHTML = `<span class="about-checking">Checking for updates\u2026</span>`;
  let data;
  try {
    data = await api("/api/update");
  } catch (e) {
    box.innerHTML = `<span class="about-update-fail">Could not reach update server.</span>`;
    return;
  }
  if (!data.ok) {
    box.innerHTML = `<span class="about-update-fail">Update check failed: ${esc(data.error || "unknown error")}</span>
      <button class="btn sm ghost" onclick="checkUpdate()">Try again</button>`;
    return;
  }
  if (data.update_available === null) {
    box.innerHTML = `<span class="about-update-ok">Running from source (v${esc(data.current)}); latest release is ${esc(data.latest)}.</span>`;
  } else if (data.update_available) {
    box.innerHTML = `<span class="about-update-new">Update available: ${esc(data.latest)}</span>
      <a class="btn sm primary" href="${esc(data.releases_url)}" target="_blank" rel="noopener">Go to releases</a>`;
  } else {
    box.innerHTML = `<span class="about-update-ok">Up to date (${esc(data.latest)}).</span>
      <button class="btn sm ghost" onclick="checkUpdate()">Check again</button>`;
  }
}

/* --------------------------------------------------------------- dialogs */
function openInstanceDialog(id) {
  const dlg = $("instanceDialog");
  const inst = id ? S.state.instances.find((i) => i.id === id) : null;
  $("inst-id").value = inst ? inst.id : "";
  $("inst-name").value = inst ? inst.name : "";
  $("inst-url").value = inst ? inst.url : "";
  $("inst-user").value = inst ? inst.username : "";
  $("inst-pass").value = inst ? inst.password : "";
  $("inst-test-result").textContent = "";
  $("inst-test-result").className = "test-result";
  dlg.showModal();

  const url = () => $("inst-url").value.trim();
  const user = () => $("inst-user").value.trim();
  const pass = () => $("inst-pass").value;

  $("inst-test").onclick = async () => {
    const res = $("inst-test-result");
    res.className = "test-result";
    res.textContent = "Testing\u2026";
    const r = await api("/api/instances/test", {
      method: "POST",
      body: JSON.stringify({ url: url(), username: user(), password: pass() }),
    });
    res.classList.toggle("ok", r.ok);
    res.classList.toggle("bad", !r.ok);
    res.textContent = r.ok
      ? `Connected \u2014 qBittorrent ${r.version || "?"}`
      : `Failed: ${r.error || "bad credentials or unreachable"}`;
  };

  $("inst-save").onclick = async () => {
    if (!url()) { alert("URL is required"); return; }
    await api("/api/instances/save", {
      method: "POST",
      body: JSON.stringify({
        id: $("inst-id").value ? Number($("inst-id").value) : null,
        name: $("inst-name").value.trim(),
        url: url(),
        username: user(),
        password: pass(),
      }),
    });
    dlg.close();
    await Promise.all([pollState(), pollTorrents()]);
  };
}

function openFeedDialog(id) {
  const dlg = $("feedDialog");
  const feed = id && S.rss ? S.rss.feeds.find((f) => f.id === id) : null;
  $("feed-id").value = feed ? feed.id : "";
  $("feed-name").value = feed ? feed.name : "";
  $("feed-url").value = feed ? feed.url : "";
  $("feed-savepath").value = feed ? (feed.savepath || "") : "";
  $("feed-category").value = feed ? (feed.category || "") : "";
  $("feed-enabled").checked = feed ? feed.enabled : true;
  const sel = $("feed-instance");
  sel.innerHTML = (S.state.instances || [])
    .map((i) => `<option value="${i.id}" ${feed && feed.instance_id === i.id ? "selected" : ""}>${esc(i.name)}</option>`)
    .join("") || `<option value="0">no instances \u2014 add one first</option>`;
  const trSel = $("feed-tracker");
  trSel.innerHTML = `<option value="">No tracker (default 50 slots / 72.5h)</option>` +
    (S.state.trackers || [])
      .map((t) => `<option value="${t.id}" ${feed && feed.tracker_id === t.id ? "selected" : ""}>${esc(trackerLabel(t))}${t.public ? " (public)" : ""}</option>`)
      .join("");

  const fillCategories = () => {
    const dl = $("feed-categories");
    const inst = (S.state.instances || []).find((i) => String(i.id) === String(sel.value));
    dl.innerHTML = (inst && inst.categories ? inst.categories : [])
      .map((c) => `<option value="${esc(c)}"></option>`)
      .join("");
  };
  fillCategories();
  sel.addEventListener("change", fillCategories);
  dlg.showModal();

  $("feed-save").onclick = async () => {
    if (!$("feed-url").value.trim()) { alert("Feed URL is required"); return; }
    const instId = Number(sel.value);
    if (!instId) { alert("Add a qBittorrent instance first."); return; }
    await api("/api/feeds/save", {
      method: "POST",
      body: JSON.stringify({
        id: $("feed-id").value ? Number($("feed-id").value) : null,
        name: $("feed-name").value.trim(),
        url: $("feed-url").value.trim(),
        instance_id: instId,
        savepath: $("feed-savepath").value.trim(),
        category: $("feed-category").value.trim(),
        tracker_id: trSel.value ? Number(trSel.value) : null,
        enabled: $("feed-enabled").checked,
      }),
    });
    dlg.close();
    await Promise.all([pollRss(), pollState()]);
  };
}

function openTrackerDialog(id) {
  const dlg = $("trackerDialog");
  const tr = id ? (S.state.trackers || []).find((t) => t.id === id) : null;
  $("tracker-id").value = tr ? tr.id : "";
  $("tracker-name").value = tr ? tr.name : "";
  $("tracker-url").value = tr ? tr.url || "" : "";
  $("tracker-abbr").value = tr ? tr.abbr || "" : "";
  $("tracker-maxslots").value = tr && tr.max_slots ? tr.max_slots : "";
  $("tracker-seedhours").value = tr && tr.seed_hours ? tr.seed_hours : "";
  $("tracker-public").checked = tr ? tr.public : false;
  dlg.showModal();

  $("tracker-save").onclick = async () => {
    if (!$("tracker-name").value.trim()) { alert("Name is required"); return; }
    await api("/api/trackers/save", {
      method: "POST",
      body: JSON.stringify({
        id: $("tracker-id").value ? Number($("tracker-id").value) : null,
        name: $("tracker-name").value.trim(),
        url: $("tracker-url").value.trim(),
        abbr: $("tracker-abbr").value.trim(),
        max_slots: $("tracker-maxslots").value ? Number($("tracker-maxslots").value) : null,
        seed_hours: $("tracker-seedhours").value ? Number($("tracker-seedhours").value) : null,
        public: $("tracker-public").checked,
      }),
    });
    dlg.close();
    await Promise.all([pollState()]);
  };
}

function openAddDialog(preferredInstanceId) {
  const dlg = $("addDialog");
  const sel = $("add-instance");
  const instances = S.state?.instances || [];
  if (!instances.length) {
    alert("Add a qBittorrent instance first.");
    return;
  }
  $("add-urls").value = "";
  $("add-savepath").value = "";
  $("add-category").value = "";
  $("add-result").textContent = "";
  $("add-result").className = "test-result";
  $("add-tracker").innerHTML = trackerOptions();
  const preferred = preferredInstanceId
    ?? (S.activeTab !== "all" ? Number(S.activeTab) : instances[0].id);
  sel.innerHTML = instances
    .map((i) => `<option value="${i.id}" ${i.id === preferred ? "selected" : ""}>${esc(i.name)}</option>`)
    .join("");

  const fillCategories = () => {
    const inst = instances.find((i) => String(i.id) === String(sel.value));
    $("add-categories").innerHTML = (inst && inst.categories ? inst.categories : [])
      .map((c) => `<option value="${esc(c)}"></option>`)
      .join("");
  };
  fillCategories();
  sel.addEventListener("change", fillCategories);
  dlg.showModal();

  $("add-go").onclick = async () => {
    const urls = $("add-urls").value.trim();
    if (!urls) { alert("Paste a magnet link or .torrent URL."); return; }
    const res = $("add-result");
    res.className = "test-result";
    res.textContent = "Adding\u2026";
    const r = await api(`/api/instances/${Number(sel.value)}/add`, {
      method: "POST",
      body: JSON.stringify({
        urls: urls,
        save_path: $("add-savepath").value.trim(),
        category: $("add-category").value.trim(),
        tracker_id: $("add-tracker").value ? Number($("add-tracker").value) : null,
      }),
    });
    res.classList.toggle("ok", r.ok);
    res.classList.toggle("bad", !r.ok);
    if (r.ok) {
      res.textContent = `Added to ${sel.options[sel.selectedIndex].text}.`;
      setTimeout(() => { dlg.close(); pollTorrents(); }, 600);
    } else {
      res.textContent = `Failed: ${r.error || "unknown error"}`;
    }
  };
}

function openUploadDialog(preferredInstanceId) {
  const dlg = $("uploadDialog");
  const sel = $("upload-instance");
  const instances = S.state?.instances || [];
  if (!instances.length) {
    alert("Add a qBittorrent instance first.");
    return;
  }
  const file = $("upload-file");
  file.value = "";
  $("upload-savepath").value = "";
  $("upload-category").value = "";
  $("upload-result").textContent = "";
  $("upload-result").className = "test-result";
  $("upload-tracker").innerHTML = trackerOptions();
  const preferred = preferredInstanceId
    ?? (S.activeTab !== "all" ? Number(S.activeTab) : instances[0].id);
  sel.innerHTML = instances
    .map((i) => `<option value="${i.id}" ${i.id === preferred ? "selected" : ""}>${esc(i.name)}</option>`)
    .join("");

  const fillCategories = () => {
    const inst = instances.find((i) => String(i.id) === String(sel.value));
    $("upload-categories").innerHTML = (inst && inst.categories ? inst.categories : [])
      .map((c) => `<option value="${esc(c)}"></option>`)
      .join("");
  };
  fillCategories();
  sel.addEventListener("change", fillCategories);
  dlg.showModal();

  $("upload-go").onclick = async () => {
    if (!file.files || !file.files.length) { alert("Choose a .torrent file."); return; }
    const res = $("upload-result");
    res.className = "test-result";
    res.textContent = "Uploading\u2026";
    const fd = new FormData();
    for (const f of file.files) fd.append("files", f);
    fd.append("save_path", $("upload-savepath").value.trim());
    fd.append("category", $("upload-category").value.trim());
    fd.append("tracker_id", $("upload-tracker").value || "");
    let r;
    try {
      const resp = await fetch(`/api/instances/${Number(sel.value)}/add-file`, { method: "POST", body: fd });
      r = await resp.json();
    } catch (e) {
      r = { ok: false, error: String(e) };
    }
    res.classList.toggle("ok", r.ok);
    res.classList.toggle("bad", !r.ok);
    if (r.ok) {
      res.textContent = `Added to ${sel.options[sel.selectedIndex].text}.`;
      setTimeout(() => { dlg.close(); pollTorrents(); }, 600);
    } else {
      res.textContent = `Failed: ${r.error || "unknown error"}`;
    }
  };
}

function openMoveDialog(hash, fromInstance) {
  const dlg = $("moveDialog");
  const instances = S.state?.instances || [];
  const t = S.torrents.find((x) => x.hash === hash && x.instance_id === fromInstance);
  const others = instances.filter((i) => i.id !== fromInstance);
  if (!others.length) {
    alert("No other instances to move to.");
    return;
  }
  $("move-hash").value = hash;
  $("move-from").value = fromInstance;
  $("move-title").textContent = t ? t.name || t.hash : hash;
  const sel = $("move-to");
  sel.innerHTML = others
    .map((i) => `<option value="${i.id}">${esc(i.name)}</option>`)
    .join("");
  $("move-result").textContent = "";
  $("move-result").className = "test-result";
  dlg.showModal();

  $("move-go").onclick = async () => {
    const res = $("move-result");
    res.className = "test-result";
    res.textContent = "Moving\u2026";
    const r = await api("/api/torrents/move", {
      method: "POST",
      body: JSON.stringify({
        from_instance: fromInstance,
        to_instance: Number(sel.value),
        hashes: [hash],
      }),
    });
    res.classList.toggle("ok", r.ok);
    res.classList.toggle("bad", !r.ok);
    if (r.ok) {
      const moved = (r.moved || []).length;
      res.textContent = moved
        ? `Moved to ${sel.options[sel.selectedIndex].text}.`
        : `Not moved: ${(r.failed || []).map((f) => f.error).join("; ") || "unknown"}`;
      if (moved) setTimeout(() => { dlg.close(); Promise.all([pollState(), pollTorrents()]); }, 600);
    } else {
      res.textContent = `Failed: ${r.error || "unknown error"}`;
    }
  };
}

/* ----------------------------------------------------------------- init */
document.querySelectorAll(".dialog").forEach((d) => {
  d.addEventListener("click", (e) => {
    if (e.target.hasAttribute("data-close") || e.target === d) d.close();
  });
});

$("btn-settings").addEventListener("click", () => {
  const dlg = $("settingsDialog");
  const set = S.state?.settings;
  if (set) {
    $("set-poll").value = set.poll_interval_seconds;
    $("set-rss").value = set.rss_scan_interval_minutes;
    $("set-data").value = set.data_folder || "";
  }
  dlg.showModal();
});
$("set-save").addEventListener("click", async () => {
  await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      poll_interval_seconds: Math.max(5, Number($("set-poll").value) || 30),
      rss_scan_interval_minutes: Math.max(1, Number($("set-rss").value) || 15),
      data_folder: $("set-data").value.trim() || "/data/torrents",
    }),
  });
  $("settingsDialog").close();
  await pollState();
});

$("btn-refresh").addEventListener("click", async () => {
  await Promise.all([pollState(), pollTorrents(), S.activeTab === "rss" ? pollRss() : null]);
});

startPolling();
renderTabs();
renderTorrents();
