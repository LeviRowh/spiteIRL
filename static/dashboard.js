const $ = id => document.getElementById(id);
const LOW_LIVE = 2000, LOW_IDLE = 300;
let hls = null, attached = false, attaching = false, starting = false, lastDests = "";

const api = async (path, method = "POST", body) => {
  const r = await fetch("/api/" + path, {method, headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined});
  if (r.status === 401) location.href = "/";
  if (!r.ok) { const e = await r.json().catch(() => ({})); alert(e.detail || "Request failed"); }
  return r.ok;
};
const badge = (el, text, cls) => { el.textContent = text; el.className = "badge " + cls; };
const fmt = s => [s / 3600 | 0, s / 60 % 60 | 0, s % 60].map(n => String(n).padStart(2, "0")).join(":");

function draw(h, low) {
  const c = $("chart"), g = c.getContext("2d"), W = c.width, H = c.height;
  g.clearRect(0, 0, W, H);
  const max = Math.max(1000, low * 1.5, ...h) * 1.1, y = v => H - 20 - (v / max) * (H - 30);
  g.strokeStyle = "#555"; g.fillStyle = "#adb5bd"; g.font = "12px sans-serif";
  for (let i = 0; i <= 4; i++) { const v = max / 4 * i, yy = y(v); g.beginPath(); g.moveTo(50, yy); g.lineTo(W, yy); g.stroke(); g.fillText(Math.round(v), 4, yy + 4); }
  g.strokeStyle = "#ffc107"; g.beginPath(); g.moveTo(50, y(low)); g.lineTo(W, y(low)); g.stroke();
  g.fillText("Low bitrate", W - 80, y(low) - 4);
  if (h.length < 2) return;
  g.strokeStyle = "#7ddc3b"; g.lineWidth = 2; g.beginPath();
  h.forEach((v, i) => { const x = 50 + (W - 50) * i / 59; i ? g.lineTo(x, y(v)) : g.moveTo(x, y(v)); });
  g.stroke(); g.lineWidth = 1;
}

async function attach() {
  if (attaching) return; attaching = true;
  for (let i = 0; i < 30; i++) {
    const r = await fetch("/hls/stream.m3u8?v=" + Date.now(), {cache: "no-store"}).catch(() => null);
    if (r && r.ok) break;
    await new Promise(s => setTimeout(s, 1000));
  }
  const v = $("v"), src = "/hls/stream.m3u8";
  if (hls) { hls.destroy(); hls = null; }
  if (v.canPlayType("application/vnd.apple.mpegurl")) v.src = src;
  else if (window.Hls && Hls.isSupported()) {
    hls = new Hls({liveSyncDurationCount: 2});
    hls.loadSource(src); hls.attachMedia(v);
    hls.on(Hls.Events.ERROR, (e, d) => { if (d.fatal) attached = false; });
  }
  v.play().catch(() => {});
  attached = true; attaching = false;
}

function renderDests(list, active) {
  const key = JSON.stringify([list, active && active.id]);
  if (key === lastDests) return; lastDests = key;
  $("destList").innerHTML = list.length ? "" : "<p>No destinations yet. Add one below.</p>";
  list.forEach(d => {
    const row = document.createElement("div"); row.className = "dest";
    const isActive = active && active.id === d.id;
    row.innerHTML = `<span><b></b> (${d.platform})</span>`;
    row.querySelector("b").textContent = d.label;
    const use = document.createElement("button"); use.className = "btn " + (isActive ? "ok" : "blue");
    use.textContent = isActive ? "Active" : "Use"; use.disabled = isActive;
    use.onclick = async () => { await api(`destinations/${d.id}/select`); tick(); };
    const del = document.createElement("button"); del.className = "btn bad"; del.textContent = "Delete";
    del.onclick = async () => { if (confirm("Delete " + d.label + "?")) { await api("destinations/" + d.id, "DELETE"); tick(); } };
    row.append(use, del); $("destList").append(row);
  });
}

async function tick() {
  let d;
  try { const r = await fetch("/api/dashboard", {cache: "no-store"}); if (r.status === 401) return location.href = "/"; d = await r.json(); }
  catch (e) { return; }
  $("banner").hidden = !!d.active;
  $("destname").textContent = d.active ? d.active.label : "No destination set";
  const online = d.running && d.bitrate > 0, low = d.live ? LOW_LIVE : LOW_IDLE;
  const state = !online ? "bad" : d.bitrate < low ? "warn" : "ok";
  const kbps = Math.round(d.bitrate).toLocaleString();
  badge($("streamBadge"), d.live ? "Live" : "Offline", d.live ? "ok" : "bad");
  badge($("ingBadge"), online ? kbps + " Kbps" : "Offline", state);
  badge($("barStream"), d.live ? "Live " + fmt(d.live_seconds) : "Stream Offline", d.live ? "ok" : "bad");
  badge($("barIngest"), online ? "Bitrate " + kbps : "Main Ingest Offline", state);
  draw(d.history, low); renderDests(d.destinations, d.active);
  if (d.running && !attached) attach();
  if (!d.running && !starting) { starting = true; attached = false; api("start").finally(() => starting = false); }
}

$("start").onclick = async () => { await api("stream/start"); tick(); };
$("stop").onclick = async () => { await api("stream/stop"); tick(); };
["mute", "unmute", "fix"].forEach(a => $(a).onclick = async () => { await api("ingest/" + a); tick(); });
$("shutdown").onclick = () => confirm("Shut down the Pi? You will need to power it back on by hand.") && api("system/shutdown");
$("reboot").onclick = () => confirm("Reboot the Pi? The stream will drop.") && api("system/reboot");
$("add").onclick = async () => {
  const label = $("label").value.trim(), stream_key = $("key").value.trim();
  if (!label || !stream_key) return alert("Enter a label and a stream key.");
  if (await api("destinations", "POST", {platform: $("platform").value, label, stream_key})) { $("label").value = $("key").value = ""; tick(); }
};
$("burger").onclick = () => { const o = $("menu").classList.toggle("open"); $("burger").setAttribute("aria-expanded", o); };
document.querySelectorAll(".card>h3").forEach(h => h.onclick = () => h.parentElement.classList.toggle("closed"));
const route = () => $("destCard").classList.toggle("show", location.hash === "#destinations");
addEventListener("hashchange", route); route();
tick(); setInterval(tick, 1000);
