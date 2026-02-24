(() => {
  // ===== Settings =====
  const ROOT = window.__ROOT_PATH__ || "../";
  const LEGACY_MANIFEST_URL = window.__SEARCH_MANIFEST__; // old builds
  const EMBED_B64 = window.__SEARCH_EMBED__; // new builds

  const XOR_KEY = new TextEncoder().encode("ReviewCatalog-v1");

  const $ = (id) => document.getElementById(id);

  const elQ = $("q");
  const elMaker = $("maker");
  const elSeries = $("series");
  const elHasImg = $("hasImg");
  const elHasMov = $("hasMov");
  const elClear = $("clear");

  const elPopularTags = $("popularTags");
  const elSelectedTags = $("selectedTags");
  const elResults = $("results");
  const elStatus = $("status");
  const elSentinel = $("sentinel");

  const state = {
    q: "",
    maker: "",
    series: "",
    hasImg: false,
    hasMov: false,
    tags: new Set(),
  };

  let manifest = null; // normalized
  let chunks = [];
  let chunkIndex = 0;
  let loading = false;

  // counts
  let scanned = 0;
  let shown = 0;

  function norm(s) {
    return (s || "").toString().toLowerCase().trim();
  }

  function xorInPlace(bytes) {
    for (let i = 0; i < bytes.length; i++) {
      bytes[i] = bytes[i] ^ XOR_KEY[i % XOR_KEY.length];
    }
    return bytes;
  }

  async function gunzip(bytes) {
    if (!("DecompressionStream" in window)) {
      // ここに来るのは「古いブラウザ」か、何かの理由で未対応のとき。
      // gzデータなので、復号できず検索機能が動かない。
      throw new Error("DecompressionStream not supported");
    }
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return await new Response(stream).text();
  }

  async function decodePayload(b64) {
    const clean = (b64 || "").replace(/\s+/g, "").trim();
    const bin = atob(clean);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    xorInPlace(bytes);
    const text = await gunzip(bytes);
    return JSON.parse(text);
  }

  function normalizeManifest(m) {
    // v3（保護版）: {v,ga,t,cs,c,pt,mk,sr}
    if (m && typeof m === "object" && m.v === 3) {
      return {
        version: 3,
        generated_at: m.ga,
        total: m.t || 0,
        chunk_size: m.cs || 0,
        chunks: (m.c || []).map((x) => ({ file: x[0], count: x[1] })),
        popular_tags: (m.pt || []).map((x) => ({ name: x[0], count: x[1] })),
        makers: m.mk || [],
        series: m.sr || [],
      };
    }

    // legacy: {version,total,chunk_size,chunks:[{file,count}],popular_tags:[{name,count}],...}
    return m;
  }

  function normalizeItem(it) {
    // v3（保護版）: [id,title,release_date,hero_image,path,tags,actresses,maker,series,has_img,img_count,has_mov,api_rank]
    if (Array.isArray(it)) {
      return {
        id: it[0],
        title: it[1],
        release_date: it[2],
        hero_image: it[3],
        path: it[4],
        tags: it[5] || [],
        actresses: it[6] || [],
        maker: it[7] || "",
        series: it[8] || "",
        has_img: !!it[9],
        img_count: Number(it[10] || 0),
        has_mov: !!it[11],
        api_rank: it[12],
      };
    }
    return it;
  }

  function clearResults() {
    elResults.innerHTML = "";
    chunkIndex = 0;
    scanned = 0;
    shown = 0;
    chunks = manifest ? manifest.chunks.slice() : [];
    updateStatus();
  }

  function updateStatus() {
    if (!manifest) return;
    elStatus.textContent = `表示: ${shown}件 / 読み込み済み: ${scanned}件 / 全体: ${manifest.total}件`;
  }

  function setOptionList(selectEl, values) {
    const first = selectEl.querySelector("option[value='']") || null;
    selectEl.innerHTML = "";
    if (first) {
      selectEl.appendChild(first);
    } else {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "すべて";
      selectEl.appendChild(opt);
    }
    values.forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      selectEl.appendChild(opt);
    });
  }

  function tagButton(name, count, active) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tag" + (active ? " is-active" : "");
    btn.textContent = count != null ? `${name} (${count})` : name;
    btn.dataset.name = name;
    return btn;
  }

  function renderTags() {
    elPopularTags.innerHTML = "";
    (manifest.popular_tags || []).forEach((t) => {
      const active = state.tags.has(t.name);
      const btn = tagButton(t.name, t.count, active);
      btn.addEventListener("click", () => toggleTag(t.name));
      elPopularTags.appendChild(btn);
    });

    elSelectedTags.innerHTML = "";
    if (state.tags.size === 0) {
      const span = document.createElement("span");
      span.className = "muted";
      span.textContent = "なし";
      elSelectedTags.appendChild(span);
      return;
    }
    [...state.tags].forEach((name) => {
      const btn = tagButton(name, null, true);
      btn.addEventListener("click", () => toggleTag(name));
      elSelectedTags.appendChild(btn);
    });
  }

  function toggleTag(name) {
    if (state.tags.has(name)) state.tags.delete(name);
    else state.tags.add(name);
    renderTags();
    debounceSearch();
  }

  function matchItem(it) {
    if (state.hasImg && !it.has_img) return false;
    if (state.hasMov && !it.has_mov) return false;

    if (state.maker && it.maker !== state.maker) return false;
    if (state.series && it.series !== state.series) return false;

    if (state.tags.size > 0) {
      const tags = Array.isArray(it.tags) ? it.tags : [];
      for (const t of state.tags) {
        if (!tags.includes(t)) return false;
      }
    }

    const q = norm(state.q);
    if (!q) return true;

    const hay = [
      it.title,
      it.maker,
      it.series,
      ...(it.tags || []),
      ...(it.actresses || []),
    ]
      .map(norm)
      .join(" ");
    return hay.includes(q);
  }

  function createCard(it) {
    const a = document.createElement("a");
    a.className = "work-card";
    a.href = ROOT + it.path;
    a.setAttribute("aria-label", it.title || it.id);

    const thumb = document.createElement("div");
    thumb.className = "work-thumb";

    // Favorite button (works on any page)
    const fav = document.createElement("button");
    fav.className = "fav-btn";
    fav.type = "button";
    fav.textContent = "☆";
    fav.setAttribute("aria-label", "お気に入り");
    fav.setAttribute("aria-pressed", "false");
    fav.dataset.workId = it.id;
    fav.dataset.workTitle = it.title || it.id;
    fav.dataset.workHero = it.hero_image || "";
    fav.dataset.workPath = it.path || ("works/" + it.id + "/");
    fav.dataset.workMaker = it.maker || "";
    fav.dataset.workRelease = (it.release_date || "").replace(" 00:00:00","");
    fav.dataset.hasImg = it.has_img ? "1" : "0";
    fav.dataset.hasMov = it.has_mov ? "1" : "0";
    thumb.appendChild(fav);

    if (it.hero_image) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = it.hero_image;
      img.alt = it.title || it.id;
      thumb.appendChild(img);
    } else {
      const ph = document.createElement("div");
      ph.className = "thumb-placeholder";
      thumb.appendChild(ph);
    }

    const badges = document.createElement("div");
    badges.className = "work-badges";
    if (it.has_img) {
      const b = document.createElement("span");
      b.className = "badge badge-img";
      b.textContent = it.img_count
        ? `🖼️ サンプル画像あり（${it.img_count}枚）`
        : "🖼️ サンプル画像あり";
      badges.appendChild(b);
    }
    if (it.has_mov) {
      const b = document.createElement("span");
      b.className = "badge badge-mov";
      b.textContent = "🎬 サンプル動画あり";
      badges.appendChild(b);
    }
    thumb.appendChild(badges);

    const meta = document.createElement("div");
    meta.className = "work-meta";

    const title = document.createElement("div");
    title.className = "work-title";
    title.textContent = it.title || it.id;

    const sub = document.createElement("div");
    sub.className = "work-sub";
    const parts = [];
    if (it.release_date) parts.push(it.release_date);
    if (it.maker) parts.push(it.maker);
    if (it.series) parts.push(it.series);
    sub.textContent = parts.join(" • ");

    meta.appendChild(title);
    meta.appendChild(sub);

    a.appendChild(thumb);
    a.appendChild(meta);
    return a;
  }

  async function loadChunkData(chunk) {
    const url = ROOT + "assets/" + chunk.file;
    const res = await fetch(url, { cache: "no-cache" });
    if (!res.ok) throw new Error(`chunk fetch failed: ${res.status}`);

    if (chunk.file.endsWith(".dat")) {
      const b64 = await res.text();
      return await decodePayload(b64);
    }
    return await res.json();
  }

  async function loadNextChunk() {
    if (loading) return;
    if (!manifest) return;
    if (chunkIndex >= chunks.length) return;

    loading = true;
    const chunk = chunks[chunkIndex];
    chunkIndex += 1;

    try {
      const rawItems = await loadChunkData(chunk);
      const items = Array.isArray(rawItems) ? rawItems : [];
      scanned += items.length;

      const frag = document.createDocumentFragment();
      for (const raw of items) {
        const it = normalizeItem(raw);
        if (matchItem(it)) {
          frag.appendChild(createCard(it));
          shown += 1;
        }
      }
      elResults.appendChild(frag);
      updateStatus();
    } catch (e) {
      console.error(e);
      elStatus.textContent = "検索データの読み込みに失敗しました（ブラウザ/通信を確認してください）";
    } finally {
      loading = false;
    }
  }

  let debounceTimer = null;
  function debounceSearch() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      clearResults();
      loadNextChunk();
    }, 120);
  }

  function bindInputs() {
    elQ.addEventListener("input", () => {
      state.q = elQ.value || "";
      debounceSearch();
    });
    elMaker.addEventListener("change", () => {
      state.maker = elMaker.value || "";
      debounceSearch();
    });
    elSeries.addEventListener("change", () => {
      state.series = elSeries.value || "";
      debounceSearch();
    });
    elHasImg.addEventListener("change", () => {
      state.hasImg = !!elHasImg.checked;
      debounceSearch();
    });
    elHasMov.addEventListener("change", () => {
      state.hasMov = !!elHasMov.checked;
      debounceSearch();
    });
    elClear.addEventListener("click", () => {
      state.q = "";
      state.maker = "";
      state.series = "";
      state.hasImg = false;
      state.hasMov = false;
      state.tags.clear();

      elQ.value = "";
      elMaker.value = "";
      elSeries.value = "";
      elHasImg.checked = false;
      elHasMov.checked = false;

      renderTags();
      debounceSearch();
    });
  }

  async function loadManifest() {
    // new (embedded)
    if (EMBED_B64 && EMBED_B64.trim()) {
      return normalizeManifest(await decodePayload(EMBED_B64));
    }

    // legacy (json)
    if (!LEGACY_MANIFEST_URL) throw new Error("manifest not configured");
    const res = await fetch(LEGACY_MANIFEST_URL, { cache: "no-cache" });
    return normalizeManifest(await res.json());
  }

  async function init() {
    try {
      manifest = await loadManifest();
    } catch (e) {
      console.error(e);
      if (!("DecompressionStream" in window)) {
        elStatus.textContent = "このブラウザは検索データの展開に未対応です（Chrome/Edge推奨）";
      } else {
        elStatus.textContent = "manifest の読み込みに失敗しました";
      }
      return;
    }

    setOptionList(elMaker, manifest.makers || []);
    setOptionList(elSeries, manifest.series || []);

    renderTags();
    bindInputs();
    clearResults();
    await loadNextChunk();

    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          loadNextChunk();
        }
      },
      { rootMargin: "800px" }
    );
    io.observe(elSentinel);
  }

  // guard
  if (!elQ || !elResults || !elStatus || !elSentinel) return;

  init();
})();


// =============================
// Favorites / Recently Viewed (localStorage)
// =============================
(() => {
  const ROOT = window.__ROOT_PATH__ || "../";

  const KEY_FAVS = "rc_favs_v1";
  const KEY_FAV_META = "rc_fav_meta_v1";
  const KEY_RECENT = "rc_recent_v1";
  const MAX_RECENT = 30;

  const $ = (id) => document.getElementById(id);

  function loadJSON(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return fallback;
      return JSON.parse(raw);
    } catch (_) {
      return fallback;
    }
  }

  function saveJSON(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) {}
  }

  function getFavList() {
    const v = loadJSON(KEY_FAVS, []);
    return Array.isArray(v) ? v : [];
  }

  function getFavMeta() {
    const v = loadJSON(KEY_FAV_META, {});
    return v && typeof v === "object" ? v : {};
  }

  function isFav(id) {
    return getFavList().includes(id);
  }

  function setFavUI(btn, on) {
    if (!btn) return;
    btn.classList.toggle("is-active", !!on);
    btn.textContent = on ? "★" : "☆";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function readMetaFromBtn(btn) {
    const ds = btn.dataset || {};
    const id = ds.workId || ds.workid || "";
    if (!id) return null;
    return {
      id,
      title: ds.workTitle || "",
      hero: ds.workHero || "",
      path: ds.workPath || ("works/" + id + "/"),
      maker: ds.workMaker || "",
      release: (ds.workRelease || "").replace(" 00:00:00", ""),
      hasImg: ds.hasImg === "1",
      hasMov: ds.hasMov === "1",
    };
  }

  function upsertFav(meta) {
    const list = getFavList();
    const metaMap = getFavMeta();
    if (!list.includes(meta.id)) list.unshift(meta.id);
    metaMap[meta.id] = meta;
    saveJSON(KEY_FAVS, list);
    saveJSON(KEY_FAV_META, metaMap);
  }

  function removeFav(id) {
    const list = getFavList().filter((x) => x !== id);
    saveJSON(KEY_FAVS, list);
    // metaは残す（再追加時に復元できる）
  }

  function toggleFav(meta) {
    if (!meta || !meta.id) return;
    const on = isFav(meta.id);
    if (on) removeFav(meta.id);
    else upsertFav(meta);
  }

  function addRecent(meta) {
    if (!meta || !meta.id) return;
    const rec = loadJSON(KEY_RECENT, []);
    const arr = Array.isArray(rec) ? rec : [];
    const now = Date.now();
    const item = { ...meta, ts: now };

    const filtered = arr.filter((x) => x && x.id !== meta.id);
    filtered.unshift(item);
    const trimmed = filtered.slice(0, MAX_RECENT);
    saveJSON(KEY_RECENT, trimmed);
  }

  function updateAllFavButtons() {
    const favs = new Set(getFavList());
    document.querySelectorAll(".fav-btn").forEach((btn) => {
      const meta = readMetaFromBtn(btn);
      if (!meta) return;
      setFavUI(btn, favs.has(meta.id));
    });
  }

  function badgeHtml(meta) {
    const parts = [];
    if (meta.hasImg) parts.push('<span class="badge badge-img">🖼️ サンプル画像あり</span>');
    if (meta.hasMov) parts.push('<span class="badge badge-mov">🎬 サンプル動画あり</span>');
    return parts.join("");
  }

  function createCard(meta) {
    const a = document.createElement("a");
    a.className = "work-card";
    a.href = ROOT + (meta.path || ("works/" + meta.id + "/"));
    a.setAttribute("aria-label", meta.title || meta.id);

    const thumb = document.createElement("div");
    thumb.className = "work-thumb";

    const fav = document.createElement("button");
    fav.className = "fav-btn";
    fav.type = "button";
    fav.textContent = "☆";
    fav.setAttribute("aria-label", "お気に入り");
    fav.setAttribute("aria-pressed", "false");
    fav.dataset.workId = meta.id;
    fav.dataset.workTitle = meta.title || meta.id;
    fav.dataset.workHero = meta.hero || "";
    fav.dataset.workPath = meta.path || ("works/" + meta.id + "/");
    fav.dataset.workMaker = meta.maker || "";
    fav.dataset.workRelease = meta.release || "";
    fav.dataset.hasImg = meta.hasImg ? "1" : "0";
    fav.dataset.hasMov = meta.hasMov ? "1" : "0";
    thumb.appendChild(fav);

    if (meta.hero) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = meta.hero;
      img.alt = meta.title || meta.id;
      thumb.appendChild(img);
    } else {
      const ph = document.createElement("div");
      ph.className = "thumb-placeholder";
      thumb.appendChild(ph);
    }

    const badges = document.createElement("div");
    badges.className = "work-badges";
    badges.innerHTML = badgeHtml(meta);
    thumb.appendChild(badges);

    const wm = document.createElement("div");
    wm.className = "work-meta";
    const t = document.createElement("div");
    t.className = "work-title";
    t.textContent = meta.title || meta.id;
    const sub = document.createElement("div");
    sub.className = "work-sub";
    const bits = [];
    if (meta.release) bits.push(meta.release);
    if (meta.maker) bits.push(meta.maker);
    sub.innerHTML = bits.map((x) => `<span class="muted">${escapeHtml(String(x))}</span>`).join('<span class="dot">•</span>');
    wm.appendChild(t);
    wm.appendChild(sub);

    a.appendChild(thumb);
    a.appendChild(wm);
    return a;
  }

  function escapeHtml(s) {
    return s
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function renderPersonal() {
    const wrap = $("personalWrap");
    const recentGrid = $("recentGrid");
    const favGrid = $("favGrid");
    const recentBlock = $("recentBlock");
    const favBlock = $("favBlock");
    if (!wrap || !recentGrid || !favGrid || !recentBlock || !favBlock) return;

    // Recent
    const rec = loadJSON(KEY_RECENT, []);
    const recArr = Array.isArray(rec) ? rec : [];
    recentGrid.innerHTML = "";
    const recItems = recArr.slice(0, 8);
    recItems.forEach((m) => {
      if (!m || !m.id) return;
      recentGrid.appendChild(createCard(m));
    });
    recentBlock.hidden = recItems.length === 0;

    // Favs
    const favIds = getFavList();
    const metaMap = getFavMeta();
    favGrid.innerHTML = "";
    const favItems = favIds
      .map((id) => metaMap[id])
      .filter(Boolean)
      .slice(0, 8);
    favItems.forEach((m) => favGrid.appendChild(createCard(m)));
    favBlock.hidden = favItems.length === 0;

    wrap.hidden = (recItems.length === 0 && favItems.length === 0);

    updateAllFavButtons();
  }

  // Click handlers
  document.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest(".fav-btn");
    if (!btn) return;
    // prevent navigation if inside <a>
    e.preventDefault();
    e.stopPropagation();
    const meta = readMetaFromBtn(btn);
    if (!meta) return;
    toggleFav(meta);
    updateAllFavButtons();
    renderPersonal();
  });

  // Clear buttons (optional)
  const recentClear = $("recentClear");
  recentClear?.addEventListener("click", (e) => {
    e.preventDefault();
    saveJSON(KEY_RECENT, []);
    renderPersonal();
  });
  const favClear = $("favClear");
  favClear?.addEventListener("click", (e) => {
    e.preventDefault();
    saveJSON(KEY_FAVS, []);
    renderPersonal();
    updateAllFavButtons();
  });

  // On detail page, record recently viewed
  if (window.__WORK_META__ && window.__WORK_META__.id) {
    const m = window.__WORK_META__;
    addRecent({
      id: String(m.id),
      title: m.title || "",
      hero: m.hero || "",
      path: m.path || ("works/" + m.id + "/"),
      maker: m.maker || "",
      release: m.release || "",
      hasImg: !!m.hasImg,
      hasMov: !!m.hasMov,
    });
  }

  // Initial paint
  function boot() {
    updateAllFavButtons();
    renderPersonal();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Sync across tabs
  window.addEventListener("storage", (ev) => {
    if (!ev) return;
    if ([KEY_FAVS, KEY_FAV_META, KEY_RECENT].includes(ev.key)) {
      updateAllFavButtons();
      renderPersonal();
    }
  });
})();
