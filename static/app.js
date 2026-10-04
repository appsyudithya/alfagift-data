/* Alfagift Data — antarmuka web. */
(() => {
  'use strict';

  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const rp = n => (n || n === 0) ? 'Rp ' + Number(n).toLocaleString('id-ID') : '–';
  const num = n => Number(n || 0).toLocaleString('id-ID');
  const fmtDate = s => s ? new Date(s).toLocaleString('id-ID', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–';
  const ago = s => {
    if (!s) return '–';
    const m = Math.round((Date.now() - new Date(s)) / 60000);
    if (m < 1) return 'baru saja';
    if (m < 60) return `${m} menit lalu`;
    if (m < 1440) return `${Math.round(m / 60)} jam lalu`;
    return `${Math.round(m / 1440)} hari lalu`;
  };

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    return r.json();
  }

  function toast(msg, err = false) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = 'toast' + (err ? ' err' : '');
    t.hidden = false;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { t.hidden = true; }, 3500);
  }

  // ─── Tabs ─────────────────────────────────────
  $('#tabs').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b));
    document.querySelectorAll('.tab').forEach(p => { p.hidden = p.dataset.pane !== b.dataset.tab; });
    if (b.dataset.tab === 'categories') loadCategories();
    if (b.dataset.tab === 'highlights') loadHighlights();
  });

  // ─── Status & ringkasan ───────────────────────
  const RUN_STATUS = {
    running: ['Berjalan', 'disc'], done: ['Selesai', 'ok'], limit: ['Batas tercapai', 'disc'],
    blocked: ['Dihentikan Alfagift', 'no'], error: ['Error', 'no'],
  };
  let status = null, pollTimer = 0, wasRunning = false;

  async function loadStatus() {
    status = await api('/api/status');
    const { counts: c, settings: s, runs } = status;
    const last = runs.find(r => r.status !== 'running');
    const nextTxt = status.next_run
      ? ` · berikutnya ${new Date(status.next_run).toLocaleString('id-ID', { weekday: 'short', hour: '2-digit', minute: '2-digit' })}`
      : ' · jadwal otomatis nonaktif';
    $('#status-line').textContent = status.running
      ? '⏳ Crawler sedang berjalan…'
      : (last ? `Crawl terakhir ${ago(last.finished || last.started)} · ${RUN_STATUS[last.status]?.[0] || last.status}` : 'Belum pernah crawl') + nextTxt;
    $('#run-btn').disabled = status.running;
    $('#run-btn').textContent = status.running ? 'Sedang berjalan…' : 'Jalankan sekarang';
    $('#stats').innerHTML = [
      ['Produk tersimpan', num(c.total)],
      ['Tersedia', num(c.in_stock), c.total ? `${Math.round((c.in_stock || 0) / c.total * 100)}%` : ''],
      ...(s.city ? [[`Ada stok di ${esc(s.city)}`, num(c.in_city)]] : []),
      ['Sedang diskon', num(c.discounted)],
      ['Harga berubah (7 hari)', num(c.changed_7d)],
      ['Toko', s.account ? `Alfamart ${esc(s.account.store_code)}` : '–', s.account ? esc(s.account.city) : ''],
    ].map(([k, v, sub]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v} ${sub ? `<small>${sub}</small>` : ''}</div></div>`).join('');
    renderRuns(runs);
    $('#f-city-label').textContent = s.city ? `Ada stok di ${s.city}` : 'Ada stok di kota';
    $('#store-notice').hidden = !s.city || c.in_city > 0 || status.running;
    $('#store-notice').innerHTML = `Belum ada data. Klik <b>Jalankan sekarang</b> untuk mengambil semua produk Alfagift dengan harga toko ${esc(s.city)} (±3–4 menit).`;

    // Selama crawler berjalan, perbarui otomatis
    clearTimeout(pollTimer);
    if (status.running) pollTimer = setTimeout(loadStatus, 3000);
    if (wasRunning && !status.running) { loadHighlights(); loadProducts(); loadBrands(); loadCatFilter(); loadStores(); notifyRunEnd(runs[0]); }
    wasRunning = status.running;
  }

  function notifyRunEnd(r) {
    if (!r) return;
    const [label] = RUN_STATUS[r.status] || [r.status];
    toast(`${label}: ${num(r.products)} produk, ${num(r.new_products)} baru, ${num(r.price_changes)} harga berubah, ${num(r.stock_checks)} ada di kota`, r.status === 'blocked' || r.status === 'error');
  }

  function renderRuns(runs) {
    $('#runs-table tbody').innerHTML = runs.length ? runs.map(r => {
      const [label, cls] = RUN_STATUS[r.status] || [r.status, ''];
      return `<tr><td>${fmtDate(r.started)}</td><td>${r.trigger === 'scheduled' ? 'Terjadwal' : 'Manual'}</td>
        <td><span class="badge ${cls}">${label}</span></td><td class="num">${num(r.requests)}</td>
        <td class="num">${num(r.products)}</td><td class="num">${num(r.new_products)}</td><td class="num">${num(r.price_changes)}</td><td class="num">${num(r.stock_checks)}</td>
        <td class="pmeta">${esc(r.message || '')}</td></tr>`;
    }).join('') : `<tr><td colspan="9" class="empty">Belum ada riwayat crawl.</td></tr>`;
  }

  $('#run-btn').onclick = async () => {
    $('#run-btn').disabled = true;
    const r = await api('/api/run', { method: 'POST' });
    toast(r.started ? 'Crawler dimulai' : 'Crawler sudah berjalan');
    setTimeout(loadStatus, 500);
  };

  // ─── Produk ───────────────────────────────────
  let page = 1;
  const pref = (k, d) => { try { return localStorage.getItem('rr.' + k) ?? d; } catch (_) { return d; } };
  const savePref = (k, v) => { try { localStorage.setItem('rr.' + k, v); } catch (_) {} };
  $('#f-stock').value = pref('stock', '1');
  $('#f-city').checked = pref('cityOnly', '1') === '1';
  const city = () => status?.settings.city || '';
  // "Kota - Nama Toko" → "Nama Toko"
  const shortStore = s => s.split(' - ').slice(1).join(' - ') || s;
  const SIZE = 50;

  function filterQuery() {
    const [sort, dir] = $('#f-sort').value.split('|');
    const q = new URLSearchParams({ q: $('#f-q').value.trim(), city: $('#f-city').checked && city() ? '1' : '',
      store: $('#f-store').value, cat: $('#f-cat').value, brand: $('#f-brand').value,
      stock: $('#f-stock').value, sort, dir });
    if ($('#f-discount').checked) q.set('discount', '1');
    if ($('#f-changed').checked) q.set('changed', '1');
    for (const [k, v] of [...q]) if (!v) q.delete(k);
    return q;
  }

  async function loadProducts() {
    const q = filterQuery();
    $('#export-btn').href = '/api/export.csv?' + q;
    q.set('page', page);
    q.set('size', SIZE);
    const d = await api('/api/products?' + q);
    const tb = $('#product-table tbody');
    if (!d.items.length) {
      tb.innerHTML = `<tr><td colspan="8" class="empty">${status?.counts.total
        ? 'Tidak ada produk yang cocok dengan filter.'
        : 'Belum ada data. Pilih kategori di tab <b>Kategori dipantau</b>, lalu klik <b>Jalankan sekarang</b>.'}</td></tr>`;
    } else {
      tb.innerHTML = d.items.map(p => {
        const ch = p.last_change && p.prev_price != null ? p.final_price - p.prev_price : 0;
        return `<tr data-sku="${esc(p.sku)}">
          <td>${p.image ? `<img class="thumb" src="${esc(p.image)}" loading="lazy" alt="">` : ''}</td>
          <td><div class="pname">${esc(p.name)}</div><div class="pmeta">ID ${esc(p.sku)}</div></td>
          <td class="num">${rp(p.final_price)}${p.discount_pct > 0 ? `<div class="strike">${rp(p.price)}</div>` : ''}
            ${ch ? `<div class="${ch > 0 ? 'up' : 'down'}">${ch > 0 ? '▲' : '▼'} ${rp(Math.abs(ch))}</div>` : ''}</td>
          <td class="num">${p.discount_pct > 0 ? `<span class="badge disc">-${p.discount_pct}%</span>` : ''}</td>
          <td>${stockCell(p)}</td>
          <td class="pmeta">${esc(p.plu || '')}</td>
          <td class="cat-cell">${esc(p.category_name || '')}</td>
          <td class="pmeta">${ago(p.last_seen)}</td></tr>`;
      }).join('');
    }
    const pages = Math.max(1, Math.ceil(d.total / SIZE));
    $('#pager').innerHTML = `<button class="btn" data-p="${page - 1}" ${page <= 1 ? 'disabled' : ''}>‹</button>
      <span>Halaman ${page} / ${pages} · ${num(d.total)} produk</span>
      <button class="btn" data-p="${page + 1}" ${page >= pages ? 'disabled' : ''}>›</button>`;
  }

  const cityStores = p => { try { return JSON.parse(p.city_stores || '[]'); } catch (_) { return []; } };
  function stockCell(p) {
    if (!p.in_stock) return `<span class="badge no">Habis</span>`;
    const c = city();
    if (!c) return `<span class="badge ok">Tersedia</span>`;
    if (p.city_name !== c || !p.city_checked) return `<span class="badge ok">Tersedia</span><div class="pmeta">${esc(c)}: belum diketahui</div>`;
    const here = cityStores(p);
    if (!here.length) return `<span class="badge ok">Tersedia</span><div class="pmeta">Tidak ada di ${esc(c)}</div>`;
    return `<span class="badge city">Ada di ${esc(c)}</span>` +
      here.map(([s, q]) => `<div class="pmeta store">${esc(shortStore(s))}${q != null ? `: ${num(q)}` : ''}</div>`).join('');
  }

  $('#pager').addEventListener('click', e => {
    const b = e.target.closest('[data-p]');
    if (!b || b.disabled) return;
    page = +b.dataset.p;
    loadProducts();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  let qTimer = 0;
  $('#f-q').addEventListener('input', () => { clearTimeout(qTimer); qTimer = setTimeout(() => { page = 1; loadProducts(); }, 300); });
  for (const id of ['#f-cat', '#f-store', '#f-brand', '#f-stock', '#f-discount', '#f-changed', '#f-sort']) {
    $(id).addEventListener('change', () => { page = 1; loadProducts(); });
  }
  $('#f-stock').addEventListener('change', () => savePref('stock', $('#f-stock').value));
  $('#f-city').addEventListener('change', () => { savePref('cityOnly', $('#f-city').checked ? '1' : '0'); page = 1; loadProducts(); loadCatFilter(); });

  async function loadStores() {
    const { stores } = await api('/api/city-stores');
    const cur = $('#f-store').value;
    $('#f-store').innerHTML = '<option value="">Semua toko</option>' + stores.map(s =>
      `<option value="${esc(s.name)}" ${s.name === cur ? 'selected' : ''}>${esc(shortStore(s.name))} (${num(s.n)})</option>`).join('');
  }

  async function loadBrands() {
    const brands = await api('/api/brands');
    const cur = $('#f-brand').value;
    $('#f-brand').innerHTML = '<option value="">Semua brand</option>' +
      brands.map(b => `<option value="${esc(b.brand)}" ${b.brand === cur ? 'selected' : ''}>${esc(b.brand)} (${num(b.n)})</option>`).join('');
  }

  // Filter kategori: dari kategori produk yang tersimpan (2 tingkat)
  let catData = null;
  async function loadCatFilter() {
    const cats = await api('/api/product-categories' + ($('#f-city').checked ? '?city=1' : ''));
    const cur = $('#f-cat').value;
    $('#f-cat').innerHTML = '<option value="">Semua kategori</option>' + cats.map(c =>
      `<option value="${esc(c.name)}" ${c.name === cur ? 'selected' : ''}>${esc(c.name)} (${num(c.n)})</option>` +
      c.children.map(ch => { const v = `${c.name} › ${ch.name}`;
        return `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>&nbsp;&nbsp;&nbsp;${esc(ch.name)} (${num(ch.n)})</option>`; }).join('')
    ).join('');
  }

  // ─── Highlight ────────────────────────────────
  const tier = s => s >= 75 ? ['Super worth it', 't1'] : s >= 60 ? ['Worth it', 't2'] : ['Lumayan', 't3'];

  async function loadHighlights() {
    const q = new URLSearchParams({ cat: $('#h-cat').value, store: $('#h-store').value, min_discount: $('#h-min').value,
      sort: $('#h-sort').value, limit: 60 });
    const [d, cats, st] = await Promise.all([
      api('/api/highlights?' + q),
      $('#h-cat').options.length > 1 ? null : api('/api/product-categories?city=1'),
      $('#h-store').options.length > 1 ? null : api('/api/city-stores'),
    ]);
    if (cats) $('#h-cat').innerHTML = '<option value="">Semua kategori</option>' + cats.map(c =>
      `<option value="${esc(c.name)}">${esc(c.name)}</option>` +
      c.children.map(ch => `<option value="${esc(c.name + ' › ' + ch.name)}">&nbsp;&nbsp;&nbsp;${esc(ch.name)}</option>`).join('')).join('');
    if (st) $('#h-store').innerHTML = '<option value="">Semua toko</option>' +
      st.stores.map(s => `<option value="${esc(s.name)}">${esc(shortStore(s.name))}</option>`).join('');
    const c = status?.settings.city;
    $('#hl-sub').textContent = `${num(d.total)} produk diskon${c ? ` yang ada stoknya di ${c}` : ''}. ` +
      'Diurutkan dari skor Worth It: diskon, nominal hemat, rating & ulasan, dan riwayat harga.';
    $('#hl-grid').innerHTML = d.items.length ? d.items.map(p => {
      const [label, cls] = tier(p.score);
      const stores = cityStores(p).map(([s]) => shortStore(s));
      return `<article class="hl-card" data-sku="${esc(p.sku)}">
        <div class="hl-img">${p.image ? `<img src="${esc(p.image)}" loading="lazy" alt="">` : ''}
          <span class="hl-disc">-${p.discount_pct}%</span></div>
        <div class="hl-body">
          <div class="hl-score ${cls}"><b>${p.score}</b><span>${label}</span><i style="width:${p.score}%"></i></div>
          <div class="hl-name">${esc(p.name)}</div>
          <div class="hl-price">${rp(p.final_price)} <span class="strike">${rp(p.price)}</span></div>
          <div class="hl-reasons">${p.reasons.map(r => `<span>${esc(r)}</span>`).join('')}</div>
          ${stores.length ? `<div class="pmeta">📍 ${esc(stores.join(' · '))}</div>` : ''}
        </div>
      </article>`;
    }).join('') : `<div class="empty">Belum ada produk diskon yang cocok. ${status?.counts.in_city ? '' : 'Jalankan crawl dulu.'}</div>`;
  }
  for (const id of ['#h-cat', '#h-store', '#h-min', '#h-sort']) $(id).addEventListener('change', loadHighlights);
  $('#hl-grid').addEventListener('click', e => {
    const card = e.target.closest('.hl-card');
    if (card) openDetail(card.dataset.sku);
  });

  // ─── Detail produk & grafik harga ─────────────
  $('#product-table').addEventListener('click', e => {
    const tr = e.target.closest('tr[data-sku]');
    if (tr) openDetail(tr.dataset.sku);
  });

  async function openDetail(sku) {
    const { product: p, history } = await api('/api/products/' + encodeURIComponent(sku));
    $('#modal-body').innerHTML = `
      <div class="detail">
        <img src="${esc(p.image)}" alt="">
        <div>
          <h3>${esc(p.name)}</h3>
          <div class="price">${rp(p.final_price)}</div>
          ${p.discount_pct > 0 ? `<div><span class="strike">${rp(p.price)}</span> <span class="badge disc">-${p.discount_pct}%</span></div>` : ''}
          <div class="kv">
            <div>SKU</div><div>${esc(p.sku)}</div>
            <div>PLU</div><div>${esc(p.plu) || '–'}</div>
            <div>Kategori</div><div>${esc(p.category_name) || '–'}</div>
            <div>Stok</div><div>${p.in_stock ? 'Tersedia' : 'Habis'}</div>
            <div>Stok ${esc(city() || 'kota')}</div><div>${storeList(p)}</div>
            <div>Pertama terlihat</div><div>${fmtDate(p.first_seen)}</div>
            <div>Terakhir terlihat</div><div>${fmtDate(p.last_seen)}</div>
            <div>Harga berubah</div><div>${p.last_change ? `${fmtDate(p.last_change)} (sebelumnya ${rp(p.prev_price)})` : 'Belum pernah'}</div>
          </div>
          <p><a class="btn" href="${esc(p.url)}" target="_blank" rel="noopener">Buka di Alfagift ↗</a></p>
        </div>
      </div>
      <div class="chart"><h4>Riwayat harga · ${history.length} catatan${history.length ? ` sejak ${history[0].date}` : ''} (titik = harga berubah)</h4>${priceChart(history)}</div>`;
    $('#modal').hidden = false;
  }
  $('#modal').addEventListener('click', e => { if (e.target.id === 'modal' || e.target.closest('[data-close]')) $('#modal').hidden = true; });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').hidden = true; });

  function storeList(p) {
    const c = city();
    if (!c) return '–';
    if (p.city_name !== c || !p.city_checked) return '<span class="muted">Belum diketahui</span>';
    const here = cityStores(p);
    const when = `<div class="pmeta">diperbarui ${fmtDate(p.city_checked)}</div>`;
    if (!here.length) return `Tidak ada stok di ${esc(c)}${when}`;
    return here.map(([s, q]) => `<div class="city-hl"><b>${esc(shortStore(s))}</b>${q != null ? `: ${num(q)} unit` : ' — tersedia'}</div>`).join('') + when;
  }

  // Riwayat hanya berisi titik perubahan → gambar sebagai garis bertingkat
  // (harga berlaku sampai perubahan berikutnya), diperpanjang sampai hari ini.
  function priceChart(h) {
    if (!h.length) return '<p class="muted small">Belum ada riwayat harga.</p>';
    const today = new Date().toLocaleDateString('sv-SE');
    const pts = h[h.length - 1].date < today ? [...h, { ...h[h.length - 1], date: today, now: true }] : h;
    if (pts.length < 2) return `<p class="muted small">Harga belum pernah berubah sejak mulai dipantau (${h[0].date}).</p>`;
    const W = 700, H = 180, P = { l: 70, r: 12, t: 12, b: 26 };
    const vals = pts.flatMap(x => [x.final_price, x.price]).filter(Boolean);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (lo === hi) { lo *= 0.95; hi *= 1.05; }
    const t0 = Date.parse(pts[0].date), t1 = Date.parse(pts[pts.length - 1].date);
    const x = d => P.l + ((Date.parse(d) - t0) / Math.max(t1 - t0, 1)) * (W - P.l - P.r);
    const y = v => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
    const step = key => pts.map((d, i) => i ? `H${x(d.date).toFixed(1)} V${y(d[key]).toFixed(1)}` : `M${x(d.date).toFixed(1)},${y(d[key]).toFixed(1)}`).join(' ');
    const ticks = [lo, (lo + hi) / 2, hi];
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      ${ticks.map(t => `<line x1="${P.l}" x2="${W - P.r}" y1="${y(t)}" y2="${y(t)}" stroke="#eee"/>
        <text x="${P.l - 6}" y="${y(t) + 4}" font-size="11" text-anchor="end" fill="#9ca3af">${rp(Math.round(t))}</text>`).join('')}
      <path d="${step('price')}" fill="none" stroke="#d1d5db" stroke-width="1.5" stroke-dasharray="4 3"/>
      <path d="${step('final_price')}" fill="none" stroke="#f26b21" stroke-width="2.5"/>
      ${h.map(d => `<circle cx="${x(d.date)}" cy="${y(d.final_price)}" r="3.5" fill="${d.in_stock ? '#f26b21' : '#dc2626'}"><title>${d.date}: ${rp(d.final_price)}${d.in_stock ? '' : ' (habis)'}</title></circle>`).join('')}
      <text x="${P.l}" y="${H - 6}" font-size="11" fill="#9ca3af">${pts[0].date}</text>
      <text x="${W - P.r}" y="${H - 6}" font-size="11" fill="#9ca3af" text-anchor="end">hari ini</text>
    </svg>`;
  }

  // ─── Pengaturan ────────────────────────────────
  let schedule = [];

  function renderSchedule() {
    $('#s-times').innerHTML = schedule.length
      ? schedule.sort().map(t => `<span class="chip">${t}<button data-del-time="${t}" title="Hapus">✕</button></span>`).join('')
      : '<span class="muted small">Belum ada jam.</span>';
  }
  $('#s-time-add').onclick = () => {
    const t = $('#s-time-new').value;
    if (t && !schedule.includes(t)) schedule.push(t);
    renderSchedule();
  };
  $('#s-times').addEventListener('click', e => {
    const b = e.target.closest('[data-del-time]');
    if (b) { schedule = schedule.filter(t => t !== b.dataset.delTime); renderSchedule(); }
  });

  async function loadCategories() {
    const [cats, st] = await Promise.all([api('/api/categories'), api('/api/city-stores')]);
    const acc = st.account || {};
    $('#account').innerHTML = acc.store_code
      ? `<div><span class="muted">Toko</span><b>Alfamart ${esc(acc.store_code)}</b></div>
         <div><span class="muted">Alamat akun</span><b>${esc(acc.address || '')}</b></div>`
      : '<div class="muted">Info akun & toko muncul setelah crawl pertama.</div>';
    const on = new Set(cats.watched);
    $('#cat-list').innerHTML = cats.tree.map(c =>
      `<label class="chk"><input type="checkbox" value="${esc(c.id)}" ${on.has(c.id) ? 'checked' : ''}>
        ${esc(c.name)} ${c.n ? `<span class="muted small">(${num(c.n)} tersedia)</span>` : ''}</label>`).join('');
    schedule = [...(status.settings.schedule || [])];
    $('#s-sched-on').checked = !!status.settings.schedule_on;
    renderSchedule();
    $('#s-delay').value = status.settings.delay;
    $('#s-max').value = status.settings.max_requests;
    updateEstimate();
  }

  function updateEstimate() {
    const d = +$('#s-delay').value || 3, m = +$('#s-max').value || 150;
    $('#s-estimate').textContent = `Seluruh katalog butuh ±60 request (200 produk/request), ±${Math.round(d * 60 / 60)} menit. ` +
      `Batas ${num(m)} request ≈ maks. ${Math.round(d * m / 60)} menit per putaran.`;
  }
  $('#s-delay').addEventListener('input', updateEstimate);
  $('#s-max').addEventListener('input', updateEstimate);

  $('#save-cats').onclick = async () => {
    try {
      await api('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ categories: [...document.querySelectorAll('#cat-list input:checked')].map(x => x.value),
          delay: +$('#s-delay').value, max_requests: +$('#s-max').value, schedule, schedule_on: $('#s-sched-on').checked }),
      });
      toast('Pengaturan disimpan');
      loadStatus();
    } catch (e) { toast(e.message, true); }
  };

  // ─── Mulai ────────────────────────────────────
  loadStatus().then(() => Promise.all([loadHighlights(), loadProducts(), loadBrands(), loadCatFilter(), loadStores()]))
    .catch(e => toast('Gagal memuat: ' + e.message, true));
})();
