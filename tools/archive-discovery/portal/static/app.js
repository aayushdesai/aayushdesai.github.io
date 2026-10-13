'use strict';

const $ = (id) => document.getElementById(id);
const state = { mode: 'name', archives: [], registry: null, resolutionId: null,
  selectedCandidate: null, candidates: [], busy: false, runId: null, generation: 0,
  records: [], selectedArchives: [], source: null, products: false, offline: false, offlineLabel: 'MAST metadata',
  targetRunId: null, targetRecords: [], inventoryRunId: null, previewRunId: null, previewGeneration: 0, previewBusy: false };
const groups = ['Spectra', 'Photometry & images', 'Time series', 'Catalogues', 'Other candidates', 'Products'];

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function display(value) {
  if (value === null || value === undefined || value === '') return null;
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
function notice(message, error = false) {
  $('notice').hidden = !message;
  $('notice').textContent = message || '';
  $('notice').classList.toggle('error', error);
}
function phase(value, active = false) {
  $('run-phase').textContent = value;
  $('run-phase').classList.toggle('active', active);
}
function setBusy(busy) {
  state.busy = busy;
  for (const input of $('source-form').querySelectorAll('input, button')) {
    input.disabled = busy || input.dataset.deferred === 'true';
  }
  $('name-tab').disabled = busy;
  $('position-tab').disabled = busy;



  $('confirm-source').disabled = busy;
  for (const input of $('identity-candidates').querySelectorAll('input')) input.disabled = busy;
  updateConfirmation();
}
function updateConfirmation() {
  $('search-confirmed').disabled = state.busy || state.selectedCandidate === null || !$('confirm-source').checked;
}
function resetIdentity() {
  state.resolutionId = null;
  state.selectedCandidate = null;
  state.candidates = [];
  $('confirm-source').checked = false;
  $('identity-panel').hidden = true;
  $('identity-candidates').replaceChildren();
  updateConfirmation();
}
function setMode(mode) {
  state.mode = mode;
  resetIdentity();
  for (const [name, id] of [['name', 'name-tab'], ['position', 'position-tab']]) {
    $(id).setAttribute('aria-selected', String(name === mode));
    $(id).tabIndex = name === mode ? 0 : -1;
  }
  $('name-input').hidden = mode !== 'name';
  $('position-input').hidden = mode !== 'position';
  $('source-name').required = mode === 'name';
  $('ra').required = mode === 'position';
  $('dec').required = mode === 'position';
  $('submit-source').replaceChildren(document.createTextNode(mode === 'name' ? 'Resolve source name' : 'Search selected archives'), element('span', '→'));
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  let data;
  try { data = await response.json(); } catch { throw new Error('The local service returned an unreadable response.'); }
  if (!response.ok) throw new Error(display(data.error) || `Local service error (${response.status}).`);
  return data;
}
function archiveLabel(id) { return state.archives.find((x) => x.id === id)?.label || id; }
function localScope(archive) {
  const scope = {
    mast: 'Observation and associated-product metadata.',
    gemini: 'Canonical file metadata; current access blocked (HTTP 403).',
    eso: 'Reduced-product metadata; current requests time out.',
    sdss: 'Catalogue targets only; spectra not enumerated.',
    sdssspectra: 'DR17 optical spectrum metadata and native product links.',
    desi: 'DR1 HEALPix-coadd metadata; native files contain multiple targets.',
    keck: 'Selected raw metadata; LRIS positive is partial, ready products unverified.',
    xmm: 'Observation and catalogue-measurement metadata; extraction unverified.',
    chandra: 'Observation/product metadata; source extraction unverified.',
    lamost: 'Spectrum-component metadata; native files not downloaded.'
  };
  return archive.implemented ? scope[archive.id] || capabilityText(archive.capabilities) : 'No current local adapter.';
}
function capabilityText(capabilities) {
  if (Array.isArray(capabilities)) return [...new Set(capabilities.map(capabilityText).filter(Boolean))].join('; ');
  if (!capabilities || typeof capabilities !== 'object') return 'Coverage has not been verified.';
  return Object.entries(capabilities).filter(([, value]) => typeof value === 'object' ? value.local && value.local !== 'none' : !!value)
    .map(([key]) => key.replaceAll('_', ' ')).join(', ') || 'No current local data route.';
}
function renderArchives(data) {
  state.archives = [...(data.archives || [])];
  const labels = { sdssspectra: 'SDSS DR17 spectra', xmm: 'XMM-Newton', chandra: 'Chandra' };
  state.archives = state.archives.map((archive) => ({ ...archive, label: labels[archive.id] || archive.label }));
  state.registry = data.registry || null;
  for (const source of state.registry?.sources || []) {
    if (source.route_state === 'deferred' && !state.archives.some((item) => item.id === source.archive)) state.archives.push({ id: source.archive, label: source.label, implemented: false, status: 'deferred', capabilities: source.capabilities });
  }
  const choices = [];
  for (const archive of state.archives) {
    const deferred = archive.status === 'deferred';
    const label = element('label', null, 'archive-choice');
    const input = element('input'); input.type = 'checkbox'; input.value = archive.id;
    input.name = 'archive'; input.checked = !!archive.implemented && !deferred;
    input.disabled = deferred; input.dataset.deferred = String(deferred);
    const body = element('span'); body.append(element('span', archive.label || archive.id, 'archive-name'));
    body.append(element('span', deferred ? 'Deferred until Swift is complete.' : localScope(archive), 'archive-limit'));
    label.append(input, body, element('span', deferred ? 'Deferred' : archive.implemented ? 'Local adapter' : 'Unsupported', 'route-label'));
    choices.push(label);
  }
  $('archive-options').replaceChildren(...choices);
  const coverage = [];
  for (const source of state.registry?.sources || []) {
    const node = element('div', null, 'coverage-entry');
    node.append(element('h4', `${source.label} · ${source.route_state}`));
    node.append(element('p', source.route));
    const holdings = Object.entries(source.capabilities || {}).filter(([, value]) => value.service !== 'none').map(([kind, value]) => `${kind.replaceAll('_', ' ')} (${value.service.replaceAll('_', ' ')})`).join(', ');
    if (holdings) node.append(element('p', `Documented archive holdings: ${holdings}.`));
    node.append(element('p', source.limits?.completeness));
    for (const reference of source.references || []) {
      const link = nativeLink(reference.url, reference.title);
      if (link) node.append(link);
    }
    coverage.push(node);
  }
  $('coverage-guide').replaceChildren(...(coverage.length ? coverage : [element('p', 'Detailed coverage metadata is not available from this service.') ]));
}
function requestOptions() {
  const radius = Number($('radius').value);
  const epoch = $('epoch').value.trim() === '' ? null : Number($('epoch').value);
  if (!Number.isFinite(radius) || radius <= 0 || radius > 3600) throw new Error('Use a radius greater than 0 and at most 3600 arcsec.');
  if (epoch !== null && !Number.isFinite(epoch)) throw new Error('Use a finite Julian year for the position epoch.');
  const archives = [...$('archive-options').querySelectorAll('input:checked:not(:disabled)')].map((x) => x.value);
  if (!archives.length) throw new Error('Select at least one archive.');
  return { radius_arcsec: radius, epoch, archives };
}
function candidateName(candidate, index) {
  return candidate.canonicalName || candidate.name || candidate.object || candidate.input_name || `Candidate ${index + 1}`;
}
function coordinate(value) {
  const number = Number(value);
  return value !== null && value !== undefined && Number.isFinite(number) ? number.toFixed(7) : 'missing';
}
function showCandidates(run) {
  const resolution = run.resolution || run.report || {};
  state.resolutionId = run.resolution_id || run.run_id || state.runId;
  state.candidates = resolution.candidates || [];
  state.selectedCandidate = null;
  $('confirm-source').checked = false;
  $('identity-candidates').replaceChildren();
  if (!state.candidates.length) {
    $('identity-panel').hidden = true;
    notice(`Name resolution: ${(resolution.status || 'no candidates').replaceAll('_', ' ')}. Supply coordinates or try a more specific identifier.`, true);
    return;
  }
  $('identity-panel').hidden = false;
  $('identity-summary').textContent = state.candidates.length === 1 ? 'One candidate was returned. Select it and confirm the identity before searching.' : `${state.candidates.length} candidates were returned. Select the intended source and confirm its identity.`;
  state.candidates.forEach((candidate, index) => {
    const label = element('label', null, 'identity-choice');
    const input = element('input'); input.type = 'radio'; input.name = 'candidate'; input.value = String(index);
    input.addEventListener('change', () => { state.selectedCandidate = index; $('confirm-source').checked = false; updateConfirmation(); });
    const body = element('span'); body.append(element('strong', candidateName(candidate, index)));
    body.append(element('small', `RA ${coordinate(candidate.ra)}° · Dec ${coordinate(candidate.decl ?? candidate.dec)}° · ICRS`));
    const provenance = [candidate.resolver, candidate.cached ? 'Cached response' : null, candidate.cacheDate].filter(Boolean).join(' · ');
    if (provenance) body.append(element('small', provenance));
    label.append(input, body); $('identity-candidates').append(label);
  });
  notice('Check the resolved identity before starting the archive search.');
  updateConfirmation();
}
function beginResults(options) {
  state.selectedArchives = options.archives;
  state.records = [];
  $('empty-state').hidden = true; $('results-content').hidden = false; $('exports').hidden = true;
  $('record-filter').value = '';
  $('back-to-target').hidden = true;
  state.targetRunId = null;
  state.inventoryRunId = null;
  state.targetRecords = [];
  $('target-overview').hidden = true;
  clearPreview();
  renderTarget({}); renderStatuses({}, false); renderRecords();
}
function clearPreview() {
  state.previewGeneration += 1;
  state.previewBusy = false;
  state.previewRunId = null;
  $('product-preview').hidden = true;
  $('preview-image').removeAttribute('src');
}
function sourceFamily(record) {
  if (hasMeasurements(record)) return 'Photometry & band fluxes';
  const native = [record.product_type, record.dataproduct_type, record.mode].filter(Boolean).join(' ').toLowerCase();
  if (/time.?series|light.?curve/.test(native)) return 'Light curves';
  if (/spectrum|spectra|spectroscopy|\bls\b|\bmos\b|\bifs\b/.test(native)) return 'Spectra';
  if (/photometr/.test(native)) return 'Photometry & band fluxes';
  if (/image/.test(native)) return 'Images';
  const group = recordGroup(record);
  if (group === 'Spectra') return 'Spectra';
  if (group === 'Time series') return 'Light curves';
  if (group === 'Photometry & images') return 'Images';
  if (group === 'Catalogues') return 'Catalogue targets';
  return 'Other products & pointings';
}
function coverageLabels(record) {
  const bands = [];
  if (record.filter_name) bands.push(String(record.filter_name));
  for (const measurement of record.measurements || []) {
    if (measurement.band_min != null && measurement.band_max != null && measurement.band_unit) bands.push(`${measurement.band_min}–${measurement.band_max} ${measurement.band_unit}`);
  }
  if (record.em_min_m != null && record.em_max_m != null) bands.push(`${record.em_min_m}–${record.em_max_m} m`);
  return bands;
}
function dateLabel(record) {
  if (record.catalogue_start_mjd != null && record.catalogue_end_mjd != null) return `Stack interval MJD ${record.catalogue_start_mjd}–${record.catalogue_end_mjd}`;
  if (record.observed_at) return String(record.observed_at);
  if (record.archive_night_mjd != null) return `Observing night MJD ${record.archive_night_mjd}`;
  if (record.observation_mjd != null) return `MJD ${record.observation_mjd}${record.observation_end_mjd != null ? `–${record.observation_end_mjd}` : ''}`;
  return null;
}
function sourceSummary(records) {
  $('target-overview').hidden = !records.length;
  const nodes = [];
  for (const family of ['Spectra', 'Photometry & band fluxes', 'Light curves', 'Images', 'Catalogue targets', 'Other products & pointings']) {
    const selected = records.filter((record) => sourceFamily(record) === family);
    if (!selected.length) continue;
    const section = element('div', null, 'source-family');
    section.append(element('h4', `${family} · ${selected.length} candidate ${selected.length === 1 ? 'record' : 'records'}`));
    const facilities = [...new Set(selected.map((record) => [record.facility || (record.archive === 'mast' ? record.collection : null) || archiveLabel(record.archive), record.instrument, record.data_release].filter(Boolean).join(' · ')))];
    section.append(element('p', facilities.join('; '), 'summary-facilities'));
    const bands = [...new Set(selected.flatMap(coverageLabels))];
    section.append(element('p', bands.length ? `Band / wavelength coverage: ${bands.join('; ')}` : 'Band or wavelength range is not supplied in these discovery records.'));
    const dates = [...new Set(selected.map(dateLabel).filter(Boolean))];
    section.append(element('p', dates.length ? `Dates: ${dates.slice(0, 3).join('; ')}${dates.length > 3 ? '; further dates are in the record details.' : ''}` : 'Observation dates are not supplied for these records.'));
    const actions = element('div', null, 'summary-actions');
    let previews = 0;
    for (const record of selected) {
      if (previews >= 3 || (!nativeLink(record.product_url) && !hasMeasurements(record))) continue;
      const label = record.filename || record.record_id || 'native item';
      const button = element('button', `${hasMeasurements(record) && !record.product_url ? 'View fluxes' : 'View product'} · ${label}`, 'small-button');
      button.type = 'button'; button.disabled = state.busy || state.previewBusy;
      button.addEventListener('click', () => startPreview(records.indexOf(record), state.targetRunId, records));
      actions.append(button);
      const link = nativeLink(record.product_url, 'Open provider file ↗'); if (link) actions.append(link);
      previews += 1;
    }
    if (actions.childNodes.length) section.append(actions);
    if (selected.length > previews) section.append(element('p', 'The full record list below retains every candidate and available product action.', 'summary-note'));
    nodes.push(section);
  }
  $('source-data-families').replaceChildren(...nodes);
}
function renderTarget(run) {
  const target = run.report?.target || run.target || state.source || {};
  const title = state.products ? 'Products for the selected observation' : target.canonical_name || target.name || target.input_name || state.source?.name || 'Coordinate search';
  const body = [element('strong', title)];
  const ra = target.ra_deg ?? target.ra ?? state.source?.ra;
  const dec = target.dec_deg ?? target.dec ?? target.decl ?? state.source?.dec;
  if (ra !== undefined && dec !== undefined) body.push(element('p', `ICRS · RA ${coordinate(ra)}° · Dec ${coordinate(dec)}° · radius ${target.radius_arcsec ?? state.source?.radius_arcsec ?? 'unspecified'} arcsec`));
  const epoch = target.position_epoch_jyear ?? target.epoch ?? state.source?.epoch;
  body.push(element('p', `Position epoch: ${epoch ?? 'not supplied'} · proper motion not applied`));
  if (run.run_id) body.push(element('p', `Saved run: ${run.run_id}`));
  $('target-summary').replaceChildren(...body);
}
function renderStatuses(statuses, terminal) {
  const nodes = [];
  const ids = [...new Set([...state.selectedArchives, ...Object.keys(statuses || {})])];
  for (const id of ids) {
    const result = statuses[id] || {};
    const status = result.status || (terminal ? 'not_reported' : 'pending');
    const card = element('div', null, 'status-card');
    card.append(element('strong', archiveLabel(id)), element('span', status.replaceAll('_', ' '), `status-tag ${status}`));
    if (result.records || result.retained_count !== undefined) card.append(element('p', `${result.retained_count ?? result.records?.length ?? 0} metadata records`));
    if (result.query_complete !== undefined) card.append(element('p', result.query_complete ? 'Requested query complete; source association remains unverified.' : 'Complete route coverage is not established.'));
    if (result.coverage) card.append(element('p', result.coverage));
    if (result.warnings?.length || result.error) {
      const detail = element('details'); detail.append(element('summary', 'Limits and response details'));
      for (const message of [...(result.warnings || []), result.error].filter(Boolean)) detail.append(element('p', display(message)));
      card.append(detail);
    }
    nodes.push(card);
  }
  $('archive-statuses').replaceChildren(...nodes);
}
function recordGroup(record) {
  const kind = String(record.record_kind || '').toLowerCase();
  if (kind === 'product') return 'Products';
  if (kind.includes('catalogue') || kind.includes('catalog')) return 'Catalogues';
  const native = [record.product_type, record.dataproduct_type, record.mode, record.data_type, record.subgroup].filter(Boolean).join(' ').toLowerCase();
  if (/time.?series|light.?curve/.test(kind + ' ' + native)) return 'Time series';
  if (/spectrum|spectra|spectroscopy|\bls\b|\bmos\b|\bifs\b/.test(kind + ' ' + native)) return 'Spectra';
  // Preserved MAST UVOT cubes link native *_sk.img sky-image bundles. This
  // only groups image metadata; it does not infer measured source photometry.
  const uvotSkyImage = String(record.instrument || '').trim().toUpperCase() === 'UVOT' &&
    String(record.product_type || record.dataproduct_type || '').toLowerCase() === 'cube' &&
    [record.product_uri, record.product_url].some((uri) => typeof uri === 'string' && /_sk\.img(?:\.gz)?(?:[?#]|$)/i.test(uri));
  if (uvotSkyImage) return 'Photometry & images';
  if (/image|photometr/.test(kind + ' ' + native)) return 'Photometry & images';
  return 'Other candidates';
}
function nativeLink(url, label = 'Open native product ↗') {
  if (typeof url !== 'string') return null;
  let parsed; try { parsed = new URL(url); } catch { return null; }
  if (!['http:', 'https:'].includes(parsed.protocol)) return null;
  const link = element('a', label); link.href = parsed.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
  return link;
}
function hasMeasurements(record) {
  return Array.isArray(record.measurements) && record.measurements.length > 0;
}
function recordCard(record, recordIndex) {
  const card = element('article', null, 'record-card');
  const top = element('div', null, 'record-top');
  top.append(element('span', record.record_id || record.filename || record.observation_id || 'Identifier missing', 'record-id'), element('span', archiveLabel(record.archive || 'archive unspecified'), 'record-archive'));
  card.append(top);
  const fields = [record.instrument, record.collection, record.data_release, record.product_type || record.mode,
    record.filter_name, record.observed_at || (record.observation_mjd != null ? `MJD ${record.observation_mjd}` : null),
    record.exposure_seconds != null ? `${record.exposure_seconds} s` : null].map(display).filter(Boolean);
  const meta = element('div', null, 'record-meta'); for (const field of fields) meta.append(element('span', field)); card.append(meta);
  const notes = [record.record_kind?.replaceAll('_', ' '), record.association_status?.replaceAll('_', ' ') || 'Source association unverified'];
  if (record.separation_arcsec != null && Number.isFinite(Number(record.separation_arcsec))) notes.push(`Centre separation ${Number(record.separation_arcsec).toFixed(3)} arcsec`);
  if (record.within_requested_cone === false) notes.push('Centre outside requested cone; inspect archive match/footprint');
  if (record.footprint_verified === false) notes.push('Footprint not verified');
  if (record.access_status) notes.push(`Access: ${record.access_status}`);
  card.append(element('div', notes.join(' · '), 'record-notes'));
  const actions = element('div', null, 'record-actions');
  const link = nativeLink(record.product_url || record.access_url, 'Open archive file ↗'); if (link) actions.append(link);
  for (const native of record.native_product_links || []) {
    const directory = nativeLink(native.url, native.relation === 'observation_product_directory' ? 'Open observation products ↗' : 'Open native product reference ↗');
    if (directory) actions.append(directory);
  }
  if (nativeLink(record.product_url) || hasMeasurements(record)) {
    const previewButton = element('button', hasMeasurements(record) && !record.product_url ? 'View native measurements' : 'View native product', 'small-button'); previewButton.type = 'button';
    previewButton.disabled = state.busy || state.previewBusy;
    previewButton.title = record.product_url ? 'Fetch and preview this selected archive file only.' : 'Preview the native measurement fields in this saved record.';
    previewButton.addEventListener('click', () => startPreview(recordIndex));
    actions.append(previewButton);
  }
  const obsid = record.product_query_obsid;
  if (record.archive === 'mast' && obsid && !state.products && !state.offline) {
    const button = element('button', 'List associated products', 'small-button'); button.type = 'button';
    button.addEventListener('click', () => startProducts(String(obsid))); button.disabled = state.busy; actions.append(button);
  }
  if (actions.childNodes.length) card.append(actions);
  const details = element('details'); details.append(element('summary', 'Full native metadata'), element('pre', JSON.stringify(record, null, 2))); card.append(details);
  return card;
}
function renderRecords() {
  const query = $('record-filter').value.trim().toLowerCase();
  const selected = state.records.filter((record) => !query || JSON.stringify(record).toLowerCase().includes(query));
  const nodes = [];
  for (const label of groups) {
    const records = selected.filter((record) => recordGroup(record) === label);
    if (!records.length) continue;
    const section = element('section', null, 'record-group'); const title = element('h4', label);
    title.append(element('span', records.length)); const list = element('div', null, 'records-list');
    for (const record of records) list.append(recordCard(record, state.records.indexOf(record))); section.append(title, list); nodes.push(section);
  }
  $('result-groups').replaceChildren(...(nodes.length ? nodes : [element('p', query ? 'No records match this filter.' : state.busy ? 'The search is in progress. Records will appear as the archives finish.' : 'No metadata records were returned. Check each archive’s status before interpreting an empty result.', 'no-records')]));
}
function renderRun(run) {
  state.inventoryRunId = run.run_id || state.runId;
  const report = run.report || {};
  const statuses = report.archives || (run.archives && !Array.isArray(run.archives) ? run.archives : null) || (state.products && report.status ? { mast: report } : {});
  const complete = run.phase === 'complete';
  state.records = run.records || report.records || Object.values(statuses).flatMap((item) => item.records || []);
  if (!state.products) state.targetRecords = state.records;
  renderTarget(run); renderStatuses(statuses, complete || run.phase === 'failed'); renderRecords();
  sourceSummary(state.targetRecords);
  $('exports').hidden = !complete;
  if (complete) {
    if (!state.products) state.targetRunId = run.run_id || state.runId;
    sourceSummary(state.targetRecords);
    $('back-to-target').hidden = !state.products || !state.targetRunId;
    const links = [];
    for (const [file, label] of [['report.json', 'Report JSON'], ['records.csv', 'Records CSV'], ['archives.csv', 'Archive status CSV'], ['export.json', 'Inventory JSON']]) {
      const link = element('a', label); link.href = `/api/runs/${encodeURIComponent(run.run_id || state.runId)}/exports/${file}`; link.download = file; links.push(link);
    }
    $('exports').replaceChildren(...links);
  }
}
async function watchRun(runId, generation, resolving = false) {
  if (generation !== state.generation) return;
  try {
    const run = await api(`/api/runs/${encodeURIComponent(runId)}`);
    if (generation !== state.generation) return;
    const phaseLabel = { complete: 'Query finished', resolved: 'Identity resolved', failed: 'Needs attention', queued: 'Queued', running: resolving ? 'Resolving' : 'Searching' }[run.phase] || run.phase.replaceAll('_', ' ');
    phase(phaseLabel, !['complete', 'failed', 'resolved'].includes(run.phase));
    if (run.phase === 'failed') {
      if (!resolving && run.report) renderRun(run);
      throw new Error(display(run.error) || 'This run failed. The saved run may contain partial metadata.');
    }
    if (resolving && ['resolved', 'complete'].includes(run.phase)) {
      setBusy(false); showCandidates(run); return;
    }
    if (!resolving) renderRun(run);
    if (run.phase === 'complete') {
      setBusy(false); renderRecords(); sourceSummary(state.targetRecords); notice(state.offline ? `Cached ${state.offlineLabel} example: ${state.records.length} records replayed for this example’s own target. No archive query was sent. Native evidence, archive status and association limits are retained.` : `${state.records.length} metadata records saved. Review archive status and association limits before using the data.`); return;
    }
    setTimeout(() => watchRun(runId, generation, resolving), 2000);
  } catch (error) {
    if (generation !== state.generation) return;
    setBusy(false); phase('Needs attention'); notice(error.message, true);
  }
}
async function submitRun(path, body, resolving = false) {
  setBusy(true); phase(resolving ? 'Resolving' : 'Searching', true);
  notice(resolving ? 'Resolving the source name. No archive search starts until you confirm the identity.' : 'Querying selected archive metadata. Each archive keeps its own status.');
  const generation = ++state.generation;
  try {
    const run = await api(path, body);
    if (!run.run_id) throw new Error('The local service did not return a saved run identifier.');
    state.runId = run.run_id; await watchRun(run.run_id, generation, resolving);
  } catch (error) { setBusy(false); phase('Needs attention'); notice(error.message, true); }
}
async function startProducts(obsid) {
  if (state.busy) return;
  const parent = state.inventoryRunId || state.runId;
  state.products = true; state.selectedArchives = ['mast'];
  clearPreview();
  await submitRun('/api/products', { run_id: parent, obsid });
}
function plotUrl(runId, path) {
  if (typeof path !== 'string' || !path) return null;
  const prefix = `/api/runs/${encodeURIComponent(runId)}/plots/`;
  if (path.startsWith(prefix) && !path.slice(prefix.length).includes('/') && !path.includes('..')) return path;
  if (/^[a-zA-Z0-9_-]+\.(png|svg|pdf)$/.test(path)) return prefix + path;
  return null;
}
function dataDownloadUrl(runId, path) {
  if (typeof path !== 'string') return null;
  const prefix = `/api/runs/${encodeURIComponent(runId)}/plots/`;
  if (/^[a-zA-Z0-9_-]+\.(csv|json)$/.test(path)) return prefix + path;
  if (path.startsWith(prefix) && !path.includes('..') && /^[a-zA-Z0-9_-]+\.(csv|json)$/.test(path.slice(prefix.length))) return path;
  return null;
}
function seriesSummary(series, index) {
  const node = element('div', null, 'series-summary');
  node.append(element('strong', series.label || series.name || series.component || `Series ${index + 1}`));
  const x = series.x_unit ?? series.wavelength_unit ?? series.time_unit ?? series.units?.x;
  const y = series.y_unit ?? series.flux_unit ?? series.units?.y;
  node.append(element('p', `Native units: ${x ?? 'x unit not supplied'} · ${y ?? 'y unit not supplied'}`));
  const points = series.point_count ?? series.n_points ?? series.points;
  const errors = series.error_count ?? series.error_point_count ?? series.errors_present;
  const masks = series.masked_count ?? series.mask_count ?? series.masked_points;
  node.append(element('p', `Points: ${display(points) ?? 'not reported'} · errors: ${display(errors) ?? 'not reported'} · masked: ${display(masks) ?? 'not reported'}`));
  for (const key of ['mask_policy', 'time_basis']) if (series[key]) node.append(element('p', `${key.replaceAll('_', ' ')}: ${display(series[key])}`));
  return node;
}
function renderPreview(run, record) {
  const preview = run.preview || {};
  const ready = preview.status === 'ready' || preview.status === 'partial';
  $('preview-title').textContent = record.filename || record.record_id || 'Selected native product';
  $('preview-status').textContent = ready ? `Native preview ${preview.status === 'partial' ? 'is partial' : 'is ready'}. Provider arrays retain their units, errors and masks.` : `Preview: ${(preview.status || run.phase || 'unavailable').replaceAll('_', ' ')}. No plot or measurement is inferred for an unsupported product.`;
  const series = Array.isArray(preview.series) ? preview.series : [];
  $('preview-series').replaceChildren(...series.map(seriesSummary));
  if (!series.length && preview.units) $('preview-series').append(element('p', `Native units: ${display(preview.units)}`));
  const association = display(preview.association) || display(record.association_status) || 'Source association unverified';
  $('preview-association').textContent = `${association.replaceAll('_', ' ')}. Readable native arrays do not establish source capture, absolute calibration or scientific readiness.`;
  const warnings = [...(preview.warnings || []), run.error].filter(Boolean);
  $('preview-warnings').replaceChildren(...warnings.map((warning) => element('p', display(warning))));
  const links = [];
  const provider = nativeLink(record.product_url, 'Open provider’s native file ↗'); if (provider) links.push(provider);
  const plots = Array.isArray(preview.plots) ? preview.plots : preview.plots ? [preview.plots] : [];
  for (const [index, plot] of plots.entries()) {
    for (const format of ['png', 'svg', 'pdf']) {
      const url = plotUrl(run.run_id || state.previewRunId, plot[format]);
      if (!url) continue;
      const link = element('a', `${format.toUpperCase()} plot${plots.length > 1 ? ` ${index + 1}` : ''}`); link.href = url; link.download = ''; links.push(link);
    }
  }
  for (const download of preview.downloads || []) {
    const filename = typeof download === 'string' ? download : download.filename || download.url;
    const url = dataDownloadUrl(run.run_id || state.previewRunId, filename);
    if (!url) continue;
    const label = typeof download === 'string' ? download === 'measurements.json' ? 'Native measurements JSON' : download === 'preview.json' ? 'Preview provenance JSON' : `Native ${download}` : download.label || 'Native data';
    const link = element('a', label); link.href = url; link.download = ''; links.push(link);
  }
  $('preview-links').replaceChildren(...links);
  const provenance = [];
  if (preview.source_hash) provenance.push(element('p', `Preview source SHA-256: ${preview.source_hash}`));
  if (preview.source_sha256) provenance.push(element('p', `Preview source SHA-256: ${preview.source_sha256}`));
  if (preview.units) provenance.push(element('p', `Provider units: ${display(preview.units)}`));
  if (preview.source_url) provenance.push(element('p', `Provider reference: ${preview.source_url}`));
  $('preview-provenance-body').replaceChildren(...provenance);
  $('preview-provenance').hidden = !provenance.length;
  for (const node of $('preview-canvas').querySelectorAll('[data-extra-plot]')) node.remove();
  const imagePlots = plots.map((plot, index) => ({ url: plotUrl(run.run_id || state.previewRunId, plot.svg || plot.png), index })).filter((plot) => plot.url);
  $('preview-canvas').hidden = true;
  if (ready && imagePlots.length) {
    const image = $('preview-image');
    image.onload = () => { if (state.previewRunId === run.run_id) $('preview-canvas').hidden = false; };
    image.onerror = () => {
      if (state.previewRunId !== run.run_id) return;
      $('preview-canvas').hidden = true;
      $('preview-warnings').append(element('p', 'The plot image could not be opened. Native metadata and provenance remain available.'));
    };
    image.src = imagePlots[0].url;
    for (const plot of imagePlots.slice(1)) {
      const figure = element('figure', null, 'extra-preview-plot'); figure.dataset.extraPlot = 'true';
      const caption = element('figcaption', series[plot.index]?.label || `Native plot ${plot.index + 1}`);
      const extra = element('img', null, 'visual-image'); extra.alt = `Native plot ${plot.index + 1}; provider units and masks are retained.`;
      extra.onerror = () => {
        extra.hidden = true;
        if (state.previewRunId === run.run_id) caption.textContent += ' · Rendered image unavailable.';
      };
      extra.src = plot.url;
      figure.append(caption, extra); $('preview-canvas').append(figure);
    }
  } else if (ready) $('preview-warnings').append(element('p', 'The native product was parsed, but no rendered plot is available.'));
}
async function pollPreview(runId, generation, record) {
  if (generation !== state.previewGeneration) return;
  try {
    const run = await api(`/api/runs/${encodeURIComponent(runId)}`);
    if (generation !== state.previewGeneration) return;
    if (['complete', 'failed'].includes(run.phase)) {
      state.previewBusy = false;
      renderPreview(run, record); renderRecords(); return;
    }
    $('preview-status').textContent = 'Reading the selected native product and rendering its supported arrays…';
    setTimeout(() => pollPreview(runId, generation, record), 2000);
  } catch (error) {
    if (generation !== state.previewGeneration) return;
    state.previewBusy = false;
    $('preview-status').textContent = error.message;
    renderRecords();
  }
}
async function startPreview(recordIndex, sourceRunId = null, sourceRecords = null) {
  if (state.busy || state.previewBusy) return;
  const record = (sourceRecords || state.records)[recordIndex];
  if (!record || (!nativeLink(record.product_url) && !hasMeasurements(record))) return;
  clearPreview(); state.previewBusy = true;
  const generation = state.previewGeneration;
  $('product-preview').hidden = false;
  $('preview-canvas').hidden = true;
  $('preview-series').replaceChildren(); $('preview-warnings').replaceChildren(); $('preview-links').replaceChildren();
  $('preview-title').textContent = record.filename || record.record_id || 'Selected native product';
  $('preview-status').textContent = record.product_url ? 'Fetching this selected native file only. Other product links are not fetched.' : 'Rendering the native measurement fields from this saved archive record.';
  $('preview-association').textContent = 'Source association, aperture capture and calibration remain unverified.';
  $('preview-provenance').hidden = true;
  $('product-preview').scrollIntoView({ behavior: 'smooth', block: 'start' });
  renderRecords();
  try {
    const run = await api('/api/preview', { run_id: sourceRunId || state.inventoryRunId || state.runId, record_index: recordIndex });
    if (generation !== state.previewGeneration) return;
    if (!run.run_id) throw new Error('The service did not return a preview identifier.');
    state.previewRunId = run.run_id;
    await pollPreview(run.run_id, generation, record);
  } catch (error) {
    if (generation !== state.previewGeneration) return;
    state.previewBusy = false; $('preview-status').textContent = error.message; renderRecords();
  }
}
$('source-form').addEventListener('submit', async (event) => {
  event.preventDefault(); if (state.busy) return;
  try {
    const options = requestOptions(); state.products = false; state.offline = false;
    if (state.mode === 'name') {
      const name = $('source-name').value.trim(); if (!name) throw new Error('Enter a source name or catalogue identifier.');
      resetIdentity(); state.source = { name, ...options }; await submitRun('/api/resolve', { name }, true);
    } else {
      const ra = Number($('ra').value), dec = Number($('dec').value);
      if ($('ra').value === '' || $('dec').value === '' || !Number.isFinite(ra) || !Number.isFinite(dec) || ra < 0 || ra >= 360 || dec < -90 || dec > 90) throw new Error('Use RA from 0 to less than 360° and Dec from −90 to 90°.');
      state.source = { ra, dec, ...options }; beginResults(options); await submitRun('/api/search', state.source);
    }
  } catch (error) { notice(error.message, true); }
});
$('search-confirmed').addEventListener('click', async () => {
  if (state.busy || state.selectedCandidate === null || !$('confirm-source').checked) return;
  try {
    const options = requestOptions(); const candidate = state.candidates[state.selectedCandidate];
    state.products = false;
    state.source = { name: candidateName(candidate, state.selectedCandidate), ra: candidate.ra, dec: candidate.decl ?? candidate.dec, ...options };
    beginResults(options);
    await submitRun('/api/search', { resolution_id: state.resolutionId, candidate_index: state.selectedCandidate, confirmed: true, ...options });
  } catch (error) { notice(error.message, true); }
});
$('confirm-source').addEventListener('change', updateConfirmation);
$('source-name').addEventListener('input', resetIdentity);
$('record-filter').addEventListener('input', renderRecords);
$('close-preview').addEventListener('click', () => { clearPreview(); renderRecords(); });
$('back-to-target').addEventListener('click', async () => {
  if (!state.targetRunId || state.busy) return;
  state.products = false; state.runId = state.targetRunId; clearPreview();
  try {
    const run = await api(`/api/runs/${encodeURIComponent(state.targetRunId)}`);
    state.selectedArchives = run.archives || Object.keys(run.report?.archives || {});
    renderRun(run);
  } catch (error) { notice(error.message, true); }
});
$('open-demo')?.addEventListener('click', async () => {
  if (state.busy) return;
  resetIdentity();
  state.products = false; state.offline = true; state.source = null;
  state.offlineLabel = 'MAST metadata';
  beginResults({ archives: ['mast'] });
  await submitRun('/api/demo', {});
});
for (const [id, archive, label] of [['open-demo-sdss', 'sdssspectra', 'SDSS native spectrum'], ['open-demo-xmm', 'xmm', 'XMM measured flux']]) {
  $(id)?.addEventListener('click', async () => {
    if (state.busy) return;
    resetIdentity(); state.products = false; state.offline = true; state.offlineLabel = label; state.source = null;
    beginResults({ archives: [archive] });
    await submitRun('/api/demo-products', { archive });
  });
}
for (const [id, mode] of [['name-tab', 'name'], ['position-tab', 'position']]) {
  $(id).addEventListener('click', () => setMode(mode));
  $(id).addEventListener('keydown', (event) => { if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); const next = mode === 'name' ? 'position' : 'name'; setMode(next); $(next === 'name' ? 'name-tab' : 'position-tab').focus(); } });
}
setMode('name');
api('/api/archives').then(renderArchives).catch((error) => { notice(error.message, true); $('submit-source').disabled = true; phase('Service unavailable'); });
