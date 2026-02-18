(() => {
  const manifestUrl = window.__SEARCH_MANIFEST__;
  const ROOT = window.__ROOT_PATH__ || "../";

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

  let manifest = null;
  let chunks = [];
  let chunkIndex = 0;
  let loading = false;

  // counts
  let scanned = 0;
  let shown = 0;

  function norm(s){
    return (s || "").toString().toLowerCase().trim();
  }

  function clearResults(){
    elResults.innerHTML = "";
    chunkIndex = 0;
    scanned = 0;
    shown = 0;
    chunks = manifest ? manifest.chunks.slice() : [];
    updateStatus();
  }

  function updateStatus(){
    if (!manifest) return;
    elStatus.textContent = `表示: ${shown}件 / 読み込み済み: ${scanned}件 / 全体: ${manifest.total}件`;
  }

  function setOptionList(selectEl, values){
    // keep first option
    const first = selectEl.querySelector("option[value='']") || null;
    selectEl.innerHTML = "";
    if (first){
      selectEl.appendChild(first);
    } else {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "すべて";
      selectEl.appendChild(opt);
    }
    values.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      selectEl.appendChild(opt);
    });
  }

  function tagButton(name, count, active){
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tag" + (active ? " is-active" : "");
    btn.textContent = count != null ? `${name} (${count})` : name;
    btn.dataset.name = name;
    return btn;
  }

  function renderTags(){
    // popular
    elPopularTags.innerHTML = "";
    (manifest.popular_tags || []).forEach(t => {
      const active = state.tags.has(t.name);
      const btn = tagButton(t.name, t.count, active);
      btn.addEventListener("click", () => toggleTag(t.name));
      elPopularTags.appendChild(btn);
    });

    // selected
    elSelectedTags.innerHTML = "";
    if (state.tags.size === 0){
      const span = document.createElement("span");
      span.className = "muted";
      span.textContent = "なし";
      elSelectedTags.appendChild(span);
      return;
    }
    [...state.tags].forEach(name => {
      const btn = tagButton(name, null, true);
      btn.addEventListener("click", () => toggleTag(name));
      elSelectedTags.appendChild(btn);
    });
  }

  function toggleTag(name){
    if (state.tags.has(name)) state.tags.delete(name);
    else state.tags.add(name);
    renderTags();
    debounceSearch();
  }

  function matchItem(it){
    if (state.hasImg && !it.has_img) return false;
    if (state.hasMov && !it.has_mov) return false;

    if (state.maker && it.maker !== state.maker) return false;
    if (state.series && it.series !== state.series) return false;

    if (state.tags.size > 0){
      const tags = Array.isArray(it.tags) ? it.tags : [];
      for (const t of state.tags){
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
    ].map(norm).join(" ");
    return hay.includes(q);
  }

  function createCard(it){
    const a = document.createElement("a");
    a.className = "work-card";
    a.href = ROOT + it.path;
    a.setAttribute("aria-label", it.title || it.id);

    const thumb = document.createElement("div");
    thumb.className = "work-thumb";

    if (it.hero_image){
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
    if (it.has_img){
      const b = document.createElement("span");
      b.className = "badge badge-img";
      b.textContent = it.img_count ? `🖼️ サンプル画像あり（${it.img_count}枚）` : "🖼️ サンプル画像あり";
      badges.appendChild(b);
    }
    if (it.has_mov){
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

  async function loadNextChunk(){
    if (loading) return;
    if (!manifest) return;
    if (chunkIndex >= chunks.length) return;

    loading = true;
    const chunk = chunks[chunkIndex];
    chunkIndex += 1;

    try{
      const res = await fetch(ROOT + "assets/" + chunk.file, { cache: "no-cache" });
      const items = await res.json();
      if (Array.isArray(items)){
        scanned += items.length;
        const frag = document.createDocumentFragment();
        for (const it of items){
          if (matchItem(it)){
            frag.appendChild(createCard(it));
            shown += 1;
          }
        }
        elResults.appendChild(frag);
        updateStatus();
      }
    }catch(e){
      console.error(e);
    }finally{
      loading = false;
    }
  }

  let debounceTimer = null;
  function debounceSearch(){
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      clearResults();
      loadNextChunk(); // first draw
    }, 120);
  }

  function bindInputs(){
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

  async function init(){
    try{
      const res = await fetch(manifestUrl, { cache: "no-cache" });
      manifest = await res.json();
    }catch(e){
      console.error(e);
      elStatus.textContent = "manifest の読み込みに失敗しました";
      return;
    }

    // populate selects
    setOptionList(elMaker, manifest.makers || []);
    setOptionList(elSeries, manifest.series || []);

    renderTags();
    bindInputs();
    clearResults();
    await loadNextChunk();

    const io = new IntersectionObserver((entries) => {
      if (entries.some(e => e.isIntersecting)){
        loadNextChunk();
      }
    }, { rootMargin: "800px" });
    io.observe(elSentinel);
  }

  init();
})();
