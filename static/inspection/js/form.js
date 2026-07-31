document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("#inspection-form");
    const submitButton = document.querySelector("#inspection-submit");

    if (!form || !submitButton) {
        return;
    }

    form.addEventListener("submit", () => {
        submitButton.disabled = true;
        submitButton.textContent = submitButton.dataset.waitingText;
        submitButton.setAttribute("aria-busy", "true");
    });
});
