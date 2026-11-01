// Mirrors schemas/bundle.schema.json (v0.1). Keep both in sync.

export type FrameRole = 'desktop' | 'tablet' | 'mobile';

export interface BundleSource {
  fileKey: string | null;
  fileName: string;
  pageId: string;
  pageName: string;
  pluginVersion: string;
  editorType: string;
  exportedAt: string;
}

export interface BundleFrame {
  role: FrameRole;
  rootId: string;
  name: string;
  page: string;
  width: number;
  height: number;
  referenceAssets: string[];
}

export interface BundleNode {
  id: string;
  name: string;
  type: string;
  parentId: string | null;
  children: string[];
  [prop: string]: unknown;
}

export interface BundleAsset {
  id: string;
  kind: 'reference' | 'image-fill' | 'svg' | 'raster-export';
  format: 'PNG' | 'JPG' | 'SVG' | 'GIF' | 'WEBP';
  nodeId: string | null;
  imageHash?: string | null;
  scale?: number;
  width?: number;
  height?: number;
  byteLength?: number;
}

export interface FontRef {
  family: string;
  style: string;
  usageCount: number;
}

export interface Diagnostic {
  nodeId: string | null;
  code: string;
  severity: 'info' | 'warning' | 'error';
  message: string;
}

export interface Bundle {
  schemaVersion: '0.1';
  source: BundleSource;
  frames: BundleFrame[];
  nodes: Record<string, BundleNode>;
  variables: Record<string, unknown>[];
  variableCollections: Record<string, unknown>[];
  components: Record<string, Record<string, unknown>>;
  fonts: FontRef[];
  assets: BundleAsset[];
  diagnostics: Diagnostic[];
}

// Messages between the main thread (code.ts) and the UI (ui.ts).
export interface SelectedFrame {
  id: string;
  name: string;
  width: number;
  height: number;
  suggestedRole: FrameRole;
}

export type MainToUi =
  | { type: 'selection'; frames: SelectedFrame[]; ignored: string[] }
  | { type: 'progress'; message: string; pct?: number }
  | { type: 'bundle'; bundle: Bundle }
  | { type: 'asset'; id: string; bytes: Uint8Array }
  | { type: 'done'; nodeCount: number; assetCount: number; ms: number }
  | { type: 'error'; message: string };

export type UiToMain =
  | { type: 'export'; roles: Record<string, FrameRole> }
  | { type: 'select-node'; nodeId: string }
  | { type: 'open-url'; url: string };
