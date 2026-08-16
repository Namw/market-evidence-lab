(() => {
    const root = document.querySelector("[data-microstructure-page]");
    if (!root) return;

    const statusUrl = root.dataset.statusUrl;
    const startUrl = root.dataset.startUrl;
    const stopUrl = root.dataset.stopUrl;
    const startForm = root.querySelector('[data-collector-action="start"]');
    const stopForm = root.querySelector('[data-collector-action="stop"]');
    const startButton = root.querySelector("[data-start-button]");
    const stopButton = root.querySelector("[data-stop-button]");
    const message = root.querySelector("[data-collector-message]");
    const csrfToken = startForm?.querySelector("input[name='csrfmiddlewaretoken']")?.value;
    let requestInFlight = false;

    const numberFormatter = new Intl.NumberFormat("zh-CN");
    const priceFormatter = new Intl.NumberFormat("zh-CN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 3,
    });
    const compactFormatter = new Intl.NumberFormat("zh-CN", {
        notation: "compact",
        maximumFractionDigits: 2,
    });
    const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
    });

    function setText(selector, value) {
        const target = root.querySelector(selector);
        if (target) target.textContent = value;
    }

    function numberValue(value) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }

    function price(value) {
        const parsed = numberValue(value);
        return parsed === null ? "—" : priceFormatter.format(parsed);
    }

    function compact(value) {
        const parsed = numberValue(value);
        return parsed === null ? "—" : `${compactFormatter.format(parsed)} USDT`;
    }

    function decimal(value, places = 4) {
        const parsed = numberValue(value);
        return parsed === null ? "—" : parsed.toFixed(places);
    }

    function localTime(value) {
        if (!value) return "—";
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? "—" : timeFormatter.format(parsed);
    }

    function showMessage(text, isError = false) {
        if (!message) return;
        message.hidden = !text;
        message.textContent = text || "";
        message.classList.toggle("is-error", isError);
    }

    function renderSummaries(rows) {
        const body = root.querySelector("[data-summary-body]");
        if (!body) return;
        body.replaceChildren();
        if (!rows.length) {
            const row = document.createElement("tr");
            const cell = document.createElement("td");
            cell.colSpan = 10;
            cell.className = "empty-state";
            cell.textContent = "运行满一个 UTC 5分钟区间后，这里会出现汇总记录。";
            row.appendChild(cell);
            body.appendChild(row);
            return;
        }
        rows.forEach((item) => {
            const values = [
                localTime(item.interval_start),
                numberFormatter.format(item.snapshot_count),
                price(item.mid_open),
                price(item.mid_high),
                price(item.mid_low),
                price(item.mid_close),
                decimal(item.spread_bps_mean, 4),
                compact(item.bid_depth_top20_quote_mean),
                compact(item.ask_depth_top20_quote_mean),
                decimal(item.imbalance_top20_mean, 4),
            ];
            const row = document.createElement("tr");
            values.forEach((value) => {
                const cell = document.createElement("td");
                cell.textContent = value;
                row.appendChild(cell);
            });
            body.appendChild(row);
        });
    }

    function render(data) {
        const run = data.run;
        const snapshot = data.latest_snapshot;
        const status = root.querySelector("[data-run-status]");
        if (status) {
            status.textContent = run.status_label;
            status.className = `collector-status status-${run.status}`;
        }
        const indicator = root.querySelector("[data-connection-indicator]");
        if (indicator) {
            indicator.className = `connection-indicator connection-${run.connection_state}`;
        }
        setText("[data-connection-label]", run.connection_label);
        setText(
            "[data-heartbeat-label]",
            run.heartbeat_at ? `最近心跳 ${localTime(run.heartbeat_at)} 北京时间` : "尚未启动页面采集"
        );
        setText("[data-received-count]", numberFormatter.format(run.received_messages));
        setText("[data-saved-count]", numberFormatter.format(run.saved_snapshots));
        setText("[data-total-snapshots]", numberFormatter.format(data.total_snapshot_count));
        setText("[data-total-summaries]", numberFormatter.format(data.total_summary_count));
        setText("[data-current-count]", numberFormatter.format(data.current_snapshot_count));
        setText("[data-interval-start]", `${localTime(data.current_interval_start)} 北京时间`);

        const progressTrack = root.querySelector("[data-progress-track]");
        const progressBar = root.querySelector("[data-progress-bar]");
        const progress = Math.max(0, Math.min(100, Number(data.current_interval_progress) || 0));
        if (progressTrack) progressTrack.setAttribute("aria-valuenow", String(Math.round(progress)));
        if (progressBar) progressBar.style.width = `${progress}%`;

        startButton.disabled = requestInFlight || !data.can_start;
        stopButton.disabled = requestInFlight || !data.can_stop;

        setText("[data-latest-time]", snapshot ? `${localTime(snapshot.sampled_at)} 北京时间` : "暂无快照");
        setText("[data-latest-mid]", snapshot ? price(snapshot.mid_price) : "—");
        setText("[data-latest-bid]", snapshot ? price(snapshot.best_bid) : "—");
        setText("[data-latest-ask]", snapshot ? price(snapshot.best_ask) : "—");
        setText("[data-latest-spread]", snapshot ? decimal(snapshot.spread_bps, 4) : "—");
        setText("[data-latest-bid-depth]", snapshot ? compact(snapshot.bid_depth_top20_quote) : "—");
        setText("[data-latest-ask-depth]", snapshot ? compact(snapshot.ask_depth_top20_quote) : "—");
        setText("[data-latest-imbalance]", snapshot ? decimal(snapshot.imbalance_top20, 4) : "—");
        renderSummaries(data.recent_summaries);

        if (run.error_message && run.status === "failed") {
            showMessage(run.error_message, true);
        }
    }

    async function refresh() {
        try {
            const response = await fetch(statusUrl, {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            if (!response.ok) throw new Error("无法读取采集状态");
            render(await response.json());
        } catch (error) {
            showMessage(error.message || "无法读取采集状态", true);
        }
    }

    async function submitAction(event, action) {
        event.preventDefault();
        if (requestInFlight) return;
        requestInFlight = true;
        startButton.disabled = true;
        stopButton.disabled = true;
        showMessage(action === "start" ? "正在启动采集…" : "正在停止采集…");
        try {
            const response = await fetch(action === "start" ? startUrl : stopUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    Accept: "application/json",
                    "X-CSRFToken": csrfToken,
                },
            });
            const payload = await response.json();
            showMessage(payload.message || (response.ok ? "操作已提交。" : "操作失败。"), !response.ok);
        } catch (error) {
            showMessage(error.message || "操作失败。", true);
        } finally {
            requestInFlight = false;
            window.setTimeout(refresh, 350);
        }
    }

    startForm?.addEventListener("submit", (event) => submitAction(event, "start"));
    stopForm?.addEventListener("submit", (event) => submitAction(event, "stop"));
    refresh();
    window.setInterval(refresh, 2000);
})();
