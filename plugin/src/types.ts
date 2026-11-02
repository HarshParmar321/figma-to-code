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

/** A component whose instances react to the user: its default variant and its other states. */
export interface InteractiveComponent {
  id: string; // main component (the default state); its layers are serialized as an extra root
  name: string;
  setId: string | null;
  setName: string | null;
  variantProperties: Record<string, string> | null;
  reactions: unknown[]; // the main component's own prototype reactions (inherited by instances)
  referenceAsset: string | null;
  states: ComponentState[];
}

export interface ComponentState {
  variantId: string; // serialized as an extra root
  name: string;
  variantProperties: Record<string, string> | null;
  trigger: 'hover' | 'press' | 'focus' | 'click' | 'timeout';
  wired: boolean; // false: a State=Hover variant without a prototype connection
  transition: unknown | null; // Figma transition (type, duration in seconds, easing)
  timeout?: number; // AFTER_TIMEOUT delay in seconds
  order?: number; // position in an auto-advancing chain (carousels)
  from?: string; // variant this state is reached from (chains, toggles)
  referenceAsset: string | null;
}

export interface OverlayFrame {
  rootId: string; // the overlay frame, serialized as an extra root
  name: string;
  positionType: string;
  background: unknown;
  closeOnOutside: boolean;
  referenceAsset: string | null;
}

export interface BundleInteractions {
  components: InteractiveComponent[];
  overlays: OverlayFrame[];
  /** Destination of every prototype connection: frame / layer name and type (for links and reports). */
  targets: Record<string, { name: string; type: string; topFrameId: string | null }>;
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
  interactions?: BundleInteractions;
}

// Messages between the main thread (code.ts) and the UI (ui.ts).
export interface SelectedFrame {
  id: string;
  name: string;
  width: number;
  height: number;
  suggestedRole: FrameRole;
  figmaPage?: string; // Figma page the frame is on (frames can come from several pages)
}

export interface FigmaPageFrames {
  id: string;
  name: string;
  frames: SelectedFrame[];
}

export type MainToUi =
  | { type: 'selection'; frames: SelectedFrame[]; ignored: string[] }
  | { type: 'pages'; pages: FigmaPageFrames[] }
  | { type: 'motion-set'; count: number; appear: string }
  | { type: 'progress'; message: string; pct?: number }
  | { type: 'bundle'; bundle: Bundle }
  | { type: 'asset'; id: string; bytes: Uint8Array }
  | { type: 'done'; nodeCount: number; assetCount: number; ms: number }
  | { type: 'error'; message: string };

export type UiToMain =
  | { type: 'export'; roles: Record<string, FrameRole> }
  | { type: 'select-node'; nodeId: string }
  | { type: 'select-all-frames' }
  | { type: 'list-pages' }
  | { type: 'set-motion'; appear: string; duration: number; delay: number }
  | { type: 'open-url'; url: string };
