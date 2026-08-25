(() => {
    const root = document.querySelector("[data-microstructure-page]");
    const assistant = document.querySelector("[data-microstructure-assistant]");
    if (!root || !assistant) return;

    const panel = assistant.querySelector("[data-assistant-panel]");
    const openButton = assistant.querySelector("[data-assistant-open]");
    const closeButton = assistant.querySelector("[data-assistant-close]");
    const form = assistant.querySelector("[data-assistant-form]");
    const input = assistant.querySelector("[data-assistant-input]");
    const messages = assistant.querySelector("[data-assistant-messages]");
    const sendButton = assistant.querySelector("[data-assistant-send]");
    const csrfToken = form.querySelector("input[name='csrfmiddlewaretoken']")?.value;

    function toggle(open) {
        panel.hidden = !open;
        openButton.hidden = open;
        openButton.setAttribute("aria-expanded", String(open));
        if (open) requestAnimationFrame(() => input.focus());
    }

    function addMessage(text, kind) {
        const item = document.createElement("div");
        item.className = `assistant-message is-${kind}`;
        item.textContent = text;
        messages.append(item);
        messages.scrollTop = messages.scrollHeight;
        return item;
    }

    async function ask(question) {
        const cleaned = question.trim();
        if (!cleaned) return;
        addMessage(cleaned, "user");
        input.value = "";
        input.style.height = "auto";
        sendButton.disabled = true;
        const pending = addMessage("正在根据页面采集数据计算…", "bot is-pending");
        try {
            const body = new URLSearchParams({ question: cleaned });
            const response = await fetch(root.dataset.assistantUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-CSRFToken": csrfToken || "",
                },
                body,
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || "请求失败");
            pending.textContent = payload.answer;
            pending.classList.remove("is-pending");
        } catch (error) {
            pending.textContent = "暂时无法读取数据，请稍后重试。";
            pending.classList.remove("is-pending");
            pending.classList.add("is-error");
        } finally {
            sendButton.disabled = false;
        }
    }

    openButton.addEventListener("click", () => toggle(true));
    closeButton.addEventListener("click", () => toggle(false));
    form.addEventListener("submit", (event) => { event.preventDefault(); ask(input.value); });
    assistant.querySelectorAll("[data-assistant-question]").forEach((button) => {
        button.addEventListener("click", () => ask(button.textContent || ""));
    });
    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = `${Math.min(input.scrollHeight, 96)}px`;
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
    });
})();
