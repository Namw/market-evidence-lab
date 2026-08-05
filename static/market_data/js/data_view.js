(() => {
    "use strict";

    const root = document.querySelector("[data-market-data-view]");
    if (!root) return;

    const readJson = (id) => {
        const node = document.getElementById(id);
        return node ? JSON.parse(node.textContent) : [];
    };
    const toKline = (row) => ({
        time: new Date(row.open_time),
        open: Number(row.open),
        high: Number(row.high),
        low: Number(row.low),
        close: Number(row.close),
        volume: Number(row.volume),
    });
    const dailyRows = readJson("market-data-daily").map(toKline);
    const hourlyRows = readJson("market-data-hourly").map(toKline);
    const oiRows = readJson("market-data-oi").map((row) => ({
        time: new Date(row.timestamp),
        value: Number(row.value),
    }));
    const fundingRows = readJson("market-data-funding").map((row) => ({
        time: new Date(row.timestamp),
        value: Number(row.value) * 100,
    }));

    const rangeStart = new Date(root.dataset.rangeStart);
    const rangeEnd = new Date(root.dataset.rangeEnd);
    const selectedStart = new Date(root.dataset.selectedStart);
    const selectedEnd = new Date(root.dataset.selectedEnd);
    const selectedDate = root.dataset.selectedStart.slice(0, 10);

    const colors = {
        grid: "#e3e7ee",
        axis: "#667085",
        ink: "#172032",
        up: "#c23b3b",
        down: "#287a5b",
        flat: "#7a8494",
        blue: "#315bce",
        blueBar: "rgba(49, 91, 206, 0.82)",
        focus: "rgba(49, 91, 206, 0.065)",
        focusEdge: "rgba(49, 91, 206, 0.38)",
        funding: "#e68a19",
        white: "#ffffff",
    };
    const font = '11px Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif';
    const selectedFont = '600 11px Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif';
    let dailyGeometry = null;

    function availableCanvasHeight(canvas, fallback) {
        const wrapHeight = canvas.parentElement?.getBoundingClientRect().height || 0;
        return Math.max(90, Math.round(wrapHeight || fallback));
    }

    function setupCanvas(canvas, cssHeight) {
        const ratio = window.devicePixelRatio || 1;
        const width = Math.max(canvas.getBoundingClientRect().width, 320);
        canvas.style.height = `${cssHeight}px`;
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(cssHeight * ratio);
        const context = canvas.getContext("2d");
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        context.clearRect(0, 0, width, cssHeight);
        context.lineCap = "round";
        context.lineJoin = "round";
        return { context, width, height: cssHeight };
    }

    function finiteKlines(rows) {
        return rows.filter((row) =>
            Number.isFinite(row.time.getTime()) &&
            [row.open, row.high, row.low, row.close].every(Number.isFinite)
        );
    }

    function formatPrice(value) {
        return value.toLocaleString(undefined, {
            minimumFractionDigits: value >= 100 ? 0 : 2,
            maximumFractionDigits: value >= 100 ? 0 : 4,
        });
    }

    function formatCompact(value) {
        const absolute = Math.abs(value);
        if (absolute >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
        if (absolute >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
        if (absolute >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
        return value.toFixed(0);
    }

    function dayLabel(value) {
        return new Intl.DateTimeFormat("zh-CN", {
            timeZone: "UTC",
            month: "2-digit",
            day: "2-digit",
        }).format(value);
    }

    function hourLabel(value) {
        return `${String(value.getUTCHours()).padStart(2, "0")}:00`;
    }

    function drawEmpty(context, width, height, message) {
        context.fillStyle = colors.axis;
        context.font = font;
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(message, width / 2, height / 2);
    }

    function priceScale(rows, top, height) {
        const minimum = Math.min(...rows.map((row) => row.low));
        const maximum = Math.max(...rows.map((row) => row.high));
        const span = maximum === minimum ? Math.max(Math.abs(maximum) * 0.01, 1) : maximum - minimum;
        const low = minimum - span * 0.08;
        const high = maximum + span * 0.08;
        return {
            low,
            high,
            y: (value) => top + ((high - value) / (high - low)) * height,
        };
    }

    function drawPriceGrid(context, bounds, scale, side = "right") {
        context.font = font;
        context.textBaseline = "middle";
        context.textAlign = side === "right" ? "left" : "right";
        for (let index = 0; index <= 4; index += 1) {
            const y = bounds.top + (bounds.height * index) / 4;
            const value = scale.high - ((scale.high - scale.low) * index) / 4;
            context.strokeStyle = colors.grid;
            context.lineWidth = 1;
            context.beginPath();
            context.moveTo(bounds.left, y);
            context.lineTo(bounds.left + bounds.width, y);
            context.stroke();
            context.fillStyle = colors.axis;
            const x = side === "right" ? bounds.left + bounds.width + 10 : bounds.left - 10;
            context.fillText(formatPrice(value), x, y);
        }
    }

    function drawCandle(context, row, x, width, y) {
        const color = row.close > row.open ? colors.up : row.close < row.open ? colors.down : colors.flat;
        context.strokeStyle = color;
        context.fillStyle = color;
        context.lineWidth = 1.15;
        context.beginPath();
        context.moveTo(x, y(row.high));
        context.lineTo(x, y(row.low));
        context.stroke();
        const bodyTop = Math.min(y(row.open), y(row.close));
        const bodyHeight = Math.max(1.5, Math.abs(y(row.open) - y(row.close)));
        context.fillRect(x - width / 2, bodyTop, width, bodyHeight);
    }

    function drawDaily() {
        const canvas = root.querySelector('[data-chart="daily"]');
        if (!canvas) return;
        const { context, width, height } = setupCanvas(
            canvas,
            availableCanvasHeight(canvas, 170)
        );
        const rows = finiteKlines(dailyRows);
        if (!rows.length) {
            drawEmpty(context, width, height, "暂无日 K 数据");
            return;
        }
        const bounds = { left: 18, right: 68, top: 14, bottom: 29 };
        bounds.width = width - bounds.left - bounds.right;
        bounds.height = height - bounds.top - bounds.bottom;
        const scale = priceScale(rows, bounds.top, bounds.height);
        drawPriceGrid(context, bounds, scale);

        const slot = bounds.width / rows.length;
        const candleWidth = Math.max(3, Math.min(10, slot * 0.58));
        let selectedIndex = -1;
        rows.forEach((row, index) => {
            const x = bounds.left + slot * (index + 0.5);
            if (row.time.toISOString().slice(0, 10) === selectedDate) selectedIndex = index;
            drawCandle(context, row, x, candleWidth, scale.y);
        });

        const tickEvery = Math.max(1, Math.ceil(rows.length / 8));
        context.font = font;
        context.textAlign = "center";
        context.textBaseline = "top";
        rows.forEach((row, index) => {
            if (index % tickEvery !== 0 && index !== rows.length - 1) return;
            const x = bounds.left + slot * (index + 0.5);
            context.fillStyle = colors.axis;
            context.fillText(dayLabel(row.time), x, bounds.top + bounds.height + 12);
        });

        if (selectedIndex >= 0) {
            const x = bounds.left + slot * (selectedIndex + 0.5);
            context.strokeStyle = colors.blue;
            context.lineWidth = 1.25;
            context.beginPath();
            context.moveTo(x, bounds.top);
            context.lineTo(x, bounds.top + bounds.height + 3);
            context.stroke();
            const label = dayLabel(rows[selectedIndex].time);
            context.font = selectedFont;
            const labelWidth = context.measureText(label).width + 12;
            context.fillStyle = colors.blue;
            context.fillRect(x - labelWidth / 2, bounds.top + bounds.height + 6, labelWidth, 22);
            context.fillStyle = colors.white;
            context.textBaseline = "middle";
            context.fillText(label, x, bounds.top + bounds.height + 17);
        }
        dailyGeometry = { left: bounds.left, width: bounds.width, slot, rows };
    }

    function timeX(value, left, width) {
        return left + ((value.getTime() - rangeStart.getTime()) / (rangeEnd.getTime() - rangeStart.getTime())) * width;
    }

    function drawFocusBand(context, bounds) {
        const startX = timeX(selectedStart, bounds.left, bounds.width);
        const endX = timeX(selectedEnd, bounds.left, bounds.width);
        context.fillStyle = colors.focus;
        context.fillRect(startX, bounds.top, endX - startX, bounds.height);
        context.strokeStyle = colors.focusEdge;
        context.setLineDash([3, 3]);
        [startX, endX].forEach((x) => {
            context.beginPath();
            context.moveTo(x, bounds.top);
            context.lineTo(x, bounds.top + bounds.height);
            context.stroke();
        });
        context.setLineDash([]);
        context.fillStyle = colors.axis;
        context.font = selectedFont;
        context.textAlign = "center";
        context.textBaseline = "bottom";
        context.fillText(`关注日 ${dayLabel(selectedStart)}`, (startX + endX) / 2, bounds.top - 5);
    }

    function drawTimeAxis(context, bounds) {
        const tick = new Date(rangeStart);
        tick.setUTCHours(Math.ceil(tick.getUTCHours() / 6) * 6, 0, 0, 0);
        context.font = font;
        context.textAlign = "center";
        context.textBaseline = "top";
        while (tick < rangeEnd) {
            const x = timeX(tick, bounds.left, bounds.width);
            const isDayBoundary = tick.getUTCHours() === 0;
            context.strokeStyle = isDayBoundary ? "#cfd6e1" : colors.grid;
            context.lineWidth = isDayBoundary ? 1.15 : 1;
            context.beginPath();
            context.moveTo(x, bounds.top);
            context.lineTo(x, bounds.top + bounds.height);
            context.stroke();
            context.fillStyle = colors.axis;
            context.fillText(hourLabel(tick), x, bounds.top + bounds.height + 10);
            tick.setUTCHours(tick.getUTCHours() + 6);
        }

        const day = new Date(rangeStart);
        day.setUTCHours(0, 0, 0, 0);
        context.font = selectedFont;
        context.textAlign = "left";
        context.textBaseline = "top";
        while (day < rangeEnd) {
            const x = timeX(day, bounds.left, bounds.width);
            context.fillStyle = colors.ink;
            context.fillText(dayLabel(day), x + 7, bounds.top + 7);
            day.setUTCDate(day.getUTCDate() + 1);
        }
    }

    function drawHourly() {
        const canvas = root.querySelector('[data-chart="hourly"]');
        if (!canvas) return;
        const { context, width, height } = setupCanvas(
            canvas,
            availableCanvasHeight(canvas, 125)
        );
        const rows = finiteKlines(hourlyRows);
        if (!rows.length || !Number.isFinite(rangeStart.getTime()) || !Number.isFinite(rangeEnd.getTime())) {
            drawEmpty(context, width, height, "当前范围暂无小时 K 数据");
            return;
        }
        const bounds = { left: 18, right: 68, top: 20, bottom: 29 };
        bounds.width = width - bounds.left - bounds.right;
        bounds.height = height - bounds.top - bounds.bottom;
        const scale = priceScale(rows, bounds.top, bounds.height);
        drawFocusBand(context, bounds);
        drawPriceGrid(context, bounds, scale);
        drawTimeAxis(context, bounds);

        const hours = Math.max(1, (rangeEnd - rangeStart) / 36e5);
        const candleWidth = Math.max(2.5, Math.min(9, (bounds.width / hours) * 0.55));
        rows.forEach((row) => {
            const candleTime = new Date(row.time.getTime() + 30 * 60 * 1000);
            drawCandle(context, row, timeX(candleTime, bounds.left, bounds.width), candleWidth, scale.y);
        });
    }

    function paddedDomain(values, includeZero = false) {
        if (!values.length) return { minimum: 0, maximum: 1 };
        let minimum = Math.min(...values);
        let maximum = Math.max(...values);
        if (includeZero) {
            minimum = Math.min(0, minimum);
            maximum = Math.max(0, maximum);
        }
        const span = maximum === minimum ? Math.max(Math.abs(maximum) * 0.1, 1) : maximum - minimum;
        return { minimum: minimum - span * 0.08, maximum: maximum + span * 0.08 };
    }

    function drawDerivativesAxes(context, bounds, oiDomain, fundingDomain) {
        context.font = font;
        context.textBaseline = "middle";
        for (let index = 0; index <= 4; index += 1) {
            const y = bounds.top + (bounds.height * index) / 4;
            const oiValue = oiDomain.maximum - ((oiDomain.maximum - oiDomain.minimum) * index) / 4;
            const fundingValue = fundingDomain.maximum - ((fundingDomain.maximum - fundingDomain.minimum) * index) / 4;
            context.strokeStyle = colors.grid;
            context.beginPath();
            context.moveTo(bounds.left, y);
            context.lineTo(bounds.left + bounds.width, y);
            context.stroke();
            context.fillStyle = colors.axis;
            context.textAlign = "right";
            context.fillText(formatCompact(oiValue), bounds.left - 10, y);
            context.textAlign = "left";
            context.fillText(`${fundingValue.toFixed(3)}%`, bounds.left + bounds.width + 10, y);
        }
        context.font = selectedFont;
        context.textBaseline = "bottom";
        context.fillStyle = colors.ink;
        context.textAlign = "left";
        context.fillText("OI (ETH)", bounds.left, bounds.top - 8);
        context.textAlign = "right";
        context.fillText("Funding (%)", bounds.left + bounds.width, bounds.top - 8);
    }

    function drawStraightLine(context, points) {
        if (!points.length) return;
        context.beginPath();
        context.moveTo(points[0].x, points[0].y);
        for (let index = 1; index < points.length; index += 1) {
            context.lineTo(points[index].x, points[index].y);
        }
        context.stroke();
    }

    function drawDerivatives() {
        const canvas = root.querySelector('[data-chart="derivatives"]');
        if (!canvas) return;
        const { context, width, height } = setupCanvas(
            canvas,
            availableCanvasHeight(canvas, 135)
        );
        const finiteOi = oiRows.filter((row) => Number.isFinite(row.time.getTime()) && Number.isFinite(row.value));
        const finiteFunding = fundingRows.filter((row) => Number.isFinite(row.time.getTime()) && Number.isFinite(row.value));
        if ((!finiteOi.length && !finiteFunding.length) || !Number.isFinite(rangeStart.getTime())) {
            drawEmpty(context, width, height, "当前范围暂无 OI / Funding 数据");
            return;
        }
        const bounds = { left: 76, right: 76, top: 25, bottom: 29 };
        bounds.width = width - bounds.left - bounds.right;
        bounds.height = height - bounds.top - bounds.bottom;
        const oiDomain = paddedDomain(finiteOi.map((row) => row.value));
        const fundingDomain = paddedDomain(finiteFunding.map((row) => row.value), true);
        const oiY = (value) => bounds.top + ((oiDomain.maximum - value) / (oiDomain.maximum - oiDomain.minimum)) * bounds.height;
        const fundingY = (value) => bounds.top + ((fundingDomain.maximum - value) / (fundingDomain.maximum - fundingDomain.minimum)) * bounds.height;

        drawFocusBand(context, bounds);
        drawDerivativesAxes(context, bounds, oiDomain, fundingDomain);
        drawTimeAxis(context, bounds);

        const hours = Math.max(1, (rangeEnd - rangeStart) / 36e5);
        const barWidth = Math.max(3, Math.min(12, (bounds.width / hours) * 0.62));
        context.fillStyle = colors.blueBar;
        finiteOi.forEach((row) => {
            const x = timeX(new Date(row.time.getTime() + 30 * 60 * 1000), bounds.left, bounds.width);
            const top = oiY(row.value);
            context.fillRect(x - barWidth / 2, top, barWidth, bounds.top + bounds.height - top);
        });

        const fundingPoints = finiteFunding.map((row) => ({
            x: timeX(row.time, bounds.left, bounds.width),
            y: fundingY(row.value),
        }));
        context.strokeStyle = colors.funding;
        context.lineWidth = 2;
        drawStraightLine(context, fundingPoints);
        fundingPoints.forEach((point) => {
            context.beginPath();
            context.fillStyle = colors.white;
            context.strokeStyle = colors.funding;
            context.lineWidth = 1.5;
            context.arc(point.x, point.y, 3, 0, Math.PI * 2);
            context.fill();
            context.stroke();
        });
    }

    function drawAll() {
        drawDaily();
        drawHourly();
        drawDerivatives();
    }

    const dailyCanvas = root.querySelector('[data-chart="daily"]');
    if (dailyCanvas) {
        dailyCanvas.addEventListener("click", (event) => {
            if (!dailyGeometry) return;
            const rect = dailyCanvas.getBoundingClientRect();
            const x = event.clientX - rect.left;
            if (x < dailyGeometry.left || x > dailyGeometry.left + dailyGeometry.width) return;
            const index = Math.min(
                dailyGeometry.rows.length - 1,
                Math.max(0, Math.floor((x - dailyGeometry.left) / dailyGeometry.slot))
            );
            const date = dailyGeometry.rows[index].time.toISOString().slice(0, 10);
            if (date !== selectedDate) {
                window.location.assign(`${root.dataset.selectUrl}?date=${encodeURIComponent(date)}`);
            }
        });
    }

    drawAll();
    if ("ResizeObserver" in window) {
        const observer = new ResizeObserver(drawAll);
        root.querySelectorAll(".data-canvas-wrap").forEach((element) => observer.observe(element));
    } else {
        window.addEventListener("resize", drawAll);
    }
})();
