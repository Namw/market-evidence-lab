const marketScanForm = document.querySelector("#market-scan-form");

if (marketScanForm) {
    marketScanForm.addEventListener("submit", () => {
        const button = marketScanForm.querySelector("button[type='submit']");
        if (!button || button.disabled) return;
        button.disabled = true;
        button.textContent = button.dataset.waitingText || "巡检中，请稍候…";
    });
}
