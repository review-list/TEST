// shorts_video.js (v1.6)
// Changes:
// - "お気に入り" ボタンをショートに追加（localStorage: catalog:favs:v1）
// - ナビのホーム絵文字を削除（ホーム）
// - 左メニューの「Review Catalog」等の見出しは生成しない（ホームのみ）
// - 「拡大」は新しいウィンドウを開かず、モーダルで拡大（再生位置維持：videoノード移動）

(() => {
  "use strict";

  const q = (sel, root = document) => root.querySelector(sel);
  const qa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ----- Favorites (shared with existing site.js if it uses same key) -----
  const FAV_KEY = "catalog:favs:v1";

  const loadFavSet = () => {
    try {
      const raw = localStorage.getItem(FAV_KEY);
      if (!raw) return new Set();
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) return new Set(arr.map(String));
    } catch {}
    return new Set();
  };

  const saveFavSet = (set) => {
    try {
      localStorage.setItem(FAV_KEY, JSON.stringify(Array.from(set)));
    } catch {}
  };

  const toggleFav = (workId) => {
    const id = String(workId || "");
    if (!id) return false;
    const set = loadFavSet();
    if (set.has(id)) set.delete(id);
    else set.add(id);
    saveFavSet(set);
    return set.has(id);
  };

  const isFav = (workId) => {
    const id = String(workId || "");
    if (!id) return false;
    const set = loadFavSet();
    return set.has(id);
  };

  const updateFavButton = (btn, on) => {
    if (!btn) return;
    btn.classList.toggle("is-on", !!on);
    btn.textContent = on ? "★" : "☆";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "お気に入り解除" : "お気に入り";
  };

  // ----- DMM/FANZA MP4 helpers -----
  const parseCidFromSampleUrl = (u) => {
    if (!u) return "";
    const s = String(u);
    let m = s.match(/cid=([^/]+)/);
    if (m && m[1]) return m[1];
    m = s.match(/[?&]cid=([^&]+)/);
    if (m && m[1]) return m[1];
    return "";
  };

  const buildMp4Candidates = (cid) => {
    if (!cid) return [];
    const c = String(cid);
    const first = c.slice(0, 1);
    const first3 = c.slice(0, 3);
    const suffixes = ["_dmb_w.mp4", "_mhb_w.mp4", "_dm_w.mp4", "_sm_w.mp4"];
    const hosts = [
      "https://cc3001.dmm.co.jp/litevideo/freepv/",
      "https://cc3002.dmm.co.jp/litevideo/freepv/",
    ];
    const list = [];
    for (const host of hosts) {
      for (const suf of suffixes) list.push(`${host}${first}/${first3}/${c}/${c}${suf}`);
    }
    return list;
  };

  const trySetVideoSrc = (video, urls, onFail) => {
    const list = (urls || []).filter(Boolean);
    let i = 0;

    const cleanup = () => {
      video.removeEventListener("error", onError);
      video.removeEventListener("loadeddata", onLoaded);
    };
    const onLoaded = () => cleanup();

    const setSrc = (u) => {
      try {
        video.src = u;
        video.load();
      } catch {
        onError();
      }
    };

    const onError = () => {
      i += 1;
      if (i >= list.length) {
        cleanup();
        if (onFail) onFail();
        return;
      }
      setSrc(list[i]);
    };

    video.addEventListener("loadeddata", onLoaded, { once: true });
    video.addEventListener("error", onError);

    if (!list.length) {
      cleanup();
      if (onFail) onFail();
      return;
    }
    setSrc(list[0]);
  };

  // ----- Modal (in-place expand) -----
  const ensureModal = () => {
    let modal = q(".sv-modal");
    if (modal) return modal;

    modal = document.createElement("div");
    modal.className = "sv-modal";
    modal.innerHTML = `
      <div class="sv-modal-inner" role="dialog" aria-modal="true" aria-label="拡大表示">
        <button class="sv-modal-close" type="button">閉じる</button>
        <div class="sv-modal-media"></div>
      </div>
    `;
    document.body.appendChild(modal);

    const close = () => closeModal();

    modal.addEventListener("click", (e) => {
      const inner = q(".sv-modal-inner", modal);
      if (inner && !inner.contains(e.target)) close();
    });
    q(".sv-modal-close", modal)?.addEventListener("click", close);

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && modal.classList.contains("is-open")) close();
    });

    return modal;
  };

  let moved = null; // { video, parent, nextSibling, controls, muted, loop, wasPlaying }

  const closeModal = () => {
    const modal = q(".sv-modal");
    if (!modal) return;

    // restore moved video (keep time)
    if (moved && moved.video) {
      const v = moved.video;
      const media = q(".sv-modal-media", modal);

      try { v.pause(); } catch {}

      try {
        if (media && media.contains(v)) media.removeChild(v);
      } catch {}

      try {
        v.controls = moved.controls;
        v.muted = moved.muted;
        v.loop = moved.loop;
      } catch {}

      try {
        if (moved.nextSibling && moved.parent && moved.parent.contains(moved.nextSibling)) {
          moved.parent.insertBefore(v, moved.nextSibling);
        } else if (moved.parent) {
          moved.parent.appendChild(v);
        }
      } catch {}

      if (moved.wasPlaying) {
        try {
          const p = v.play();
          if (p && typeof p.catch === "function") p.catch(() => {});
        } catch {}
      }
      moved = null;
    }

    // remove iframes in modal
    const media = q(".sv-modal-media", modal);
    if (media) qa("iframe", media).forEach((f) => f.remove());

    modal.classList.remove("is-open");
  };

  const openModalWithVideoNode = (videoEl) => {
    const modal = ensureModal();
    const media = q(".sv-modal-media", modal);
    if (!media) return;

    // restore any previous
    if (moved && moved.video && moved.video !== videoEl) closeModal();

    if (!moved) {
      moved = {
        video: videoEl,
        parent: videoEl.parentElement,
        nextSibling: videoEl.nextSibling,
        controls: videoEl.controls,
        muted: videoEl.muted,
        loop: videoEl.loop,
        wasPlaying: !videoEl.paused,
      };
    }

    modal.classList.add("is-open");

    try { videoEl.pause(); } catch {}
    try {
      videoEl.controls = true;
      videoEl.loop = false;
      media.appendChild(videoEl);
      const p = videoEl.play();
      if (p && typeof p.catch === "function") p.catch(() => {});
    } catch {}
  };

  const openModalWithIframe = (src) => {
    const modal = ensureModal();
    const media = q(".sv-modal-media", modal);
    if (!media) return;

    // restore moved video if any
    if (moved) closeModal();

    media.innerHTML = "";
    const f = document.createElement("iframe");
    f.allow = "autoplay; fullscreen; picture-in-picture";
    f.allowFullscreen = true;
    f.referrerPolicy = "no-referrer-when-downgrade";
    f.src = src || "about:blank";
    media.appendChild(f);

    modal.classList.add("is-open");
  };

  // ----- Nav (home, no emoji) -----
  const inferRootPrefix = () => {
    const s = q('script[src*="assets/shorts_video.js"]');
    if (!s) return "../";
    const src = s.getAttribute("src") || "";
    const idx = src.lastIndexOf("assets/shorts_video.js");
    if (idx >= 0) return src.slice(0, idx);
    return "../";
  };

  const buildLinksFromHeader = () => {
    const links = qa(".site-header a").map((a) => ({
      href: a.getAttribute("href") || "",
      text: (a.textContent || "").trim(),
    })).filter((x) => x.href && x.text);

    const seen = new Set();
    const uniq = [];
    for (const l of links) {
      if (seen.has(l.href)) continue;
      seen.add(l.href);
      uniq.push(l);
    }
    return uniq;
  };

  const ensureNav = () => {
    const root = inferRootPrefix();

    if (!q(".sv-leftnav")) {
      const headerLinks = buildLinksFromHeader();
      const items = headerLinks.length ? headerLinks.slice(0, 12) : [
        { text: "ホーム", href: root || "../" },
        { text: "検索", href: (root || "../") + "search/" },
        { text: "ショート", href: (root || "../") + "shorts/" },
      ];

      const nav = document.createElement("nav");
      nav.className = "sv-leftnav";
      nav.innerHTML = `
        <div class="sv-navlist">
          <a href="${root}" aria-label="ホーム">ホーム</a>
          ${items.filter(it => it.text !== "ホーム").map((it) => `<a href="${it.href}">${it.text}</a>`).join("")}
          <div class="sv-small">Escで拡大を閉じます</div>
        </div>
      `;
      document.body.appendChild(nav);
    }

    if (!q(".sv-homebtn")) {
      const a = document.createElement("a");
      a.className = "sv-homebtn";
      a.href = root;
      a.textContent = "ホーム";
      a.setAttribute("aria-label", "ホーム");
      document.body.appendChild(a);
    }
  };

  // ----- Shorts init -----
  const initShortsUI = () => {
    const feed = q("#shortsFeed");
    if (!feed) return;

    document.body.classList.add("sv-shorts", "sv-watch");
    ensureNav();

    const items = qa(".short-item", feed);
    if (!items.length) return;

    items.forEach((item) => {
      if (item.dataset.svEnhanced === "1") return;

      const iframe = q("iframe", item);
      if (!iframe) return;

      const workId = item.getAttribute("data-id") || item.dataset.id || "";
      const url = iframe.getAttribute("data-src") || iframe.getAttribute("src") || "";
      const cid = parseCidFromSampleUrl(url);
      const mp4s = buildMp4Candidates(cid);

      const v = document.createElement("video");
      v.className = "sv-enhanced-video sv-short-video";
      v.muted = true;
      v.loop = true;
      v.playsInline = true;
      v.preload = "metadata";
      v.setAttribute("webkit-playsinline", "true");

      iframe.classList.add("sv-hidden");
      iframe.parentElement.insertBefore(v, iframe);

      item.dataset.svEnhanced = "1";
      item._sv = { id: String(workId), video: v, iframe, mp4s, url };

      const failToIframe = () => {
        v.classList.add("sv-hidden");
        iframe.classList.remove("sv-hidden");
        const cur = iframe.getAttribute("src") || "";
        if (!cur || cur === "about:blank") iframe.setAttribute("src", url || "about:blank");
      };
      v._svFailToIframe = failToIframe;

      // Ensure Favorite button
      const actions = q(".short-actions", item);
      if (actions && !q(".sv-fav", actions)) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-ghost sv-fav";
        b.setAttribute("aria-pressed", "false");
        actions.insertBefore(b, actions.firstChild);
        updateFavButton(b, isFav(workId));
      }

      // Ensure Expand button exists (does not navigate)
      if (actions && !q(".sv-expand", actions)) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-ghost sv-expand";
        b.textContent = "拡大";
        actions.appendChild(b);
      }
    });

    const activate = (item) => {
      const data = item._sv;
      if (!data) return;

      // pause others
      items.forEach((it) => {
        if (it === item) return;
        const d = it._sv;
        if (!d) return;
        try { d.video.pause(); } catch {}
      });

      // iframe fallback
      if (data.iframe && !data.iframe.classList.contains("sv-hidden")) return;

      const v = data.video;
      if (!v.src) trySetVideoSrc(v, data.mp4s, v._svFailToIframe);

      const p = v.play();
      if (p && typeof p.catch === "function") p.catch(() => {});
    };

    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting && e.intersectionRatio >= 0.6) activate(e.target);
      });
    }, { threshold: [0.0, 0.6, 1.0] });
    items.forEach((it) => io.observe(it));

    // Tap anywhere (except links/buttons): pause/resume
    feed.addEventListener("click", (ev) => {
      const t = ev.target;
      if (t && t.closest && t.closest("a,button")) return;

      const item = t && t.closest ? t.closest(".short-item") : null;
      if (!item || !item._sv) return;

      const v = item._sv.video;
      if (!v || v.classList.contains("sv-hidden")) return;

      if (v.paused) activate(item);
      else { try { v.pause(); } catch {} }
    });

    // Click handlers for fav/expand (capture inside feed)
    feed.addEventListener("click", (ev) => {
      const t = ev.target;
      const favBtn = t && t.closest ? t.closest(".sv-fav") : null;
      if (favBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        const item = favBtn.closest(".short-item");
        if (!item || !item._sv) return;
        const on = toggleFav(item._sv.id);
        updateFavButton(favBtn, on);
        return;
      }

      const exp = t && t.closest ? t.closest(".sv-expand") : null;
      if (exp) {
        ev.preventDefault();
        ev.stopPropagation();
        const item = exp.closest(".short-item");
        if (!item || !item._sv) return;
        const data = item._sv;

        if (data.video && !data.video.classList.contains("sv-hidden") && data.video.src) {
          openModalWithVideoNode(data.video);
        } else {
          openModalWithIframe(data.url);
        }
      }
    });

    // initial
    activate(items[0]);
  };

  document.addEventListener("DOMContentLoaded", () => {
    try { initShortsUI(); } catch {}
  });
})();
