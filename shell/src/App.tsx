import { useEffect, useState } from "react";
import {
  FluentProvider, Text, Button, Spinner, Badge, Card,
  ProgressBar, Divider, Tooltip, Field, Input,
  Menu, MenuTrigger, MenuPopover, MenuList, MenuItem,
} from "@fluentui/react-components";
import {
  DocumentAdd24Regular, Library24Regular, Settings24Regular,
  CheckmarkCircle20Filled, Warning20Filled, ErrorCircle20Filled,
  ArrowClockwise20Regular, FolderOpen20Regular, TextFont24Regular,
  BookOpen24Regular, Dismiss16Regular, Delete16Regular,
  PlugConnected20Regular,
} from "@fluentui/react-icons";
import { open, message } from "@tauri-apps/plugin-dialog";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { getCurrentWebview } from "@tauri-apps/api/webview";

import { lightTheme, darkTheme, qualityTones } from "./theme";
import { useApp, isActive, jobTitle, type Job } from "./store";
import {
  engine, onEngineMessage, onEngineClosed,
  formatBytes, formatDuration, qualityBadge,
  type EngineSettings, type AzureTestResult,
} from "./engine";
import "./App.css";

const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;

/** The Warraq folder beside a source PDF: the parent of every book folder. */
const rootFor = (path: string) =>
  path.replace(/[/\\][^/\\]+$/, "") + "\\Warraq";

// Characters Windows refuses in a path component, plus the device names it
// still reserves. Arabic text is fine; punctuation in a book title is not.
const INVALID_PATH_CHARS = /[<>:"/\\|?*\u0000-\u001f]/g;
const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;

/** A safe folder name for a book, derived from its title. */
function folderName(job: Job): string {
  const raw = (job.analysis?.title || job.name.replace(/\.pdf$/i, "")).trim();
  const safe = raw
    .replace(INVALID_PATH_CHARS, " ")
    .replace(/\s+/g, " ")
    .slice(0, 80)
    .replace(/[. ]+$/, "")   // Windows silently drops these
    .trim();
  if (!safe || RESERVED.test(safe)) return `Book ${job.id}`;
  return safe;
}

/**
 * Where a book's files go: its own folder inside the Warraq folder.
 *
 * Two different books can share a guessed title, so a name already claimed by
 * another book from the same source folder is suffixed rather than silently
 * overwritten.
 */
function outDirFor(job: Job, all: Job[]): string {
  const root = rootFor(job.path);
  const base = folderName(job);
  const claimed = all.filter(
    (o) => o.id !== job.id && o.outDir?.startsWith(`${root}\\`),
  ).map((o) => o.outDir!.slice(root.length + 1).toLowerCase());

  let name = base;
  for (let n = 2; claimed.includes(name.toLowerCase()); n++) {
    name = `${base} (${n})`;
  }
  return `${root}\\${name}`;
}

/**
 * Open a folder in the file manager.
 *
 * Two ways in, because `openPath` is scope-gated and can be refused outright:
 * revealing the folder in its parent works even then. A failure is reported to
 * the user rather than swallowed - a button that does nothing when clicked is
 * worse than one that explains itself.
 */
async function openFolder(dir: string): Promise<void> {
  const attempts: string[] = [];
  try {
    await openPath(dir);
    return;
  } catch (e) {
    attempts.push(`open: ${e}`);
  }
  try {
    await revealItemInDir(dir);
    return;
  } catch (e) {
    attempts.push(`reveal: ${e}`);
  }
  console.error("could not open", dir, attempts);
  await message(
    `Warraq could not open this folder:\n\n${dir}\n\n${attempts.join("\n")}`,
    { title: "Could not open folder", kind: "error" },
  ).catch(() => undefined);
}

export default function App() {
  const s = useApp();
  const [dragging, setDragging] = useState(false);

  // Boot the engine once and wire the event stream. This is the only
  // subscription in the app; every message is routed to its job by id, which
  // is what makes several books in flight safe.
  useEffect(() => {
    let unlistenMsg: (() => void) | undefined;
    let unlistenClosed: (() => void) | undefined;

    (async () => {
      unlistenMsg = await onEngineMessage((msg) => {
        const st = useApp.getState();
        if ("event" in msg && msg.event === "ready") {
          st.setEngineReady(true, msg.engineVersion);
          engine.capabilities().then(st.setCapabilities).catch(() => undefined);
          return;
        }
        const id = (msg as { id?: string }).id;
        if (!id) return;
        if ("result" in msg) st.finishJob(id, msg.result);
        else if ("error" in msg) {
          st.failJob(id, `${msg.error.code}: ${msg.error.message}`);
        } else if ("event" in msg && msg.event === "stage") {
          st.applyStage(id, msg);
        } else if ("event" in msg && msg.event === "warning") {
          st.addWarning(id, msg.message);
        }
      });
      unlistenClosed = await onEngineClosed(() =>
        useApp.getState().setEngineReady(false));
      await engine.start();
    })();

    return () => { unlistenMsg?.(); unlistenClosed?.(); };
  }, []);

  // Work out where past books live, so "Open Warraq folder" is useful on the
  // very first run: drop remembered folders that have since been deleted, and
  // pick up any that already exist from before this app recorded anything.
  useEffect(() => {
    (async () => {
      const st = useApp.getState();
      try {
        const [alive, found] = await Promise.all([
          engine.existingDirs(st.outputRoots),
          engine.discoverOutputDirs(),
        ]);
        st.setRoots(Array.from(new Set([...alive, ...found])));
      } catch (e) {
        console.error("could not resolve output folders", e);
      }
    })();
  }, []);

  // Native file drop. The webview's own drag events are suppressed by Tauri,
  // so the OS-level drag-drop event is the only thing that fires.
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    (async () => {
      unlisten = await getCurrentWebview().onDragDropEvent((event) => {
        if (event.payload.type === "over") {
          setDragging(true);
        } else if (event.payload.type === "drop") {
          setDragging(false);
          const pdfs = event.payload.paths.filter((p) =>
            p.toLowerCase().endsWith(".pdf"));
          pdfs.forEach(analyze);
        } else {
          setDragging(false);
        }
      });
    })();
    return () => unlisten?.();
  }, []);

  const pickFiles = async () => {
    try {
      const picked = await open({
        multiple: true,
        directory: false,
        title: "Choose PDF books",
        filters: [{ name: "PDF book", extensions: ["pdf"] }],
      });
      const paths = Array.isArray(picked) ? picked : picked ? [picked] : [];
      paths.filter((p) => typeof p === "string").forEach(analyze);
    } catch (e) {
      console.error("file picker failed", e);
    }
  };

  const analyze = async (path: string) => {
    const id = useApp.getState().addJob(path);
    try {
      useApp.getState().setAnalysis(id, await engine.analyze(path));
    } catch (e) {
      useApp.getState().failLocal(id, String(e));
    }
  };

  // Hand a book to the engine. The engine keeps its own queue, so this can be
  // called for several books back to back; they run one after another.
  const convert = async (job: Job) => {
    const st = useApp.getState();
    const all = st.order.map((id) => st.jobs[id]).filter(Boolean);
    const outDir = outDirFor(job, all);
    st.markQueued(job.id, outDir);
    // The engine creates this folder as the job starts, so the Warraq folder
    // above it is a valid destination from now on - not only once the book
    // finishes.
    st.rememberRoot(rootFor(job.path));
    try {
      const { jobId } = await engine.convert({
        path: job.path,
        outDir,
        workers: st.capabilities?.defaults.workers ?? 4,
      });
      useApp.getState().attachEngineId(job.id, jobId);
    } catch (e) {
      useApp.getState().failLocal(job.id, String(e));
    }
  };

  // Remove books from the queue. Anything already handed to the engine is
  // cancelled first, otherwise a "queued" book would vanish from the list and
  // still be converted.
  const discard = async (targets: Job[]) => {
    if (targets.length === 0) return;
    useApp.getState().removeMany(targets.map((j) => j.id));
    await Promise.all(
      targets
        .filter((j) => j.engineId)
        .map((j) => engine.cancel(j.engineId!).catch(() => undefined)),
    );
  };

  const jobs = s.order.map((id) => s.jobs[id]).filter(Boolean);
  const selected = s.selected ? s.jobs[s.selected] : null;
  const running = jobs.filter((j) => j.status === "converting");
  const waiting = jobs.filter(
    (j) => j.status === "queued" || j.status === "ready" || j.status === "analyzing",
  );
  const busy = jobs.filter(isActive).length;

  return (
    <FluentProvider theme={prefersDark ? darkTheme : lightTheme}>
      <div className="app">
        <nav className="rail">
          <div className="brand" title="Warraq">و</div>
          <RailButton
            icon={<Library24Regular />} label="Library"
            active={s.view === "library"} onClick={() => s.setView("library")}
          />
          {selected && (
            <RailButton
              icon={<BookOpen24Regular />}
              label={jobTitle(selected)}
              badge={busy > 0}
              active={s.view === "job"}
              onClick={() => s.select(selected.id)}
            />
          )}
          <RailButton
            icon={<Settings24Regular />} label="Settings"
            active={s.view === "settings"} onClick={() => s.setView("settings")}
          />
        </nav>

        <main className="main">
          {s.view === "library" && (
            <LibraryView
              jobs={jobs} dragging={dragging}
              onPick={pickFiles} onConvert={convert} onDiscard={discard}
            />
          )}
          {s.view === "job" && selected && (
            <JobView job={selected} onConvert={convert} onPick={pickFiles}
                     onDiscard={() => discard([selected])} />
          )}
          {s.view === "job" && !selected && (
            <div className="view center">
              <Text className="muted">No book selected.</Text>
            </div>
          )}
          {s.view === "settings" && <SettingsView />}
        </main>

        <footer className="status">
          <StatusDot ok={s.engineReady} />
          <Text size={200}>
            {s.engineReady
              ? `Engine ready · ${s.engineVersion ?? ""}`
              : "Starting engine…"}
          </Text>
          {(running.length > 0 || waiting.length > 0) && <Divider vertical />}
          {running.length > 0 && (
            <button className="status-link"
                    onClick={() => s.select(running[0].id)}>
              Converting {jobTitle(running[0])} ·{" "}
              {Math.round(running[0].overallPct * 100)}%
            </button>
          )}
          {waiting.length > 0 && (
            <Menu>
              <MenuTrigger disableButtonEnhancement>
                <button className="status-link muted-link">
                  +{waiting.length} waiting
                </button>
              </MenuTrigger>
              <MenuPopover>
                <MenuList>
                  {waiting.map((j) => (
                    <MenuItem key={j.id} onClick={() => s.select(j.id)}
                              secondaryContent={STATUS_LABEL[j.status]}>
                      <span className="rtl-aware">{jobTitle(j)}</span>
                    </MenuItem>
                  ))}
                  <MenuItem onClick={() => s.setView("library")}>
                    Show the whole queue
                  </MenuItem>
                </MenuList>
              </MenuPopover>
            </Menu>
          )}
          {s.capabilities && (
            <>
              <Divider vertical />
              <Text size={200}>
                {s.capabilities.tools.azure.available
                  ? "Azure connected" : "Offline OCR"}
              </Text>
              <Divider vertical />
              <span className="amiri-chip">
                <TextFont24Regular fontSize={14} />
                <Text size={200}>Amiri active</Text>
              </span>
            </>
          )}
        </footer>
      </div>
    </FluentProvider>
  );
}

function RailButton(
  { icon, label, active, badge, onClick }:
  { icon: React.ReactElement; label: string; active: boolean;
    badge?: boolean; onClick: () => void },
) {
  return (
    <Tooltip content={label} relationship="label" positioning="after">
      <button className={`rail-btn${active ? " active" : ""}`} onClick={onClick}>
        {icon}
        {badge && <span className="rail-badge" />}
      </button>
    </Tooltip>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`dot${ok ? " ok" : ""}`} />;
}

/* ------------------------------------------------------------- Library */
function LibraryView(
  { jobs, dragging, onPick, onConvert, onDiscard }: {
    jobs: Job[]; dragging: boolean;
    onPick: () => void; onConvert: (j: Job) => void;
    onDiscard: (targets: Job[]) => void;
  },
) {
  const select = useApp((s) => s.select);
  const outputRoots = useApp((s) => s.outputRoots);
  const ready = jobs.filter((j) => j.status === "ready");
  const pending = jobs.filter((j) => j.status === "ready" || j.status === "queued");
  const finished = jobs.filter((j) => !isActive(j));

  // Folders written to this session, plus any remembered from earlier ones, so
  // the button works even on a fresh start with an empty queue.
  const roots = Array.from(new Set([
    ...finished.filter((j) => j.result).map((j) => rootFor(j.path)),
    ...outputRoots,
  ]));

  return (
    <div className="view">
      <div className="library-head">
        <div>
          <Text as="h1" size={800} weight="semibold">Library</Text>
          <Text size={300} className="muted">
            Turn scanned Arabic books into Kindle books that actually read like
            books.
          </Text>
        </div>
        <OpenOutputButton roots={roots} />
      </div>

      <button
        className={`dropzone${dragging ? " dragging" : ""}${
          jobs.length === 0 ? " hero" : ""
        }`}
        onClick={onPick}
      >
        {jobs.length === 0 && (
          <div className="hero-mark" aria-hidden="true">
            <div className="hero-word" />
            <div className="hero-rule">
              <span /><i /><span />
            </div>
            <Text size={200} className="hero-gloss">
              warrāq · the copyist who reproduced books by hand
            </Text>
          </div>
        )}
        <DocumentAdd24Regular fontSize={32} />
        <Text size={500} weight="semibold">
          {dragging ? "Release to add these books" : "Drop PDF books here"}
        </Text>
        <Text size={300} className="muted">
          or click to browse · add as many as you like
        </Text>
      </button>

      {jobs.length > 0 && (
        <>
          <div className="section-head">
            <Text as="h2" size={500} weight="semibold">
              Queue · {jobs.length}
            </Text>
            <div className="section-actions">
              {ready.length > 1 && (
                <Button size="small" appearance="primary"
                        onClick={() => ready.forEach(onConvert)}>
                  Convert all {ready.length}
                </Button>
              )}
              {pending.length > 0 && (
                <Button size="small" icon={<Delete16Regular />}
                        onClick={() => onDiscard(pending)}>
                  Remove {pending.length} pending
                </Button>
              )}
              {finished.length > 0 && (
                <Button size="small" onClick={() => onDiscard(finished)}>
                  Clear finished
                </Button>
              )}
            </div>
          </div>

          <div className="queue">
            {jobs.map((j) => (
              <QueueRow key={j.id} job={j}
                        onOpen={() => select(j.id)}
                        onConvert={() => onConvert(j)}
                        onRemove={() => onDiscard([j])} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Opens the folder Warraq writes books into. Books are written beside their
 * source PDF, so there can be more than one such folder; in that case the
 * button becomes a menu instead of guessing.
 *
 * The button is always present: with nothing known yet it asks the user where
 * their books are, rather than disappearing and leaving no way to find them.
 */
function OpenOutputButton({ roots }: { roots: string[] }) {
  const forgetRoot = useApp((s) => s.forgetRoot);
  const rememberRoot = useApp((s) => s.rememberRoot);

  const locate = async () => {
    try {
      const picked = await open({
        multiple: false, directory: true,
        title: "Where are your converted books?",
      });
      const dir = Array.isArray(picked) ? picked[0] : picked;
      if (typeof dir === "string" && dir) {
        rememberRoot(dir);
        await openFolder(dir);
      }
    } catch (e) {
      console.error("folder picker failed", e);
    }
  };

  if (roots.length === 0) {
    return (
      <Tooltip
        content="No converted books yet. Choose the folder to open, or convert a book and Warraq will remember it."
        relationship="label"
      >
        <Button icon={<FolderOpen20Regular />} onClick={locate}>
          Open Warraq folder
        </Button>
      </Tooltip>
    );
  }

  if (roots.length === 1) {
    return (
      <Button icon={<FolderOpen20Regular />} onClick={() => openFolder(roots[0])}>
        Open Warraq folder
      </Button>
    );
  }

  return (
    <Menu>
      <MenuTrigger disableButtonEnhancement>
        <Button icon={<FolderOpen20Regular />}>
          Open Warraq folder ({roots.length})
        </Button>
      </MenuTrigger>
      <MenuPopover>
        <MenuList>
          {roots.map((dir) => (
            <MenuItem key={dir} onClick={() => openFolder(dir)}
                      secondaryContent={
                        <span className="menu-forget"
                              role="button" tabIndex={0}
                              title="Forget this folder"
                              onClick={(e) => { e.stopPropagation(); forgetRoot(dir); }}>
                          ✕
                        </span>
                      }>
              {dir}
            </MenuItem>
          ))}
          <MenuItem onClick={locate}>Choose another folder…</MenuItem>
        </MenuList>
      </MenuPopover>
    </Menu>
  );
}

const STATUS_LABEL: Record<Job["status"], string> = {  analyzing: "Inspecting…",
  ready: "Ready to convert",
  queued: "Waiting for the engine",
  converting: "Converting",
  done: "Done",
  failed: "Failed",
};

function QueueRow(
  { job, onOpen, onConvert, onRemove }: {
    job: Job; onOpen: () => void; onConvert: () => void; onRemove: () => void;
  },
) {
  const pct = Math.round(job.overallPct * 100);
  return (
    <Card className={`queue-row ${job.status}`}>
      <button className="queue-main" onClick={onOpen}>
        <div className="queue-title">
          <Text weight="semibold" className="rtl-aware">{jobTitle(job)}</Text>
          {job.status === "done" && job.result && (
            <Badge appearance="filled"
                   color={job.result.qualityGate === "PASS" ? "success" : "warning"}>
              {job.result.qualityScore}/100
            </Badge>
          )}
          {job.status === "failed" && (
            <Badge appearance="filled" color="danger">Failed</Badge>
          )}
        </div>
        <Text size={200} className="muted">
          {job.status === "converting"
            ? `${STATUS_LABEL[job.status]} · ${pct}%`
            : STATUS_LABEL[job.status]}
        </Text>
        {(job.status === "converting" || job.status === "queued") && (
          <ProgressBar value={job.status === "queued" ? 0 : job.overallPct} />
        )}
      </button>

      <div className="queue-actions">
        {job.status === "ready" && (
          <Button size="small" appearance="primary" onClick={onConvert}>
            Convert
          </Button>
        )}
        {job.status === "failed" && (
          <Button size="small" onClick={onConvert}>Retry</Button>
        )}
        <Tooltip
          content={
            job.status === "converting" ? "Stop and remove"
              : job.status === "queued" ? "Cancel and remove"
                : "Remove from queue"
          }
          relationship="label"
        >
          <Button size="small" appearance="subtle"
                  icon={job.status === "analyzing"
                    ? <Dismiss16Regular /> : <Delete16Regular />}
                  onClick={onRemove}
                  aria-label="Remove from queue" />
        </Tooltip>
      </div>
    </Card>
  );
}

/* ----------------------------------------------------------- Job router */
function JobView(
  { job, onConvert, onPick, onDiscard }: {
    job: Job; onConvert: (j: Job) => void; onPick: () => void;
    onDiscard: () => void;
  },
) {
  if (job.status === "analyzing") {
    return (
      <div className="view center">
        <Spinner label="Inspecting the book…" />
        <Text size={200} className="muted rtl-aware">{job.name}</Text>
        <Button onClick={onDiscard}>Remove</Button>
      </div>
    );
  }
  if (job.status === "ready") {
    return (
      <InspectView job={job} onConvert={() => onConvert(job)}
                   onDiscard={onDiscard} />
    );
  }
  if (job.status === "queued" || job.status === "converting") {
    return <ProcessingView job={job} onDiscard={onDiscard} />;
  }
  return <ResultsView job={job} onAgain={onPick} onRetry={() => onConvert(job)} />;
}

/* ------------------------------------------------------------- Inspect */
function InspectView(
  { job, onConvert, onDiscard }: {
    job: Job; onConvert: () => void; onDiscard: () => void;
  },
) {
  const analysis = job.analysis;
  if (!analysis) return null;

  return (
    <div className="view">
      <Text as="h1" size={700} weight="semibold" className="rtl-aware">
        {analysis.title}
      </Text>
      <Text size={300} className="muted">
        {analysis.author} · {analysis.pageCount} pages
      </Text>

      <Card className="panel">
        {analysis.findings.map((f, i) => (
          <div className="finding" key={i}>
            <span className="spark">✦</span>
            <Text>{f}</Text>
          </div>
        ))}
      </Card>

      <Card className="panel plan">
        <Text weight="semibold">Plan</Text>
        <Text>
          {analysis.plan.willUseAzure ? "Azure OCR → " : ""}
          Amiri typography → Kindle files
        </Text>
        <Text size={200} className="muted">
          Estimated time: about {formatDuration(analysis.plan.estimatedSeconds)}
        </Text>
        <Text size={200} className="muted" style={{ wordBreak: "break-all" }}>
          Saves to {rootFor(job.path)}\{folderName(job)}
        </Text>
      </Card>

      <div className="actions">
        <Button appearance="secondary" icon={<Delete16Regular />}
                onClick={onDiscard}>
          Remove
        </Button>
        <Button appearance="primary" onClick={onConvert}>Convert</Button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- Processing */
function ProcessingView({ job, onDiscard }: { job: Job; onDiscard: () => void }) {
  const capabilities = useApp((s) => s.capabilities);
  const waiting = useApp((s) =>
    s.order.filter((id) => s.jobs[id]?.status === "queued").length);
  const labels = Object.fromEntries(
    (capabilities?.stages ?? []).map((s) => [s.id, s.label]),
  );
  const { stages, overallPct, warnings } = job;

  return (
    <div className="view">
      <Text as="h1" size={600} weight="semibold" className="rtl-aware">
        {job.status === "queued" ? "Waiting to convert" : "Converting"} ·{" "}
        {jobTitle(job)}
      </Text>
      {job.status === "queued" && (
        <Text size={300} className="muted">
          The engine converts one book at a time. This one starts as soon as the
          book ahead of it finishes.
        </Text>
      )}
      <ProgressBar value={overallPct} thickness="large" />
      <Text size={200} className="muted">
        {Math.round(overallPct * 100)}%
        {waiting > 0 && ` · ${waiting} book${waiting > 1 ? "s" : ""} queued`}
      </Text>

      <div className="actions">
        <Button appearance="secondary" icon={<Delete16Regular />}
                onClick={onDiscard}>
          {job.status === "queued" ? "Cancel and remove" : "Stop and remove"}
        </Button>
      </div>

      <Card className="panel">
        {stages.map((st) => (
          <div className={`stage ${st.status}`} key={st.stage}>
            <span className="stage-icon">
              {st.status === "done" ? "✓" : st.status === "running" ? "⟳" : "○"}
            </span>
            <Text>{labels[st.stage] ?? st.stage}</Text>
            {st.detail && <Text size={200} className="muted">{st.detail}</Text>}
          </div>
        ))}
      </Card>

      {warnings.length > 0 && (
        <Card className="panel warn">
          {warnings.map((w, i) => (
            <div className="finding" key={i}>
              <Warning20Filled /><Text size={300}>{w}</Text>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- Results */
function ResultsView(
  { job, onAgain, onRetry }: {
    job: Job; onAgain: () => void; onRetry: () => void;
  },
) {
  const setView = useApp((s) => s.setView);
  const { result, error } = job;

  if (error) {
    const friendly = error.includes("FILE_NOT_FOUND")
      ? "Warraq could not read that file. It may have been moved, or the name contains characters the engine could not decode."
      : error.includes("CANCELLED")
        ? "Conversion was cancelled."
        : error.replace(/^Error:\s*/, "");
    return (
      <div className="view center">
        <ErrorCircle20Filled fontSize={40} className="warn-icon" />
        <Text size={500} weight="semibold">Conversion failed</Text>
        <Text className="muted" style={{ maxWidth: 460, textAlign: "center" }}>
          {friendly}
        </Text>
        <Text size={200} className="muted rtl-aware">{job.path}</Text>
        <div className="actions">
          <Button appearance="primary" onClick={onRetry}>Try again</Button>
          <Button onClick={() => setView("library")}>Back to library</Button>
          <Button onClick={onAgain}>Add another book</Button>
        </div>
      </div>
    );
  }
  if (!result) return null;

  const badge = qualityBadge(result);
  const tone = qualityTones[badge.tone];
  const ty = result.typography;

  const anchor = result.files.find((f) => f.recommended) ?? result.files[0];
  const outDir = anchor?.path.replace(/[/\\][^/\\]+$/, "") ?? "";

  const openOutputFolder = async () => {
    if (anchor) {
      try {
        await revealItemInDir(anchor.path);
        return;
      } catch (e) {
        console.error("could not reveal", anchor.path, e);
      }
    }
    if (outDir) await openFolder(outDir);
  };

  return (
    <div className="view">
      <div className="result-head">
        <div>
          <Text as="h1" size={700} weight="semibold" className="rtl-aware">
            {result.title}
          </Text>
          <Text size={300} className="muted">
            {result.author} · {formatDuration(result.elapsedSeconds)}
          </Text>
        </div>
        <div className="score" style={{ background: tone.bg, color: tone.fg }}>
          <Text size={600} weight="bold">{result.qualityScore}</Text>
          <Text size={200}>{badge.label}</Text>
        </div>
      </div>

      {ty && (
        <Card className="panel">
          <Text weight="semibold">Arabic typography</Text>
          <Check ok={ty.fontEmbedded && ty.fontIntact}
                 label={`${ty.font[0].toUpperCase() + ty.font.slice(1)} font embedded`} />
          <Check ok={ty.rtlValidated} label="Right-to-left validated" />
          <Check ok={ty.shapingValid}
                 label={`Letter joining verified · ${ty.wordsChecked.toLocaleString()} words`} />
          <Check ok={ty.issues.length === 0} label="No shaping issues" />
        </Card>
      )}

      <Card className="panel">
        <Text weight="semibold">Recognition</Text>
        {result.ocr.used ? (
          <Check ok
                 label={`${result.ocr.engine === "azure" ? "Azure AI" : "Offline OCR"} · ${result.ocr.confidence}% confidence`} />
        ) : (
          <Check ok label="Native text layer — no OCR needed" />
        )}
        {result.content.vocabRecall !== null && (
          <Check ok={result.content.vocabRecall > 0.9}
                 label={`${Math.round(result.content.vocabRecall * 100)}% of source content retained`} />
        )}
        <Check ok label={`${result.content.chapters} chapters in the table of contents`} />
      </Card>

      <Card className="panel">
        <Text weight="semibold">Your files</Text>
        {result.files.filter((f) => f.kind !== "report").map((f) => (
          <div className="file" key={f.kind}>
            <Text>{f.recommended ? "★" : "　"} {f.label}</Text>
            <Text size={200} className="muted">{formatBytes(f.sizeBytes)}</Text>
          </div>
        ))}
        {outDir && (
          <Text size={200} className="muted" style={{ wordBreak: "break-all" }}>
            {outDir}
          </Text>
        )}
      </Card>

      <div className="actions">
        <Button icon={<FolderOpen20Regular />} onClick={openOutputFolder}>
          Open folder
        </Button>
        <Button icon={<ArrowClockwise20Regular />} onClick={onAgain}>
          Convert another
        </Button>
        <Button appearance="subtle" onClick={() => setView("library")}>
          Back to library
        </Button>
      </div>
    </div>
  );
}

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="finding">
      {ok ? <CheckmarkCircle20Filled className="ok-icon" />
          : <Warning20Filled className="warn-icon" />}
      <Text>{label}</Text>
    </div>
  );
}

/* ------------------------------------------------------------ Settings */
function SettingsView() {
  const caps = useApp((s) => s.capabilities);
  if (!caps) return <div className="view center"><Spinner /></div>;
  return (
    <div className="view">
      <Text as="h1" size={700} weight="semibold">Settings</Text>

      <Card className="panel">
        <Text weight="semibold">Arabic typography</Text>
        {caps.arabicFonts.map((f) => (
          <div className="finding" key={f.id}>
            <Text>{f.default ? "●" : "○"} {f.id}</Text>
            <Text size={200} className="muted">{f.description}</Text>
          </div>
        ))}
        <Text size={200} className="muted">
          Amiri is the default for every Arabic book and is applied automatically.
        </Text>
      </Card>

      <OcrSettings />

      <Card className="panel">
        <Text weight="semibold">Engine</Text>
        <Check ok={caps.tools.calibre.available} label="Calibre" />
        <Check ok={caps.tools.tesseract.available}
               label={`Tesseract · ${caps.tools.tesseract.languages.join(", ")}`} />
      </Card>

      <Card className="panel">
        <Text weight="semibold">Device</Text>
        <Text>{caps.device.name}</Text>
        <Text size={200} className="muted">
          {caps.device.screen_px[0]}×{caps.device.screen_px[1]} · {caps.device.ppi} ppi
        </Text>
      </Card>
    </div>
  );
}

/* --------------------------------------------------------- OCR settings */
/**
 * Lets the user choose how scanned books are read. Both modes are supported:
 * offline needs nothing, Azure needs a resource the user owns. Without this
 * screen the only way to connect Azure was to hand-edit JSON in %APPDATA%.
 */
function OcrSettings() {
  const [settings, setSettings] = useState<EngineSettings | null>(null);
  const [endpoint, setEndpoint] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<"" | "saving" | "testing">("");
  const [test, setTest] = useState<AzureTestResult | null>(null);

  const load = async () => {
    try {
      const s = await engine.getSettings();
      setSettings(s);
      setEndpoint(s.azureEndpoint);
    } catch (e) {
      console.error("could not read settings", e);
    }
  };

  useEffect(() => { load(); }, []);

  if (!settings) {
    return <Card className="panel"><Spinner size="tiny" label="Reading settings…" /></Card>;
  }

  const mode = settings.ocrMode;

  const save = async (values: { azureEndpoint?: string; azureKey?: string }) => {
    setBusy("saving");
    setTest(null);
    try {
      const s = await engine.setSettings(values);
      setSettings(s);
      setEndpoint(s.azureEndpoint);
      setKey("");
      // Capabilities drive the status bar, so refresh them too.
      engine.capabilities().then(useApp.getState().setCapabilities)
        .catch(() => undefined);
    } catch (e) {
      setTest({ ok: false, reason: String(e) });
    } finally {
      setBusy("");
    }
  };

  const runTest = async () => {
    setBusy("testing");
    setTest(null);
    try {
      setTest(await engine.testAzure());
    } catch (e) {
      setTest({ ok: false, reason: String(e) });
    } finally {
      setBusy("");
    }
  };

  return (
    <Card className="panel">
      <Text weight="semibold">Reading scanned books (OCR)</Text>
      <Text size={200} className="muted">
        Books that already contain text are converted without OCR. This only
        affects scans.
      </Text>

      <div className="mode-grid">
        <button className={`mode-card${mode === "offline" ? " on" : ""}`}
                onClick={() => mode === "azure" && save({ azureEndpoint: "", azureKey: "" })}>
          <Text weight="semibold">Offline · free</Text>
          <Text size={200} className="muted">
            Tesseract, bundled with Arabic language data. Nothing leaves your
            machine. No account needed.
          </Text>
          {mode === "offline" && <Badge appearance="filled" color="success">Active</Badge>}
        </button>

        <button className={`mode-card${mode === "azure" ? " on" : ""}`}
                onClick={() => document.getElementById("az-endpoint")?.focus()}>
          <Text weight="semibold">Azure Document Intelligence</Text>
          <Text size={200} className="muted">
            Noticeably better on Arabic scans. Needs an Azure resource you own;
            Microsoft bills you directly.
          </Text>
          {mode === "azure" && <Badge appearance="filled" color="success">Active</Badge>}
        </button>
      </div>

      <Divider />

      <Field label="Azure endpoint"
             hint="From your Azure AI Services or Document Intelligence resource.">
        <Input id="az-endpoint" value={endpoint} placeholder="https://<resource>.cognitiveservices.azure.com/"
               onChange={(_, d) => setEndpoint(d.value)} />
      </Field>

      <Field label={settings.hasAzureKey ? "API key (one is stored)" : "API key (optional)"}
             hint="Leave empty to sign in with your Azure identity — run 'az login' once.">
        <Input type="password" value={key} placeholder={settings.hasAzureKey ? "••••••••" : ""}
               onChange={(_, d) => setKey(d.value)} />
      </Field>

      <div className="actions">
        <Button appearance="primary" disabled={busy !== ""}
                onClick={() => save({ azureEndpoint: endpoint, azureKey: key })}>
          {busy === "saving" ? "Saving…" : "Save"}
        </Button>
        <Button disabled={busy !== "" || !endpoint}
                icon={busy === "testing" ? <Spinner size="tiny" /> : <PlugConnected20Regular />}
                onClick={runTest}>
          {busy === "testing" ? "Testing…" : "Test connection"}
        </Button>
        {mode === "azure" && (
          <Button appearance="subtle" disabled={busy !== ""}
                  onClick={() => save({ azureEndpoint: "", azureKey: "" })}>
            Disconnect
          </Button>
        )}
      </div>

      {test && (
        <div className={`test-result ${test.ok ? "ok" : "bad"}`}>
          {test.ok ? <CheckmarkCircle20Filled className="ok-icon" />
                   : <ErrorCircle20Filled className="warn-icon" />}
          <div>
            <Text size={300}>{test.reason}</Text>
            {test.hint && (
              <Text size={200} className="muted" style={{ display: "block" }}>
                {test.hint}
              </Text>
            )}
          </div>
        </div>
      )}

      {settings.envOverride && (
        <Text size={200} className="muted">
          Note: the KBO_AZURE_DI_ENDPOINT environment variable is set and
          overrides this setting.
        </Text>
      )}

      <Text size={200} className="muted" style={{ wordBreak: "break-all" }}>
        Saved in {settings.configPath}
      </Text>
      <Text size={200} className="muted">{settings.azure.privacy}</Text>
    </Card>
  );
}
