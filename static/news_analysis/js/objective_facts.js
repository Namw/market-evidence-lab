document.querySelectorAll("[data-objective-run-form]").forEach((form) => {
    form.addEventListener("submit", () => {
        const button = form.querySelector('button[type="submit"]');
        if (!button || button.disabled) {
            return;
        }
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.textContent = button.dataset.runningLabel || "执行中…";
    });
});
