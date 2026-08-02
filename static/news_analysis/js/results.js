document.addEventListener("DOMContentLoaded", () => {
    const dialog = document.querySelector("#news-content-dialog");
    if (!dialog) return;

    const title = dialog.querySelector("#news-dialog-title");
    const conclusion = dialog.querySelector("#news-dialog-conclusion");
    const status = dialog.querySelector("#news-content-status");
    const body = dialog.querySelector("#news-content-body");
    const sourceLink = dialog.querySelector("#news-source-link");

    const closeDialog = () => dialog.close();
    dialog.querySelectorAll("[data-dialog-close]").forEach((button) => {
        button.addEventListener("click", closeDialog);
    });
    dialog.addEventListener("click", (event) => {
        if (event.target === dialog) closeDialog();
    });

    document.querySelectorAll(".news-detail-button").forEach((button) => {
        button.addEventListener("click", async () => {
            title.textContent = button.dataset.title || "新闻详情";
            conclusion.textContent = button.dataset.conclusion || "";
            conclusion.className = `conclusion-badge conclusion-${
                button.dataset.conclusion === "利好" ? "bullish" :
                button.dataset.conclusion === "利空" ? "bearish" : "unclear"
            }`;
            body.textContent = "";
            status.textContent = "正在连接新闻来源…";
            sourceLink.href = button.dataset.sourceUrl || "#";
            dialog.showModal();
            try {
                const response = await fetch(button.dataset.contentUrl, {
                    headers: {Accept: "application/json"},
                });
                if (!response.ok) throw new Error("content request failed");
                const payload = await response.json();
                body.textContent = payload.content || "暂时没有可显示的正文内容。";
                sourceLink.href = payload.source_url || sourceLink.href;
                status.textContent = payload.origin === "source"
                    ? "已连接新闻来源，以下为源头正文。"
                    : "源头正文暂时无法读取，以下为已保存的正文摘要。";
            } catch (error) {
                status.textContent = "正文读取失败，请通过下方来源地址打开原文。";
                body.textContent = "暂时没有可显示的正文内容。";
            }
        });
    });
});
