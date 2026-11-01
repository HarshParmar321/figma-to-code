// Plugin UI: role assignment, export trigger, upload to the Engine, report summary.
import type { Bundle, FrameRole, MainToUi, SelectedFrame, UiToMain } from './types.ts';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const framesEl = $('frames');
const ignoredEl = $('ignored');
const statusEl = $('status');
const reportEl = $('report');
const exportBtn = $<HTMLButtonElement>('export');
const uploadBtn = $<HTMLButtonElement>('upload');
const downloadBtn = $<HTMLButtonElement>('download');
const engineInput = $<HTMLInputElement>('engine');

let frames: SelectedFrame[] = [];
const roles: Record<string, FrameRole | 'skip'> = {};
let bundle: Bundle | null = null;
const assets = new Map<string, Uint8Array>();

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

function renderFrames(ignored: string[]) {
  if (frames.length === 0) {
    framesEl.innerHTML = '<div class="empty">Select one or more frames on the canvas.</div>';
  } else {
    framesEl.innerHTML = '';
    for (const f of frames) {
      if (!roles[f.id]) roles[f.id] = f.suggestedRole;
      const row = document.createElement('div');
      row.className = 'frame';
      row.innerHTML = `<span class="name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
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
  exportBtn.disabled = frames.length === 0;
}

function chosenRoles(): Record<string, FrameRole> {
  const out: Record<string, FrameRole> = {};
  for (const f of frames) {
    const r = roles[f.id];
    if (r && r !== 'skip') out[f.id] = r;
  }
  return out;
}

exportBtn.onclick = () => {
  bundle = null;
  assets.clear();
  uploadBtn.disabled = true;
  downloadBtn.disabled = true;
  reportEl.innerHTML = '';
  exportBtn.disabled = true;
  setStatus('Starting export…');
  send({ type: 'export', roles: chosenRoles() });
};

downloadBtn.onclick = () => {
  if (!bundle) return;
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${bundle.source.fileName.replace(/[^\w.-]+/g, '_')}-bundle.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

uploadBtn.onclick = async () => {
  if (!bundle) return;
  const base = engineInput.value.replace(/\/+$/, '');
  try { localStorage.setItem('engineUrl', base); } catch { /* ignore */ }
  uploadBtn.disabled = true;
  try {
    setStatus('Uploading bundle…');
    const res = await fetch(`${base}/v1/bundles`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(bundle),
    });
    if (!res.ok) throw new Error(`Engine rejected the bundle (${res.status}): ${await res.text()}`);
    const { bundleId } = (await res.json()) as { bundleId: string };

    let i = 0;
    for (const [id, bytes] of assets) {
      i += 1;
      setStatus(`Uploading assets ${i}/${assets.size}…`);
      const put = await fetch(`${base}/v1/bundles/${bundleId}/assets/${encodeURIComponent(id)}`, {
        method: 'PUT', headers: { 'content-type': 'application/octet-stream' }, body: bytes as unknown as BodyInit,
      });
      if (!put.ok) throw new Error(`Asset ${id} failed (${put.status}): ${await put.text()}`);
    }

    setStatus('Building extraction report…');
    const rep = await fetch(`${base}/v1/bundles/${bundleId}/report`);
    if (!rep.ok) throw new Error(`Report failed (${rep.status})`);
    renderReport(bundleId, await rep.json());
    setStatus(`Uploaded as ${bundleId}.`);
  } catch (e) {
    setStatus(String(e instanceof Error ? e.message : e), true);
  } finally {
    uploadBtn.disabled = false;
  }
};

interface Report {
  summary: Record<string, unknown> & { nodeCount: number; assetUploaded: number; assetDeclared: number };
  layout: { autoLayoutCoverage: number };
  text: { textNodes: number; fonts: { family: string; style: string }[] };
  interactions: { nodesWithReactions: number };
  fieldCoverage: { ratio: number };
  findings: { severity: 'error' | 'warning' | 'info'; code: string; message: string; nodeIds?: string[] }[];
}

function renderReport(bundleId: string, r: Report) {
  const pct = (x: number) => `${Math.round(x * 100)}%`;
  const rows: [string, string][] = [
    ['Layers', String(r.summary.nodeCount)],
    ['Auto Layout coverage', pct(r.layout.autoLayoutCoverage)],
    ['Field coverage', pct(r.fieldCoverage.ratio)],
    ['Text layers', String(r.text.textNodes)],
    ['Fonts', String(r.text.fonts.length)],
    ['Layers with interactions', String(r.interactions.nodesWithReactions)],
    ['Assets uploaded', `${r.summary.assetUploaded}/${r.summary.assetDeclared}`],
  ];
  const findings = r.findings.length
    ? r.findings.map((f, idx) => `<div class="finding ${f.severity}" data-idx="${idx}"><strong>${escapeHtml(f.code)}</strong> ${escapeHtml(f.message)}</div>`).join('')
    : '<div class="empty">No findings.</div>';
  reportEl.innerHTML = `<h2>Extraction report · ${escapeHtml(bundleId)}</h2>
    <table>${rows.map(([k, v]) => `<tr><td>${k}</td><td class="v">${v}</td></tr>`).join('')}</table>
    <h2>Findings</h2>${findings}`;
  reportEl.querySelectorAll<HTMLElement>('.finding').forEach((el) => {
    const f = r.findings[Number(el.dataset.idx)];
    const nodeId = f.nodeIds?.[0];
    if (nodeId) {
      el.style.cursor = 'pointer';
      el.title = 'Select the first affected layer';
      el.onclick = () => send({ type: 'select-node', nodeId });
    }
  });
}

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
      exportBtn.disabled = false;
      uploadBtn.disabled = false;
      downloadBtn.disabled = false;
      setStatus(`Exported ${msg.nodeCount} layers and ${msg.assetCount} assets in ${(msg.ms / 1000).toFixed(1)} s.`);
      break;
    case 'error':
      exportBtn.disabled = frames.length === 0;
      setStatus(msg.message, true);
      break;
  }
};
