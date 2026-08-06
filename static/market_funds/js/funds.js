(() => {
    const charts = Array.from(document.querySelectorAll("[data-fund-chart]"));
    if (!charts.length) return;

    const colors = {
        axis: "#7a8699",
        grid: "#e9edf3",
        guide: "#8b96a9",
        blue: "#3867d8",
        green: "#1d9a6c",
        red: "#d25555",
        white: "#ffffff",
    };
    const dateShort = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" });
    const dateLong = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" });

    const parseDate = (value) => new Date(`${value}T00:00:00`);

    const formatCompactUsd = (value, signed = false) => {
        if (!Number.isFinite(value)) return "—";
        const prefix = value < 0 ? "−" : signed && value > 0 ? "+" : "";
        const absolute = Math.abs(value);
        const units = [
            [1e12, "T"],
            [1e9, "B"],
            [1e6, "M"],
            [1e3, "K"],
        ];
        const unit = units.find(([threshold]) => absolute >= threshold);
        if (!unit) return `${prefix}$${absolute.toFixed(0)}`;
        const scaled = absolute / unit[0];
        const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
        return `${prefix}$${scaled.toFixed(digits)}${unit[1]}`;
    };

    const niceStep = (range, targetTicks = 4) => {
        const rough = Math.max(range / targetTicks, Number.EPSILON);
        const power = 10 ** Math.floor(Math.log10(rough));
        const normalized = rough / power;
        const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
        return factor * power;
    };

    const scaleBounds = (values, includeZero) => {
        let minimum = Math.min(...values);
        let maximum = Math.max(...values);
        if (includeZero) {
            minimum = Math.min(0, minimum);
            maximum = Math.max(0, maximum);
        }
        if (minimum === maximum) {
            const fallback = Math.abs(minimum) * .08 || 1;
            minimum -= fallback;
            maximum += fallback;
        }
        const padding = (maximum - minimum) * .08;
        if (!includeZero || minimum < 0) minimum -= padding;
        if (!includeZero || maximum > 0) maximum += padding;
        const step = niceStep(maximum - minimum);
        const niceMinimum = includeZero && minimum >= 0 ? 0 : Math.floor(minimum / step) * step;
        const niceMaximum = includeZero && maximum <= 0 ? 0 : Math.ceil(maximum / step) * step;
        const ticks = [];
        for (let value = niceMinimum; value <= niceMaximum + step / 2; value += step) {
            ticks.push(Math.abs(value) < step / 1000 ? 0 : value);
        }
        return { minimum: niceMinimum, maximum: niceMaximum, ticks };
    };

    const createTooltip = (tooltip, item, previous, type) => {
        tooltip.replaceChildren();
        const heading = document.createElement("strong");
        heading.textContent = dateLong.format(parseDate(item.date));
        const values = document.createElement("dl");
        const rows = type === "area"
            ? [
                ["总供应量", formatCompactUsd(item.value)],
                ["日变化", previous ? formatCompactUsd(item.value - previous.value, true) : "—"],
            ]
            : [
                ["当日净流", formatCompactUsd(item.value, true)],
                ["方向", item.value > 0 ? "净流入" : item.value < 0 ? "净流出" : "零流量"],
            ];
        rows.forEach(([label, value]) => {
            const row = document.createElement("div");
            const term = document.createElement("dt");
            const description = document.createElement("dd");
            term.textContent = label;
            description.textContent = value;
            row.append(term, description);
            values.appendChild(row);
        });
        tooltip.append(heading, values);
        tooltip.hidden = false;
    };

    charts.forEach((stage) => {
        const canvas = stage.querySelector("canvas");
        const tooltip = stage.querySelector(".fund-chart-tooltip");
        const source = document.getElementById(stage.dataset.source);
        if (!canvas || !tooltip || !source) return;

        let data;
        try {
            data = JSON.parse(source.textContent).map((item) => ({
                date: item.date,
                value: Number(item.value),
            })).filter((item) => Number.isFinite(item.value));
        } catch (error) {
            return;
        }
        if (data.length < 2) return;

        const context = canvas.getContext("2d");
        const type = stage.dataset.fundChart;
        let hoverIndex = null;
        let geometry = null;

        canvas.tabIndex = 0;

        const render = () => {
            const rect = canvas.getBoundingClientRect();
            const width = Math.max(320, rect.width);
            const height = rect.height || 310;
            const ratio = Math.min(window.devicePixelRatio || 1, 2);
            canvas.width = Math.round(width * ratio);
            canvas.height = Math.round(height * ratio);
            context.setTransform(ratio, 0, 0, ratio, 0, 0);
            context.clearRect(0, 0, width, height);
            context.lineWidth = 1;
            context.font = "11px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";

            const margins = { top: 16, right: 18, bottom: 38, left: width < 520 ? 58 : 70 };
            const plot = {
                left: margins.left,
                right: width - margins.right,
                top: margins.top,
                bottom: height - margins.bottom,
            };
            plot.width = plot.right - plot.left;
            plot.height = plot.bottom - plot.top;

            const scale = scaleBounds(data.map((item) => item.value), type === "bars");
            const yFor = (value) => plot.bottom - ((value - scale.minimum) / (scale.maximum - scale.minimum)) * plot.height;
            const xFor = (index) => type === "area"
                ? plot.left + (index / (data.length - 1)) * plot.width
                : plot.left + ((index + .5) / data.length) * plot.width;

            context.textAlign = "right";
            context.textBaseline = "middle";
            scale.ticks.forEach((tick) => {
                const y = yFor(tick);
                context.strokeStyle = tick === 0 && type === "bars" ? "#bcc5d2" : colors.grid;
                context.setLineDash(tick === 0 && type === "bars" ? [] : [3, 4]);
                context.beginPath();
                context.moveTo(plot.left, y);
                context.lineTo(plot.right, y);
                context.stroke();
                context.setLineDash([]);
                context.fillStyle = colors.axis;
                context.fillText(formatCompactUsd(tick), plot.left - 9, y);
            });

            const labelIndexes = Array.from(new Set([0, .25, .5, .75, 1].map((ratioValue) =>
                Math.round((data.length - 1) * ratioValue)
            )));
            context.textBaseline = "top";
            labelIndexes.forEach((index, position) => {
                const x = xFor(index);
                context.textAlign = position === 0 ? "left" : position === labelIndexes.length - 1 ? "right" : "center";
                context.fillStyle = colors.axis;
                context.fillText(dateShort.format(parseDate(data[index].date)), x, plot.bottom + 12);
            });

            if (type === "area") {
                const gradient = context.createLinearGradient(0, plot.top, 0, plot.bottom);
                gradient.addColorStop(0, "rgba(56, 103, 216, .24)");
                gradient.addColorStop(1, "rgba(56, 103, 216, .015)");
                context.beginPath();
                data.forEach((item, index) => {
                    const x = xFor(index);
                    const y = yFor(item.value);
                    if (index === 0) context.moveTo(x, y);
                    else context.lineTo(x, y);
                });
                context.lineTo(xFor(data.length - 1), plot.bottom);
                context.lineTo(xFor(0), plot.bottom);
                context.closePath();
                context.fillStyle = gradient;
                context.fill();

                context.beginPath();
                data.forEach((item, index) => {
                    const x = xFor(index);
                    const y = yFor(item.value);
                    if (index === 0) context.moveTo(x, y);
                    else context.lineTo(x, y);
                });
                context.strokeStyle = colors.blue;
                context.lineWidth = 2.5;
                context.lineJoin = "round";
                context.lineCap = "round";
                context.stroke();
            } else {
                const zeroY = yFor(0);
                const band = plot.width / data.length;
                const barWidth = Math.max(5, Math.min(34, band * .6));
                data.forEach((item, index) => {
                    const x = xFor(index) - barWidth / 2;
                    const valueY = yFor(item.value);
                    const y = Math.min(zeroY, valueY);
                    const barHeight = Math.max(1.5, Math.abs(zeroY - valueY));
                    context.fillStyle = item.value > 0 ? colors.green : item.value < 0 ? colors.red : "#98a3b4";
                    context.fillRect(x, y, barWidth, barHeight);
                });
            }

            if (hoverIndex !== null) {
                const item = data[hoverIndex];
                const x = xFor(hoverIndex);
                const y = yFor(item.value);
                context.strokeStyle = colors.guide;
                context.lineWidth = 1;
                context.setLineDash([4, 4]);
                context.beginPath();
                context.moveTo(x, plot.top);
                context.lineTo(x, plot.bottom);
                context.stroke();
                context.setLineDash([]);
                context.beginPath();
                context.arc(x, y, 5, 0, Math.PI * 2);
                context.fillStyle = colors.white;
                context.fill();
                context.strokeStyle = type === "area" ? colors.blue : item.value >= 0 ? colors.green : colors.red;
                context.lineWidth = 2.5;
                context.stroke();
            }

            geometry = { plot, xFor, yFor };
        };

        const showTooltip = (index) => {
            hoverIndex = Math.max(0, Math.min(data.length - 1, index));
            render();
            const item = data[hoverIndex];
            createTooltip(tooltip, item, data[hoverIndex - 1], type);
            const x = geometry.xFor(hoverIndex);
            const y = geometry.yFor(item.value);
            tooltip.style.left = `${x}px`;
            tooltip.style.top = `${y}px`;
            tooltip.classList.toggle("is-left", x + tooltip.offsetWidth + 24 > stage.clientWidth);
            tooltip.classList.toggle("is-below", y < tooltip.offsetHeight + 18);
        };

        const hideTooltip = () => {
            hoverIndex = null;
            tooltip.hidden = true;
            render();
        };

        const showAtPointer = (event) => {
            if (!geometry) return;
            const rect = canvas.getBoundingClientRect();
            const x = event.clientX - rect.left;
            if (x < geometry.plot.left || x > geometry.plot.right) {
                hideTooltip();
                return;
            }
            const relative = (x - geometry.plot.left) / geometry.plot.width;
            const index = type === "area"
                ? Math.round(relative * (data.length - 1))
                : Math.floor(relative * data.length);
            showTooltip(index);
        };

        canvas.addEventListener("pointermove", showAtPointer);
        canvas.addEventListener("pointerdown", showAtPointer);
        canvas.addEventListener("pointerleave", hideTooltip);
        canvas.addEventListener("focus", () => showTooltip(data.length - 1));
        canvas.addEventListener("blur", hideTooltip);
        canvas.addEventListener("keydown", (event) => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            event.preventDefault();
            const current = hoverIndex === null ? data.length - 1 : hoverIndex;
            showTooltip(current + (event.key === "ArrowRight" ? 1 : -1));
        });

        render();
        if ("ResizeObserver" in window) {
            new ResizeObserver(render).observe(stage);
        } else {
            window.addEventListener("resize", render);
        }
    });
})();
