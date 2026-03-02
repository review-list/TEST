// shorts_video.js (v1.7)
// New: TikTokっぽい「おすすめ」並び替え
// - 最初はランダム
// - お気に入りが増えるほど「お気に入りに関連する作品」を上に出す
// - お気に入りON時に、その作品の特徴（タグ/女優/メーカー等）を学習して localStorage に保存
// - 次回以降、そのプロファイルでスコアリングして順番を組む（探索も混ぜる）
//
// 既存仕様維持:
// - MP4優先 + ミュート自動再生（freepv）
// - 左メニュー（ホーム）
// - お気に入り（☆/★）
// - 拡大：新しいウィンドウを開かず、ページ内モーダル（videoノード移動で再生位置維持）

(() => {
  "use strict";

  const q = (sel, root = document) => root.querySelector(sel);
  const qa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ---------- RNG (seeded) ----------
  const hash32 = (s) => {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
  };

  const mulberry32 = (a) => {
    let t = a >>> 0;
    return () => {
      t += 0x6D2B79F5;
      let r = Math.imul(t ^ (t >>> 15), 1 | t);
      r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  };

  const shuffleInPlace = (arr, rand) => {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  };

  // ---------- Favorites ----------
  const FAV_KEY = "catalog:favs:v1";
  const FAV_FEATURES_KEY = "shorts:fav_features:v1"; // {id: {tags, actresses, maker, series, label}}

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

  const loadFavFeatures = () => {
    try {
      const raw = localStorage.getItem(FAV_FEATURES_KEY);
      if (!raw) return {};
      const obj = JSON.parse(raw);
      if (obj && typeof obj === "object") return obj;
    } catch {}
    return {};
  };

  const saveFavFeatures = (obj) => {
    try {
      localStorage.setItem(FAV_FEATURES_KEY, JSON.stringify(obj));
    } catch {}
  };

  const isFav = (workId) => {
    const id = String(workId || "");
    if (!id) return false;
    return loadFavSet().has(id);
  };

  const updateFavButton = (btn, on) => {
    if (!btn) return;
    btn.classList.toggle("is-on", !!on);
    btn.textContent = on ? "★" : "☆";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "お気に入り解除" : "お気に入り";
  };

  const parseMetaJSON = (s, fallback) => {
    if (typeof s !== "string" || !s.trim()) return fallback;
    try {
      return JSON.parse(s);
    } catch {
      return fallback;
    }
  };

  const readItemMeta = (item) => {
    const tags = parseMetaJSON(item.getAttribute("data-tags"), []);
    const actresses = parseMetaJSON(item.getAttribute("data-actresses"), []);
    const maker = parseMetaJSON(item.getAttribute("data-maker"), "");
    const series = parseMetaJSON(item.getAttribute("data-series"), "");
    const label = parseMetaJSON(item.getAttribute("data-label"), "");
    return {
      tags: Array.isArray(tags) ? tags.map(String).filter(Boolean) : [],
      actresses: Array.isArray(actresses) ? actresses.map(String).filter(Boolean) : [],
      maker: typeof maker === "string" ? maker : "",
      series: typeof series === "string" ? series : "",
      label: typeof label === "string" ? label : "",
    };
  };

  const learnFavFeatures = (id, item) => {
    if (!id || !item) return;
    const feats = loadFavFeatures();
    feats[String(id)] = readItemMeta(item);
    saveFavFeatures(feats);
  };

  const forgetFavFeatures = (id) => {
    if (!id) return;
    const feats = loadFavFeatures();
    delete feats[String(id)];
    saveFavFeatures(feats);
  };

  const toggleFav = (workId, item) => {
    const id = String(workId || "");
    if (!id) return false;

    const set = loadFavSet();
    let on = false;

    if (set.has(id)) {
      set.delete(id);
      on = false;
      forgetFavFeatures(id);
    } else {
      set.add(id);
      on = true;
      // 学習
      learnFavFeatures(id, item);
    }

    saveFavSet(set);
    return on;
  };

  // ---------- Recommendation ----------
  const buildProfile = (favSet) => {
    const feats = loadFavFeatures();
    const prof = {
      tags: new Map(),
      actresses: new Map(),
      maker: new Map(),
      series: new Map(),
      label: new Map(),
    };

    const addCount = (map, key, w = 1) => {
      if (!key) return;
      const k = String(key);
      map.set(k, (map.get(k) || 0) + w);
    };

    for (const id of favSet) {
      const f = feats[String(id)];
      if (!f) continue;

      (f.tags || []).forEach((t) => addCount(prof.tags, t, 1));
      (f.actresses || []).forEach((a) => addCount(prof.actresses, a, 1));
      if (f.maker) addCount(prof.maker, f.maker, 1);
      if (f.series) addCount(prof.series, f.series, 1);
      if (f.label) addCount(prof.label, f.label, 1);
    }
    return prof;
  };

  const profileIsEmpty = (prof) => {
    return (
      prof.tags.size === 0 &&
      prof.actresses.size === 0 &&
      prof.maker.size === 0 &&
      prof.series.size === 0 &&
      prof.label.size === 0
    );
  };

  const scoreItem = (meta, prof) => {
    let s = 0;

    // tags: broad
    for (const t of meta.tags) s += (prof.tags.get(t) || 0) * 0.8;

    // actresses: strong
    for (const a of meta.actresses) s += (prof.actresses.get(a) || 0) * 2.2;

    // maker/series/label: medium
    if (meta.maker) s += (prof.maker.get(meta.maker) || 0) * 1.1;
    if (meta.series) s += (prof.series.get(meta.series) || 0) * 1.8;
    if (meta.label) s += (prof.label.get(meta.label) || 0) * 0.8;

    return s;
  };

  const recommendOrder = (items) => {
    const favSet = loadFavSet();
    const favCount = favSet.size;

    // Seed: day + favorites snapshot (changes when fav changes)
    const day = new Date().toISOString().slice(0, 10);
    const favKey = Array.from(favSet).sort().join(",");
    const rand = mulberry32(hash32(`${day}|${favKey}`));

    // 0 fav => random shuffle
    if (favCount === 0) return shuffleInPlace(items.slice(), rand);

    const prof = buildProfile(favSet);
    if (profileIsEmpty(prof)) return shuffleInPlace(items.slice(), rand);

    const scored = items.map((it) => {
      const id = it.getAttribute("data-id") || it.dataset.id || "";
      const meta = readItemMeta(it);
      let sc = scoreItem(meta, prof);
      // 好き登録済みの作品は“おすすめ”としては少し下げる（同じのばかりにならない）
      if (favSet.has(String(id))) sc -= 3.5;
      // 微小ノイズで安定しすぎないように
      sc += rand() * 0.05;
      return { it, sc };
    });

    // related list
    scored.sort((a, b) => b.sc - a.sc);
    const related = scored.map((x) => x.it);

    // random list
    const random = shuffleInPlace(items.slice(), rand);

    // お気に入りが増えるほど関連を増やす
    const relRatio = Math.min(0.88, 0.25 + 0.06 * favCount); // 1=0.31, 5=0.55, 10=0.85

    const out = [];
    const used = new Set();
    let ri = 0, qi = 0;

    while (out.length < items.length) {
      const pickRelated = (ri < related.length) && (qi >= random.length || rand() < relRatio);
      const cand = pickRelated ? related[ri++] : random[qi++];
      if (!cand) break;
      const id = cand.getAttribute("data-id") || cand.dataset.id || "";
      if (used.has(id)) continue;
      used.add(id);
      out.push(cand);
    }
    return out;
  };

  // ---------- DMM/FANZA MP4 helpers ----------
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
      try { video.src = u; video.load(); } catch { onError(); }
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

    if (!list.length) { cleanup(); if (onFail) onFail(); return; }
    setSrc(list[0]);
  };

  // ---------- Modal (in-place expand) ----------
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

    if (moved && moved.video) {
      const v = moved.video;
      const media = q(".sv-modal-media", modal);

      try { v.pause(); } catch {}

      try { if (media && media.contains(v)) media.removeChild(v); } catch {}

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

    const media = q(".sv-modal-media", modal);
    if (media) qa("iframe", media).forEach((f) => f.remove());

    modal.classList.remove("is-open");
  };

  const openModalWithVideoNode = (videoEl) => {
    const modal = ensureModal();
    const media = q(".sv-modal-media", modal);
    if (!media) return;

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

  // ---------- Nav (home) ----------
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

  // ---------- Shorts init ----------
  const initShortsUI = () => {
    const feed = q("#shortsFeed");
    if (!feed) return;

    document.body.classList.add("sv-shorts", "sv-watch");
    ensureNav();

    // Items (order will be replaced)
    let items = qa(".short-item", feed);
    if (!items.length) return;

    // 1) Recommended order (before wiring)
    items = recommendOrder(items);

    // Apply order to DOM
    const frag = document.createDocumentFragment();
    items.forEach((it) => frag.appendChild(it));
    feed.appendChild(frag);

    // 2) Prepare each item (video + buttons)
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

      const actions = q(".short-actions", item);
      if (actions && !q(".sv-fav", actions)) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-ghost sv-fav";
        b.setAttribute("aria-pressed", "false");
        actions.insertBefore(b, actions.firstChild);
        updateFavButton(b, isFav(workId));
      }

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

      items.forEach((it) => {
        if (it === item) return;
        const d = it._sv;
        if (!d) return;
        try { d.video.pause(); } catch {}
      });

      if (data.iframe && !data.iframe.classList.contains("sv-hidden")) return;

      const v = data.video;
      if (!v.src) trySetVideoSrc(v, data.mp4s, v._svFailToIframe);

      const p = v.play();
      if (p && typeof p.catch === "function") p.catch(() => {});
    };

    // Observer to autoplay the visible item
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

    // Click handlers for fav/expand
    feed.addEventListener("click", (ev) => {
      const t = ev.target;

      const favBtn = t && t.closest ? t.closest(".sv-fav") : null;
      if (favBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        const item = favBtn.closest(".short-item");
        if (!item || !item._sv) return;
        const on = toggleFav(item._sv.id, item);
        updateFavButton(favBtn, on);
        // 学習済みは次回のおすすめに反映される
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
