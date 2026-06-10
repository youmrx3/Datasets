(() => {
  const elements = {
    searchInput: document.getElementById('searchInput'),
    domainFilter: document.getElementById('domainFilter'),
    languageFilter: document.getElementById('languageFilter'),
    typeFilter: document.getElementById('typeFilter'),
    formatFilter: document.getElementById('formatFilter'),
    sortFilter: document.getElementById('sortFilter'),
    favoriteOnly: document.getElementById('favoriteOnly'),
    datasetsGrid: document.getElementById('datasetsGrid'),
    emptyState: document.getElementById('emptyState'),
    resultsCount: document.getElementById('resultsCount'),
    resultsLabel: document.getElementById('resultsLabel'),
    cardsViewBtn: document.getElementById('cardsViewBtn'),
    tableViewBtn: document.getElementById('tableViewBtn'),
    imageModal: document.getElementById('imageModal'),
    imageModalImg: document.getElementById('imageModalImg'),
    imageModalCaption: document.getElementById('imageModalCaption'),
  };

  function openImageModal(url, caption) {
    if (!elements.imageModal || !elements.imageModalImg || !elements.imageModalCaption) {
      return;
    }
    elements.imageModalImg.src = url;
    elements.imageModalImg.alt = caption || 'Image preview';
    elements.imageModalCaption.textContent = caption || '';
    elements.imageModal.classList.add('is-open');
    elements.imageModal.setAttribute('aria-hidden', 'false');
  }

  function closeImageModal() {
    if (!elements.imageModal || !elements.imageModalImg || !elements.imageModalCaption) {
      return;
    }
    elements.imageModal.classList.remove('is-open');
    elements.imageModal.setAttribute('aria-hidden', 'true');
    elements.imageModalImg.src = '';
    elements.imageModalCaption.textContent = '';
  }

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-image-preview]');
    if (trigger) {
      event.preventDefault();
      openImageModal(trigger.dataset.imagePreview, trigger.dataset.imageTitle);
      return;
    }

    if (event.target.closest('[data-modal-close]')) {
      closeImageModal();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeImageModal();
    }
  });

  document.addEventListener('click', async (event) => {
    const copyButton = event.target.closest('[data-copy-folder]');
    if (!copyButton) {
      return;
    }

    const folderPath = copyButton.dataset.copyFolder;
    if (!folderPath) {
      return;
    }

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(folderPath);
        copyButton.textContent = 'Copied';
        window.setTimeout(() => {
          copyButton.textContent = 'Copy path';
        }, 1200);
      }
    } catch (error) {
      console.error(error);
    }
  });

  const uploadInputs = document.getElementById('uploadInputs');
  const addUploadInputButton = document.getElementById('addUploadInput');

  function createUploadRow(index) {
    const row = document.createElement('label');
    row.className = 'upload-row';
    row.innerHTML = `
      <span>Image ${index}</span>
      <div class="upload-row-controls">
        <input name="screenshots" type="file" accept="image/*">
        <button class="button ghost upload-remove" type="button">Remove</button>
      </div>
    `;

    row.querySelector('.upload-remove')?.addEventListener('click', () => {
      if (!uploadInputs) {
        return;
      }
      const rows = uploadInputs.querySelectorAll('.upload-row');
      if (rows.length <= 1) {
        const input = row.querySelector('input[type="file"]');
        if (input) {
          input.value = '';
        }
        return;
      }
      row.remove();
      renumberUploadRows();
    });

    return row;
  }

  function renumberUploadRows() {
    if (!uploadInputs) {
      return;
    }
    Array.from(uploadInputs.querySelectorAll('.upload-row')).forEach((row, index) => {
      const label = row.querySelector('span');
      if (label) {
        label.textContent = `Image ${index + 1}`;
      }
    });
  }

  addUploadInputButton?.addEventListener('click', () => {
    if (!uploadInputs) {
      return;
    }
    const nextIndex = uploadInputs.querySelectorAll('.upload-row').length + 1;
    uploadInputs.appendChild(createUploadRow(nextIndex));
    renumberUploadRows();
  });

  if (uploadInputs) {
    uploadInputs.querySelectorAll('.upload-row').forEach((row) => {
      const input = row.querySelector('input[type="file"]');
      const removeButton = row.querySelector('.upload-remove');
      if (!removeButton) {
        const controls = row.querySelector('.upload-row-controls');
        if (controls) {
          const button = document.createElement('button');
          button.className = 'button ghost upload-remove';
          button.type = 'button';
          button.textContent = 'Remove';
          button.addEventListener('click', () => {
            const rows = uploadInputs.querySelectorAll('.upload-row');
            if (rows.length <= 1) {
              if (input) {
                input.value = '';
              }
              return;
            }
            row.remove();
            renumberUploadRows();
          });
          controls.appendChild(button);
        }
      }
    });
  }

  const app = window.DATASET_CATALOG;
  if (!app) {
    return;
  }

  const state = {
    view: localStorage.getItem('datasetCatalogView') || 'cards',
    query: '',
    domain: '',
    language: '',
    datasetType: '',
    datasetFormat: '',
    sort: 'created_desc',
    favoriteOnly: false,
    datasets: [],
    requestId: 0,
  };

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function formatDate(value) {
    if (!value) {
      return 'Unknown';
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    }).format(date);
  }

  function buildQueryParams() {
    const params = new URLSearchParams();
    if (state.query) params.set('q', state.query);
    if (state.domain) params.set('domain', state.domain);
    if (state.language) params.set('language', state.language);
    if (state.datasetType) params.set('dataset_type', state.datasetType);
    if (state.datasetFormat) params.set('format', state.datasetFormat);
    if (state.sort) params.set('sort', state.sort);
    if (state.favoriteOnly) params.set('favorite', '1');
    return params;
  }

  function setView(view) {
    state.view = view;
    localStorage.setItem('datasetCatalogView', view);
    elements.cardsViewBtn.classList.toggle('is-active', view === 'cards');
    elements.tableViewBtn.classList.toggle('is-active', view === 'table');
    render();
  }

  function chipMarkup(values) {
    if (!values || !values.length) {
      return '<span class="muted">None</span>';
    }
    return values.map((value) => `<span class="chip">${escapeHtml(value)}</span>`).join('');
  }

  function favoriteMarkup(dataset) {
    return dataset.favorite ? '<span class="chip favorite">Important</span>' : '';
  }

  function imagePreviewMarkup(dataset) {
    const attachment = dataset.attachments && dataset.attachments[0];
    if (!attachment) {
      const description = escapeHtml(dataset.description || dataset.notes || 'No preview available yet.');
      return `
        <div class="card-preview-fallback">
          <span class="fallback-eyebrow">Dataset preview</span>
          <strong>${escapeHtml(dataset.name)}</strong>
          <p>${description}</p>
          <div class="fallback-meta">
            <span>${escapeHtml(dataset.domain || 'Uncategorized')}</span>
            <span>${escapeHtml(dataset.dataset_type || 'Dataset')}</span>
            <span>${escapeHtml((dataset.languages_list || []).join(', ') || 'No languages')}</span>
          </div>
        </div>
      `;
    }
    return `<img class="card-image" src="${escapeHtml(attachment.url)}" alt="${escapeHtml(attachment.original_filename)}">`;
  }

  function renderCards() {
    return state.datasets.map((dataset) => `
      <article class="dataset-card">
        <a class="card-preview" href="/datasets/${dataset.id}">
          ${imagePreviewMarkup(dataset)}
        </a>
        <div class="card-body">
          <div class="card-topline">
            <div>
              <a class="card-title" href="/datasets/${dataset.id}">${escapeHtml(dataset.name)}</a>
              <p class="card-subtitle">${escapeHtml(dataset.domain || 'Uncategorized')} · ${escapeHtml(dataset.dataset_type || 'Dataset')}</p>
            </div>
            ${favoriteMarkup(dataset)}
          </div>
          <p class="card-description">${escapeHtml(dataset.description || 'No description provided yet.')}</p>
          <div class="card-meta">
            <div><span>Languages</span><strong>${escapeHtml((dataset.languages_list || []).join(', ') || 'None')}</strong></div>
            <div><span>Format</span><strong>${escapeHtml(dataset.format || 'Unknown')}</strong></div>
            <div><span>Updated</span><strong>${escapeHtml(formatDate(dataset.updated_at))}</strong></div>
          </div>
          <div class="card-tags">${chipMarkup(dataset.tags_list || [])}</div>
          <div class="card-actions">
            <a class="button ghost" href="/datasets/${dataset.id}">Open</a>
            ${dataset.folder_open_url ? `<a class="button ghost folder-link" href="${escapeHtml(dataset.folder_open_url)}">Folder</a>` : ''}
            <a class="button ghost" href="/datasets/${dataset.id}/edit">Edit</a>
            <form method="post" action="/datasets/${dataset.id}/delete" onsubmit="return confirm('Delete this dataset?');">
              <button class="button danger" type="submit">Delete</button>
            </form>
          </div>
        </div>
      </article>
    `).join('');
  }

  function renderTable() {
    const rows = state.datasets.map((dataset) => `
      <tr>
        <td>
          <div class="table-title-row">
            <a href="/datasets/${dataset.id}">${escapeHtml(dataset.name)}</a>
            ${dataset.favorite ? '<span class="chip favorite">Important</span>' : ''}
          </div>
          <p class="table-muted">${escapeHtml(dataset.description || 'No description yet.')}</p>
        </td>
        <td>${escapeHtml(dataset.domain || 'Uncategorized')}</td>
        <td>${escapeHtml(dataset.dataset_type || 'Dataset')}</td>
        <td>${escapeHtml((dataset.languages_list || []).join(', ') || 'None')}</td>
        <td>${escapeHtml(dataset.format || 'Unknown')}</td>
        <td>${escapeHtml((dataset.tags_list || []).join(', ') || 'None')}</td>
        <td>
          <div class="table-actions">
            <a class="button ghost" href="/datasets/${dataset.id}">Open</a>
            ${dataset.folder_open_url ? `<a class="button ghost folder-link" href="${escapeHtml(dataset.folder_open_url)}">Folder</a>` : ''}
            <a class="button ghost" href="/datasets/${dataset.id}/edit">Edit</a>
          </div>
        </td>
      </tr>
    `).join('');

    return `
      <div class="table-wrap">
        <table class="dataset-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Domain</th>
              <th>Type</th>
              <th>Languages</th>
              <th>Format</th>
              <th>Tags</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  function render() {
    if (!elements.datasetsGrid) {
      return;
    }
    if (state.datasets.length === 0) {
      elements.datasetsGrid.innerHTML = '';
      elements.emptyState.classList.remove('hidden');
      elements.resultsCount.textContent = '0 datasets';
      elements.resultsLabel.textContent = 'No matching datasets';
      return;
    }

    elements.emptyState.classList.add('hidden');
    elements.resultsCount.textContent = `${state.datasets.length} dataset${state.datasets.length === 1 ? '' : 's'}`;
    elements.resultsLabel.textContent = state.query || state.domain || state.language || state.datasetType || state.favoriteOnly
      ? 'Filtered datasets'
      : 'All datasets';
    elements.datasetsGrid.innerHTML = state.view === 'table' ? renderTable() : renderCards();
    elements.datasetsGrid.className = state.view === 'table' ? 'datasets-grid table-view' : 'datasets-grid cards-view';
  }

  async function loadDatasets() {
    const requestId = ++state.requestId;
    elements.resultsCount.textContent = 'Loading...';
    const response = await fetch(`${app.apiUrl}?${buildQueryParams().toString()}`);
    if (requestId !== state.requestId) {
      return;
    }
    const data = await response.json();
    state.datasets = data.datasets || [];
    render();
  }

  let searchTimer = null;
  function queueReload() {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(loadDatasets, 200);
  }

  elements.searchInput?.addEventListener('input', (event) => {
    state.query = event.target.value.trim();
    queueReload();
  });
  elements.domainFilter?.addEventListener('change', (event) => {
    state.domain = event.target.value;
    loadDatasets();
  });
  elements.languageFilter?.addEventListener('change', (event) => {
    state.language = event.target.value;
    loadDatasets();
  });
  elements.typeFilter?.addEventListener('change', (event) => {
    state.datasetType = event.target.value;
    loadDatasets();
  });
  elements.formatFilter?.addEventListener('change', (event) => {
    state.datasetFormat = event.target.value;
    loadDatasets();
  });
  elements.sortFilter?.addEventListener('change', (event) => {
    state.sort = event.target.value;
    loadDatasets();
  });
  elements.favoriteOnly?.addEventListener('change', (event) => {
    state.favoriteOnly = event.target.checked;
    loadDatasets();
  });
  elements.cardsViewBtn?.addEventListener('click', () => setView('cards'));
  elements.tableViewBtn?.addEventListener('click', () => setView('table'));

  setView(state.view);
  loadDatasets().catch((error) => {
    console.error(error);
    if (elements.resultsCount) {
      elements.resultsCount.textContent = 'Unable to load datasets';
    }
  });
})();
