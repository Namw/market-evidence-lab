document.querySelectorAll("#schedule-config-form, #schedule-run-form").forEach((form) => {
    form.addEventListener("submit", () => {
        const button = form.querySelector("button[type='submit']");
        if (!button || button.disabled) return;
        button.disabled = true;
        button.textContent = button.dataset.waitingText || "处理中…";
    });
});
