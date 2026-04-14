Promise.all([fetch("/static/fps_vs_time.json").then(r => r.json()),
            fetch("/static/q_vs_time.json").then(r => r.json())
]).then(([data1, data2]) => {
  const formatted1 = data1.map(d => ({ x: d.time, y: d.fps }));
  const formatted2 = data2.map(d => ({ x: d.time, y: d.q }));

  const ctx = document.getElementById("fpsChart");
  const ctx2 = document.getElementById("qualityChart");

  new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        {
          label: "FPS",
          data: formatted1,          // [{x:..., y:...}, ...]
          borderColor: "blue",
          fill: false,
          parsing: false
        }
      ]
    },
    options: {
      responsive: true,
      parsing: false,          // Important if using {x, y} objects
      scales: {
        x: { 
          type: "linear",
          title: {
            display: true,
            text: "Time (frames)"
          }
        }
      }
    },
    plugins: [{
      id: 'customCanvasBackgroundColor',
      beforeDraw(chart) {
        const {ctx} = chart;
        ctx.save();
        ctx.globalCompositeOperation = 'destination-over';
        ctx.fillStyle = 'white';
        ctx.fillRect(0, 0, chart.width, chart.height);
        ctx.restore();
      }
    }]
  });

  new Chart(ctx2, {
    type: "line",
    data: {
      datasets: [
        {
          label: "Quality (q)",
          data: formatted2,          // [{x:..., y:...}, ...]
          borderColor: "red",
          fill: false,
          parsing: false
        }
      ]
    },
    options: {
      responsive: true,
      parsing: false,          // Important if using {x, y} objects
      scales: {
        x: { 
          type: "linear",
          title: {
            display: true,
            text: "Time (frames)"
          }
        }
      }
    },
    plugins: [{
      id: 'customCanvasBackgroundColor',
      beforeDraw(chart) {
        const {ctx} = chart;
        ctx.save();
        ctx.globalCompositeOperation = 'destination-over';
        ctx.fillStyle = 'white';
        ctx.fillRect(0, 0, chart.width, chart.height);
        ctx.restore();
      }
    }]
  });
});