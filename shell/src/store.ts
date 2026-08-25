/**
 * Application state.
 *
 * Deliberately thin: the engine owns all conversion decisions, so the UI only
 * tracks what is on screen and what the engine has told us.
 */
import { create } from "zustand";
import type {
  Analysis, Capabilities, ConversionResult, StageEvent, StageId,
} from "./engine";

export type View = "library" | "inspect" | "processing" | "results" | "settings";

export interface StageProgress {
  stage: StageId;
  status: "pending" | "running" | "done";
  detail?: string;
  startedAt?: number;
  finishedAt?: number;
}

export interface HistoryItem {
  id: string;
  title: string;
  author: string;
  language: string | null;
  qualityScore: number;
  qualityGate: string;
  files: number;
  at: number;
  outDir: string;
}

interface AppState {
  view: View;
  engineReady: boolean;
  engineVersion: string | null;
  capabilities: Capabilities | null;

  // current job
  file: string | null;
  analysis: Analysis | null;
  analyzing: boolean;
  stages: StageProgress[];
  overallPct: number;
  liveMetrics: Record<string, number | string>;
  warnings: string[];
  logLines: string[];
  result: ConversionResult | null;
  error: string | null;

  history: HistoryItem[];

  setView: (v: View) => void;
  setEngineReady: (ready: boolean, version?: string) => void;
  setCapabilities: (c: Capabilities) => void;
  startAnalysis: (file: string) => void;
  setAnalysis: (a: Analysis) => void;
  beginConversion: () => void;
  applyStage: (e: StageEvent) => void;
  addWarning: (w: string) => void;
  addLog: (line: string) => void;
  finish: (r: ConversionResult) => void;
  fail: (message: string) => void;
  reset: () => void;
}

const STAGE_ORDER: StageId[] = [
  "analyze", "clean", "ocr", "extract", "typography", "build", "qa",
];

const freshStages = (): StageProgress[] =>
  STAGE_ORDER.map((stage) => ({ stage, status: "pending" }));

export const useApp = create<AppState>((set, get) => ({
  view: "library",
  engineReady: false,
  engineVersion: null,
  capabilities: null,

  file: null,
  analysis: null,
  analyzing: false,
  stages: freshStages(),
  overallPct: 0,
  liveMetrics: {},
  warnings: [],
  logLines: [],
  result: null,
  error: null,

  history: [],

  setView: (view) => set({ view }),
  setEngineReady: (engineReady, engineVersion) =>
    set({ engineReady, engineVersion: engineVersion ?? get().engineVersion }),
  setCapabilities: (capabilities) => set({ capabilities }),

  startAnalysis: (file) =>
    set({
      file, analyzing: true, analysis: null, result: null, error: null,
      view: "inspect", warnings: [], logLines: [],
    }),

  setAnalysis: (analysis) => set({ analysis, analyzing: false }),

  beginConversion: () =>
    set({
      view: "processing", stages: freshStages(), overallPct: 0,
      warnings: [], logLines: [], liveMetrics: {}, result: null, error: null,
    }),

  applyStage: (e) =>
    set((s) => {
      const idx = STAGE_ORDER.indexOf(e.stage);
      const stages = s.stages.map((st, i) => {
        if (i < idx && st.status !== "done") {
          return { ...st, status: "done" as const, finishedAt: Date.now() };
        }
        if (i !== idx) return st;
        return {
          ...st,
          status: e.status === "done" ? ("done" as const) : ("running" as const),
          detail: e.detail ?? st.detail,
          startedAt: st.startedAt ?? Date.now(),
          finishedAt: e.status === "done" ? Date.now() : st.finishedAt,
        };
      });
      return { stages, overallPct: Math.max(s.overallPct, e.overallPct) };
    }),

  addWarning: (w) => set((s) => ({ warnings: [...s.warnings, w] })),

  addLog: (line) =>
    set((s) => ({ logLines: [...s.logLines.slice(-400), line] })),

  finish: (result) =>
    set((s) => ({
      result,
      view: "results",
      overallPct: 1,
      stages: s.stages.map((st) => ({ ...st, status: "done" as const })),
      history: [
        {
          id: result.jobId,
          title: result.title,
          author: result.author,
          language: result.language,
          qualityScore: result.qualityScore,
          qualityGate: result.qualityGate,
          files: result.files.length,
          at: Date.now(),
          outDir: result.files[0]?.path ?? "",
        },
        ...s.history,
      ].slice(0, 50),
    })),

  fail: (error) => set({ error, view: "results" }),

  reset: () =>
    set({
      view: "library", file: null, analysis: null, analyzing: false,
      stages: freshStages(), overallPct: 0, warnings: [], logLines: [],
      result: null, error: null, liveMetrics: {},
    }),
}));

export { STAGE_ORDER };
