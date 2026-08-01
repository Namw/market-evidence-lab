document.addEventListener("DOMContentLoaded", () => {
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
