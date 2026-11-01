// Pure helpers with no Figma globals, so they can be unit-tested in Node.
import type { FrameRole } from './types.ts';

export function suggestRole(width: number): FrameRole {
  if (width >= 1024) return 'desktop';
  if (width >= 600) return 'tablet';
  return 'mobile';
}

/** Asset ids must match ^[A-Za-z0-9_.-]+$ (they become file names on the Engine). */
export function assetIdFor(prefix: string, raw: string): string {
  return `${prefix}-${raw.replace(/[^A-Za-z0-9_.-]+/g, '_')}`;
}

export function detectImageFormat(bytes: Uint8Array): 'PNG' | 'JPG' | 'GIF' | 'WEBP' | null {
  if (bytes.length >= 4 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return 'PNG';
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'JPG';
  if (bytes.length >= 3 && bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46) return 'GIF';
  if (
    bytes.length >= 12 &&
    bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46 &&
    bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50
  ) return 'WEBP';
  return null;
}

/** Walks any JSON value and returns the ids of every VARIABLE_ALIAS it contains. */
export function collectVariableAliasIds(value: unknown, out: Set<string> = new Set()): Set<string> {
  if (Array.isArray(value)) {
    for (const item of value) collectVariableAliasIds(item, out);
  } else if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (obj.type === 'VARIABLE_ALIAS' && typeof obj.id === 'string') out.add(obj.id);
    for (const key of Object.keys(obj)) collectVariableAliasIds(obj[key], out);
  }
  return out;
}

export const VECTOR_TYPES = new Set(['VECTOR', 'BOOLEAN_OPERATION', 'STAR', 'POLYGON']);
const ICON_PART_TYPES = new Set(['VECTOR', 'BOOLEAN_OPERATION', 'STAR', 'POLYGON', 'ELLIPSE', 'RECTANGLE', 'LINE', 'GROUP', 'FRAME']);
export const ICON_MAX_SIZE = 128;

export interface IconProbe {
  type: string;
  width: number;
  height: number;
  hasImageFill: boolean;
  /** Types of all descendants (not including the node itself). */
  descendantTypes: string[];
}

/**
 * A small container whose descendants are all vector-ish shapes (and at least one real vector)
 * is exported as one SVG icon instead of being rebuilt from divs.
 */
export function isIconCandidate(p: IconProbe): boolean {
  if (!['FRAME', 'GROUP', 'INSTANCE', 'COMPONENT'].includes(p.type)) return false;
  if (p.width > ICON_MAX_SIZE || p.height > ICON_MAX_SIZE) return false;
  if (p.hasImageFill || p.descendantTypes.length === 0) return false;
  if (!p.descendantTypes.every((t) => ICON_PART_TYPES.has(t))) return false;
  return p.descendantTypes.some((t) => VECTOR_TYPES.has(t));
}

/** Validates the role assignment chosen in the UI. Returns an error message or null. */
export function validateRoles(roles: Record<string, FrameRole>): string | null {
  const values = Object.values(roles);
  if (values.length === 0) return 'Select at least one frame.';
  const count = (r: FrameRole) => values.filter((v) => v === r).length;
  if (count('desktop') !== 1) return 'Assign exactly one frame as Desktop.';
  if (count('mobile') > 1) return 'Assign at most one frame as Mobile.';
  if (count('tablet') > 1) return 'Assign at most one frame as Tablet.';
  return null;
}
