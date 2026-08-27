/**
 * Typed client for the Warraq conversion engine.
 *
 * The shell process owns the engine sidecar; this module is the only place the
 * UI talks to it. Long-running conversions report progress through the
 * `engine://message` event stream rather than a promise, because a book can
 * take minutes.
 */
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type StageId =
  | "analyze" | "clean" | "ocr" | "extract"
  | "typography" | "build" | "qa";

export interface StageEvent {
  id: string;
  event: "stage";
  stage: StageId;
  status: "running" | "done";
  detail?: string;
  overallPct: number;
  stagePct?: number;
  etaSec?: number;
}

export interface WarningEvent {
  id: string;
  event: "warning";
  code: string;
  message: string;
}

export interface LogEvent { id: string; event: "log"; line: string }
export interface ReadyEvent { event: "ready"; schemaVersion: number; engineVersion: string }

export interface ArabicFont {
  id: string;
  description: string;
  lineHeight: number;
  hasBold: boolean;
  default: boolean;
}

export interface Capabilities {
  schemaVersion: number;
  engineVersion: string;
  device: { name: string; screen_px: [number, number]; ppi: number };
  arabicFonts: ArabicFont[];
  defaults: {
    arabicFont: string;
    fontMode: string;
    ocrEngine: string;
    makePdf: string;
    workers: number;
  };
  tools: {
    calibre: { path: string; available: boolean };
    tesseract: { path: string; available: boolean; languages: string[] };
    azure: {
      available: boolean;
      auth: string;
      endpoint: string | null;
      model: string;
      configPath: string;
      privacy: string;
    };
    kfx: { can_generate_kfx: boolean; reason: string; note: string };
  };
  stages: { id: StageId; label: string; weight: number }[];
}

export interface Analysis {
  path: string;
  title: string;
  author: string;
  pageCount: number;
  analyzedPages: number;
  sampled: boolean;
  language: "ar" | "en" | "bilingual" | null;
  docType: "text" | "scanned" | "text_over_scan" | "mixed";
  columns: number;
  tocEntries: number;
  blankPages: number;
  duplicatePages: number;
  rotatedPages: number;
  noisyPages: number;
  skewMedian: number;
  arabicRatio: number;
  plan: {
    route: string;
    reason: string;
    estimatedSeconds: number;
    willUseAzure: boolean;
  };
  findings: string[];
}

export interface Typography {
  font: string;
  fontEmbedded: boolean;
  fontIntact: boolean;
  preShaped: boolean;
  rtlValidated: boolean;
  shapingValid: boolean;
  wordsChecked: number;
  issues: string[];
}

export interface OutputFile {
  kind: string;
  label: string;
  path: string;
  name: string;
  sizeBytes: number;
  recommended: boolean;
}

export interface ConversionResult {
  jobId: string;
  input: string;
  title: string;
  author: string;
  language: string | null;
  rtl: boolean;
  route: string;
  routeReason: string;
  qualityGate: "PASS" | "WARN" | "FAIL";
  qualityScore: number;
  ocr: {
    used: boolean;
    engine: string | null;
    confidence: number | null;
    verdict: string | null;
    pagesOcred: number | null;
  };
  typography: Typography | null;
  content: {
    tokenRatio: number | null;
    vocabRecall: number | null;
    chapters: number;
    headingsDetected: number;
    headingsFromBookmarks: number;
    footnotes: number;
    images: number;
    pagesKeptAsImage: number;
  };
  reviewPages: number[];
  warnings: string[];
  files: OutputFile[];
  previews: string[];
  elapsedSeconds: number;
  schemaVersion: number;
}

export interface ConvertOptions {
  path: string;
  outDir: string;
  arabicFont?: string;
  fontMode?: "auto" | "native" | "preshape" | "embed";
  ocrEngine?: "auto" | "azure" | "tesseract";
  makePdf?: "auto" | "always" | "never";
  forceRoute?: "auto" | "reflow" | "ocr" | "fixed";
  aggressiveClean?: boolean;
  maxPages?: number;
  workers?: number;
}

type AnyMessage =
  | StageEvent | WarningEvent | LogEvent | ReadyEvent
  | { id: string; result: ConversionResult }
  | { id: string; error: { code: string; message: string; detail?: string } };

export type { AnyMessage };

export interface EngineSettings {
  ocrMode: "azure" | "offline";
  azureEndpoint: string;
  hasAzureKey: boolean;
  configPath: string;
  envOverride: boolean;
  azure: Capabilities["tools"]["azure"] & { api_version?: string };
  saved?: boolean;
}

export interface AzureTestResult {
  ok: boolean;
  reason: string;
  hint?: string;
  endpoint?: string;
  auth?: string;
}

export const engine = {
  start: () => invoke<{ started?: boolean; alreadyRunning?: boolean }>("engine_start"),
  stop: () => invoke<{ stopped: boolean }>("engine_stop"),
  status: () => invoke<{ running: boolean }>("engine_status"),
  capabilities: () => invoke<Capabilities>("engine_capabilities"),
  analyze: (path: string, samplePages?: number) =>
    invoke<Analysis>("engine_analyze", { path, samplePages }),
  convert: (options: ConvertOptions) =>
    invoke<{ jobId: string }>("engine_convert", { options }),
  cancel: (jobId: string) => invoke<{ cancelled: boolean }>("engine_cancel", { jobId }),
  /** Filters a list of folders down to the ones that still exist. */
  existingDirs: (paths: string[]) => invoke<string[]>("existing_dirs", { paths }),
  /** Warraq output folders found in the usual document locations. */
  discoverOutputDirs: () => invoke<string[]>("discover_output_dirs"),
  getSettings: () => invoke<EngineSettings>("engine_get_settings"),
  setSettings: (settings: { azureEndpoint?: string; azureKey?: string }) =>
    invoke<EngineSettings>("engine_set_settings", { settings }),
  /** Makes a real round trip to Azure; expect this to take a few seconds. */
  testAzure: () => invoke<AzureTestResult>("engine_test_azure"),
};

/** Subscribe to every engine message. Returns an unlisten function. */
export function onEngineMessage(
  handler: (msg: AnyMessage) => void,
): Promise<UnlistenFn> {
  return listen<AnyMessage>("engine://message", (e) => handler(e.payload));
}

export function onEngineClosed(handler: () => void): Promise<UnlistenFn> {
  return listen("engine://closed", () => handler());
}

/**
 * Run a conversion and resolve when the engine reports a terminal message.
 *
 * Removed in favour of central routing in App: this opened a second listener
 * per job and, because `jobId` was only known after the `convert` invoke
 * resolved, its id guard let another job's events through in the meantime.
 * Progress is now dispatched from the single app-level listener by job id.
 */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDuration(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return s ? `${m}m ${s}s` : `${m}m`;
}

export function qualityBadge(r: Pick<ConversionResult, "qualityGate" | "qualityScore" | "reviewPages">) {
  if (r.qualityGate === "FAIL")
    return { label: "Page-exact only", tone: "grey" as const };
  if (r.reviewPages.length > 0 || r.qualityGate === "WARN")
    return { label: "Review", tone: "amber" as const };
  if (r.qualityScore >= 90) return { label: "Excellent", tone: "teal" as const };
  return { label: "Good", tone: "green" as const };
}
