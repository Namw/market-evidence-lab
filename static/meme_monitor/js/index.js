(() => {
    const root = document.querySelector("[data-refresh-seconds]");
    if (!root) return;

    const initial = Number(root.dataset.refreshSeconds || 30);
    const output = root.querySelector("[data-refresh-countdown]");
    const button = root.querySelector("[data-refresh-now]");
    let remaining = initial;

    button?.addEventListener("click", () => window.location.reload());
    window.setInterval(() => {
        if (document.hidden) return;
        remaining -= 1;
        if (remaining <= 0) {
            window.location.reload();
            return;
        }
        if (output) output.textContent = `${remaining} 秒`;
    }, 1000);
})();
