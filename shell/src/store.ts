/**
 * Application state.
 *
 * The engine executes one conversion at a time but accepts an unbounded queue
 * of them, and tags every event with the job id it belongs to. The UI mirrors
 * that: it holds a map of jobs and routes each incoming event to its owner, so
 * adding a second book never disturbs the first one's progress.
 */
import { create } from "zustand";
import type {
  Analysis, Capabilities, ConversionResult, StageEvent, StageId,
} from "./engine";

export type View = "library" | "job" | "settings";

export type JobStatus =
  | "analyzing"   // inspecting the PDF
  | "ready"       // inspected, waiting for the user to press Convert
  | "queued"      // handed to the engine, not yet started
  | "converting"
  | "done"
  | "failed";

export interface StageProgress {
  stage: StageId;
  status: "pending" | "running" | "done";
  detail?: string;
  startedAt?: number;
  finishedAt?: number;
}

export interface Job {
  id: string;
  /** Assigned by the engine when the job is accepted. Null until then. */
  engineId: string | null;
  path: string;
  name: string;
  /** Folder this book's files are written to. Fixed when it is queued. */
  outDir: string | null;
  status: JobStatus;
  analysis: Analysis | null;
  stages: StageProgress[];
  overallPct: number;
  warnings: string[];
  result: ConversionResult | null;
  error: string | null;
  addedAt: number;
}

/**
 * Events that arrived before we learned the engine id for a job. The `convert`
 * invoke is a round trip, and the engine can start emitting before it returns,
 * so these are held and replayed once the id is known.
 */
type Buffered =
  | { kind: "stage"; event: StageEvent }
  | { kind: "warning"; message: string }
  | { kind: "result"; result: ConversionResult }
  | { kind: "error"; message: string };

interface AppState {
  view: View;
  selected: string | null;
  engineReady: boolean;
  engineVersion: string | null;
  capabilities: Capabilities | null;

  jobs: Record<string, Job>;
  order: string[];
  buffer: Record<string, Buffered[]>;
  /** Engine ids the user removed; their late events are dropped, not buffered. */
  dismissed: Record<string, true>;
  /** Folders Warraq has written books into, most recent first. */
  outputRoots: string[];

  setView: (v: View) => void;
  select: (id: string) => void;
  setEngineReady: (ready: boolean, version?: string) => void;
  setCapabilities: (c: Capabilities) => void;

  addJob: (path: string) => string;
  setAnalysis: (id: string, a: Analysis) => void;
  markQueued: (id: string, outDir: string) => void;
  attachEngineId: (id: string, engineId: string) => void;

  applyStage: (engineId: string, e: StageEvent) => void;
  addWarning: (engineId: string, message: string) => void;
  finishJob: (engineId: string, result: ConversionResult) => void;
  failJob: (engineId: string, message: string) => void;
  failLocal: (id: string, message: string) => void;

  removeJob: (id: string) => void;
  removeMany: (ids: string[]) => void;
  clearFinished: () => void;
  rememberRoot: (dir: string) => void;
  rememberRoots: (dirs: string[]) => void;
  forgetRoot: (dir: string) => void;
  setRoots: (dirs: string[]) => void;
}

const STAGE_ORDER: StageId[] = [
  "analyze", "clean", "ocr", "extract", "typography", "build", "qa",
];

const freshStages = (): StageProgress[] =>
  STAGE_ORDER.map((stage) => ({ stage, status: "pending" }));

const basename = (p: string) => p.split(/[/\\]/).pop() ?? p;

const dirname = (p: string) => p.replace(/[/\\][^/\\]+$/, "");

/**
 * Output folders seen in past sessions. The queue itself is deliberately not
 * persisted, but "where did my books go?" must still be answerable after a
 * restart, so just the folders are remembered.
 */
const ROOTS_KEY = "warraq.outputRoots";

function loadRoots(): string[] {
  try {
    const raw = localStorage.getItem(ROOTS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((r) => typeof r === "string") : [];
  } catch {
    return [];
  }
}

function saveRoots(roots: string[]): void {
  try {
    localStorage.setItem(ROOTS_KEY, JSON.stringify(roots));
  } catch {
    /* storage unavailable - the button just won't survive a restart */
  }
}

let counter = 0;
const nextId = () => `job-${Date.now().toString(36)}-${counter++}`;

/** Human label for a job, best available source first. */
export function jobTitle(j: Job): string {
  return j.result?.title || j.analysis?.title || j.name;
}

export const ACTIVE: JobStatus[] = ["analyzing", "ready", "queued", "converting"];

export const isActive = (j: Job) => ACTIVE.includes(j.status);

export const useApp = create<AppState>((set, get) => {
  /** Apply a change to the job owning `engineId`, or buffer it if unknown. */
  const withJob = (
    engineId: string,
    buffered: Buffered,
    change: (j: Job) => Job,
  ) =>
    set((s) => {
      const id = s.order.find((i) => s.jobs[i]?.engineId === engineId);
      if (!id) {
        // The job was removed by the user: its trailing events are noise.
        if (s.dismissed[engineId]) return {};
        return {
          buffer: {
            ...s.buffer,
            [engineId]: [...(s.buffer[engineId] ?? []), buffered],
          },
        };
      }
      return { jobs: { ...s.jobs, [id]: change(s.jobs[id]) } };
    });

  const applyStageTo = (j: Job, e: StageEvent): Job => {
    const idx = STAGE_ORDER.indexOf(e.stage);
    const stages = j.stages.map((st, i) => {
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
    return {
      ...j,
      status: j.status === "queued" ? "converting" : j.status,
      stages,
      overallPct: Math.max(j.overallPct, e.overallPct),
    };
  };

  const finishTo = (j: Job, result: ConversionResult): Job => ({
    ...j,
    result,
    status: "done",
    overallPct: 1,
    stages: j.stages.map((st) => ({ ...st, status: "done" as const })),
  });

  const failTo = (j: Job, message: string): Job => ({
    ...j, error: message, status: "failed",
  });

  return {
    view: "library",
    selected: null,
    engineReady: false,
    engineVersion: null,
    capabilities: null,

    jobs: {},
    order: [],
    buffer: {},
    dismissed: {},
    outputRoots: loadRoots(),

    setView: (view) => set({ view }),
    select: (selected) => set({ selected, view: "job" }),
    setEngineReady: (engineReady, engineVersion) =>
      set({ engineReady, engineVersion: engineVersion ?? get().engineVersion }),
    setCapabilities: (capabilities) => set({ capabilities }),

    addJob: (path) => {
      const id = nextId();
      const job: Job = {
        id, engineId: null, path, name: basename(path), outDir: null,
        status: "analyzing", analysis: null, stages: freshStages(),
        overallPct: 0, warnings: [], result: null, error: null,
        addedAt: Date.now(),
      };
      set((s) => ({
        jobs: { ...s.jobs, [id]: job },
        order: [...s.order, id],
        selected: id,
        view: "job",
      }));
      return id;
    },

    setAnalysis: (id, analysis) =>
      set((s) =>
        s.jobs[id]
          ? { jobs: { ...s.jobs, [id]: { ...s.jobs[id], analysis, status: "ready" } } }
          : {}),

    markQueued: (id, outDir) =>
      set((s) =>
        s.jobs[id]
          ? {
              jobs: {
                ...s.jobs,
                [id]: {
                  ...s.jobs[id], status: "queued", outDir, stages: freshStages(),
                  overallPct: 0, warnings: [], result: null, error: null,
                },
              },
            }
          : {}),

    attachEngineId: (id, engineId) =>
      set((s) => {
        if (!s.jobs[id]) return {};
        let job: Job = { ...s.jobs[id], engineId };
        for (const b of s.buffer[engineId] ?? []) {
          if (b.kind === "stage") job = applyStageTo(job, b.event);
          else if (b.kind === "warning") {
            job = { ...job, warnings: [...job.warnings, b.message] };
          } else if (b.kind === "result") job = finishTo(job, b.result);
          else job = failTo(job, b.message);
        }
        const buffer = { ...s.buffer };
        delete buffer[engineId];
        return { jobs: { ...s.jobs, [id]: job }, buffer };
      }),

    applyStage: (engineId, event) =>
      withJob(engineId, { kind: "stage", event }, (j) => applyStageTo(j, event)),

    addWarning: (engineId, message) =>
      withJob(engineId, { kind: "warning", message },
              (j) => ({ ...j, warnings: [...j.warnings, message] })),

    finishJob: (engineId, result) => {
      // Record where the book landed while the paths are in hand.
      const dir = dirname(result.files[0]?.path ?? "");
      if (dir) get().rememberRoot(dir);
      withJob(engineId, { kind: "result", result }, (j) => finishTo(j, result));
    },

    failJob: (engineId, message) =>
      withJob(engineId, { kind: "error", message }, (j) => failTo(j, message)),

    failLocal: (id, message) =>
      set((s) =>
        s.jobs[id]
          ? { jobs: { ...s.jobs, [id]: failTo(s.jobs[id], message) } }
          : {}),

    removeJob: (id) => get().removeMany([id]),

    removeMany: (ids) =>
      set((s) => {
        const drop = new Set(ids);
        const jobs = { ...s.jobs };
        const buffer = { ...s.buffer };
        const dismissed = { ...s.dismissed };

        for (const id of drop) {
          const engineId = jobs[id]?.engineId;
          if (engineId) {
            dismissed[engineId] = true;
            delete buffer[engineId];
          }
          delete jobs[id];
        }

        const order = s.order.filter((i) => !drop.has(i));
        const stillSelected = s.selected && !drop.has(s.selected);
        const selected = stillSelected
          ? s.selected
          : (order[order.length - 1] ?? null);
        return {
          jobs, order, buffer, dismissed, selected,
          view: selected ? s.view : "library",
        };
      }),

    clearFinished: () =>
      get().removeMany(
        get().order.filter((i) => {
          const j = get().jobs[i];
          return j && !isActive(j);
        }),
      ),

    rememberRoot: (dir) => get().rememberRoots([dir]),

    rememberRoots: (dirs) =>
      set((s) => {
        const fresh = dirs.filter(Boolean);
        if (fresh.length === 0) return {};
        const outputRoots = [
          ...fresh,
          ...s.outputRoots.filter((r) => !fresh.includes(r)),
        ].slice(0, 8);
        saveRoots(outputRoots);
        return { outputRoots };
      }),

    setRoots: (dirs) =>
      set(() => {
        const outputRoots = dirs.slice(0, 8);
        saveRoots(outputRoots);
        return { outputRoots };
      }),

    forgetRoot: (dir) =>
      set((s) => {
        const outputRoots = s.outputRoots.filter((r) => r !== dir);
        saveRoots(outputRoots);
        return { outputRoots };
      }),
  };
});

export { STAGE_ORDER };
