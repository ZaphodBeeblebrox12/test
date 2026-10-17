(() => {
  document.addEventListener("click", event => {
    const tab = event.target.closest("[data-tab-name]");
    if (tab) {
      const root = tab.closest("[data-learning-tabs]");
      if (!root) return;
      root.querySelectorAll("[data-tab-name]").forEach(button => {
        const active = button === tab;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", String(active));
      });
      root.querySelectorAll("[data-tab-panel]").forEach(panel => {
        panel.hidden = panel.dataset.tabPanel !== tab.dataset.tabName;
      });
    }
  });

  document.addEventListener("input", event => {
    if (!event.target.matches("[data-outline-search]")) return;
    const query = event.target.value.trim().toLocaleLowerCase();
    document.querySelectorAll("[data-outline-item]").forEach(item => {
      item.hidden = query !== "" && !item.textContent.toLocaleLowerCase().includes(query);
    });
  });
})();
