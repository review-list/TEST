(() => {
  const KEY_FAV = "catalog:favs:v1";
  const KEY_REC = "catalog:recents:v1";
  const KEY_INFO = "catalog:workinfo:v1";

  const load = (k, defv) => {
    try { const v = JSON.parse(localStorage.getItem(k) || ""); return (v ?? defv); } catch { return defv; }
  };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };

  const getIdFromHref = (href) => {
    try {
      const u = new URL(href, location.href);
      const m = u.pathname.match(/\/works\/([^\/]+)\//);
      return m ? m[1] : null;
    } catch { return null; }
  };

  const getIdFromPath = () => {
    const m = location.pathname.match(/\/works\/([^\/]+)\//);
    return m ? m[1] : null;
  };

  const getInfoFromCard = (a) => {
    const id = getIdFromHref(a.href);
    if (!id) return null;
    const title = (a.querySelector(".work-title")?.textContent || a.getAttribute("aria-label") || "").trim();
    const img = a.querySelector("img")?.getAttribute("src") || "";
    const href = a.getAttribute("href") || "";
    return { id, title, img, href };
  };

  const getInfoFromDetail = () => {
    const id = getIdFromPath();
    if (!id) return null;
    const title = (document.querySelector(".work-title--detail")?.textContent || document.title || "").trim();
    const img = document.querySelector(".hero--work img")?.getAttribute("src") || "";
    const href = location.pathname.startsWith("/") ? location.pathname : ("/" + location.pathname);
    return { id, title, img, href };
  };

  const state = {
    favs: load(KEY_FAV, []),
    rec: load(KEY_REC, []),
    info: load(KEY_INFO, {}),
  };

  const persist = () => {
    save(KEY_FAV, state.favs);
    save(KEY_REC, state.rec);
    save(KEY_INFO, state.info);
  };

  const upsertInfo = (info) => {
    if (!info || !info.id) return;
    state.info[info.id] = {
      id: info.id,
      title: info.title || state.info[info.id]?.title || info.id,
      img: info.img || state.info[info.id]?.img || "",
      href: info.href || state.info[info.id]?.href || ("/works/" + info.id + "/"),
    };
  };

  const isFav = (id) => state.favs.includes(id);

  const toggleFav = (id) => {
    if (!id) return;
    const i = state.favs.indexOf(id);
    if (i >= 0) state.favs.splice(i, 1);
    else state.favs.unshift(id);
    // keep size reasonable
    state.favs = state.favs.slice(0, 300);
    persist();
  };

  const addRecent = (id) => {
    if (!id) return;
    state.rec = state.rec.filter(x => x !== id);
    state.rec.unshift(id);
    state.rec = state.rec.slice(0, 80);
    persist();
  };

  // ----- UI: star buttons -----
  const attachFavButtonToCard = (a) => {
    if (!a || a.__favAttached) return;
    a.__favAttached = true;
    const info = getInfoFromCard(a);
    if (!info) return;
    upsertInfo(info);
    persist();

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fav-btn";
    btn.setAttribute("aria-label", "お気に入り");
    const sync = () => {
      const on = isFav(info.id);
      btn.classList.toggle("is-on", on);
      btn.textContent = on ? "★" : "☆";
    };
    sync();

    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      toggleFav(info.id);
      sync();
      updateBar();
      if (drawer.classList.contains("is-open")) renderList(activeTab);
    });

    a.appendChild(btn);
  };

  const attachFavButtonToDetail = () => {
    const info = getInfoFromDetail();
    if (!info) return;
    upsertInfo(info);
    addRecent(info.id);

    // add button near title (does not shift to top)
    const h1 = document.querySelector(".work-title--detail");
    if (h1 && !document.querySelector(".fav-detail-btn")) {
      const wrap = document.createElement("div");
      wrap.style.display = "flex";
      wrap.style.alignItems = "center";
      wrap.style.gap = "10px";
      // move title into wrapper
      const parent = h1.parentElement;
      if (parent) {
        parent.insertBefore(wrap, h1);
        wrap.appendChild(h1);
      }
      const b = document.createElement("button");
      b.type = "button";
      b.className = "mybar-btn fav-detail-btn";
      const sync = () => {
        const on = isFav(info.id);
        b.textContent = on ? "★ お気に入り解除" : "☆ お気に入り";
      };
      sync();
      b.addEventListener("click", () => {
        toggleFav(info.id);
        sync();
        updateBar();
        if (drawer.classList.contains("is-open")) renderList(activeTab);
      });
      wrap.appendChild(b);
    }

    persist();
    updateBar();
  };

  // initial cards
  const attachAllCards = () => {
    document.querySelectorAll("a.work-card").forEach(attachFavButtonToCard);
  };

  // watch search results dynamic
  const observeDynamicCards = () => {
    const host = document.getElementById("results");
    if (!host) return;
    const mo = new MutationObserver(() => attachAllCards());
    mo.observe(host, { childList: true, subtree: true });
  };

  
  // ----- Shorts feed (vertical snap) -----
  const initShortsFeed = () => {
    const feed = document.getElementById("shortsFeed");
    if (!feed) return;

    const items = Array.from(feed.querySelectorAll(".short-item"));
    if (!items.length) return;

    const ensureVideoSrc = (v) => {
      if (!v) return;
      if (v.getAttribute("src")) return;
      const src = v.getAttribute("data-src");
      if (src) v.setAttribute("src", src);
    };

    const ensureFrameSrc = (f) => {
      if (!f) return;
      const cur = f.getAttribute("src") || "";
      if (cur && cur !== "about:blank") return;
      const src = f.getAttribute("data-src");
      if (src) f.setAttribute("src", src);
    };

    const stopItem = (it) => {
      const v = it.querySelector("video");
      if (v) {
        try { v.pause(); } catch {}
        return;
      }
      const f = it.querySelector("iframe");
      if (f) {
        // stop playback by unloading (best-effort)
        try { f.setAttribute("src", "about:blank"); } catch {}
      }
    };

    const activate = (it) => {
      items.forEach((x) => { if (x !== it) stopItem(x); });

      const v = it.querySelector("video");
      if (v) {
        ensureVideoSrc(v);
        v.muted = true; // autoplay requires muted
        v.loop = true;
        // autoplay (best-effort). If blocked, show controls so user can start.
        const p = v.play();
        if (p && typeof p.catch === "function") {
          p.catch(() => { try { v.controls = true; } catch {} });
        }
        return;
      }

      const f = it.querySelector("iframe");
      if (f) {
        ensureFrameSrc(f);
      }
    };

    // tap to toggle play/pause (except clicking links/buttons)
    feed.addEventListener("click", (ev) => {
      const t = ev.target;
      if (t && t.closest && t.closest("a,button")) return;

      const item = t && t.closest ? t.closest(".short-item") : null;
      if (!item) return;

      const v = item.querySelector("video");
      if (v) {
        ensureVideoSrc(v);
        if (v.paused) {
          v.muted = true;
          const p = v.play();
          if (p && typeof p.catch === "function") p.catch(() => { try { v.controls = true; } catch {} });
        } else {
          try { v.pause(); } catch {}
        }
        return;
      }

      const f = item.querySelector("iframe");
      if (f) {
        // iframe は中身次第なので、次へスクロール
        const idx = items.indexOf(item);
        const next = items[idx + 1];
        if (next) next.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });

    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting && e.intersectionRatio >= 0.6) {
          activate(e.target);
        }
      });
    }, { threshold: [0.0, 0.6, 1.0] });

    items.forEach((it) => io.observe(it));
    activate(items[0]);
  };

// ----- MyBar -----


  const mybar = document.createElement("div");
  mybar.className = "mybar";
  mybar.innerHTML = `
    <div class="mybar__inner">
      <div class="mybar__btns">
        <button type="button" class="mybar-btn" data-tab="favs">★ お気に入り <span class="mybar-pill" id="favCount">0</span></button>
        <button type="button" class="mybar-btn" data-tab="rec">🕒 最近見た <span class="mybar-pill" id="recCount">0</span></button>
      </div>
      <button type="button" class="mybar-btn" id="toTop">↑ 上へ</button>
    </div>
  `;

  const drawer = document.createElement("div");
  drawer.className = "mybar-drawer";
  drawer.innerHTML = `
    <div class="mybar-backdrop" data-close="1"></div>
    <div class="mybar-panel" role="dialog" aria-modal="true" aria-label="マイメニュー">
      <div class="mybar-panel__head">
        <div class="mybar-tabs">
          <button type="button" class="mybar-tab is-active" data-tab="favs">お気に入り</button>
          <button type="button" class="mybar-tab" data-tab="rec">最近見た</button>
        </div>
        <button type="button" class="mybar-close" data-close="1">閉じる</button>
      </div>
      <div class="mybar-list" id="mybarList"></div>
    </div>
  `;

  document.addEventListener("DOMContentLoaded", () => {
    document.body.appendChild(mybar);
    document.body.appendChild(drawer);

    attachAllCards();
    observeDynamicCards();
    attachFavButtonToDetail();

    initShortsFeed();

    mybar.querySelectorAll("button[data-tab]").forEach(btn => {
      btn.addEventListener("click", () => openDrawer(btn.getAttribute("data-tab")));
    });
    mybar.querySelector("#toTop").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

    drawer.addEventListener("click", (ev) => {
      const t = ev.target;
      if (t && t.getAttribute && t.getAttribute("data-close") === "1") closeDrawer();
      if (t && t.classList && t.classList.contains("mybar-tab")) openDrawer(t.getAttribute("data-tab"));
    });

    updateBar();
  });

  let activeTab = "favs";
  const openDrawer = (tab) => {
    activeTab = tab || "favs";
    drawer.classList.add("is-open");
    drawer.querySelectorAll(".mybar-tab").forEach(b => b.classList.toggle("is-active", b.getAttribute("data-tab") === activeTab));
    renderList(activeTab);
  };
  const closeDrawer = () => drawer.classList.remove("is-open");

  const renderList = (tab) => {
    const list = drawer.querySelector("#mybarList");
    const ids = (tab === "rec") ? state.rec : state.favs;
    if (!ids.length) {
      list.innerHTML = `<div style="color:rgba(255,255,255,.7);padding:6px 2px;">まだありません</div>`;
      return;
    }
    const html = ids.map(id => {
      const info = state.info[id] || { id, title: id, img: "", href: "/works/" + id + "/" };
      const img = info.img ? `<img src="${escapeHtml(info.img)}" alt="">` : `<div style="width:72px;height:54px;border-radius:10px;background:rgba(255,255,255,.06)"></div>`;
      return `
        <a class="mybar-item" href="${escapeHtml(info.href)}">
          ${img}
          <div>
            <div class="mybar-item__title">${escapeHtml(info.title || info.id)}</div>
            <div class="mybar-item__meta">ID: ${escapeHtml(info.id)}</div>
          </div>
        </a>
      `;
    }).join("");
    list.innerHTML = html;
  };

  const updateBar = () => {
    const fav = document.getElementById("favCount");
    const rec = document.getElementById("recCount");
    if (fav) fav.textContent = String(state.favs.length);
    if (rec) rec.textContent = String(state.rec.length);
  };

  const escapeHtml = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);

})();