(() => {
    const root = document.querySelector("[data-deribit-options]");
    if (!root) return;

    const buttons = Array.from(root.querySelectorAll("[data-oi-view]"));
    const rows = Array.from(root.querySelectorAll(".deribit-expiry-row"));

    const setView = (view) => {
        buttons.forEach((button) => {
            button.classList.toggle("is-selected", button.dataset.oiView === view);
        });
        rows.forEach((row) => {
            const call = row.querySelector(".deribit-call-fill");
            const put = row.querySelector(".deribit-put-fill");
            const value = row.querySelector("strong");
            if (!call || !put || !value) return;
            if (view === "call") {
                call.style.width = `${call.dataset.singleWidth}%`;
                put.style.width = "0%";
                value.textContent = row.dataset.callValue;
            } else if (view === "put") {
                call.style.width = "0%";
                put.style.width = `${put.dataset.singleWidth}%`;
                value.textContent = row.dataset.putValue;
            } else {
                call.style.width = `${call.dataset.bothWidth}%`;
                put.style.width = `${put.dataset.bothWidth}%`;
                value.textContent = row.dataset.totalValue;
            }
        });
    };

    buttons.forEach((button) => {
        button.addEventListener("click", () => setView(button.dataset.oiView));
    });

    const svgNamespace = "http://www.w3.org/2000/svg";

    root.querySelectorAll("[data-hover-chart]").forEach((chart) => {
        const svg = chart.querySelector("svg");
        const tooltip = chart.querySelector(".deribit-chart-tooltip");
        const points = Array.from(chart.querySelectorAll("[data-chart-point]"));
        if (!svg || !tooltip || !points.length) return;

        const bounds = {
            left: Number(svg.dataset.chartLeft),
            right: Number(svg.dataset.chartRight),
            top: Number(svg.dataset.chartTop),
            bottom: Number(svg.dataset.chartBottom),
        };
        const verticalGuide = document.createElementNS(svgNamespace, "line");
        const horizontalGuide = document.createElementNS(svgNamespace, "line");
        const focusPoint = document.createElementNS(svgNamespace, "circle");

        verticalGuide.classList.add("deribit-hover-guide");
        horizontalGuide.classList.add("deribit-hover-guide");
        focusPoint.classList.add("deribit-hover-point");
        verticalGuide.setAttribute("y1", bounds.top);
        verticalGuide.setAttribute("y2", bounds.bottom);
        horizontalGuide.setAttribute("x1", bounds.left);
        horizontalGuide.setAttribute("x2", bounds.right);
        focusPoint.setAttribute("r", "6");
        [verticalGuide, horizontalGuide, focusPoint].forEach((element) => {
            element.setAttribute("aria-hidden", "true");
            element.setAttribute("hidden", "");
            svg.appendChild(element);
        });

        const hideHover = () => {
            tooltip.hidden = true;
            verticalGuide.setAttribute("hidden", "");
            horizontalGuide.setAttribute("hidden", "");
            focusPoint.setAttribute("hidden", "");
        };

        svg.addEventListener("pointermove", (event) => {
            const rect = svg.getBoundingClientRect();
            const viewBox = svg.viewBox.baseVal;
            const cursor = {
                x: viewBox.x + ((event.clientX - rect.left) / rect.width) * viewBox.width,
                y: viewBox.y + ((event.clientY - rect.top) / rect.height) * viewBox.height,
            };
            if (
                cursor.x < bounds.left || cursor.x > bounds.right ||
                cursor.y < bounds.top || cursor.y > bounds.bottom
            ) {
                hideHover();
                return;
            }

            const nearest = points.reduce((best, point) => {
                const dx = Number(point.getAttribute("cx")) - cursor.x;
                const dy = Number(point.getAttribute("cy")) - cursor.y;
                const distance = dx * dx + dy * dy;
                return !best || distance < best.distance ? { point, distance } : best;
            }, null).point;
            const x = Number(nearest.getAttribute("cx"));
            const y = Number(nearest.getAttribute("cy"));

            verticalGuide.setAttribute("x1", x);
            verticalGuide.setAttribute("x2", x);
            horizontalGuide.setAttribute("y1", y);
            horizontalGuide.setAttribute("y2", y);
            focusPoint.setAttribute("cx", x);
            focusPoint.setAttribute("cy", y);
            focusPoint.style.stroke = getComputedStyle(nearest).stroke;
            verticalGuide.removeAttribute("hidden");
            horizontalGuide.removeAttribute("hidden");
            focusPoint.removeAttribute("hidden");

            tooltip.replaceChildren();
            const heading = document.createElement("strong");
            heading.textContent = nearest.dataset.series;
            const values = document.createElement("dl");
            [
                [svg.dataset.xLabel, nearest.dataset.xValue],
                [svg.dataset.yLabel, nearest.dataset.yValue],
            ].forEach(([label, value]) => {
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

            const chartRect = chart.getBoundingClientRect();
            const pointLeft = rect.left - chartRect.left + ((x - viewBox.x) / viewBox.width) * rect.width;
            const pointTop = rect.top - chartRect.top + ((y - viewBox.y) / viewBox.height) * rect.height;
            const tooltipWidth = tooltip.offsetWidth;
            const placeOnLeft = pointLeft + tooltipWidth + 20 > chart.clientWidth;
            const placeBelow = pointTop < tooltip.offsetHeight + 20;
            tooltip.style.left = `${pointLeft + (placeOnLeft ? -10 : 10)}px`;
            tooltip.style.top = `${pointTop + (placeBelow ? 12 : -12)}px`;
            tooltip.classList.toggle("is-left", placeOnLeft);
            tooltip.classList.toggle("is-below", placeBelow);
        });

        svg.addEventListener("pointerleave", hideHover);
    });
})();
