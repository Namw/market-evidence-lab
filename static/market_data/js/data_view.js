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
    const fiveMinuteRows = readJson("market-data-five-minute").map(toKline);
    const oiRows = readJson("market-data-oi").map((row) => ({
        time: new Date(row.timestamp),
        value: Number(row.value),
    }));
    const fundingRows = readJson("market-data-funding").map((row) => ({
        time: new Date(row.timestamp),
        value: Number(row.value) * 100,
    }));
    const fiveMinuteOiRows = readJson("market-data-five-minute-oi").map((row) => ({
        time: new Date(row.timestamp),
        value: Number(row.value),
        valueUsdt: Number(row.value_usdt),
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
    let hourlyGeometry = null;
    let fiveMinuteGeometry = null;
    let selectedHourStart = null;
    let hoveredHourStart = null;
    let hoveredFiveMinuteStart = null;

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

    function percentage(open, close) {
        return open ? ((close - open) / open) * 100 : 0;
    }

    function amplitude(row) {
        return row.open ? ((row.high - row.low) / row.open) * 100 : 0;
    }

    function formatChange(value) {
        return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
    }

    function setDirectionalValue(element, value) {
        if (!element) return;
        element.textContent = formatChange(value);
        element.classList.toggle("is-up", value > 0);
        element.classList.toggle("is-down", value < 0);
    }

    const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000;
    const SIX_HOURS_MS = 6 * 60 * 60 * 1000;
    const DAY_MS = 24 * 60 * 60 * 1000;

    function exchangeDayLabel(value) {
        return new Intl.DateTimeFormat("zh-CN", {
            timeZone: "UTC",
            month: "2-digit",
            day: "2-digit",
        }).format(value);
    }

    function beijingValue(value) {
        return new Date(value.getTime() + BEIJING_OFFSET_MS);
    }

    function beijingDayLabel(value) {
        const local = beijingValue(value);
        return `${String(local.getUTCMonth() + 1).padStart(2, "0")}/${String(local.getUTCDate()).padStart(2, "0")}`;
    }

    function beijingTimeLabel(value) {
        const local = beijingValue(value);
        return `${String(local.getUTCHours()).padStart(2, "0")}:${String(local.getUTCMinutes()).padStart(2, "0")}`;
    }

    function beijingDateHourLabel(value) {
        const local = beijingValue(value);
        return `${local.getUTCFullYear()}-${String(local.getUTCMonth() + 1).padStart(2, "0")}-${String(local.getUTCDate()).padStart(2, "0")} ${String(local.getUTCHours()).padStart(2, "0")}`;
    }

    function beijingDateMinuteLabel(value) {
        return `${beijingDateHourLabel(value)}:${String(beijingValue(value).getUTCMinutes()).padStart(2, "0")}`;
    }

    function updateKlineReadout(selector, row, timeLabel) {
        const readout = root.querySelector(selector);
        if (!readout || !row) return;
        const values = {
            time: timeLabel(row.time),
            open: formatPrice(row.open),
            high: formatPrice(row.high),
            low: formatPrice(row.low),
            close: formatPrice(row.close),
            amplitude: `${amplitude(row).toFixed(2)}%`,
        };
        Object.entries(values).forEach(([key, value]) => {
            const target = readout.querySelector(`[data-kline-${key}]`);
            if (target) target.textContent = value;
        });
        setDirectionalValue(
            readout.querySelector("[data-kline-change]"),
            percentage(row.open, row.close)
        );
    }

    function selectedRangeSummary(rows) {
        if (!rows.length) return null;
        const first = rows[0];
        const last = rows[rows.length - 1];
        const high = Math.max(...rows.map((row) => row.high));
        const low = Math.min(...rows.map((row) => row.low));
        return {
            change: percentage(first.open, last.close),
            amplitude: first.open ? ((high - low) / first.open) * 100 : 0,
        };
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
            context.fillText(exchangeDayLabel(row.time), x, bounds.top + bounds.height + 12);
        });

        if (selectedIndex >= 0) {
            const x = bounds.left + slot * (selectedIndex + 0.5);
            context.strokeStyle = colors.blue;
            context.lineWidth = 1.25;
            context.beginPath();
            context.moveTo(x, bounds.top);
            context.lineTo(x, bounds.top + bounds.height + 3);
            context.stroke();
            const label = exchangeDayLabel(rows[selectedIndex].time);
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
        context.fillText(`UTC 日 K ${exchangeDayLabel(selectedStart)}`, (startX + endX) / 2, bounds.top - 5);
    }

    function drawTimeAxis(context, bounds) {
        const tick = new Date(
            Math.ceil((rangeStart.getTime() + BEIJING_OFFSET_MS) / SIX_HOURS_MS)
                * SIX_HOURS_MS
                - BEIJING_OFFSET_MS
        );
        context.font = font;
        context.textAlign = "center";
        context.textBaseline = "top";
        while (tick < rangeEnd) {
            const x = timeX(tick, bounds.left, bounds.width);
            const isDayBoundary = beijingValue(tick).getUTCHours() === 0;
            context.strokeStyle = isDayBoundary ? "#cfd6e1" : colors.grid;
            context.lineWidth = isDayBoundary ? 1.15 : 1;
            context.beginPath();
            context.moveTo(x, bounds.top);
            context.lineTo(x, bounds.top + bounds.height);
            context.stroke();
            context.fillStyle = colors.axis;
            context.fillText(beijingTimeLabel(tick), x, bounds.top + bounds.height + 10);
            tick.setTime(tick.getTime() + SIX_HOURS_MS);
        }

        const day = new Date(
            Math.floor((rangeStart.getTime() + BEIJING_OFFSET_MS) / DAY_MS)
                * DAY_MS
                - BEIJING_OFFSET_MS
        );
        context.font = selectedFont;
        context.textAlign = "left";
        context.textBaseline = "top";
        while (day < rangeEnd) {
            const x = Math.max(bounds.left, timeX(day, bounds.left, bounds.width));
            context.fillStyle = colors.ink;
            context.fillText(beijingDayLabel(new Date(Math.max(day.getTime(), rangeStart.getTime()))), x + 7, bounds.top + 7);
            day.setTime(day.getTime() + DAY_MS);
        }
    }

    function drawHourly() {
        const canvas = root.querySelector('[data-chart="hourly"]');
        if (!canvas) return;
        const { context, width, height } = setupCanvas(
            canvas,
            availableCanvasHeight(canvas, 190)
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
        if (selectedHourStart) {
            const selectedX = timeX(
                new Date(selectedHourStart.getTime() + 30 * 60 * 1000),
                bounds.left,
                bounds.width
            );
            context.strokeStyle = colors.blue;
            context.lineWidth = 1.5;
            context.strokeRect(
                selectedX - candleWidth / 2 - 2,
                bounds.top,
                candleWidth + 4,
                bounds.height
            );
        }
        if (hoveredHourStart && hoveredHourStart.getTime() !== selectedHourStart?.getTime()) {
            const hoveredX = timeX(
                new Date(hoveredHourStart.getTime() + 30 * 60 * 1000),
                bounds.left,
                bounds.width
            );
            context.strokeStyle = colors.axis;
            context.lineWidth = 1;
            context.setLineDash([3, 3]);
            context.beginPath();
            context.moveTo(hoveredX, bounds.top);
            context.lineTo(hoveredX, bounds.top + bounds.height);
            context.stroke();
            context.setLineDash([]);
        }
        hourlyGeometry = { bounds, rows };
    }

    function detailX(value, left, width, start, end) {
        return left + ((value.getTime() - start.getTime()) / (end.getTime() - start.getTime())) * width;
    }

    function drawFiveMinuteTimeAxis(context, bounds, start, end) {
        context.font = font;
        context.textAlign = "center";
        context.textBaseline = "top";
        for (let minute = 0; minute <= 60; minute += 15) {
            const tick = new Date(start.getTime() + minute * 60 * 1000);
            const x = detailX(tick, bounds.left, bounds.width, start, end);
            context.strokeStyle = colors.grid;
            context.beginPath();
            context.moveTo(x, bounds.top);
            context.lineTo(x, bounds.top + bounds.height);
            context.stroke();
            context.fillStyle = colors.axis;
            context.fillText(
                beijingTimeLabel(tick),
                x,
                bounds.top + bounds.height + 9
            );
        }
    }

    function selectedFiveMinuteRows(rows) {
        if (!selectedHourStart) return [];
        const end = selectedHourStart.getTime() + 60 * 60 * 1000;
        return rows.filter((row) => {
            const value = row.time.getTime();
            return value >= selectedHourStart.getTime() && value < end;
        });
    }

    function drawFiveMinute() {
        const canvas = root.querySelector('[data-chart="five-minute"]');
        if (!canvas || !selectedHourStart) return;
        const { context, width, height } = setupCanvas(
            canvas,
            availableCanvasHeight(canvas, 190)
        );
        const rows = finiteKlines(selectedFiveMinuteRows(fiveMinuteRows));
        if (!rows.length) {
            drawEmpty(context, width, height, "该小时暂无5m K线数据");
            return;
        }
        const start = selectedHourStart;
        const end = new Date(start.getTime() + 60 * 60 * 1000);
        const bounds = { left: 62, right: 68, top: 14, bottom: 29 };
        bounds.width = width - bounds.left - bounds.right;
        bounds.height = height - bounds.top - bounds.bottom;
        const scale = priceScale(rows, bounds.top, bounds.height);
        drawPriceGrid(context, bounds, scale);
        drawFiveMinuteTimeAxis(context, bounds, start, end);
        const candleWidth = Math.max(3, Math.min(12, bounds.width / 12 * 0.55));
        rows.forEach((row) => {
            const center = new Date(row.time.getTime() + 2.5 * 60 * 1000);
            drawCandle(
                context,
                row,
                detailX(center, bounds.left, bounds.width, start, end),
                candleWidth,
                scale.y
            );
        });
        if (hoveredFiveMinuteStart) {
            const hovered = rows.find(
                (row) => row.time.getTime() === hoveredFiveMinuteStart.getTime()
            );
            if (hovered) {
                const center = new Date(hovered.time.getTime() + 2.5 * 60 * 1000);
                const x = detailX(center, bounds.left, bounds.width, start, end);
                context.strokeStyle = colors.blue;
                context.lineWidth = 1.25;
                context.strokeRect(
                    x - candleWidth / 2 - 2,
                    bounds.top,
                    candleWidth + 4,
                    bounds.height
                );
            }
        }
        // 5m OI 叠加：OI timestamp 为周期结束时刻，归属到其对应 K 线
        // （timestamp - 5min = 该周期 K 线 open_time），最新一根未结束 K 线无 OI。
        const oiRows = selectedFiveMinuteRows(fiveMinuteOiRows)
            .filter(
                (row) =>
                    row.time.getTime() >= start.getTime() + 5 * 60 * 1000 &&
                    row.time.getTime() < end.getTime() &&
                    Number.isFinite(row.time.getTime()) &&
                    Number.isFinite(row.value)
            );
        if (oiRows.length) {
            const oiDomain = paddedDomain(oiRows.map((row) => row.value));
            const oiY = (value) =>
                bounds.top +
                ((oiDomain.maximum - value) / (oiDomain.maximum - oiDomain.minimum)) *
                    bounds.height;
            context.font = font;
            context.textBaseline = "middle";
            context.textAlign = "right";
            for (let index = 0; index <= 4; index += 1) {
                const gridY = bounds.top + bounds.height * index / 4;
                const value = oiDomain.maximum - (oiDomain.maximum - oiDomain.minimum) * index / 4;
                context.fillStyle = colors.axis;
                context.fillText(
                    formatCompact(value),
                    bounds.left - 10,
                    gridY
                );
            }
            const oiPoints = oiRows.map((row) => ({
                x: detailX(
                    new Date(row.time.getTime() - 2.5 * 60 * 1000),
                    bounds.left,
                    bounds.width,
                    start,
                    end
                ),
                y: oiY(row.value),
            }));
            context.strokeStyle = colors.blue;
            context.lineWidth = 2;
            drawStraightLine(context, oiPoints);
            oiPoints.forEach((point) => {
                context.beginPath();
                context.fillStyle = colors.blue;
                context.arc(point.x, point.y, 2.5, 0, Math.PI * 2);
                context.fill();
            });
        }
        fiveMinuteGeometry = { bounds, rows, start, end };
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
        drawFiveMinute();
        drawDerivatives();
    }

    function nearestHourlyRow(event) {
        if (!hourlyGeometry?.rows.length) return null;
        const rect = event.currentTarget.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const { bounds, rows } = hourlyGeometry;
        if (x < bounds.left || x > bounds.left + bounds.width) return null;
        const targetTime = rangeStart.getTime()
            + ((x - bounds.left) / bounds.width)
                * (rangeEnd.getTime() - rangeStart.getTime());
        return rows.reduce((closest, row) => (
            Math.abs(row.time.getTime() + 30 * 60 * 1000 - targetTime)
                < Math.abs(closest.time.getTime() + 30 * 60 * 1000 - targetTime)
                ? row
                : closest
        ));
    }

    function fallbackHourlyRow() {
        if (selectedHourStart) {
            const selected = hourlyRows.find(
                (row) => row.time.getTime() === selectedHourStart.getTime()
            );
            if (selected) return selected;
        }
        const focusedRows = hourlyRows.filter(
            (row) => row.time >= selectedStart && row.time < selectedEnd
        );
        return focusedRows[focusedRows.length - 1] || hourlyRows[hourlyRows.length - 1];
    }

    const hourlyCanvas = root.querySelector('[data-chart="hourly"]');
    if (hourlyCanvas) {
        hourlyCanvas.addEventListener("mousemove", (event) => {
            const hovered = nearestHourlyRow(event);
            if (!hovered) return;
            hoveredHourStart = new Date(hovered.time);
            updateKlineReadout(
                "[data-hourly-readout]",
                hovered,
                (value) => `${beijingDateHourLabel(value)}:00`
            );
            drawHourly();
        });
        hourlyCanvas.addEventListener("mouseleave", () => {
            hoveredHourStart = null;
            const fallback = fallbackHourlyRow();
            if (fallback) {
                updateKlineReadout(
                    "[data-hourly-readout]",
                    fallback,
                    (value) => `${beijingDateHourLabel(value)}:00`
                );
            }
            drawHourly();
        });
        hourlyCanvas.addEventListener("click", (event) => {
            const selected = nearestHourlyRow(event);
            if (!selected) return;
            selectedHourStart = new Date(selected.time);
            hoveredHourStart = new Date(selected.time);
            updateKlineReadout(
                "[data-hourly-readout]",
                selected,
                (value) => `${beijingDateHourLabel(value)}:00`
            );
            const detail = root.querySelector("[data-five-minute-detail]");
            const label = root.querySelector("[data-five-minute-label]");
            if (detail) detail.hidden = false;
            root.classList.add("has-five-minute-detail");
            const selectedKlines = selectedFiveMinuteRows(fiveMinuteRows);
            const defaultFiveMinute = selectedKlines[selectedKlines.length - 1];
            hoveredFiveMinuteStart = defaultFiveMinute
                ? new Date(defaultFiveMinute.time)
                : null;
            if (defaultFiveMinute) {
                updateKlineReadout(
                    "[data-five-minute-readout]",
                    defaultFiveMinute,
                    beijingDateMinuteLabel
                );
            }
            if (label) {
                const klineCount = selectedKlines.length;
                const oiCount = selectedFiveMinuteRows(fiveMinuteOiRows).length;
                const dateHour = beijingDateHourLabel(selectedHourStart);
                const finalMinute = new Date(selectedHourStart.getTime() + 59 * 60 * 1000);
                const summary = selectedRangeSummary(selectedKlines);
                const rangeText = summary
                    ? ` · 区间涨跌 ${formatChange(summary.change)} · 振幅 ${summary.amplitude.toFixed(2)}%`
                    : "";
                label.textContent = `${dateHour}:00–${beijingTimeLabel(finalMinute)} 北京时间 · ${klineCount}根K线 · ${oiCount}条OI${rangeText}`;
            }
            drawAll();
        });
    }

    const fiveMinuteCanvas = root.querySelector('[data-chart="five-minute"]');
    if (fiveMinuteCanvas) {
        fiveMinuteCanvas.addEventListener("mousemove", (event) => {
            if (!fiveMinuteGeometry?.rows.length) return;
            const rect = fiveMinuteCanvas.getBoundingClientRect();
            const x = event.clientX - rect.left;
            const { bounds, rows, start, end } = fiveMinuteGeometry;
            if (x < bounds.left || x > bounds.left + bounds.width) return;
            const targetTime = start.getTime()
                + ((x - bounds.left) / bounds.width) * (end.getTime() - start.getTime());
            const hovered = rows.reduce((closest, row) => (
                Math.abs(row.time.getTime() + 2.5 * 60 * 1000 - targetTime)
                    < Math.abs(closest.time.getTime() + 2.5 * 60 * 1000 - targetTime)
                    ? row
                    : closest
            ));
            hoveredFiveMinuteStart = new Date(hovered.time);
            updateKlineReadout(
                "[data-five-minute-readout]",
                hovered,
                beijingDateMinuteLabel
            );
            drawFiveMinute();
        });
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

    const initialHourlyRow = fallbackHourlyRow();
    if (initialHourlyRow) {
        updateKlineReadout(
            "[data-hourly-readout]",
            initialHourlyRow,
            (value) => `${beijingDateHourLabel(value)}:00`
        );
    }
    drawAll();
    if ("ResizeObserver" in window) {
        const observer = new ResizeObserver(drawAll);
        root.querySelectorAll(".data-canvas-wrap").forEach((element) => observer.observe(element));
    } else {
        window.addEventListener("resize", drawAll);
    }
})();
