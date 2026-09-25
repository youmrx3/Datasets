(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const csrfToken = $('meta[name="csrf-token"]')?.content || '';

  const storage = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
  };

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  async function postJson(url) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken, Accept: 'application/json' },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.message || `Request failed (${response.status}).`);
    }
    return data;
  }

  /* ---------------------------------------------------------------- Theme */

  $('[data-theme-toggle]')?.addEventListener('click', () => {
    const root = document.documentElement;
    const current = root.dataset.theme
      || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    storage.set('dc-theme', next);
  });

  /* --------------------------------------------------------------- Toasts */

  const toastStack = $('#toastStack');

  function dismissToast(toast) {
    toast.classList.add('is-leaving');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }

  function armToast(toast) {
    $('.toast-close', toast)?.addEventListener('click', () => dismissToast(toast));
    if (!toast.classList.contains('error')) {
      window.setTimeout(() => toast.isConnected && dismissToast(toast), 4500);
    }
  }

  function showToast(message, type = 'success') {
    if (!toastStack) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.setAttribute('role', 'status');
    toast.innerHTML = `<span>${escapeHtml(message)}</span><button type="button" class="toast-close" aria-label="Dismiss">×</button>`;
    toastStack.appendChild(toast);
    armToast(toast);
  }

  $$('.toast').forEach(armToast);

  /* ------------------------------------------------- Shared click actions */

  document.addEventListener('submit', (event) => {
    const form = event.target.closest('form[data-confirm]');
    if (form && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
    }
  });

  document.addEventListener('click', async (event) => {
    const star = event.target.closest('[data-favorite]');
    if (star) {
      event.preventDefault();
      try {
        const { favorite } = await postJson(`/api/datasets/${star.dataset.favorite}/favorite`);
        $$(`[data-favorite="${star.dataset.favorite}"]`).forEach((button) => {
          button.setAttribute('aria-pressed', String(favorite));
          const label = favorite ? 'Remove star' : 'Star this dataset';
          button.setAttribute('aria-label', label);
          button.title = label;
        });
        star.classList.add('pop');
        window.setTimeout(() => star.classList.remove('pop'), 180);
        catalogue?.onFavoriteChanged(Number(star.dataset.favorite), favorite);
      } catch (error) {
        showToast(error.message, 'error');
      }
      return;
    }

    const folder = event.target.closest('[data-open-folder]');
    if (folder) {
      event.preventDefault();
      try {
        const data = await postJson(`/api/datasets/${folder.dataset.openFolder}/open-folder`);
        showToast(data.message);
      } catch (error) {
        showToast(error.message, 'error');
      }
      return;
    }

    const copy = event.target.closest('[data-copy]');
    if (copy) {
      try {
        await navigator.clipboard.writeText(copy.dataset.copy);
        showToast('Path copied to clipboard.');
      } catch {
        showToast("Couldn't copy. Select the path and copy it manually.", 'error');
      }
    }
  });

  /* ------------------------------------------------------------- Lightbox */

  const lightbox = $('#lightbox');
  const lightboxState = { items: [], index: 0 };

  function showLightboxItem() {
    const item = lightboxState.items[lightboxState.index];
    if (!item) return;
    $('#lightboxImg').src = item.url;
    $('#lightboxImg').alt = item.title;
    $('#lightboxCaption').textContent = item.title;
    $('#lightboxCount').textContent = `${lightboxState.index + 1} / ${lightboxState.items.length}`;
    lightbox.classList.toggle('single', lightboxState.items.length < 2);
  }

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-image-preview]');
    if (!trigger || !lightbox) return;
    event.preventDefault();
    const group = trigger.closest('[data-gallery]');
    const triggers = group ? $$('[data-image-preview]', group) : [trigger];
    lightboxState.items = triggers.map((el) => ({ url: el.dataset.imagePreview, title: el.dataset.imageTitle || '' }));
    lightboxState.index = triggers.indexOf(trigger);
    showLightboxItem();
    lightbox.showModal();
  });

  lightbox?.addEventListener('click', (event) => {
    if (event.target === lightbox || event.target.closest('[data-lightbox-close]')) {
      lightbox.close();
    }
    const step = event.target.closest('[data-lightbox-step]');
    if (step) stepLightbox(Number(step.dataset.lightboxStep));
  });

  function stepLightbox(delta) {
    const count = lightboxState.items.length;
    if (count < 2) return;
    lightboxState.index = (lightboxState.index + delta + count) % count;
    showLightboxItem();
  }

  lightbox?.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowRight') stepLightbox(1);
    if (event.key === 'ArrowLeft') stepLightbox(-1);
  });

  /* ------------------------------------------------------------ Catalogue */

  const config = window.DATASET_CATALOG;
  const catalogue = config ? createCatalogue(config) : null;

  function createCatalogue(app) {
    const el = {
      grid: $('#datasetsGrid'),
      empty: $('#emptyState'),
      error: $('#loadError'),
      count: $('#resultsCount'),
      search: $('#searchInput'),
      sort: $('#sortFilter'),
      favorite: $('#favoriteOnly'),
      clear: $('#clearFilters'),
      cardsBtn: $('#cardsViewBtn'),
      tableBtn: $('#tableViewBtn'),
      exportCsv: $('#exportCsv'),
      exportJson: $('#exportJson'),
      spectrum: $('.spectrum'),
      tip: $('#chartTip'),
    };
    const selects = $$('select[data-param]').filter((select) => select !== el.sort);
    const FILTER_KEYS = ['q', 'language', 'dataset_type', 'format', 'domain', 'favorite'];
    const LOG_MIN = 1;
    const LOG_MAX = 6;

    const state = {
      view: storage.get('dc-view') === 'table' ? 'table' : 'cards',
      params: new URLSearchParams(window.location.search),
      datasets: [],
      requestId: 0,
    };

    function languageColor(name) {
      const slot = app.languageSlots[(name || '').toLowerCase()];
      return slot ? `var(--series-${slot})` : 'var(--series-other)';
    }

    function magnitude(samples) {
      if (!samples) return 0;
      const value = (Math.log10(Math.max(samples, 10)) - LOG_MIN) / (LOG_MAX - LOG_MIN);
      return Math.min(100, Math.max(2, value * 100));
    }

    function formatCount(value) {
      if (value == null) return '—';
      return new Intl.NumberFormat(undefined, { notation: value >= 10000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(value);
    }

    function diskSize(size) {
      const part = (size || '').split('/')[1];
      return part ? part.trim() : '';
    }

    function langChips(list) {
      return list.map((name) => `<span class="lang-chip"><i class="swatch" style="--c:${languageColor(name)}"></i>${escapeHtml(name)}</span>`).join('');
    }

    function starButton(dataset) {
      const label = dataset.favorite ? 'Remove star' : 'Star this dataset';
      return `<button class="star-button" type="button" data-favorite="${dataset.id}" aria-pressed="${dataset.favorite}" aria-label="${label}" title="${label}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9L12 3.5Z"/></svg>
      </button>`;
    }

    function folderAction(dataset) {
      if (!app.canOpenFolders || !dataset.has_folder) return '';
      return `<button class="card-action" type="button" data-open-folder="${dataset.id}">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-9Z"/></svg>Open folder</button>`;
    }

    function renderCards() {
      return state.datasets.map((d, index) => {
        const color = languageColor(d.languages_list[0]);
        const kicker = [d.types_list.join(', '), d.format].filter(Boolean).join(' · ');
        const thumb = d.thumbnail
          ? `<div class="card-thumb"><img src="${escapeHtml(d.thumbnail)}" alt="" loading="lazy"></div>`
          : `<div class="card-thumb is-empty" style="--c:${color}"></div>`;
        const disk = diskSize(d.size);
        return `
          <article class="card" style="--i:${Math.min(index, 20)}">
            ${thumb}
            <div class="card-body">
              <div class="card-top">
                <div>
                  <a class="card-title" href="${d.url}" dir="auto">${escapeHtml(d.name)}</a>
                  ${kicker ? `<p class="card-kicker">${escapeHtml(kicker)}</p>` : ''}
                </div>
                ${starButton(d)}
              </div>
              ${d.description ? `<p class="card-desc" dir="auto">${escapeHtml(d.description)}</p>` : ''}
              <div class="magnitude" title="${d.samples ? `${d.samples.toLocaleString()} samples` : 'No sample count recorded'}">
                <div class="magnitude-track"><div class="magnitude-fill" style="--w:${magnitude(d.samples)}%; --c:${color}"></div></div>
                <div class="magnitude-label mono">
                  <span>${d.samples ? `<strong>${formatCount(d.samples)}</strong> samples` : 'no sample count'}</span>
                  <span>${escapeHtml(disk)}</span>
                </div>
              </div>
              <div class="card-foot">
                ${langChips(d.languages_list)}
                ${d.tags_list.slice(0, 3).map((tag) => `<span class="tag small">#${escapeHtml(tag)}</span>`).join('')}
              </div>
            </div>
            <div class="card-actions">
              <a class="card-action" href="${d.edit_url}">
                <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/></svg>Edit</a>
              ${folderAction(d)}
              ${d.source_url ? `<a class="card-action" href="${escapeHtml(d.source_url)}" target="_blank" rel="noopener noreferrer">
                <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-8 8M17 14v4a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 5 18V8.5A1.5 1.5 0 0 1 6.5 7H10"/></svg>Source</a>` : ''}
            </div>
          </article>`;
      }).join('');
    }

    function renderTable() {
      const rows = state.datasets.map((d) => {
        const color = languageColor(d.languages_list[0]);
        return `
          <tr>
            <td class="star-cell">${starButton(d)}</td>
            <td>
              <a class="table-name" href="${d.url}" dir="auto">${escapeHtml(d.name)}</a>
              ${d.description ? `<p class="table-desc" dir="auto">${escapeHtml(d.description)}</p>` : ''}
            </td>
            <td><div class="table-langs">${langChips(d.languages_list) || '<span class="muted">—</span>'}</div></td>
            <td>${escapeHtml(d.types_list.join(', ')) || '<span class="muted">—</span>'}</td>
            <td class="mono">${escapeHtml(d.format || '—')}</td>
            <td class="num mono">${d.samples ? d.samples.toLocaleString() : '<span class="muted">—</span>'}<span class="mini-bar"><i style="--w:${magnitude(d.samples)}%; --c:${color}"></i></span></td>
            <td class="mono muted">${escapeHtml(diskSize(d.size) || '—')}</td>
            <td class="mono muted">${escapeHtml(formatDate(d.updated_at))}</td>
          </tr>`;
      }).join('');
      return `
        <table class="data-table">
          <thead><tr>
            <th><span class="visually-hidden">Starred</span></th><th>Name</th><th>Languages</th><th>Modality</th><th>Format</th>
            <th class="num">Samples</th><th>Disk</th><th>Edited</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }

    function formatDate(value) {
      const date = new Date(value);
      if (!value || Number.isNaN(date.getTime())) return value || '';
      return new Intl.DateTimeFormat(undefined, { day: '2-digit', month: 'short', year: 'numeric' }).format(date);
    }

    function hasFilters() {
      return FILTER_KEYS.some((key) => state.params.get(key));
    }

    function render() {
      const total = state.datasets.length;
      el.grid.setAttribute('aria-busy', 'false');
      el.grid.className = `datasets ${state.view}-view`;
      el.empty.classList.toggle('hidden', total > 0);
      el.clear.classList.toggle('hidden', !hasFilters());
      el.count.innerHTML = hasFilters()
        ? `<strong>${total}</strong> matching dataset${total === 1 ? '' : 's'}`
        : `<strong>${total}</strong> dataset${total === 1 ? '' : 's'}`;
      el.grid.innerHTML = total === 0 ? '' : (state.view === 'table' ? renderTable() : renderCards());
    }

    function renderSkeleton() {
      if (state.view === 'cards' && !state.datasets.length) {
        el.grid.innerHTML = '<div class="skeleton"></div>'.repeat(6);
      }
    }

    function syncControls() {
      el.search.value = state.params.get('q') || '';
      selects.forEach((select) => {
        const value = state.params.get(select.dataset.param) || '';
        // Match case-insensitively so links like ?language=arabic still select the option.
        const option = Array.from(select.options).find((o) => o.value.toLowerCase() === value.toLowerCase());
        select.value = option ? option.value : '';
        select.classList.toggle('is-set', Boolean(select.value));
      });
      el.sort.value = state.params.get('sort') || 'created_desc';
      el.favorite.setAttribute('aria-pressed', String(state.params.get('favorite') === '1'));
      el.cardsBtn.setAttribute('aria-pressed', String(state.view === 'cards'));
      el.tableBtn.setAttribute('aria-pressed', String(state.view === 'table'));
      syncSpectrum();
    }

    function syncSpectrum() {
      if (!el.spectrum) return;
      const language = (state.params.get('language') || '').toLowerCase();
      el.spectrum.classList.toggle('is-filtered', Boolean(language));
      $$('.bar', el.spectrum).forEach((bar) => {
        const languages = (bar.dataset.languages || '').toLowerCase().split('|');
        bar.classList.toggle('is-match', Boolean(language) && languages.includes(language));
      });
      $$('[data-legend-language]', el.spectrum).forEach((item) => {
        item.setAttribute('aria-pressed', String(item.dataset.legendLanguage.toLowerCase() === language));
      });
    }

    function syncUrl() {
      const query = state.params.toString();
      window.history.replaceState(null, '', query ? `?${query}` : window.location.pathname);
      const exportQuery = new URLSearchParams(state.params);
      el.exportCsv.href = `${app.exportCsv}?${exportQuery}`;
      el.exportJson.href = `${app.exportJson}?${exportQuery}`;
    }

    function setParam(key, value) {
      if (value) state.params.set(key, value);
      else state.params.delete(key);
      syncControls();
      syncUrl();
      load();
    }

    async function load() {
      const requestId = ++state.requestId;
      el.grid.setAttribute('aria-busy', 'true');
      renderSkeleton();
      try {
        const response = await fetch(`${app.apiUrl}?${state.params}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (requestId !== state.requestId) return;
        state.datasets = data.datasets || [];
        el.error.classList.add('hidden');
        render();
      } catch (error) {
        if (requestId !== state.requestId) return;
        console.error(error);
        el.grid.innerHTML = '';
        el.count.textContent = '';
        el.error.classList.remove('hidden');
      }
    }

    let searchTimer = null;
    el.search.addEventListener('input', () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => setParam('q', el.search.value.trim()), 180);
    });
    el.search.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && el.search.value) {
        el.search.value = '';
        setParam('q', '');
      }
    });
    selects.forEach((select) => select.addEventListener('change', () => setParam(select.dataset.param, select.value)));
    el.sort.addEventListener('change', () => setParam('sort', el.sort.value === 'created_desc' ? '' : el.sort.value));
    el.favorite.addEventListener('click', () => setParam('favorite', state.params.get('favorite') === '1' ? '' : '1'));

    function clearFilters() {
      FILTER_KEYS.forEach((key) => state.params.delete(key));
      syncControls();
      syncUrl();
      load();
    }
    el.clear.addEventListener('click', clearFilters);
    $$('[data-clear-filters]').forEach((button) => button.addEventListener('click', clearFilters));

    function setView(view) {
      state.view = view;
      storage.set('dc-view', view);
      syncControls();
      render();
    }
    el.cardsBtn.addEventListener('click', () => setView('cards'));
    el.tableBtn.addEventListener('click', () => setView('table'));

    // "/" focuses search, like most research tools.
    document.addEventListener('keydown', (event) => {
      const typing = event.target.closest('input, textarea, select, [contenteditable]');
      if (event.key === '/' && !typing && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        el.search.focus();
      }
    });

    // Spectrum: legend filters by language, bars show a tooltip.
    if (el.spectrum) {
      $$('.bar', el.spectrum).forEach((bar, index) => bar.style.setProperty('--i', index));
      $$('[data-legend-language]', el.spectrum).forEach((item) => {
        item.addEventListener('click', () => {
          const current = (state.params.get('language') || '').toLowerCase();
          const value = item.dataset.legendLanguage;
          setParam('language', current === value.toLowerCase() ? '' : value);
        });
      });

      const showTip = (bar) => {
        const [title, value, meta] = $$('strong, span, em', el.tip);
        title.textContent = bar.dataset.tipTitle;
        value.textContent = bar.dataset.tipValue;
        meta.textContent = bar.dataset.tipMeta;
        el.tip.hidden = false;
        const rect = bar.getBoundingClientRect();
        const tipRect = el.tip.getBoundingClientRect();
        const left = Math.min(Math.max(8, rect.left + rect.width / 2 - tipRect.width / 2), window.innerWidth - tipRect.width - 8);
        const barTop = rect.bottom - (rect.height * parseFloat(bar.style.getPropertyValue('--h') || 0)) / 100;
        el.tip.style.left = `${left}px`;
        el.tip.style.top = `${Math.max(8, barTop - tipRect.height - 10)}px`;
      };
      const hideTip = () => { el.tip.hidden = true; };
      $$('.bar', el.spectrum).forEach((bar) => {
        bar.addEventListener('mouseenter', () => showTip(bar));
        bar.addEventListener('focus', () => showTip(bar));
        bar.addEventListener('mouseleave', hideTip);
        bar.addEventListener('blur', hideTip);
      });
      window.addEventListener('scroll', hideTip, { passive: true });
    }

    syncControls();
    syncUrl();
    load();

    return {
      onFavoriteChanged(id, favorite) {
        const dataset = state.datasets.find((d) => d.id === id);
        if (dataset) dataset.favorite = favorite;
        if (!favorite && state.params.get('favorite') === '1') load();
      },
    };
  }

  /* ----------------------------------------------------------- Form page */

  // Chip groups: type a new value and press Enter to add it as a checked chip.
  $$('[data-chip-group]').forEach((group) => {
    const input = $('[data-chip-new]', group);
    const name = group.dataset.chipGroup;
    input?.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ',') return;
      event.preventDefault();
      const value = input.value.trim().replace(/,$/, '');
      if (!value) return;
      const existing = $$('input[type="checkbox"]', group).find((box) => box.value.toLowerCase() === value.toLowerCase());
      if (existing) {
        existing.checked = true;
      } else {
        const label = document.createElement('label');
        label.className = 'chip-option';
        label.innerHTML = `<input type="checkbox" name="${name}" value="${escapeHtml(value)}" checked><span>${escapeHtml(value)}</span>`;
        input.parentElement.before(label);
      }
      input.value = '';
    });
  });

  // Live preview of the sample count parsed from the Size field.
  const sizeInput = $('[data-size-input]');
  const sizeHint = $('[data-size-hint]');
  if (sizeInput && sizeHint) {
    const defaultHint = sizeHint.textContent;
    const parse = (text) => {
      const head = (text || '').split('/')[0];
      const match = head.match(/(\d[\d,.\s]*\d|\d)\s*([kKmM])?\b/);
      if (!match) return null;
      let raw = match[1].replace(/\s/g, '');
      raw = /^\d{1,3}([.,]\d{3})+$/.test(raw) ? raw.replace(/[.,]/g, '') : raw.replace(/,/g, '');
      const number = Number(raw) * ({ k: 1e3, m: 1e6 }[(match[2] || '').toLowerCase()] || 1);
      return Number.isFinite(number) ? Math.round(number) : null;
    };
    const update = () => {
      const count = parse(sizeInput.value);
      sizeHint.innerHTML = count
        ? `Read as <strong>${count.toLocaleString()} samples</strong>.`
        : escapeHtml(defaultHint);
    };
    sizeInput.addEventListener('input', update);
    update();
  }

  // Image dropzone: click, drag-and-drop or paste. Files accumulate across picks.
  const dropzone = $('[data-dropzone]');
  if (dropzone) {
    const input = $('[data-dropzone-input]', dropzone);
    const previews = $('[data-dropzone-previews]');
    const allowed = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
    let files = [];

    const sync = () => {
      const transfer = new DataTransfer();
      files.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
      previews.innerHTML = '';
      files.forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'pending-image';
        const url = URL.createObjectURL(file);
        item.innerHTML = `<img src="${url}" alt=""><span>${escapeHtml(file.name)}</span>
          <button type="button" aria-label="Remove ${escapeHtml(file.name)}">×</button>`;
        $('img', item).addEventListener('load', () => URL.revokeObjectURL(url), { once: true });
        $('button', item).addEventListener('click', () => {
          files.splice(index, 1);
          sync();
        });
        previews.appendChild(item);
      });
    };

    const addFiles = (list) => {
      const incoming = Array.from(list || []);
      const images = incoming.filter((file) => allowed.includes(file.type));
      if (images.length < incoming.length) {
        showToast('Only PNG, JPG, GIF and WebP images can be attached.', 'error');
      }
      images.forEach((file, i) => {
        // Pasted screenshots all arrive as "image.png"; give them distinct names.
        if (file.name === 'image.png') {
          const stamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-');
          file = new File([file], `pasted-${stamp}-${i + 1}.png`, { type: file.type });
        }
        files.push(file);
      });
      sync();
    };

    dropzone.addEventListener('click', (event) => {
      if (event.target !== input) input.click();
    });
    dropzone.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        input.click();
      }
    });
    input.addEventListener('click', (event) => event.stopPropagation());
    input.addEventListener('change', () => {
      // input.files was replaced by the picker; merge with what we already had.
      const picked = Array.from(input.files);
      addFiles(picked);
    });
    ['dragenter', 'dragover'].forEach((type) => dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.add('is-over');
    }));
    ['dragleave', 'drop'].forEach((type) => dropzone.addEventListener(type, () => dropzone.classList.remove('is-over')));
    dropzone.addEventListener('drop', (event) => {
      event.preventDefault();
      addFiles(event.dataTransfer.files);
    });
    document.addEventListener('paste', (event) => {
      const pasted = Array.from(event.clipboardData?.files || []);
      if (!pasted.length) return;
      event.preventDefault();
      addFiles(pasted);
      showToast(`${pasted.length} image${pasted.length === 1 ? '' : 's'} added from the clipboard.`);
    });
  }

  // Warn before leaving a form with unsaved changes.
  const guarded = $('form[data-dirty-guard]');
  if (guarded) {
    let dirty = false;
    guarded.addEventListener('input', () => { dirty = true; });
    guarded.addEventListener('change', () => { dirty = true; });
    guarded.addEventListener('submit', () => { dirty = false; });
    window.addEventListener('beforeunload', (event) => {
      if (dirty) event.preventDefault();
    });
  }

  /* --------------------------------------------------------- Options page */

  $$('.option-rename').forEach((form) => {
    const input = $('input[name="value"]', form);
    input.addEventListener('input', () => {
      form.classList.toggle('is-dirty', input.value.trim() !== input.dataset.original);
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        input.value = input.dataset.original;
        form.classList.remove('is-dirty');
        input.blur();
      }
    });
  });
})();
