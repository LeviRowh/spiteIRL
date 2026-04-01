const video = document.getElementById("v");
      const statusEl = document.getElementById("status");

      const startBtn = document.getElementById("start");
      const stopBtn = document.getElementById("stop");
      const reloadBtn = document.getElementById("reload");
      const unmuteBtn = document.getElementById("unmute");

    const addDestinationBtn = document.getElementById("addDestination");
    const platformEl = document.getElementById("platform");
    const destLabelEl = document.getElementById("destLabel");
    const streamKeyEl = document.getElementById("streamKey");
    const destStatusEl = document.getElementById("destStatus");
    const destinationListEl = document.getElementById("destinationList");

    function safeOnClick(element, handler) {
      if (element) {
        element.addEventListener("click", handler);
      }
    }

    async function loadDestinations() {
      try {
        const res = await fetch("/api/destinations", { cache: "no-store" });
        const destinations = await res.json();

        destinationListEl.innerHTML = "";

        if (!destinations.length) {
          destinationListEl.innerHTML = "<p>No destinations added yet.</p>";
          return;
        }

        destinations.forEach(dest => {
          const div = document.createElement("div");
          div.innerHTML = `
            <strong>${dest.label}</strong> (${dest.platform})
            - enabled: ${dest.enabled}
            - running: ${dest.running}
            <button onclick="toggleDestination('${dest.id}', ${!dest.enabled})">
              ${dest.enabled ? "Disable" : "Enable"}
            </button>
            <button onclick="deleteDestination('${dest.id}')">Delete</button>
          `;
          destinationListEl.appendChild(div);
        });
      } catch (err) {
        console.error(err);
        destStatusEl.textContent = "Error loading destinations.";
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
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            platform,
            label,
            stream_key: streamKey
          })
        });

        const data = await res.json();

        if (!res.ok) {
          destStatusEl.textContent = data.detail || "Failed to add destination.";
          return;
        }

        destStatusEl.textContent = `Added destination: ${data.label}`;
        destLabelEl.value = "";
        streamKeyEl.value = "";
        loadDestinations();
      } catch (err) {
        console.error(err);
        destStatusEl.textContent = "Error adding destination.";
      }
    }

    async function toggleDestination(destId, enabled) {
      try {
        const res = await fetch(`/api/destinations/${destId}`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({ enabled })
        });

        const data = await res.json();

        if (!res.ok) {
          destStatusEl.textContent = data.detail || "Failed to update destination.";
          return;
        }

        destStatusEl.textContent = `Updated ${data.label}`;
        loadDestinations();
      } catch (err) {
        console.error(err);
        destStatusEl.textContent = "Error updating destination.";
      }
    }

    async function deleteDestination(destId) {
      try {
        const res = await fetch(`/api/destinations/${destId}`, {
          method: "DELETE"
        });

        const data = await res.json();

        if (!res.ok) {
          destStatusEl.textContent = data.detail || "Failed to delete destination.";
          return;
        }

        destStatusEl.textContent = "Destination deleted.";
        loadDestinations();
      } catch (err) {
        console.error(err);
        destStatusEl.textContent = "Error deleting destination.";
      }
    }

    if (addDestinationBtn) {
      addDestinationBtn.addEventListener("click", addDestination);
    }

    window.toggleDestination = toggleDestination;
    window.deleteDestination = deleteDestination;
    loadDestinations();

      // Cache-bust so we don't get a stale playlist after restarting FFmpeg
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

        // Native HLS (Safari)
        if (video.canPlayType("application/vnd.apple.mpegurl")) {
          destroyHls();
          video.src = src;
          safePlay();
          return;
        }

        // HLS.js (Chrome/Firefox/Edge)
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

      async function waitForPlaylist(timeoutMs = 12000) {
        const start = Date.now();
        while (Date.now() - start < timeoutMs) {
          try {
            const res = await fetch(streamUrl(), { cache: "no-store" });
            if (res.ok) return true;
          } catch (_) {}
          await new Promise(r => setTimeout(r, 250));
        }
        return false;
      }

      async function startAndAutoLoad() {
        startBtn.disabled = true;
        setStatus(false, "starting...");

        try {
          const res = await fetch("/api/start", { method: "POST" });
          const data = await res.json();

          if (!data.running) {
            setStatus(false, "failed to start");
            return;
          }

          setStatus(true, "waiting for stream...");

          // Wait a few seconds for FFmpeg to generate the first HLS segments
          await new Promise(resolve => setTimeout(resolve, 10000));

          attachStream();
          setStatus(true, "playing");
          loadDestinations();

        } catch (e) {
          console.error(e);
          setStatus(false, "error starting stream");
        } finally {
          startBtn.disabled = false;
        }
      }

      async function stopStream() {
        stopBtn.disabled = true;

        try {
          const res = await fetch("/api/stop", { method: "POST" });
          const data = await res.json();

          destroyHls();

          // Clear video so it doesn't freeze on the last frame
          video.pause();
          video.removeAttribute("src");
          video.load();

          setStatus(!!data.running, data.message || "");
          loadDestinations();
          
        } catch (e) {
          console.error(e);
          setStatus(false, "error stopping stream");
        } finally {
          stopBtn.disabled = false;
        }
      }

      // Button wiring
      safeOnClick(startBtn, startAndAutoLoad);

      safeOnClick(stopBtn, stopStream);

      safeOnClick(reloadBtn, () => {
        attachStream();
        setStatus(true, "reloaded");
      });

      safeOnClick(unmuteBtn, () => {
        video.muted = false;
        safePlay();
      });

      // On page load: show status, and if already running, attach automatically
      (async () => {
        try {
          const running = await apiStatus();
          if (running) {
            setStatus(true, "loading...");
            const ok = await waitForPlaylist(8000);
            if (ok) {
              attachStream();
              setStatus(true, "playing");
            } else {
              setStatus(true, "running, but playlist not found yet");
            }
          } else {
            setStatus(false);
          }
        } catch (e) {
          console.error(e);
          statusEl.textContent = "Status: error contacting backend";
        }
      })();