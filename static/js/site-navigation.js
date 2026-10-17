(() => {
  const sidebar = document.getElementById("siteSidebar");
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const scrim = document.querySelector("[data-sidebar-scrim]");
  if (!sidebar || !toggle || !scrim) return;

  const close = () => {
    sidebar.classList.remove("show");
    toggle.setAttribute("aria-expanded", "false");
    scrim.hidden = true;
    document.body.classList.remove("tt-nav-open");
  };
  const open = () => {
    sidebar.classList.add("show");
    toggle.setAttribute("aria-expanded", "true");
    scrim.hidden = false;
    document.body.classList.add("tt-nav-open");
  };
  toggle.addEventListener("click", () => {
    if (toggle.getAttribute("aria-expanded") === "true") close();
    else open();
  });
  scrim.addEventListener("click", close);
  sidebar.querySelectorAll("a").forEach(link => link.addEventListener("click", close));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") close();
  });
})();
