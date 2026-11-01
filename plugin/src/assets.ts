// Exports reference images, image fills and SVG icons. Bytes go to the UI, which uploads them.
import type { BundleAsset, Diagnostic, FrameRole } from './types.ts';
import { assetIdFor, detectImageFormat } from './util.ts';

export interface ExportedAsset {
  meta: BundleAsset;
  bytes: Uint8Array;
}

const REFERENCE_SCALES = [1, 2];

export async function exportReference(frame: SceneNode, role: FrameRole, diagnostics: Diagnostic[]): Promise<ExportedAsset[]> {
  const out: ExportedAsset[] = [];
  for (const scale of REFERENCE_SCALES) {
    try {
      const bytes = await frame.exportAsync({ format: 'PNG', constraint: { type: 'SCALE', value: scale } });
      out.push({
        meta: {
          id: assetIdFor('ref', `${role}-${frame.id}-${scale}x`), kind: 'reference', format: 'PNG', nodeId: frame.id,
          scale, width: Math.round(frame.width * scale), height: Math.round(frame.height * scale),
          byteLength: bytes.byteLength,
        },
        bytes,
      });
    } catch (e) {
      diagnostics.push({
        nodeId: frame.id, code: 'REFERENCE_EXPORT_FAILED', severity: scale === 1 ? 'error' : 'warning',
        message: `Could not export the ${role} frame at ${scale}x: ${String(e)}`,
      });
    }
  }
  return out;
}

export async function exportImageFills(hashes: Map<string, string[]>, diagnostics: Diagnostic[]): Promise<ExportedAsset[]> {
  const out: ExportedAsset[] = [];
  for (const [hash, nodeIds] of hashes) {
    const image = figma.getImageByHash(hash);
    if (!image) {
      diagnostics.push({ nodeId: nodeIds[0] ?? null, code: 'IMAGE_NOT_FOUND', severity: 'error', message: `Image ${hash} is referenced but not available.` });
      continue;
    }
    try {
      const bytes = await image.getBytesAsync();
      const size = await image.getSizeAsync();
      const format = detectImageFormat(bytes) ?? 'PNG';
      out.push({
        meta: {
          id: assetIdFor('img', hash.slice(0, 20)), kind: 'image-fill', format, nodeId: nodeIds[0] ?? null,
          imageHash: hash, width: size.width, height: size.height, byteLength: bytes.byteLength,
        },
        bytes,
      });
    } catch (e) {
      diagnostics.push({ nodeId: nodeIds[0] ?? null, code: 'IMAGE_EXPORT_FAILED', severity: 'error', message: String(e) });
    }
  }
  return out;
}

export async function exportRasters(nodeIds: string[], diagnostics: Diagnostic[]): Promise<ExportedAsset[]> {
  const out: ExportedAsset[] = [];
  for (const id of nodeIds) {
    const node = await figma.getNodeByIdAsync(id);
    if (!node || !('exportAsync' in node)) continue;
    try {
      const bytes = await (node as SceneNode).exportAsync({ format: 'PNG', constraint: { type: 'SCALE', value: 2 } });
      const sn = node as SceneNode;
      out.push({
        meta: {
          id: assetIdFor('png', id), kind: 'raster-export', format: 'PNG', nodeId: id, scale: 2,
          width: sn.width, height: sn.height, byteLength: bytes.byteLength,
        },
        bytes,
      });
    } catch (e) {
      diagnostics.push({ nodeId: id, code: 'PNG_EXPORT_FAILED', severity: 'warning', message: String(e) });
    }
  }
  return out;
}

export async function exportSvgs(nodeIds: string[], diagnostics: Diagnostic[]): Promise<ExportedAsset[]> {
  const out: ExportedAsset[] = [];
  for (const id of nodeIds) {
    const node = await figma.getNodeByIdAsync(id);
    if (!node || !('exportAsync' in node)) continue;
    try {
      const bytes = await (node as SceneNode).exportAsync({ format: 'SVG', svgIdAttribute: false, svgOutlineText: true, svgSimplifyStroke: true });
      const sn = node as SceneNode;
      out.push({
        meta: {
          id: assetIdFor('svg', id), kind: 'svg', format: 'SVG', nodeId: id,
          width: sn.width, height: sn.height, byteLength: bytes.byteLength,
        },
        bytes,
      });
    } catch (e) {
      diagnostics.push({ nodeId: id, code: 'SVG_EXPORT_FAILED', severity: 'warning', message: String(e) });
    }
  }
  return out;
}
