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
})();
