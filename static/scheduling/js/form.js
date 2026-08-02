document.querySelectorAll("#schedule-config-form, #schedule-run-form, #news-schedule-config-form, #news-schedule-run-form").forEach((form) => {
    form.addEventListener("submit", () => {
        const button = form.querySelector("button[type='submit']");
        if (!button || button.disabled) return;
        button.disabled = true;
        button.textContent = button.dataset.waitingText || "处理中…";
    });
});

document.querySelectorAll("[data-dialog-target]").forEach((button) => {
    button.addEventListener("click", () => {
        const dialog = document.getElementById(button.dataset.dialogTarget);
        if (dialog && !dialog.open) dialog.showModal();
    });
});

document.querySelectorAll("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
});

document.querySelectorAll("dialog.schedule-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
        if (event.target === dialog) dialog.close();
    });
    if (dialog.hasAttribute("data-open-dialog")) dialog.showModal();
});
