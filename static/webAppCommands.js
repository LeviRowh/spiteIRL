const video = document.getElementById("v");
      const statusEl = document.getElementById("status");

      const startBtn = document.getElementById("start");
      const stopBtn = document.getElementById("stop");
      const reloadBtn = document.getElementById("reload");
      const unmuteBtn = document.getElementById("unmute");

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
        } catch (e) {
          console.error(e);
          setStatus(false, "error stopping stream");
        } finally {
          stopBtn.disabled = false;
        }
      }

      // Button wiring
      startBtn.addEventListener("click", startAndAutoLoad);

      reloadBtn.addEventListener("click", () => {
        attachStream();
        setStatus(true, "reloaded");
      });

      stopBtn.addEventListener("click", stopStream);

      unmuteBtn.addEventListener("click", () => {
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