// shorts_video.js (v1.8)
// TikTok風「おすすめ」+ 視聴行動学習（B案）
// - 最初はランダム（お気に入りがあれば関連寄せ + ランダム混在）
// - 視聴中に学習：
//    * 長めに見た → 好みとして加点（デフォルト12秒、動画長に応じて可変）
//    * すぐスキップ（2.5秒未満） → 苦手として減点
// - スクロール（次の動画がアクティブ）するたびに「先読み範囲（最大40件）」だけ再並び替え
//   ※全体を毎回作り直さない（TikTokの挙動に近い）
//
// 保存（localStorage）:
// - お気に入り: catalog:favs:v1
// - お気に入り特徴: shorts:fav_features:v1
// - 視聴学習プロファイル: shorts:watch_profile:v1

(() => {
  "use strict";

  const q  = (sel, root = document) => root.querySelector(sel);
  const qa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ---- Tunables ----
  const BASE_POS_SEC = 12.0;   // 5秒は短いので12秒
  const NEG_SEC = 2.5;         // 即スキップ
  const REORDER_SKIP_FIRST = 2;
  const REORDER_WINDOW = 40;
  const DECAY = 0.985;
  const CLAMP = 25;

  // ---- RNG (seeded) ----
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

  // ---- Storage keys ----
  const FAV_KEY = "catalog:favs:v1";
  const FAV_FEATURES_KEY = "shorts:fav_features:v1";
  const WATCH_PROFILE_KEY = "shorts:watch_profile:v1";

  // ---- JSON meta parse ----
  const parseMetaJSON = (s, fallback) => {
    if (typeof s !== "string" || !s.trim()) return fallback;
    try { return JSON.parse(s); } catch { return fallback; }
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

  // ---- Favorites ----
  const loadFavSet = () => {
    try {
      const raw = localStorage.getItem(FAV_KEY);
      if (!raw) return new Set();
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) return new Set(arr.map(String));
    } catch {}
    return new Set();
  };
  const saveFavSet = (set) => { try { localStorage.setItem(FAV_KEY, JSON.stringify([...set])); } catch {} };

  const loadFavFeatures = () => {
    try {
      const raw = localStorage.getItem(FAV_FEATURES_KEY);
      if (!raw) return {};
      const obj = JSON.parse(raw);
      if (obj && typeof obj === "object") return obj;
    } catch {}
    return {};
  };
  const saveFavFeatures = (obj) => { try { localStorage.setItem(FAV_FEATURES_KEY, JSON.stringify(obj)); } catch {} };

  const isFav = (id) => loadFavSet().has(String(id || ""));
  const updateFavButton = (btn, on) => {
    if (!btn) return;
    btn.classList.toggle("is-on", !!on);
    btn.textContent = on ? "★" : "☆";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "お気に入り解除" : "お気に入り";
  };

  const learnFavFeatures = (id, item) => {
    const feats = loadFavFeatures();
    feats[String(id)] = readItemMeta(item);
    saveFavFeatures(feats);
  };
  const forgetFavFeatures = (id) => {
    const feats = loadFavFeatures();
    delete feats[String(id)];
    saveFavFeatures(feats);
  };

  const toggleFav = (id, item) => {
    const key = String(id || "");
    if (!key) return false;
    const set = loadFavSet();
    let on = false;
    if (set.has(key)) {
      set.delete(key);
      on = false;
      forgetFavFeatures(key);
    } else {
      set.add(key);
      on = true;
      learnFavFeatures(key, item);
    }
    saveFavSet(set);
    return on;
  };

  // ---- Watch-learning profile ----
  const loadWatchProfile = () => {
    try {
      const raw = localStorage.getItem(WATCH_PROFILE_KEY);
      if (!raw) return null;
      const obj = JSON.parse(raw);
      if (obj && typeof obj === "object") return obj;
    } catch {}
    return null;
  };
  const saveWatchProfile = (p) => { try { localStorage.setItem(WATCH_PROFILE_KEY, JSON.stringify(p)); } catch {} };

  const ensureWatchProfile = () => {
    const p = loadWatchProfile();
    if (p) return p;
    const init = { tags:{}, actresses:{}, maker:{}, series:{}, label:{}, n_pos:0, n_neg:0 };
    saveWatchProfile(init);
    return init;
  };
  const clamp = (x) => Math.max(-CLAMP, Math.min(CLAMP, x));
  const decayMap = (m) => {
    for (const k of Object.keys(m)) {
      m[k] = m[k] * DECAY;
      if (Math.abs(m[k]) < 0.01) delete m[k];
    }
  };
  const addW = (m, k, w) => {
    if (!k) return;
    const key = String(k);
    m[key] = clamp((Number(m[key] || 0)) + w);
  };

  const applyWatchSignal = (item, dwellSec, durationSec) => {
    // 長さに応じて “好み判定” を可変に（短すぎを避ける）
    let posSec = BASE_POS_SEC;
    if (Number.isFinite(durationSec) && durationSec > 0) {
      // だいたい「1/3見た」 or 最低12秒（上限22秒）
      posSec = Math.max(BASE_POS_SEC, Math.min(22, durationSec * 0.33));
    }

    let signal = 0;
    if (dwellSec >= posSec) {
      signal = Math.min(2.2, 1.0 + (dwellSec - posSec) / (posSec * 1.2));
    } else if (dwellSec < NEG_SEC) {
      signal = -1.0;
    } else {
      return; // neutral
    }

    const p = ensureWatchProfile();
    decayMap(p.tags); decayMap(p.actresses); decayMap(p.maker); decayMap(p.series); decayMap(p.label);

    const meta = readItemMeta(item);

    const wTag = 0.35 * signal;
    const wAct = 1.10 * signal;
    const wMaker = 0.55 * signal;
    const wSeries = 0.95 * signal;
    const wLabel = 0.35 * signal;

    meta.tags.forEach((t) => addW(p.tags, t, wTag));
    meta.actresses.forEach((a) => addW(p.actresses, a, wAct));
    if (meta.maker) addW(p.maker, meta.maker, wMaker);
    if (meta.series) addW(p.series, meta.series, wSeries);
    if (meta.label) addW(p.label, meta.label, wLabel);

    if (signal > 0) p.n_pos = (p.n_pos || 0) + 1;
    if (signal < 0) p.n_neg = (p.n_neg || 0) + 1;

    saveWatchProfile(p);
  };

  // ---- Scoring ----
  const buildFavProfile = (favSet) => {
    const feats = loadFavFeatures();
    const prof = { tags: new Map(), actresses: new Map(), maker: new Map(), series: new Map(), label: new Map() };
    const add = (mp, k, w=1) => { if(!k) return; const key=String(k); mp.set(key, (mp.get(key)||0)+w); };

    for (const id of favSet) {
      const f = feats[String(id)];
      if (!f) continue;
      (f.tags||[]).forEach((t)=>add(prof.tags,t,1));
      (f.actresses||[]).forEach((a)=>add(prof.actresses,a,1));
      if (f.maker) add(prof.maker,f.maker,1);
      if (f.series) add(prof.series,f.series,1);
      if (f.label) add(prof.label,f.label,1);
    }
    return prof;
  };
  const favEmpty = (p) => p.tags.size===0 && p.actresses.size===0 && p.maker.size===0 && p.series.size===0 && p.label.size===0;

  const scoreFromFav = (m, p) => {
    let s=0;
    m.tags.forEach((t)=>s+=(p.tags.get(t)||0)*0.8);
    m.actresses.forEach((a)=>s+=(p.actresses.get(a)||0)*2.2);
    if(m.maker) s+=(p.maker.get(m.maker)||0)*1.1;
    if(m.series) s+=(p.series.get(m.series)||0)*1.8;
    if(m.label) s+=(p.label.get(m.label)||0)*0.8;
    return s;
  };
  const scoreFromWatch = (m, p) => {
    if(!p) return 0;
    let s=0;
    m.tags.forEach((t)=>s+=(Number((p.tags||{})[t]||0))*0.7);
    m.actresses.forEach((a)=>s+=(Number((p.actresses||{})[a]||0))*1.6);
    if(m.maker) s+=(Number((p.maker||{})[m.maker]||0))*1.0;
    if(m.series) s+=(Number((p.series||{})[m.series]||0))*1.3;
    if(m.label) s+=(Number((p.label||{})[m.label]||0))*0.7;
    return s;
  };

  const initialOrder = (items) => {
    const favSet = loadFavSet();
    const watchP = loadWatchProfile();
    const day = new Date().toISOString().slice(0,10);
    const favKey = [...favSet].sort().join(",");
    const watchKey = watchP ? `${watchP.n_pos||0},${watchP.n_neg||0}` : "0,0";
    const rand = mulberry32(hash32(`${day}|${favKey}|${watchKey}`));

    const noSignals = favSet.size===0 && (!watchP || ((watchP.n_pos||0)+(watchP.n_neg||0)===0));
    if (noSignals) return shuffleInPlace(items.slice(), rand);

    const favProf = buildFavProfile(favSet);

    const scored = items.map((it)=>{
      const id = it.getAttribute("data-id")||it.dataset.id||"";
      const meta = readItemMeta(it);
      let sc=0;
      if(favSet.size>0 && !favEmpty(favProf)) sc+=scoreFromFav(meta,favProf);
      sc+=scoreFromWatch(meta,watchP);
      if(favSet.has(String(id))) sc-=3.5;
      sc+=rand()*0.08;
      return {it,sc};
    }).sort((a,b)=>b.sc-a.sc);

    const related = scored.map(x=>x.it);
    const random = shuffleInPlace(items.slice(), rand);

    const conf = Math.min(12, favSet.size + Math.floor(((watchP?(watchP.n_pos||0):0)/2)));
    const relRatio = Math.min(0.9, 0.25 + 0.06*conf);

    const out=[];
    const used=new Set();
    let ri=0, qi=0;
    while(out.length<items.length){
      const pickRel = (ri<related.length) && (qi>=random.length || rand()<relRatio);
      const cand = pickRel ? related[ri++] : random[qi++];
      if(!cand) break;
      const id = cand.getAttribute("data-id")||cand.dataset.id||"";
      if(used.has(id)) continue;
      used.add(id); out.push(cand);
    }
    return out;
  };

  const reorderUpcoming = (feed, currentItem) => {
    const all = qa(".short-item", feed);
    const idx = all.indexOf(currentItem);
    if(idx<0) return;

    const start = idx + REORDER_SKIP_FIRST;
    const end = Math.min(all.length, start + REORDER_WINDOW);
    if(end-start<=3) return;

    const slice = all.slice(start,end);
    const favSet = loadFavSet();
    const watchP = loadWatchProfile();
    const favProf = buildFavProfile(favSet);

    const day = new Date().toISOString().slice(0,10);
    const rand = mulberry32(hash32(`${day}|win|${idx}|${favSet.size}|${watchP?(watchP.n_pos||0):0}`));

    const scored = slice.map((it)=>{
      const id = it.getAttribute("data-id")||it.dataset.id||"";
      const meta = readItemMeta(it);
      let sc=0;
      if(favSet.size>0 && !favEmpty(favProf)) sc+=scoreFromFav(meta,favProf);
      sc+=scoreFromWatch(meta,watchP);
      if(favSet.has(String(id))) sc-=3.5;
      sc+=(rand()-0.5)*0.35; // exploration
      return {it,sc};
    }).sort((a,b)=>b.sc-a.sc);

    const marker = all[end] || null;
    const prevScroll = feed.scrollTop;

    const frag = document.createDocumentFragment();
    scored.forEach(x=>frag.appendChild(x.it));
    if(marker) feed.insertBefore(frag, marker);
    else feed.appendChild(frag);

    feed.scrollTop = prevScroll;
  };

  // ---- MP4 enhance ----
  const parseCid = (u) => {
    if(!u) return "";
    const s=String(u);
    let m=s.match(/cid=([^/]+)/); if(m&&m[1]) return m[1];
    m=s.match(/[?&]cid=([^&]+)/); if(m&&m[1]) return m[1];
    return "";
  };
  const mp4Candidates = (cid) => {
    if(!cid) return [];
    const c=String(cid), a=c.slice(0,1), b=c.slice(0,3);
    const suf=["_dmb_w.mp4","_mhb_w.mp4","_dm_w.mp4","_sm_w.mp4"];
    const hosts=["https://cc3001.dmm.co.jp/litevideo/freepv/","https://cc3002.dmm.co.jp/litevideo/freepv/"];
    const list=[];
    for(const h of hosts) for(const s of suf) list.push(`${h}${a}/${b}/${c}/${c}${s}`);
    return list;
  };
  const trySetVideoSrc = (video, urls, onFail) => {
    const list=(urls||[]).filter(Boolean);
    let i=0;
    const cleanup=()=>{video.removeEventListener("error",onError);video.removeEventListener("loadeddata",onLoaded);};
    const onLoaded=()=>cleanup();
    const setSrc=(u)=>{try{video.src=u;video.load();}catch{onError();}};
    const onError=()=>{i+=1; if(i>=list.length){cleanup(); onFail&&onFail(); return;} setSrc(list[i]);};
    video.addEventListener("loadeddata",onLoaded,{once:true});
    video.addEventListener("error",onError);
    if(!list.length){cleanup(); onFail&&onFail(); return;}
    setSrc(list[0]);
  };

  // ---- Modal (in-place expand, keeps currentTime) ----
  const ensureModal = () => {
    let modal=q(".sv-modal");
    if(modal) return modal;
    modal=document.createElement("div");
    modal.className="sv-modal";
    modal.innerHTML=`
      <div class="sv-modal-inner" role="dialog" aria-modal="true" aria-label="拡大表示">
        <button class="sv-modal-close" type="button">閉じる</button>
        <div class="sv-modal-media"></div>
      </div>`;
    document.body.appendChild(modal);
    const close=()=>closeModal();
    modal.addEventListener("click",(e)=>{const inner=q(".sv-modal-inner",modal); if(inner && !inner.contains(e.target)) close();});
    q(".sv-modal-close",modal)?.addEventListener("click",close);
    document.addEventListener("keydown",(e)=>{if(e.key==="Escape" && modal.classList.contains("is-open")) close();});
    return modal;
  };

  let moved=null; // {video,parent,nextSibling,controls,muted,loop,wasPlaying}

  const closeModal = () => {
    const modal=q(".sv-modal"); if(!modal) return;
    const media=q(".sv-modal-media",modal);
    if(moved && moved.video){
      const v=moved.video;
      try{v.pause();}catch{}
      try{if(media && media.contains(v)) media.removeChild(v);}catch{}
      try{v.controls=moved.controls; v.muted=moved.muted; v.loop=moved.loop;}catch{}
      try{
        if(moved.nextSibling && moved.parent && moved.parent.contains(moved.nextSibling)) moved.parent.insertBefore(v,moved.nextSibling);
        else if(moved.parent) moved.parent.appendChild(v);
      }catch{}
      if(moved.wasPlaying){try{const p=v.play(); if(p&&p.catch)p.catch(()=>{});}catch{}}
      moved=null;
    }
    if(media) qa("iframe",media).forEach(f=>f.remove());
    modal.classList.remove("is-open");
  };

  const openModalWithVideoNode = (videoEl) => {
    const modal=ensureModal();
    const media=q(".sv-modal-media",modal);
    if(!media) return;
    if(moved && moved.video && moved.video!==videoEl) closeModal();
    if(!moved){
      moved={video:videoEl,parent:videoEl.parentElement,nextSibling:videoEl.nextSibling,controls:videoEl.controls,muted:videoEl.muted,loop:videoEl.loop,wasPlaying:!videoEl.paused};
    }
    modal.classList.add("is-open");
    try{videoEl.pause();}catch{}
    try{
      videoEl.controls=true; videoEl.loop=false;
      media.appendChild(videoEl);
      const p=videoEl.play(); if(p&&p.catch)p.catch(()=>{});
    }catch{}
  };

  const openModalWithIframe = (src) => {
    const modal=ensureModal();
    const media=q(".sv-modal-media",modal);
    if(!media) return;
    if(moved) closeModal();
    media.innerHTML="";
    const f=document.createElement("iframe");
    f.allow="autoplay; fullscreen; picture-in-picture";
    f.allowFullscreen=true;
    f.referrerPolicy="no-referrer-when-downgrade";
    f.src=src||"about:blank";
    media.appendChild(f);
    modal.classList.add("is-open");
  };

  // ---- Left nav (home) ----
  const inferRootPrefix = () => {
    const s=q('script[src*="assets/shorts_video.js"]');
    if(!s) return "../";
    const src=s.getAttribute("src")||"";
    const idx=src.lastIndexOf("assets/shorts_video.js");
    if(idx>=0) return src.slice(0,idx);
    return "../";
  };
  const buildLinksFromHeader = () => {
    const links=qa(".site-header a").map(a=>({href:a.getAttribute("href")||"", text:(a.textContent||"").trim()})).filter(x=>x.href&&x.text);
    const seen=new Set(); const out=[];
    for(const l of links){ if(seen.has(l.href)) continue; seen.add(l.href); out.push(l); }
    return out;
  };
  const ensureNav = () => {
    const root=inferRootPrefix();
    if(!q(".sv-leftnav")){
      const headerLinks=buildLinksFromHeader();
      const items=headerLinks.length ? headerLinks.slice(0,12) : [
        {text:"ホーム", href:root||"../"},
        {text:"検索", href:(root||"../")+"search/"},
        {text:"ショート", href:(root||"../")+"shorts/"},
      ];
      const nav=document.createElement("nav");
      nav.className="sv-leftnav";
      nav.innerHTML=`
        <div class="sv-navlist">
          <a href="${root}" aria-label="ホーム">ホーム</a>
          ${items.filter(it=>it.text!=="ホーム").map(it=>`<a href="${it.href}">${it.text}</a>`).join("")}
          <div class="sv-small">Escで拡大を閉じます</div>
        </div>`;
      document.body.appendChild(nav);
    }
    if(!q(".sv-homebtn")){
      const a=document.createElement("a");
      a.className="sv-homebtn"; a.href=root; a.textContent="ホーム"; a.setAttribute("aria-label","ホーム");
      document.body.appendChild(a);
    }
  };

  // ---- Init ----
  const initShortsUI = () => {
    const feed=q("#shortsFeed");
    if(!feed) return;

    document.body.classList.add("sv-shorts","sv-watch");
    ensureNav();

    let items=qa(".short-item",feed);
    if(!items.length) return;

    // 1) initial order
    items = initialOrder(items);
    const frag=document.createDocumentFragment();
    items.forEach(it=>frag.appendChild(it));
    feed.appendChild(frag);

    // 2) enhance each item
    items.forEach((item)=>{
      if(item.dataset.svEnhanced==="1") return;

      const iframe=q("iframe",item);
      if(!iframe) return;

      const id=item.getAttribute("data-id")||item.dataset.id||"";
      const url=iframe.getAttribute("data-src")||iframe.getAttribute("src")||"";
      const cid=parseCid(url);
      const mp4s=mp4Candidates(cid);

      const v=document.createElement("video");
      v.className="sv-enhanced-video sv-short-video";
      v.muted=true; v.loop=true; v.playsInline=true; v.preload="none";
      v.setAttribute("webkit-playsinline","true");

      iframe.classList.add("sv-hidden");
      iframe.parentElement.insertBefore(v, iframe);

      item.dataset.svEnhanced="1";
      item._sv={id:String(id), video:v, iframe, mp4s, url};

      const failToIframe=()=>{
        v.classList.add("sv-hidden");
        iframe.classList.remove("sv-hidden");
        const cur=iframe.getAttribute("src")||"";
        if(!cur || cur==="about:blank") iframe.setAttribute("src", url||"about:blank");
      };
      v._svFailToIframe=failToIframe;

      const actions=q(".short-actions",item);
      if(actions && !q(".sv-fav",actions)){
        const b=document.createElement("button");
        b.type="button"; b.className="btn btn-ghost sv-fav";
        b.setAttribute("aria-pressed","false");
        actions.insertBefore(b, actions.firstChild);
        updateFavButton(b, isFav(id));
      }
      if(actions && !q(".sv-expand",actions)){
        const b=document.createElement("button");
        b.type="button"; b.className="btn btn-ghost sv-expand";
        b.textContent="拡大";
        actions.appendChild(b);
      }
    });

    // 3) learning state
    let activeItem=null;
    let activeStart=0;

    const setActive = (item) => {
      const now=performance.now();
      if(activeItem && activeItem!==item && activeStart>0){
        const dwell=(now-activeStart)/1000;
        const dur = activeItem._sv && activeItem._sv.video ? activeItem._sv.video.duration : NaN;
        applyWatchSignal(activeItem, dwell, dur);
        try{reorderUpcoming(feed, activeItem);}catch{}
      }
      activeItem=item; activeStart=now;
    };
    // --- Preload strategy (TikTok-like, minimal extra traffic) ---
    // Active: preload="auto"
    // Next1 : preload="auto" + set src (no autoplay)
    // Next2 : preload="metadata" + set src (no autoplay)
    // NOTE: preloading must NOT trigger iframe fallback (heavy). On fail, clear src and retry on activation.
    const _svNextShortItem = (it, steps) => {
      let n = it;
      for(let k=0;k<steps;k++){
        if(!n) return null;
        n = n.nextElementSibling;
        while(n && !(n.classList && n.classList.contains("short-item"))){
          n = n.nextElementSibling;
        }
      }
      return n;
    };

    const _svGetPlayableVideo = (it) => {
      const d = it && it._sv;
      if(!d) return null;
      // If iframe is currently shown, don't preload video
      if(d.iframe && !d.iframe.classList.contains("sv-hidden")) return null;
      const v = d.video;
      if(!v || v.classList.contains("sv-hidden")) return null;
      // Don't touch the video while it's moved into the modal
      if(typeof moved !== "undefined" && moved && moved.video === v) return null;
      return { v, d };
    };

    const _svClearSrc = (v) => {
      try{ v.pause(); }catch{}
      try{ v.removeAttribute("src"); v.load(); }catch{}
    };

    const _svPrimeItem = (it, preloadMode) => {
      const pack = _svGetPlayableVideo(it);
      if(!pack) return;
      const { v, d } = pack;
      try{ v.preload = preloadMode; }catch{}
      // If src is not set yet, set it and start loading (without autoplay).
      // On total failure, clear src so activation can retry + fallback safely.
      if(!v.src){
        const onFail = () => _svClearSrc(v);
        try{ trySetVideoSrc(v, d.mp4s, onFail); }catch{ _svClearSrc(v); }
      } else {
        try{ v.load(); }catch{}
      }
    };

    const applyPreloadPolicy = () => {
      if(!activeItem) return;
      // active
      const curPack = _svGetPlayableVideo(activeItem);
      if(curPack){
        try{ curPack.v.preload = "auto"; }catch{}
      }
      // next items
      _svPrimeItem(_svNextShortItem(activeItem, 1), "auto");
      _svPrimeItem(_svNextShortItem(activeItem, 2), "metadata");
    };
    // --- /Preload strategy ---


    const activate=(item)=>{
      const data=item._sv;
      if(!data) return;

      setActive(item);

      qa(".short-item",feed).forEach(it=>{
        if(it===item) return;
        const d=it._sv; if(!d) return;
        try{d.video.pause();}catch{}
        try{d.video.preload="none";}catch{}
      });

      if(data.iframe && !data.iframe.classList.contains("sv-hidden")) return;

      const v=data.video;
      if(!v.src) trySetVideoSrc(v, data.mp4s, v._svFailToIframe);

      const p=v.play();
      if(p && p.catch) p.catch(()=>{});
      try{applyPreloadPolicy();}catch{}
    };

    // autoplay visible item
    const io=new IntersectionObserver((entries)=>{
      entries.forEach(e=>{
        if(e.isIntersecting && e.intersectionRatio>=0.6) activate(e.target);
      });
    },{threshold:[0.0,0.6,1.0]});
    qa(".short-item",feed).forEach(it=>io.observe(it));

    // tap pause/resume
    feed.addEventListener("click",(ev)=>{
      const t=ev.target;
      if(t && t.closest && t.closest("a,button")) return;
      const item=t && t.closest ? t.closest(".short-item") : null;
      if(!item || !item._sv) return;
      const v=item._sv.video;
      if(!v || v.classList.contains("sv-hidden")) return;
      if(v.paused) activate(item); else {try{v.pause();}catch{}}
    });

    // fav / expand
    feed.addEventListener("click",(ev)=>{
      const t=ev.target;

      const favBtn = t && t.closest ? t.closest(".sv-fav") : null;
      if(favBtn){
        ev.preventDefault(); ev.stopPropagation();
        const item=favBtn.closest(".short-item");
        if(!item || !item._sv) return;
        const on=toggleFav(item._sv.id, item);
        updateFavButton(favBtn, on);
        return;
      }

      const exp = t && t.closest ? t.closest(".sv-expand") : null;
      if(exp){
        ev.preventDefault(); ev.stopPropagation();
        const item=exp.closest(".short-item");
        if(!item || !item._sv) return;
        const data=item._sv;
        if(data.video && !data.video.classList.contains("sv-hidden") && data.video.src) openModalWithVideoNode(data.video);
        else openModalWithIframe(data.url);
      }
    });

    // first
    const first=q(".short-item",feed);
    if(first) activate(first);
  };

  document.addEventListener("DOMContentLoaded", () => {
    try { initShortsUI(); } catch {}
  });
})();