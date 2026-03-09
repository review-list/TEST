(() => {
  const q = document.getElementById("q");
  const grid = document.getElementById("grid");
  if (!q || !grid) return;

  const items = Array.from(grid.querySelectorAll(".work"));

  const norm = (s) => (s || "").toString().toLowerCase();

  q.addEventListener("input", () => {
    const needle = norm(q.value).trim();
    for (const el of items) {
      const title = norm(el.dataset.title || el.textContent);
      const hit = needle === "" || title.includes(needle);
      el.style.display = hit ? "" : "none";
    }
  });
})();
