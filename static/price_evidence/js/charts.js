(() => {
    "use strict";

    const root = document.querySelector("[data-price-evidence-chart]");
    const dataNode = document.getElementById("price-evidence-chart-data");
    if (!root || !dataNode) return;

    const rows = JSON.parse(dataNode.textContent).map((row) => ({
        time: new Date(row.open_time),
        open: Number(row.open),
        high: Number(row.high),
        low: Number(row.low),
        close: Number(row.close),
        volume: Number(row.volume),
    }));
    const finiteRows = rows.filter((row) =>
        [row.open, row.high, row.low, row.close, row.volume].every(Number.isFinite)
    );
    if (!finiteRows.length) return;

    const colors = {
        grid: "#e3e7ee",
        muted: "#667085",
        ink: "#172032",
        up: "#c23b3b",
        down: "#287a5b",
        flat: "#7a8494",
        volume: "rgba(49, 91, 206, 0.62)",
    };

    function setupCanvas(canvas, cssHeight) {
        const ratio = window.devicePixelRatio || 1;
        const width = Math.max(canvas.getBoundingClientRect().width, 320);
        canvas.style.height = `${cssHeight}px`;
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(cssHeight * ratio);
        const context = canvas.getContext("2d");
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        context.clearRect(0, 0, width, cssHeight);
        return { context, width, height: cssHeight };
    }

    function hourX(row, left, plotWidth) {
        const hour = row.time.getUTCHours() + row.time.getUTCMinutes() / 60;
        return left + ((hour + 0.5) / 24) * plotWidth;
    }

    function drawXAxis(context, left, top, plotWidth, plotHeight) {
        context.strokeStyle = colors.grid;
        context.fillStyle = colors.muted;
        context.font = "11px system-ui, sans-serif";
        context.textAlign = "center";
        for (let hour = 0; hour < 24; hour += 4) {
            const x = left + ((hour + 0.5) / 24) * plotWidth;
            context.beginPath();
            context.moveTo(x, top);
            context.lineTo(x, top + plotHeight);
            context.stroke();
            context.fillText(`${String(hour).padStart(2, "0")}:00`, x, top + plotHeight + 18);
        }
        context.textAlign = "right";
        context.fillText("UTC", left + plotWidth, top + plotHeight + 18);
    }

    function drawCandles() {
        const canvas = root.querySelector('[data-chart="candles"]');
        const { context, width, height } = setupCanvas(canvas, 320);
        const bounds = { left: 62, right: 18, top: 18, bottom: 30 };
        const plotWidth = width - bounds.left - bounds.right;
        const plotHeight = height - bounds.top - bounds.bottom;
        const minimum = Math.min(...finiteRows.map((row) => row.low));
        const maximum = Math.max(...finiteRows.map((row) => row.high));
        const span = maximum === minimum ? Math.max(Math.abs(maximum) * 0.01, 1) : maximum - minimum;
        const paddedMinimum = minimum - span * 0.04;
        const paddedMaximum = maximum + span * 0.04;
        const paddedSpan = paddedMaximum - paddedMinimum;
        const y = (value) => bounds.top + ((paddedMaximum - value) / paddedSpan) * plotHeight;

        context.font = "11px system-ui, sans-serif";
        context.textAlign = "right";
        context.textBaseline = "middle";
        for (let tick = 0; tick <= 4; tick += 1) {
            const value = paddedMaximum - (paddedSpan * tick) / 4;
            const yPosition = bounds.top + (plotHeight * tick) / 4;
            context.strokeStyle = colors.grid;
            context.beginPath();
            context.moveTo(bounds.left, yPosition);
            context.lineTo(bounds.left + plotWidth, yPosition);
            context.stroke();
            context.fillStyle = colors.muted;
            context.fillText(value.toLocaleString(undefined, { maximumFractionDigits: 4 }), bounds.left - 8, yPosition);
        }
        drawXAxis(context, bounds.left, bounds.top, plotWidth, plotHeight);

        const candleWidth = Math.max(3, Math.min(13, (plotWidth / 24) * 0.56));
        finiteRows.forEach((row) => {
            const x = hourX(row, bounds.left, plotWidth);
            const color = row.close > row.open ? colors.up : row.close < row.open ? colors.down : colors.flat;
            context.strokeStyle = color;
            context.fillStyle = color;
            context.lineWidth = 1.25;
            context.beginPath();
            context.moveTo(x, y(row.high));
            context.lineTo(x, y(row.low));
            context.stroke();
            const bodyTop = Math.min(y(row.open), y(row.close));
            const bodyHeight = Math.max(1.5, Math.abs(y(row.close) - y(row.open)));
            context.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
        });
    }

    function drawVolume() {
        const canvas = root.querySelector('[data-chart="volume"]');
        const { context, width, height } = setupCanvas(canvas, 150);
        const bounds = { left: 62, right: 18, top: 12, bottom: 30 };
        const plotWidth = width - bounds.left - bounds.right;
        const plotHeight = height - bounds.top - bounds.bottom;
        const maximum = Math.max(...finiteRows.map((row) => row.volume), 0);
        const scaleMaximum = maximum > 0 ? maximum : 1;
        drawXAxis(context, bounds.left, bounds.top, plotWidth, plotHeight);
        context.fillStyle = colors.volume;
        const barWidth = Math.max(3, Math.min(15, (plotWidth / 24) * 0.62));
        finiteRows.forEach((row) => {
            const x = hourX(row, bounds.left, plotWidth);
            const barHeight = Math.max(0, (row.volume / scaleMaximum) * plotHeight);
            context.fillRect(x - barWidth / 2, bounds.top + plotHeight - barHeight, barWidth, barHeight);
        });
        context.fillStyle = colors.muted;
        context.font = "11px system-ui, sans-serif";
        context.textAlign = "right";
        context.fillText(maximum.toLocaleString(undefined, { maximumFractionDigits: 2 }), bounds.left - 8, bounds.top + 8);
    }

    function drawAll() {
        drawCandles();
        drawVolume();
    }

    drawAll();
    if ("ResizeObserver" in window) {
        new ResizeObserver(drawAll).observe(root);
    } else {
        window.addEventListener("resize", drawAll);
    }
})();
