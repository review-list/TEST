(() => {
  const SEARCH_KEY = 'ReviewCatalog-v1';
  const PAGE_SIZE = 24;

  const els = {
    q: document.getElementById('q'),
    maker: document.getElementById('maker'),
    series: document.getElementById('series'),
    hasImg: document.getElementById('hasImg'),
    hasMov: document.getElementById('hasMov'),
    clear: document.getElementById('clear'),
    popularTags: document.getElementById('popularTags'),
    selectedTags: document.getElementById('selectedTags'),
    status: document.getElementById('status'),
    results: document.getElementById('results'),
    sentinel: document.getElementById('sentinel'),
  };

  if (!els.results || !window.__SEARCH_EMBED__) return;

  const state = {
    manifest: null,
    cards: [],
    filtered: [],
    rendered: 0,
    selectedTags: new Set(),
    observer: null,
    loading: false,
  };

  const textDecoder = new TextDecoder('utf-8');

  const esc = (s) => String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

  const yen = (n) => {
    try { return new Intl.NumberFormat('ja-JP').format(Number(n || 0)); } catch { return String(n || ''); }
  };

  const normalize = (value) => String(value || '')
    .toLowerCase()
    .normalize('NFKC')
    .replace(/[\u3000\s]+/g, ' ')
    .trim();

  const base64ToBytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

  const xorBytes = (bytes) => {
    const key = new TextEncoder().encode(SEARCH_KEY);
    const out = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i += 1) out[i] = bytes[i] ^ key[i % key.length];
    return out;
  };

  const inflateGzip = async (bytes) => {
    if (typeof DecompressionStream !== 'undefined') {
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      const ab = await new Response(stream).arrayBuffer();
      return new Uint8Array(ab);
    }
    throw new Error('このブラウザは gzip 展開に対応していません。Chrome / Edge の最新版で開いてください。');
  };

  const decodePayload = async (b64) => {
    const obfuscated = base64ToBytes(b64);
    const gz = xorBytes(obfuscated);
    const raw = await inflateGzip(gz);
    return JSON.parse(textDecoder.decode(raw));
  };

  const buildNeedle = (card) => normalize([
    card.title,
    card.id,
    card.maker,
    card.series,
    ...(card.tags || []),
    ...(card.actresses || []),
  ].join(' '));

  const mapCard = (row) => ({
    id: row[0] || '',
    title: row[1] || '',
    releaseDate: String(row[2] || '').replace(' 00:00:00', ''),
    heroImage: row[3] || '',
    path: row[4] || '',
    tags: Array.isArray(row[5]) ? row[5] : [],
    actresses: Array.isArray(row[6]) ? row[6] : [],
    maker: row[7] || '',
    series: row[8] || '',
    hasImg: !!row[9],
    imgCount: Number(row[10] || 0),
    hasMov: !!row[11],
    apiRank: row[12] ?? null,
    needle: '',
  });

  const renderSelectOptions = (select, values) => {
    if (!select) return;
    const current = select.value;
    select.innerHTML = '<option value="">すべて</option>';
    values.forEach((value) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
    select.value = values.includes(current) ? current : '';
  };

  const renderTags = () => {
    if (!els.popularTags || !els.selectedTags) return;

    els.popularTags.innerHTML = '';
    (state.manifest?.pt || []).forEach(([tag, count]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'tag';
      if (state.selectedTags.has(tag)) button.classList.add('is-active');
      button.innerHTML = `${esc(tag)} <span class="tag-count">${yen(count)}</span>`;
      button.addEventListener('click', () => {
        if (state.selectedTags.has(tag)) state.selectedTags.delete(tag);
        else state.selectedTags.add(tag);
        renderTags();
        applyFilters();
      });
      els.popularTags.appendChild(button);
    });

    els.selectedTags.innerHTML = '';
    if (!state.selectedTags.size) {
      const empty = document.createElement('span');
      empty.className = 'muted';
      empty.textContent = '未選択';
      els.selectedTags.appendChild(empty);
      return;
    }

    Array.from(state.selectedTags).sort((a, b) => a.localeCompare(b, 'ja')).forEach((tag) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'tag is-active';
      button.textContent = `${tag} ×`;
      button.addEventListener('click', () => {
        state.selectedTags.delete(tag);
        renderTags();
        applyFilters();
      });
      els.selectedTags.appendChild(button);
    });
  };

  const cardHtml = (card) => {
    const href = `${window.__ROOT_PATH__ || '../'}${card.path}`;
    const metaBits = [card.releaseDate, card.maker].filter(Boolean);
    const people = card.actresses?.slice(0, 2).filter(Boolean) || [];
    if (people.length) metaBits.push(people.join(' / '));

    const badges = [];
    if (card.hasImg) badges.push('<span class="badge badge-img">🖼️ サンプル画像あり</span>');
    if (card.hasMov) badges.push('<span class="badge badge-mov">🎬 サンプル動画あり</span>');

    return `
      <a class="work-card search-card" href="${esc(href)}" aria-label="${esc(card.title)}">
        <div class="work-thumb">
          ${card.heroImage
            ? `<img loading="lazy" src="${esc(card.heroImage)}" alt="${esc(card.title)}">`
            : '<div class="thumb-placeholder"></div>'}
          <div class="work-badges">${badges.join('')}</div>
        </div>
        <div class="work-meta">
          <div class="work-title">${esc(card.title)}</div>
          <div class="work-sub search-card__sub">
            ${metaBits.map((bit, idx) => `${idx ? '<span class="dot">•</span>' : ''}<span class="muted">${esc(bit)}</span>`).join('')}
          </div>
          ${card.tags?.length
            ? `<div class="search-card__tags">${card.tags.slice(0, 4).map((tag) => `<span class="chip search-chip">${esc(tag)}</span>`).join('')}</div>`
            : ''}
        </div>
      </a>`;
  };

  const renderNextPage = () => {
    if (state.rendered >= state.filtered.length) return;
    const nextItems = state.filtered.slice(state.rendered, state.rendered + PAGE_SIZE);
    const html = nextItems.map(cardHtml).join('');
    els.results.insertAdjacentHTML('beforeend', html);
    state.rendered += nextItems.length;
    updateStatus();
  };

  const updateStatus = () => {
    if (!els.status) return;
    const total = state.filtered.length;
    if (state.loading) {
      els.status.textContent = '検索データを読み込み中…';
      return;
    }
    if (!state.cards.length) {
      els.status.textContent = '検索データがありません。';
      return;
    }
    if (!total) {
      els.status.textContent = '該当作品はありません。条件を変えてみてください。';
      return;
    }
    els.status.textContent = `${total.toLocaleString('ja-JP')}件ヒット / ${state.rendered.toLocaleString('ja-JP')}件表示`;
  };

  const renderEmpty = () => {
    els.results.innerHTML = '<div class="search-empty">該当作品はありません。キーワードや条件を変えてください。</div>';
    state.rendered = 0;
    updateStatus();
  };

  const applyFilters = () => {
    const q = normalize(els.q?.value || '');
    const maker = normalize(els.maker?.value || '');
    const series = normalize(els.series?.value || '');
    const needImg = !!els.hasImg?.checked;
    const needMov = !!els.hasMov?.checked;
    const selectedTags = Array.from(state.selectedTags);

    state.filtered = state.cards.filter((card) => {
      if (q && !card.needle.includes(q)) return false;
      if (maker && normalize(card.maker) !== maker) return false;
      if (series && normalize(card.series) !== series) return false;
      if (needImg && !card.hasImg) return false;
      if (needMov && !card.hasMov) return false;
      if (selectedTags.length && !selectedTags.every((tag) => card.tags.includes(tag))) return false;
      return true;
    });

    els.results.innerHTML = '';
    state.rendered = 0;

    if (!state.filtered.length) {
      renderEmpty();
      return;
    }

    renderNextPage();
  };

  const bindEvents = () => {
    ['input', 'change'].forEach((eventName) => {
      els.q?.addEventListener(eventName, applyFilters);
      els.maker?.addEventListener(eventName, applyFilters);
      els.series?.addEventListener(eventName, applyFilters);
      els.hasImg?.addEventListener(eventName, applyFilters);
      els.hasMov?.addEventListener(eventName, applyFilters);
    });

    els.clear?.addEventListener('click', () => {
      if (els.q) els.q.value = '';
      if (els.maker) els.maker.value = '';
      if (els.series) els.series.value = '';
      if (els.hasImg) els.hasImg.checked = false;
      if (els.hasMov) els.hasMov.checked = false;
      state.selectedTags.clear();
      renderTags();
      applyFilters();
    });

    if (els.sentinel) {
      state.observer = new IntersectionObserver((entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        renderNextPage();
      }, { rootMargin: '320px 0px' });
      state.observer.observe(els.sentinel);
    }
  };

  const loadSearchData = async () => {
    state.loading = true;
    updateStatus();

    state.manifest = await decodePayload(window.__SEARCH_EMBED__);
    renderSelectOptions(els.maker, state.manifest.mk || []);
    renderSelectOptions(els.series, state.manifest.sr || []);
    renderTags();

    const rootPath = window.__ROOT_PATH__ || '../';
    const chunkDefs = Array.isArray(state.manifest.c) ? state.manifest.c : [];
    const cardRows = [];

    for (const [chunkPath] of chunkDefs) {
      const url = `${rootPath}assets/${chunkPath}`;
      const response = await fetch(url, { cache: 'no-cache' });
      if (!response.ok) throw new Error(`検索データの読込に失敗しました: ${response.status}`);
      const body = await response.text();
      const rows = await decodePayload(body.trim());
      if (Array.isArray(rows)) cardRows.push(...rows);
    }

    state.cards = cardRows.map(mapCard);
    state.cards.forEach((card) => { card.needle = buildNeedle(card); });
    state.loading = false;
    applyFilters();
  };

  bindEvents();
  loadSearchData().catch((error) => {
    console.error(error);
    state.loading = false;
    els.results.innerHTML = `<div class="search-empty">検索データの読み込みに失敗しました。build後にもう一度確認してください。</div>`;
    if (els.status) els.status.textContent = '検索データの読み込みに失敗しました。';
  });
})();
