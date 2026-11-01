// Plugin UI: one-click Convert (read → upload → generate + verify), results, and advanced tools.
import type { Bundle, FrameRole, MainToUi, SelectedFrame, UiToMain } from './types.ts';
import { pageName } from './util.ts';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const framesEl = $('frames');
const ignoredEl = $('ignored');
const statusEl = $('status');
const resultEl = $('result');
const reportEl = $('report');
const stepsEl = $('steps');
const convertBtn = $<HTMLButtonElement>('convert');
const exportBtn = $<HTMLButtonElement>('export');
const uploadBtn = $<HTMLButtonElement>('upload');
const downloadBtn = $<HTMLButtonElement>('download');
const engineInput = $<HTMLInputElement>('engine');

let frames: SelectedFrame[] = [];
const roles: Record<string, FrameRole | 'skip'> = {};
let bundle: Bundle | null = null;
const assets = new Map<string, Uint8Array>();
let busy = false;
let exportWaiter: { resolve: () => void; reject: (e: Error) => void } | null = null;

try {
  const saved = localStorage.getItem('engineUrl');
  if (saved) engineInput.value = saved;
} catch { /* storage unavailable in some Figma contexts */ }

function send(msg: UiToMain) {
  parent.postMessage({ pluginMessage: msg }, '*');
}

function setStatus(text: string, isError = false) {
  statusEl.textContent = text;
  statusEl.className = isError ? 'error' : '';
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
}

function engineBase() {
  const base = engineInput.value.replace(/\/+$/, '');
  try { localStorage.setItem('engineUrl', base); } catch { /* ignore */ }
  return base;
}

function setStep(step: string | null) {
  stepsEl.hidden = step === null && stepsEl.hidden;
  let reached = false;
  stepsEl.querySelectorAll<HTMLElement>('li').forEach((li) => {
    li.className = '';
    if (step === 'done') { li.className = 'done'; return; }
    if (li.dataset.step === step) { li.className = 'active'; reached = true; }
    else if (!reached && step) li.className = 'done';
  });
}

function updateButtons() {
  convertBtn.disabled = busy || frames.length === 0;
  exportBtn.disabled = busy || frames.length === 0;
  uploadBtn.disabled = busy || !bundle;
  downloadBtn.disabled = busy || !bundle;
}

function renderFrames(ignored: string[]) {
  if (frames.length === 0) {
    framesEl.innerHTML = '<div class="empty">Select one or more frames on the canvas.</div>';
  } else {
    framesEl.innerHTML = '';
    for (const f of frames) {
      if (!roles[f.id]) roles[f.id] = f.suggestedRole;
      const row = document.createElement('div');
      row.className = 'frame';
      row.innerHTML = `<span class="name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}<br><span class="page">Page: ${escapeHtml(pageName(f.name))}</span></span>
        <span class="size">${Math.round(f.width)}×${Math.round(f.height)}</span>`;
      const select = document.createElement('select');
      for (const r of ['desktop', 'tablet', 'mobile', 'skip']) {
        const opt = document.createElement('option');
        opt.value = r;
        opt.textContent = r === 'skip' ? 'Skip' : r[0].toUpperCase() + r.slice(1);
        select.appendChild(opt);
      }
      select.value = roles[f.id];
      select.onchange = () => { roles[f.id] = select.value as FrameRole | 'skip'; };
      row.appendChild(select);
      framesEl.appendChild(row);
    }
  }
  ignoredEl.textContent = ignored.length ? `Ignored (not frames): ${ignored.join(', ')}` : '';
  updateButtons();
}

function chosenRoles(): Record<string, FrameRole> {
  const out: Record<string, FrameRole> = {};
  for (const f of frames) {
    const r = roles[f.id];
    if (r && r !== 'skip') out[f.id] = r;
  }
  return out;
}

// ------------------------------------------------------------------ steps
function exportDesign(): Promise<void> {
  bundle = null;
  assets.clear();
  return new Promise((resolve, reject) => {
    exportWaiter = { resolve, reject };
    send({ type: 'export', roles: chosenRoles() });
  });
}

async function uploadBundle(): Promise<string> {
  if (!bundle) throw new Error('Nothing exported yet.');
  const base = engineBase();
  let res: Response;
  try {
    res = await fetch(`${base}/v1/bundles`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(bundle),
    });
  } catch {
    throw new Error(`Can't reach the Engine at ${base}. Is it running?`);
  }
  if (!res.ok) throw new Error(`Engine rejected the design (${res.status}): ${await res.text()}`);
  const { bundleId } = (await res.json()) as { bundleId: string };
  let i = 0;
  for (const [id, bytes] of assets) {
    i += 1;
    setStatus(`Uploading images ${i}/${assets.size}…`);
    const put = await fetch(`${base}/v1/bundles/${bundleId}/assets/${encodeURIComponent(id)}`, {
      method: 'PUT', headers: { 'content-type': 'application/octet-stream' }, body: bytes as unknown as BodyInit,
    });
    if (!put.ok) throw new Error(`Image ${id} failed (${put.status}): ${await put.text()}`);
  }
  return bundleId;
}

async function loadDesignCheck(bundleId: string) {
  const rep = await fetch(`${engineBase()}/v1/bundles/${bundleId}/report`);
  if (rep.ok) renderDesignCheck(await rep.json());
}

interface PageResult {
  name: string;
  file: string;
  fidelity?: { score: number; band: string; corrections: number; firstScore: number };
}

interface Conversion {
  bundleId: string;
  pages: PageResult[];
  components: string[];
  seconds: number;
  previewUrl: string;
  reportUrl: string;
  zipUrl: string;
  assumptions: string[];
  warnings: string[];
  fidelity: null | {
    score: number;
    band: string;
    subScores: Record<string, number>;
    counts: Record<string, number>;
    sections: { section: string; ssim: number }[];
    topIssues: { severity: string; category: string; name: string | null; message: string; id: string | null }[];
    missingFonts?: string[];
    firstScore?: number;
    corrections?: number;
    pages?: number;
  };
}

async function generate(bundleId: string): Promise<Conversion> {
  const base = engineBase();
  const start = await fetch(`${base}/v1/bundles/${bundleId}/convert`, { method: 'POST' });
  if (!start.ok && start.status !== 202) throw new Error(`Conversion failed (${start.status}): ${await start.text()}`);
  // The Engine converts in the background; poll its progress instead of holding one long request.
  for (;;) {
    await new Promise((r) => setTimeout(r, 1000));
    let res: Response;
    try {
      res = await fetch(`${base}/v1/bundles/${bundleId}/convert/status`);
    } catch {
      continue; // transient network hiccup: keep polling
    }
    if (!res.ok) throw new Error(`Status check failed (${res.status})`);
    const job = (await res.json()) as { status: string; stage?: string; elapsed?: number; error?: string; result?: Conversion };
    if (job.status === 'done' && job.result) return job.result;
    if (job.status === 'failed') throw new Error(`Conversion failed: ${job.error ?? 'unknown error'}`);
    setStatus(`${job.stage ?? 'Working'}… ${job.elapsed ? `${Math.round(job.elapsed)} s` : ''}`);
  }
}

// ------------------------------------------------------------------ rendering
const LABELS: Record<string, string> = {
  G: 'Layout & positions', T: 'Typography', C: 'Colours', A: 'Images & icons', S: 'Visual similarity', M: 'Motion', R: 'Responsive',
};

function renderResult(r: Conversion) {
  const f = r.fidelity;
  const band = f ? f.band.split(' ')[0] : '';
  const scoreHtml = f
    ? `<div class="score"><b class="${band}">${f.score.toFixed(1)}</b><span class="band ${band}">${escapeHtml(f.band)}</span></div>
       <div class="note">${f.counts.geometryPass}/${f.counts.elements} elements in place · ${f.counts.critical} critical, ${f.counts.major} major, ${f.counts.minor} minor issues · ${r.seconds.toFixed(1)} s</div>
       ${f.corrections ? `<div class="note">Self-corrected ${f.corrections} elements (score before corrections: ${f.firstScore?.toFixed(1)})</div>` : ''}
       <table>${Object.entries(f.subScores).filter(([k]) => k !== 'M').map(([k, v]) => `<tr><td>${LABELS[k] ?? k}</td><td class="v">${Math.round(v)}</td></tr>`).join('')}</table>`
    : `<div class="score"><b>✓</b><span class="band">Code generated</span></div><div class="note">The design check was skipped.</div>`;
  const issues = f && f.topIssues.length
    ? `<h2>Top issues</h2>${f.topIssues.slice(0, 5).map((i, idx) => `<div class="finding ${i.severity}" data-idx="${idx}"><strong>${escapeHtml(i.name ?? i.category)}</strong> ${escapeHtml(i.message)}</div>`).join('')}`
    : '';
  const notes = [...r.assumptions, ...r.warnings].map((n) => `<div class="note">• ${escapeHtml(n)}</div>`).join('');
  const pagesHtml = r.pages && r.pages.length > 1
    ? `<h2>Pages</h2><table>${r.pages.map((p, i) => `<tr><td><a href="#" data-page="${i}">${escapeHtml(p.name)}</a></td><td class="v">${p.fidelity ? p.fidelity.score.toFixed(1) : '–'}</td></tr>`).join('')}</table>
       <div class="note">Shared components: ${r.components.length ? r.components.map(escapeHtml).join(', ') : 'none found'}</div>`
    : r.components && r.components.length ? `<div class="note">Shared components: ${r.components.map(escapeHtml).join(', ')}</div>` : '';
  const missing = f?.missingFonts ?? [];
  const fontsHtml = missing.length
    ? `<div class="fonts"><h2>Missing fonts</h2>
       <div class="note">These fonts are not free web fonts, so a fallback font was used and the text does not match exactly.
       Add the font files (.woff2, .woff, .otf or .ttf, one per weight) and convert again.</div>
       ${missing.map((fam, i) => `<label class="font-row"><span>${escapeHtml(fam)}</span>
         <input type="file" id="font-${i}" data-family="${escapeHtml(fam)}" accept=".woff2,.woff,.otf,.ttf" multiple></label>`).join('')}
       <div class="row"><button id="fonts-reconvert">Upload fonts and convert again</button></div></div>`
    : '';
  resultEl.innerHTML = `<div class="result">${scoreHtml}
    <div class="row"><button id="open-preview">Open preview</button><button id="open-report" class="secondary">Report</button><button id="get-code" class="secondary">Download code</button></div>
    ${pagesHtml}${fontsHtml}${notes}${issues}</div>`;
  resultEl.querySelectorAll<HTMLElement>('a[data-page]').forEach((a) => {
    a.onclick = (ev) => {
      ev.preventDefault();
      const page = r.pages[Number(a.dataset.page)];
      send({ type: 'open-url', url: r.previewUrl.replace(/index\.html$/, page.file) });
    };
  });
  if (missing.length) $('fonts-reconvert').onclick = () => run(() => uploadFontsAndReconvert(r.bundleId));
  $('open-preview').onclick = () => send({ type: 'open-url', url: r.previewUrl });
  $('open-report').onclick = () => send({ type: 'open-url', url: r.reportUrl });
  $('get-code').onclick = () => send({ type: 'open-url', url: r.zipUrl });
  if (f) {
    resultEl.querySelectorAll<HTMLElement>('.finding').forEach((el) => {
      const id = f.topIssues[Number(el.dataset.idx)].id;
      if (id) {
        el.style.cursor = 'pointer';
        el.title = 'Select this layer in Figma';
        el.onclick = () => send({ type: 'select-node', nodeId: id });
      }
    });
  }
}

interface DesignCheck {
  summary: { nodeCount: number; exportedGroups?: number; exportedLayers?: number };
  layout: { autoLayoutCoverage: number };
  responsive: { sectionsByRole: Record<string, number>; sections: Record<string, { heading: string | null }[]> };
  findings: { severity: string; code: string; message: string; nodeIds?: string[] }[];
}

function renderDesignCheck(r: DesignCheck) {
  const secs = Object.entries(r.responsive.sectionsByRole)
    .map(([role, n]) => `${role}: ${n} (${(r.responsive.sections[role] ?? []).map((s) => escapeHtml(s.heading?.split('\n')[0] ?? '–')).join(' · ')})`)
    .join('<br>');
  const findings = r.findings
    .map((f, idx) => `<div class="finding ${f.severity}" data-idx="${idx}"><strong>${escapeHtml(f.code)}</strong> ${escapeHtml(f.message)}</div>`)
    .join('');
  const packed = r.summary.exportedLayers
    ? `<div class="note">${r.summary.exportedLayers.toLocaleString()} of these layers are inside ${r.summary.exportedGroups} illustrations, each exported as one image, so only ${(r.summary.nodeCount - r.summary.exportedLayers).toLocaleString()} layers become HTML.</div>`
    : '';
  reportEl.innerHTML = `<details><summary>Design check: ${r.summary.nodeCount.toLocaleString()} layers · Auto Layout ${Math.round(r.layout.autoLayoutCoverage * 100)}% · ${r.findings.length} tips</summary>
    ${packed}<div class="note">Sections found: ${secs}</div>${findings}</details>`;
  reportEl.querySelectorAll<HTMLElement>('.finding').forEach((el) => {
    const nodeId = r.findings[Number(el.dataset.idx)].nodeIds?.[0];
    if (nodeId) {
      el.style.cursor = 'pointer';
      el.onclick = () => send({ type: 'select-node', nodeId });
    }
  });
}

async function uploadFontsAndReconvert(bundleId: string) {
  const inputs = Array.from(resultEl.querySelectorAll<HTMLInputElement>('input[type=file][data-family]'));
  const files = inputs.flatMap((input) => Array.from(input.files ?? []).map((file) => ({ family: input.dataset.family!, file })));
  if (!files.length) throw new Error('Choose at least one font file first.');
  let i = 0;
  for (const { family, file } of files) {
    i += 1;
    setStatus(`Uploading font ${i}/${files.length}: ${file.name}…`);
    const res = await fetch(`${engineBase()}/v1/fonts/${encodeURIComponent(family)}/${encodeURIComponent(file.name)}`, {
      method: 'PUT', headers: { 'content-type': 'application/octet-stream' }, body: await file.arrayBuffer(),
    });
    if (!res.ok) throw new Error(`Font ${file.name} failed (${res.status}): ${await res.text()}`);
  }
  setStatus('Converting again with your fonts…');
  const r = await generate(bundleId);
  setStatus('');
  renderResult(r);
}

// ------------------------------------------------------------------ actions
async function run(task: () => Promise<void>) {
  if (busy) return;
  busy = true;
  updateButtons();
  try {
    await task();
  } catch (e) {
    setStatus(e instanceof Error ? e.message : String(e), true);
  } finally {
    busy = false;
    updateButtons();
  }
}

convertBtn.onclick = () => run(async () => {
  resultEl.innerHTML = '';
  reportEl.innerHTML = '';
  stepsEl.hidden = false;
  setStep('read');
  setStatus('Reading the design…');
  await exportDesign();
  setStep('upload');
  setStatus('Uploading…');
  const id = await uploadBundle();
  void loadDesignCheck(id);
  setStep('generate');
  setStatus('Generating code and comparing it with your design (a few seconds)…');
  const r = await generate(id);
  setStep('done');
  setStatus('');
  renderResult(r);
});

exportBtn.onclick = () => run(async () => {
  setStatus('Reading the design…');
  await exportDesign();
});

uploadBtn.onclick = () => run(async () => {
  const id = await uploadBundle();
  await loadDesignCheck(id);
  setStatus(`Uploaded as ${id}.`);
});

downloadBtn.onclick = () => {
  if (!bundle) return;
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${bundle.source.fileName.replace(/[^\w.-]+/g, '_')}-bundle.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

window.onmessage = (event: MessageEvent) => {
  const msg = event.data?.pluginMessage as MainToUi | undefined;
  if (!msg) return;
  switch (msg.type) {
    case 'selection':
      frames = msg.frames;
      renderFrames(msg.ignored);
      break;
    case 'progress':
      setStatus(msg.message);
      break;
    case 'bundle':
      bundle = msg.bundle;
      break;
    case 'asset':
      assets.set(msg.id, msg.bytes);
      break;
    case 'done':
      setStatus(`Read ${msg.nodeCount} layers and ${msg.assetCount} images in ${(msg.ms / 1000).toFixed(1)} s.`);
      exportWaiter?.resolve();
      exportWaiter = null;
      updateButtons();
      break;
    case 'error':
      if (exportWaiter) {
        exportWaiter.reject(new Error(msg.message));
        exportWaiter = null;
      } else {
        setStatus(msg.message, true);
      }
      break;
  }
};
