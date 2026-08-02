document.addEventListener("DOMContentLoaded", () => {
    const root = document.documentElement;
    const toggle = document.querySelector("[data-sidebar-toggle]");
    const storageKey = "market-evidence-sidebar-collapsed";

    const syncSidebarToggle = () => {
        if (!toggle) return;
        const collapsed = root.classList.contains("sidebar-collapsed");
        const label = collapsed ? "展开侧边栏" : "收起侧边栏";
        toggle.setAttribute("aria-expanded", String(!collapsed));
        toggle.setAttribute("aria-label", label);
        toggle.setAttribute("title", label);
    };

    if (toggle) {
        syncSidebarToggle();
        toggle.addEventListener("click", () => {
            root.classList.toggle("sidebar-collapsed");
            const collapsed = root.classList.contains("sidebar-collapsed");
            try {
                localStorage.setItem(storageKey, String(collapsed));
            } catch (error) {
                // The toggle remains usable even when storage is unavailable.
            }
            syncSidebarToggle();
            window.dispatchEvent(new Event("resize"));
        });
    }

    const navigation = document.querySelector(".navigation");
    if (!navigation) {
        return;
    }

    const groups = Array.from(navigation.querySelectorAll("details.nav-group"));
    groups.forEach((group) => {
        group.addEventListener("toggle", () => {
            if (!group.open) {
                return;
            }
            groups.forEach((otherGroup) => {
                if (otherGroup !== group) {
                    otherGroup.open = false;
                }
            });
        });
    });
});
