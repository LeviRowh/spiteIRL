const video = document.getElementById("v");
const statusEl = document.getElementById("status");
const addDestinationBtn = document.getElementById("addDestination");
const platformEl = document.getElementById("platform");
const destLabelEl = document.getElementById("destLabel");
const streamKeyEl = document.getElementById("streamKey");
const destStatusEl = document.getElementById("destStatus");
const destinationListEl = document.getElementById("destinationList");

const streamUrl = () => `/hls/stream.m3u8?v=${Date.now()}`;

async function safePlay() {
    try { await video.play(); } catch (_) {}
}

function setStatus(running, extra = "") {
    statusEl.textContent = `Status: ${running ? "RUNNING" : "STOPPED"}${extra ? " — " + extra : ""}`;
}

async function apiStatus() {
    const res = await fetch("/api/status", { cache: "no-store" });
    const data = await res.json();
    return !!data.running;
}

function destroyHls() {
    if (window._hls) {
        try { window._hls.destroy(); } catch (_) {}
        window._hls = null;
    }
}

function attachStream() {
    const src = streamUrl();
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
        destroyHls();
        video.src = src;
        safePlay();
        return;
    }
    if (window.Hls && Hls.isSupported()) {
        destroyHls();
        const hls = new Hls({});
        window._hls = hls;
        hls.loadSource(src);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, safePlay);
        hls.on(Hls.Events.ERROR, (e, data) => {
            console.error("HLS error:", data);
        });
        return;
    }
    alert("This browser can't play HLS.");
}

async function waitForPlaylist(timeoutMs = 15000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        try {
            const res = await fetch(streamUrl(), { cache: "no-store" });
            if (res.ok) return true;
        } catch (_) {}
        await new Promise(r => setTimeout(r, 500));
    }
    return false;
}

async function autoStartPreview() {
    // Start FFmpeg preview if not already running
    const running = await apiStatus();
    if (!running) {
        setStatus(false, "starting preview...");
        await fetch("/api/start", { method: "POST" });
    }
    setStatus(true, "waiting for stream...");
    const ok = await waitForPlaylist(15000);
    if (ok) {
        attachStream();
        setStatus(true, "playing");
    } else {
        setStatus(false, "could not load stream");
    }
    loadDestinations();
}

// Destinations
async function loadDestinations() {
    try {
        const res = await fetch("/api/destinations", { cache: "no-store" });
        const destinations = await res.json();
        destinationListEl.innerHTML = "";
        if (!destinations.length) {
            destinationListEl.innerHTML = "<p>No destinations added yet.</p>";
            return;
        }
        const anyRunning = destinations.some(d => d.running);

destinations.forEach(dest => {
    const div = document.createElement("div");
    div.className = "dest-row";
    const badge = dest.running
        ? `<span class="badge online">ONLINE</span>`
        : `<span class="badge offline">OFFLINE</span>`;
    const actionBtn = dest.running
        ? `<button onclick="stopDest('${dest.id}')">Stop</button>`
        : `<button onclick="startDest('${dest.id}')" ${anyRunning ? 'disabled title="Stop current stream first"' : ''}>Go Live</button>`;
    div.innerHTML = `
        <strong>${dest.label}</strong>
        <span class="platform-tag">${dest.platform}</span>
        ${badge}
        ${actionBtn}
        <button class="delete-btn" onclick="deleteDest('${dest.id}')">Delete</button>
    `;
    destinationListEl.appendChild(div);
});
    } catch (err) {
        console.error(err);
        destStatusEl.textContent = "Error loading destinations.";
    }
}

async function startDest(destId) {
    try {
        const res = await fetch(`/api/destinations/${destId}/start`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
            destStatusEl.textContent = data.detail || "Failed to start stream.";
            return;
        }
        destStatusEl.textContent = "Stream started!";
        setTimeout(loadDestinations, 2000);
    } catch (err) {
        console.error(err);
        destStatusEl.textContent = "Error starting stream.";
    }
}

async function stopDest(destId) {
    try {
        const res = await fetch(`/api/destinations/${destId}/stop`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
            destStatusEl.textContent = data.detail || "Failed to stop stream.";
            return;
        }
        destStatusEl.textContent = "Stream stopped.";
        loadDestinations();
    } catch (err) {
        console.error(err);
        destStatusEl.textContent = "Error stopping stream.";
    }
}

async function addDestination() {
    const platform = platformEl.value;
    const label = destLabelEl.value.trim();
    const streamKey = streamKeyEl.value.trim();
    if (!label || !streamKey) {
        destStatusEl.textContent = "Please enter a label and stream key.";
        return;
    }
    try {
        const res = await fetch("/api/destinations", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ platform, label, stream_key: streamKey })
        });
        const data = await res.json();
        if (!res.ok) {
            destStatusEl.textContent = data.detail || "Failed to add destination.";
            return;
        }
        destStatusEl.textContent = `Added: ${data.label}`;
        destLabelEl.value = "";
        streamKeyEl.value = "";
        loadDestinations();
    } catch (err) {
        console.error(err);
        destStatusEl.textContent = "Error adding destination.";
    }
}

async function deleteDest(destId) {
    try {
        await fetch(`/api/destinations/${destId}`, { method: "DELETE" });
        loadDestinations();
    } catch (err) {
        console.error(err);
    }
}

window.startDest = startDest;
window.stopDest = stopDest;
window.deleteDest = deleteDest;

if (addDestinationBtn) {
    addDestinationBtn.addEventListener("click", addDestination);
}

// Auto-start preview on page load
autoStartPreview();
