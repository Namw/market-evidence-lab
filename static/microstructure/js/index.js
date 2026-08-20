(() => {
    const root = document.querySelector("[data-microstructure-page]");
    if (!root) return;

    const initialNode = document.getElementById("microstructure-initial-data");
    const canvases = [...root.querySelectorAll("canvas[data-chart]")];
    const startForm = root.querySelector('[data-collector-action="start"]');
    const stopForm = root.querySelector('[data-collector-action="stop"]');
    const startButton = root.querySelector("[data-start-button]");
    const stopButton = root.querySelector("[data-stop-button]");
    const runSelect = root.querySelector("[data-run-select]");
    const loadOlderButton = root.querySelector("[data-load-older]");
    const csrfToken = startForm?.querySelector("input[name='csrfmiddlewaretoken']")?.value;
    const initialData = initialNode ? JSON.parse(initialNode.textContent) : null;
    const state = {
        data: initialData,
        minutes: [],
        cache: new Map(),
        cacheRunId: null,
        oiCache: new Map(),
        hasMore: false,
        oldestLoaded: null,
        loadingOlder: false,
        selected: -1,
        selectedRunId: initialData?.selected_run_id ?? null,
        followLatest: true,
        viewEndStamp: null,
        hoverCanvas: null,
        hoverY: null,
        busy: false,
    };

    const WINDOW_SIZE = 120;
    const PREFETCH_MARGIN = 30;
    const SPREAD_MAX_BPS = 3;

    const COLORS = {
        grid: "rgba(139, 158, 175, .13)", text: "#8fa0ac", green: "#24cd78",
        red: "#ff5252", cyan: "#39d5dd", purple: "#b16be0", yellow: "#f0b90b",
        white: "#e7edf0", selection: "rgba(62, 130, 177, .10)", selectionLine: "rgba(128, 177, 210, .55)",
    };
    const minuteMs = 60_000;
    const number = (value) => {
        if (value === null || value === undefined || value === "") return null;
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    };
    const setText = (selector, value) => {
        const node = root.querySelector(selector);
        if (node) node.textContent = value;
    };
    const compact = (value, digits = 2) => {
        const parsed = number(value);
        if (parsed === null) return "—";
        const absolute = Math.abs(parsed);
        if (absolute >= 1e9) return `${(parsed / 1e9).toFixed(digits)}B`;
        if (absolute >= 1e6) return `${(parsed / 1e6).toFixed(digits)}M`;
        if (absolute >= 1e3) return `${(parsed / 1e3).toFixed(digits)}K`;
        return parsed.toFixed(digits);
    };
    const price = (value) => {
        const parsed = number(value);
        return parsed === null ? "—" : parsed.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    };
    const signedCompact = (value) => {
        const parsed = number(value);
        if (parsed === null) return "—";
        return `${parsed >= 0 ? "+" : ""}${compact(parsed)}`;
    };
    const signedPrice = (value) => {
        const parsed = number(value);
        if (parsed === null) return "—";
        const sign = parsed > 0 ? "+" : parsed < 0 ? "-" : "";
        return `${sign}${price(Math.abs(parsed))}`;
    };
    const percent = (value) => {
        const parsed = number(value);
        if (parsed === null) return "—";
        return `${parsed >= 0 ? "+" : ""}${parsed.toFixed(2)}%`;
    };
    const time = (date) => date.toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false });
    const dateLabel = (date) => date.toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).replaceAll("/", "-");

    function latestAvailableIndex() {
        return state.minutes.map((row, index) => row.missing ? -1 : index).filter((index) => index >= 0).at(-1) ?? -1;
    }

    function defaultSelectedIndex() {
        const latest = latestAvailableIndex();
        if (state.data?.selected_run_active) return latest;
        return state.minutes.map((row, index) => !row.missing && row.closed ? index : -1).filter((index) => index >= 0).at(-1) ?? latest;
    }

    function renderRunOptions(data) {
        if (!runSelect) return;
        runSelect.replaceChildren();
        const runs = Array.isArray(data.available_runs) ? data.available_runs : [];
        if (!runs.length) {
            const option = document.createElement("option");
            option.value = ""; option.textContent = "暂无采集记录";
            runSelect.appendChild(option); runSelect.disabled = true;
            return;
        }
        runSelect.disabled = false;
        runs.forEach((run, index) => {
            const started = new Date(run.started_at);
            const stopped = run.stopped_at ? new Date(run.stopped_at) : null;
            const option = document.createElement("option");
            option.value = String(run.id);
            const status = run.status === "running" ? "采集中" : run.status_label;
            option.textContent = `${index === 0 ? "最新 · " : ""}#${run.id} · ${dateLabel(started)} ${time(started)}–${stopped ? time(stopped) : "现在"} · ${status}`;
            runSelect.appendChild(option);
        });
        if (data.selected_run_id !== null) runSelect.value = String(data.selected_run_id);
    }

    function normalizeMinutes(rows, slotLimit = 10080) {
        if (!Array.isArray(rows) || !rows.length) return [];
        const byTime = new Map();
        rows.forEach((row) => {
            const stamp = new Date(row.minute_start).getTime();
            if (Number.isFinite(stamp)) byTime.set(stamp, { ...row, stamp });
        });
        const stamps = [...byTime.keys()].sort((a, b) => a - b);
        if (!stamps.length) return [];
        const end = stamps.at(-1);
        const start = Math.max(stamps[0], end - (slotLimit - 1) * minuteMs);
        const result = [];
        for (let stamp = start; stamp <= end; stamp += minuteMs) {
            result.push(byTime.get(stamp) || { minute_start: new Date(stamp).toISOString(), stamp, missing: true });
        }
        return result;
    }

    function mergeIntoCache(rows) {
        if (!Array.isArray(rows)) return;
        rows.forEach((row) => {
            const stamp = new Date(row.minute_start).getTime();
            if (Number.isFinite(stamp)) state.cache.set(stamp, { ...row, stamp });
        });
    }

    function mergeOiCache(rows) {
        if (!Array.isArray(rows)) return;
        rows.forEach((row) => {
            const stamp = new Date(row.timestamp).getTime();
            if (Number.isFinite(stamp)) state.oiCache.set(stamp, { ...row, stamp });
        });
    }

    function oiBarForStamp(stamp) {
        let value = null;
        for (const row of state.oiCache.values()) {
            if (row.stamp - 5 * minuteMs < stamp && stamp <= row.stamp) {
                value = number(row.value);
            }
        }
        return value;
    }

    function setupCanvas(canvas) {
        const rect = canvas.getBoundingClientRect();
        const ratio = Math.max(1, window.devicePixelRatio || 1);
        const width = Math.max(1, Math.round(rect.width));
        const height = Math.max(1, Math.round(rect.height));
        if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
            canvas.width = Math.round(width * ratio);
            canvas.height = Math.round(height * ratio);
        }
        const context = canvas.getContext("2d");
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        context.clearRect(0, 0, width, height);
        return { context, width, height, left: 50, right: 56, top: 8, bottom: 10 };
    }

    function plotGeometry(frame, count) {
        const plotWidth = Math.max(1, frame.width - frame.left - frame.right);
        const plotHeight = Math.max(1, frame.height - frame.top - frame.bottom);
        const n = Math.max(1, count);
        return {
            ...frame, plotWidth, plotHeight,
            x: (index) => frame.left + (index + .5) * plotWidth / n,
            slotWidth: plotWidth / n,
        };
    }

    function viewRange() {
        const total = state.minutes.length;
        let end = total - 1;
        if (!state.followLatest && state.viewEndStamp !== null) {
            const index = state.minutes.findIndex((row) => row.stamp === state.viewEndStamp);
            if (index >= 0) end = index;
            else state.followLatest = true;
        }
        const start = Math.max(0, end - WINDOW_SIZE + 1);
        return { start, end, count: end - start + 1 };
    }

    function panTo(endIndex) {
        const total = state.minutes.length;
        if (!total) return;
        const clamped = Math.max(0, Math.min(total - 1, endIndex));
        const row = state.minutes[clamped];
        if (!row) return;
        state.viewEndStamp = row.stamp;
        state.followLatest = clamped >= total - 1;
        drawCharts();
    }

    function maybePrefetch() {
        if (state.loadingOlder || !state.hasMore) return;
        const view = viewRange();
        if (view.start <= PREFETCH_MARGIN) loadOlder();
    }

    function drawGrid(plot, horizontal = 4) {
        const { context: ctx } = plot;
        const view = viewRange();
        ctx.save();
        ctx.strokeStyle = COLORS.grid;
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 3]);
        for (let row = 0; row <= horizontal; row += 1) {
            const y = plot.top + row * plot.plotHeight / horizontal;
            ctx.beginPath(); ctx.moveTo(plot.left, y); ctx.lineTo(plot.width - plot.right, y); ctx.stroke();
        }
        const every = view.count > 80 ? 15 : view.count > 35 ? 10 : 5;
        for (let local = 0; local < view.count; local += 1) {
            if (local % every !== 0) continue;
            const x = plot.x(local);
            ctx.beginPath(); ctx.moveTo(x, plot.top); ctx.lineTo(x, plot.height - plot.bottom); ctx.stroke();
        }
        ctx.restore();
    }

    function drawSelection(plot) {
        if (state.selected < 0) return;
        const view = viewRange();
        const selected = state.minutes[state.selected];
        const groupStart = Math.floor(selected.stamp / minuteMs / 5) * 5 * minuteMs;
        const indices = state.minutes
            .map((row, index) => row.stamp >= groupStart && row.stamp < groupStart + 5 * minuteMs ? index : -1)
            .filter((index) => index >= 0)
            .filter((index) => index >= view.start && index <= view.end);
        const { context: ctx } = plot;
        ctx.save();
        if (indices.length) {
            const startX = plot.x(indices[0] - view.start) - plot.slotWidth / 2;
            const endX = plot.x(indices.at(-1) - view.start) + plot.slotWidth / 2;
            ctx.fillStyle = COLORS.selection;
            ctx.fillRect(startX, plot.top, endX - startX, plot.plotHeight);
        }
        if (state.selected >= view.start && state.selected <= view.end) {
            const x = plot.x(state.selected - view.start);
            ctx.strokeStyle = COLORS.selectionLine;
            ctx.setLineDash([3, 3]);
            ctx.beginPath(); ctx.moveTo(x, plot.top); ctx.lineTo(x, plot.height - plot.bottom); ctx.stroke();
        }
        ctx.restore();
    }

    function drawAxisLabels(plot, min, max, formatter = compact) {
        const { context: ctx } = plot;
        ctx.save();
        ctx.fillStyle = COLORS.text;
        ctx.font = "10px ui-sans-serif, system-ui";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        for (let row = 0; row <= 4; row += 1) {
            const value = max - (max - min) * row / 4;
            const y = plot.top + plot.plotHeight * row / 4;
            ctx.fillText(formatter(value), plot.left - 7, y);
        }
        ctx.restore();
    }

    function trailingAverage(field, count = 60) {
        const view = viewRange();
        const values = state.minutes
            .slice(Math.max(0, view.end - count + 1), view.end + 1)
            .map((row) => number(row[field]))
            .filter((value) => value !== null);
        if (!values.length) return null;
        return values.reduce((sum, value) => sum + value, 0) / values.length;
    }

    function trailingCombinedAverage(fields, count = 60) {
        const view = viewRange();
        const values = state.minutes
            .slice(Math.max(0, view.end - count + 1), view.end + 1)
            .flatMap((row) => fields.map((field) => number(row[field])))
            .filter((value) => value !== null);
        if (!values.length) return null;
        return values.reduce((sum, value) => sum + value, 0) / values.length;
    }

    function drawAverageLine(plot, y, color) {
        const { context: ctx } = plot;
        ctx.save();
        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash([8, 6]);
        ctx.beginPath(); ctx.moveTo(plot.left, y); ctx.lineTo(plot.width - plot.right, y); ctx.stroke();
        ctx.restore();
    }

    function renderAverages() {
        const fmt = (value) => value === null ? "—" : compact(value);
        setText("[data-avg-buy]", fmt(trailingAverage("taker_buy_quote")));
        setText("[data-avg-sell]", fmt(trailingAverage("taker_sell_quote")));
        setText("[data-avg-bid-depth]", fmt(trailingAverage("bid_depth_mean")));
        setText("[data-avg-ask-depth]", fmt(trailingAverage("ask_depth_mean")));
        setText("[data-avg-depth]", fmt(trailingCombinedAverage(["bid_depth_mean", "ask_depth_mean"])));
    }

    function drawHoverAxis(plot, min, max, formatter = compact) {
        if (state.hoverCanvas !== plot.context.canvas || state.hoverY === null) return;
        const { context: ctx } = plot;
        const y = state.hoverY;
        if (y < plot.top || y > plot.height - plot.bottom) return;
        const value = max - (y - plot.top) / plot.plotHeight * (max - min);
        ctx.save();
        ctx.strokeStyle = "rgba(128, 177, 210, .5)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(plot.left, y); ctx.lineTo(plot.width - plot.right, y); ctx.stroke();
        ctx.setLineDash([]);
        const label = formatter(value);
        ctx.font = "10px ui-sans-serif, system-ui";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        const width = ctx.measureText(label).width + 12;
        ctx.fillStyle = "#16324a";
        ctx.fillRect(plot.left - width, y - 8, width, 16);
        ctx.strokeStyle = "rgba(128, 177, 210, .8)";
        ctx.strokeRect(plot.left - width, y - 8, width, 16);
        ctx.fillStyle = "#eaf0f4";
        ctx.fillText(label, plot.left - 6, y);
        ctx.restore();
    }

    function drawPrice(canvas) {
        const view = viewRange();
        const plot = plotGeometry(setupCanvas(canvas), view.count);
        drawGrid(plot); drawSelection(plot);
        const values = state.minutes.flatMap((row) => [number(row.low), number(row.high)]).filter((value) => value !== null);
        if (!values.length) return;
        let min = Math.min(...values), max = Math.max(...values);
        const padding = Math.max((max - min) * .08, max * .0003);
        min -= padding; max += padding;
        const y = (value) => plot.top + (max - value) / (max - min) * plot.plotHeight;
        drawAxisLabels(plot, min, max, price);
        const ctx = plot.context;
        for (let local = 0; local < view.count; local += 1) {
            const row = state.minutes[view.start + local];
            const open = number(row.open), high = number(row.high), low = number(row.low), close = number(row.close);
            if ([open, high, low, close].some((value) => value === null)) continue;
            const rising = close >= open;
            const color = rising ? COLORS.red : COLORS.green;
            const x = plot.x(local);
            const bodyWidth = Math.max(2, Math.min(8, plot.slotWidth * .66));
            ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(x, y(high)); ctx.lineTo(x, y(low)); ctx.stroke();
            const top = Math.min(y(open), y(close));
            const height = Math.max(1, Math.abs(y(open) - y(close)));
            ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, height);
        }
        const selected = state.minutes[state.selected];
        const close = number(selected?.close);
        if (close !== null && state.selected >= view.start && state.selected <= view.end) {
            ctx.fillStyle = close >= number(selected.open) ? COLORS.red : COLORS.green;
            ctx.fillRect(plot.width - plot.right + 5, y(close) - 9, 49, 18);
            ctx.fillStyle = "#fff"; ctx.font = "10px ui-sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
            ctx.fillText(price(close), plot.width - 26, y(close));
        }
        drawHoverAxis(plot, min, max, price);
    }

    function drawFlow(canvas) {
        const view = viewRange();
        const plot = plotGeometry(setupCanvas(canvas), view.count);
        drawGrid(plot); drawSelection(plot);
        const max = Math.max(1, ...state.minutes.flatMap((row) => [number(row.taker_buy_quote) || 0, number(row.taker_sell_quote) || 0, Math.abs(number(row.delta_quote) || 0)]));
        const center = plot.top + plot.plotHeight / 2;
        const scale = plot.plotHeight * .45 / max;
        const ctx = plot.context;
        ctx.strokeStyle = "rgba(205, 217, 225, .25)"; ctx.beginPath(); ctx.moveTo(plot.left, center); ctx.lineTo(plot.width - plot.right, center); ctx.stroke();
        drawAxisLabels(plot, -max, max);
        for (let local = 0; local < view.count; local += 1) {
            const row = state.minutes[view.start + local];
            const buy = number(row.taker_buy_quote), sell = number(row.taker_sell_quote);
            const x = plot.x(local), width = Math.max(1, Math.min(7, plot.slotWidth * .62));
            if (buy !== null) { ctx.fillStyle = "rgba(255, 82, 82, .78)"; ctx.fillRect(x - width / 2, center - buy * scale, width, buy * scale); }
            if (sell !== null) { ctx.fillStyle = "rgba(36, 205, 120, .72)"; ctx.fillRect(x - width / 2, center, width, sell * scale); }
        }
        ctx.strokeStyle = COLORS.white; ctx.lineWidth = 1.35; ctx.beginPath();
        let started = false;
        for (let local = 0; local < view.count; local += 1) {
            const delta = number(state.minutes[view.start + local].delta_quote);
            if (delta === null) { started = false; continue; }
            const x = plot.x(local), y = center - delta * scale;
            if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
        }
        ctx.stroke();
        const avgBuy = trailingAverage("taker_buy_quote");
        const avgSell = trailingAverage("taker_sell_quote");
        if (avgBuy !== null) drawAverageLine(plot, center - avgBuy * 2 * scale, "rgba(255, 82, 82, .95)");
        if (avgSell !== null) drawAverageLine(plot, center + avgSell * 2 * scale, "rgba(36, 205, 120, .95)");
        drawHoverAxis(plot, -max, max);
    }

    function drawDepth(canvas) {
        const view = viewRange();
        const plot = plotGeometry(setupCanvas(canvas), view.count);
        drawGrid(plot); drawSelection(plot);
        const depths = state.minutes.flatMap((row) => [number(row.bid_depth_mean), number(row.ask_depth_mean)]).filter((value) => value !== null);
        if (!depths.length) return;
        const max = Math.max(1, ...depths) * 1.08;
        const y = (value) => plot.top + (max - value) / max * plot.plotHeight;
        drawAxisLabels(plot, 0, max);
        const ctx = plot.context;
        [["bid_depth_mean", COLORS.cyan], ["ask_depth_mean", COLORS.purple]].forEach(([field, color]) => {
            ctx.strokeStyle = color; ctx.lineWidth = 1.4; ctx.beginPath();
            let started = false;
            for (let local = 0; local < view.count; local += 1) {
                const value = number(state.minutes[view.start + local][field]);
                if (value === null) { started = false; continue; }
                if (!started) { ctx.moveTo(plot.x(local), y(value)); started = true; } else ctx.lineTo(plot.x(local), y(value));
            }
            ctx.stroke();
        });
        const avgDepth = trailingCombinedAverage(["bid_depth_mean", "ask_depth_mean"]);
        if (avgDepth !== null) drawAverageLine(plot, y(avgDepth * 2), COLORS.white);
        drawHoverAxis(plot, 0, max);
    }

    function drawSpread(canvas) {
        const view = viewRange();
        const plot = plotGeometry(setupCanvas(canvas), view.count);
        drawGrid(plot); drawSelection(plot);
        const ctx = plot.context;
        const max = SPREAD_MAX_BPS;
        const y = (value) => plot.top + (max - value) / max * plot.plotHeight;
        const label = (value) => `${Number(value.toFixed(2))}`;
        drawAxisLabels(plot, 0, max, label);
        const bottom = plot.height - plot.bottom;
        const selectedLocal = state.selected >= view.start && state.selected <= view.end ? state.selected - view.start : -1;
        for (let local = 0; local < view.count; local += 1) {
            const value = number(state.minutes[view.start + local].spread_bps_p95);
            if (value === null) continue;
            const top = Math.max(plot.top, y(value));
            if (top >= bottom) continue;
            ctx.fillStyle = local === selectedLocal ? "rgba(240, 185, 11, 1)" : "rgba(240, 185, 11, .72)";
            ctx.fillRect(plot.x(local) - Math.max(1, plot.slotWidth * .2), top, Math.max(1, plot.slotWidth * .4), bottom - top);
        }
        drawHoverAxis(plot, 0, max, (value) => `${value.toFixed(2)} bps`);
    }

    function drawOi(canvas) {
        const view = viewRange();
        const plot = plotGeometry(setupCanvas(canvas), view.count);
        drawGrid(plot); drawSelection(plot);
        const ctx = plot.context;
        const viewStart = state.minutes[view.start]?.stamp;
        const viewEnd = state.minutes[view.end]?.stamp + minuteMs;
        if (!Number.isFinite(viewStart) || !Number.isFinite(viewEnd)) return;
        const bars = [...state.oiCache.values()]
            .filter((row) => row.stamp >= viewStart && row.stamp <= viewEnd)
            .sort((a, b) => a.stamp - b.stamp);
        if (!bars.length) return;
        const values = bars.map((row) => number(row.value)).filter((value) => value !== null);
        if (!values.length) return;
        const max = Math.max(...values) * 1.08;
        const y = (value) => plot.top + (max - value) / max * plot.plotHeight;
        drawAxisLabels(plot, 0, max);
        // OI timestamp 为 5 分钟周期结束时刻，条形画在该周期中心（timestamp - 2.5min）
        bars.forEach((row) => {
            const value = number(row.value);
            if (value === null) return;
            const centerStamp = row.stamp - 2.5 * minuteMs;
            const x = plot.left + ((centerStamp - viewStart) / (viewEnd - viewStart)) * plot.plotWidth;
            const width = Math.max(2, Math.min(9, plot.slotWidth * 5 * .62));
            const top = y(value);
            ctx.fillStyle = "rgba(91, 155, 255, .82)";
            ctx.fillRect(x - width / 2, top, width, plot.height - plot.bottom - top);
        });
    }

    function drawOne(canvas) {
        if (canvas.dataset.chart === "price") drawPrice(canvas);
        if (canvas.dataset.chart === "oi") drawOi(canvas);
        if (canvas.dataset.chart === "flow") drawFlow(canvas);
        if (canvas.dataset.chart === "depth") drawDepth(canvas);
        if (canvas.dataset.chart === "spread") drawSpread(canvas);
    }

    function drawCharts() {
        canvases.forEach(drawOne);
    }

    function colorMetric(node, value) {
        if (!node) return;
        node.classList.toggle("positive", value > 0);
        node.classList.toggle("negative", value < 0);
    }

    function renderGroup(selected) {
        const container = root.querySelector("[data-minute-group]");
        if (!container || !selected) return;
        container.replaceChildren();
        const groupStart = Math.floor(selected.stamp / minuteMs / 5) * 5 * minuteMs;
        const byTime = new Map(state.minutes.map((row, index) => [row.stamp, { row, index }]));
        for (let position = 0; position < 5; position += 1) {
            const stamp = groupStart + position * minuteMs;
            const found = byTime.get(stamp);
            const row = found?.row;
            const change = row && number(row.open) && number(row.close) !== null ? (number(row.close) / number(row.open) - 1) * 100 : null;
            const priceChange = row && number(row.open) && number(row.close) !== null ? number(row.close) - number(row.open) : null;
            const button = document.createElement("button");
            button.type = "button"; button.className = "group-minute";
            if (found?.index === state.selected) button.classList.add("is-selected");
            if (change > 0) button.classList.add("is-up");
            if (change < 0) button.classList.add("is-down");
            button.disabled = !found || row.missing;
            button.innerHTML = `<span>${time(new Date(stamp))}</span><strong>${position + 1}</strong><small>${change === null ? "—" : percent(change)}</small><em>${priceChange === null ? "—" : signedPrice(priceChange)}</em>`;
            if (found) button.addEventListener("click", () => select(found.index, true));
            container.appendChild(button);
        }
    }

    function renderSelected() {
        const row = state.minutes[state.selected];
        if (!row) return;
        const start = new Date(row.stamp), end = new Date(row.stamp + minuteMs);
        setText("[data-selected-range]", `${time(start)}–${time(end)}`);
        const open = number(row.open), close = number(row.close), high = number(row.high), low = number(row.low);
        const change = open && close !== null ? (close / open - 1) * 100 : null;
        const range = open && high !== null && low !== null ? (high - low) / open * 100 : null;
        const delta = number(row.delta_quote);
        const changeNode = root.querySelector("[data-price-change]");
        const deltaNode = root.querySelector("[data-delta]");
        setText("[data-price-change]", percent(change)); colorMetric(changeNode, change);
        setText("[data-price-range]", range === null ? "—" : `${range.toFixed(2)}%`);
        setText("[data-quote-volume]", compact(row.quote_volume));
        setText("[data-buy-volume]", compact(row.taker_buy_quote));
        setText("[data-sell-volume]", compact(row.taker_sell_quote));
        setText("[data-delta]", signedCompact(delta)); colorMetric(deltaNode, delta);
        setText("[data-bid-depth]", `${compact(row.bid_depth_open)} → ${compact(row.bid_depth_close)}`);
        setText("[data-ask-depth]", `${compact(row.ask_depth_open)} → ${compact(row.ask_depth_close)}`);
        setText("[data-spread]", number(row.spread_bps_p95) === null ? "—" : `${number(row.spread_bps_p95).toFixed(2)} bps`);
        setText("[data-coverage]", number(row.coverage_ratio) === null ? "—" : `${(number(row.coverage_ratio) * 100).toFixed(1)}%`);
        setText("[data-ohlc]", `O ${price(row.open)}  H ${price(row.high)}  L ${price(row.low)}  C ${price(row.close)}  ${percent(change)}`);
        const flowBuy = number(row.taker_buy_quote), flowSell = number(row.taker_sell_quote);
        setText("[data-flow-summary]", `买 ${compact(flowBuy)} / 卖 ${compact(flowSell)} / Δ ${signedCompact(delta)}`);
        setText("[data-depth-summary]", `买深 ${compact(row.bid_depth_mean)} / 卖深 ${compact(row.ask_depth_mean)}`);
        setText("[data-spread-summary]", number(row.spread_bps_p95) === null ? "—" : `${number(row.spread_bps_p95).toFixed(2)} bps`);
        const oiValue = oiBarForStamp(row.stamp);
        setText("[data-oi-summary]", oiValue === null ? "—" : `${compact(oiValue)}`);
        renderGroup(row);
    }

    function select(index, userInitiated = false) {
        if (index < 0 || index >= state.minutes.length || state.minutes[index].missing) return;
        state.selected = index;
        if (userInitiated) state.followLatest = index === latestAvailableIndex();
        renderSelected(); drawCharts();
    }

    function renderStatus(data, preserveSelection = true) {
        const previousStamp = preserveSelection && !state.followLatest ? state.minutes[state.selected]?.stamp : null;
        state.data = data;
        if (data.selected_run_id !== state.cacheRunId) {
            state.cache.clear();
            state.cacheRunId = data.selected_run_id;
        }
        mergeIntoCache(data.minutes);
        mergeOiCache(data.oi_5m);
        state.selectedRunId = data.selected_run_id;
        state.hasMore = data.has_more;
        state.oldestLoaded = data.oldest_loaded_stamp;
        state.minutes = normalizeMinutes([...state.cache.values()]);
        renderAverages();
        const previousIndex = previousStamp === null ? -1 : state.minutes.findIndex((row) => row.stamp === previousStamp);
        state.selected = previousIndex >= 0 ? previousIndex : defaultSelectedIndex();
        renderRunOptions(data);
        const live = root.querySelector("[data-live-state]");
        if (live) live.className = `live-state state-${data.run.connection_state}`;
        setText("[data-live-label]", data.run.connection_label);
        const oiLive = root.querySelector("[data-oi-live]");
        if (oiLive) oiLive.hidden = !(data.run.oi_process_id && data.run.status === "running");
        startButton.disabled = state.busy || !data.can_start;
        stopButton.disabled = state.busy || !data.can_stop;
        if (loadOlderButton) {
            loadOlderButton.hidden = !data.has_more;
            loadOlderButton.disabled = state.loadingOlder;
        }
        const empty = root.querySelector("[data-empty-chart]");
        if (empty) empty.hidden = state.minutes.length > 0;
        if (state.minutes.length) {
            const view = viewRange();
            const first = new Date(state.minutes[view.start].stamp), last = new Date(state.minutes[view.end].stamp);
            setText("[data-range-start]", time(first)); setText("[data-range-end]", time(last)); setText("[data-range-date]", dateLabel(last));
            const shown = state.cache.size;
            const countLabel = shown < data.minute_count ? `显示 ${shown} / ${data.minute_count}` : `${shown}`;
            const timeLabel = state.followLatest ? `最新 ${time(last)}` : `窗口 ${time(first)}–${time(last)}`;
            setText("[data-last-update]", `${timeLabel} · ${countLabel} 分钟`);
            renderSelected();
        } else {
            setText("[data-selected-range]", "—");
            setText("[data-last-update]", "该采集记录暂无分钟数据");
            setText("[data-ohlc]", "O —  H —  L —  C —");
            setText("[data-flow-summary]", "—");
            setText("[data-depth-summary]", "—");
            setText("[data-spread-summary]", "—");
            setText("[data-oi-summary]", "—");
            root.querySelector("[data-minute-group]")?.replaceChildren();
        }
        if (data.run.status === "failed" && data.run.error_message) showMessage(data.run.error_message, true);
        drawCharts();
    }

    function showMessage(text, error = false) {
        const node = root.querySelector("[data-message]");
        if (!node) return;
        node.hidden = !text; node.textContent = text || ""; node.classList.toggle("is-error", error);
    }

    const DEFAULT_LOAD = 360;
    const OLDER_CHUNK = 720;

    async function refresh(preserveSelection = true) {
        try {
            const params = new URLSearchParams({ minutes: String(DEFAULT_LOAD) });
            if (state.selectedRunId !== null) params.set("run_id", String(state.selectedRunId));
            const response = await fetch(`${root.dataset.statusUrl}?${params}`, { headers: { Accept: "application/json" }, credentials: "same-origin" });
            if (!response.ok) throw new Error("无法读取分钟数据");
            renderStatus(await response.json(), preserveSelection);
        } catch (error) {
            showMessage(error.message || "无法读取分钟数据", true);
        }
    }

    async function loadOlder() {
        if (state.loadingOlder || !state.hasMore || !state.oldestLoaded) return;
        state.loadingOlder = true;
        if (loadOlderButton) loadOlderButton.disabled = true;
        try {
            const params = new URLSearchParams({ minutes: String(OLDER_CHUNK) });
            if (state.selectedRunId !== null) params.set("run_id", String(state.selectedRunId));
            params.set("before", state.oldestLoaded);
            const response = await fetch(`${root.dataset.statusUrl}?${params}`, { headers: { Accept: "application/json" }, credentials: "same-origin" });
            if (!response.ok) throw new Error("无法读取更早数据");
            renderStatus(await response.json(), true);
        } catch (error) {
            showMessage(error.message || "无法读取更早数据", true);
        } finally {
            state.loadingOlder = false;
            if (loadOlderButton) loadOlderButton.disabled = false;
        }
    }

    async function submitAction(event, action) {
        event.preventDefault();
        if (state.busy) return;
        state.busy = true; startButton.disabled = true; stopButton.disabled = true;
        showMessage(action === "start" ? "正在启动实时采集…" : "正在停止采集…");
        try {
            const response = await fetch(action === "start" ? root.dataset.startUrl : root.dataset.stopUrl, {
                method: "POST", credentials: "same-origin", headers: { Accept: "application/json", "X-CSRFToken": csrfToken },
            });
            const payload = await response.json();
            showMessage(payload.message || (response.ok ? "操作已提交。" : "操作失败。"), !response.ok);
        } catch (error) {
            showMessage(error.message || "操作失败。", true);
        } finally {
            state.busy = false;
            if (action === "start") {
                state.selectedRunId = null;
                state.followLatest = true;
                state.viewEndStamp = null;
            }
            window.setTimeout(() => refresh(false), 500);
        }
    }

    let drag = null;
    let suppressClick = false;
    canvases.forEach((canvas) => {
        canvas.addEventListener("pointerdown", (event) => {
            if (!state.minutes.length) return;
            canvas.setPointerCapture(event.pointerId);
            const endRow = state.minutes[viewRange().end];
            drag = { pointerId: event.pointerId, startX: event.clientX, startStamp: endRow.stamp, moved: false };
        });
        canvas.addEventListener("pointermove", (event) => {
            if (drag && drag.pointerId === event.pointerId) {
                const dx = event.clientX - drag.startX;
                if (Math.abs(dx) < 4) return;
                drag.moved = true;
                const rect = canvas.getBoundingClientRect();
                const plotWidth = rect.width - 50 - 56;
                const bars = Math.round(dx / (plotWidth / WINDOW_SIZE));
                const startIndex = state.minutes.findIndex((row) => row.stamp === drag.startStamp);
                if (startIndex < 0) return;
                panTo(startIndex - bars);
                maybePrefetch();
                return;
            }
            if (!state.minutes.length) return;
            if (state.hoverCanvas !== canvas) {
                if (state.hoverCanvas && state.hoverCanvas !== canvas) {
                    state.hoverY = null;
                    drawOne(state.hoverCanvas);
                }
                state.hoverCanvas = canvas;
                state.hoverY = null;
            }
            const rect = canvas.getBoundingClientRect();
            const y = event.clientY - rect.top;
            if (state.hoverY === y) return;
            state.hoverY = y;
            drawOne(canvas);
        });
        canvas.addEventListener("pointerleave", () => {
            if (state.hoverCanvas !== canvas) return;
            state.hoverCanvas = null;
            state.hoverY = null;
            drawOne(canvas);
        });
        const endDrag = (event) => {
            if (!drag || drag.pointerId !== event.pointerId) return;
            const wasDrag = drag.moved;
            drag = null;
            suppressClick = wasDrag;
            if (wasDrag) event.preventDefault();
        };
        canvas.addEventListener("pointerup", endDrag);
        canvas.addEventListener("pointercancel", endDrag);
    });
    canvases.forEach((canvas) => canvas.addEventListener("click", (event) => {
        if (suppressClick) { suppressClick = false; return; }
        if (!state.minutes.length || drag) return;
        const rect = canvas.getBoundingClientRect();
        const plotWidth = rect.width - 50 - 56;
        const view = viewRange();
        const local = Math.floor((event.clientX - rect.left - 50) / plotWidth * view.count);
        select(view.start + Math.max(0, Math.min(view.count - 1, local)), true);
    }));
    runSelect?.addEventListener("change", () => {
        const selected = Number(runSelect.value);
        state.selectedRunId = Number.isInteger(selected) && selected > 0 ? selected : null;
        state.followLatest = true;
        state.viewEndStamp = null;
        refresh(false);
    });
    loadOlderButton?.addEventListener("click", () => loadOlder());
    startForm?.addEventListener("submit", (event) => submitAction(event, "start"));
    stopForm?.addEventListener("submit", (event) => submitAction(event, "stop"));
    new ResizeObserver(() => drawCharts()).observe(root.querySelector(".chart-stack"));
    if (state.data) renderStatus(state.data, false); else refresh(false);
    window.setInterval(() => refresh(true), 60_000);
})();
