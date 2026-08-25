import { useEffect } from "react";
import {
  FluentProvider, Text, Button, Spinner, Badge, Card,
  ProgressBar, Divider, Tooltip,
} from "@fluentui/react-components";
import {
  DocumentAdd24Regular, Library24Regular, Settings24Regular,
  CheckmarkCircle20Filled, Warning20Filled, ErrorCircle20Filled,
  ArrowClockwise20Regular, FolderOpen20Regular, TextFont24Regular,
} from "@fluentui/react-icons";
import { open } from "@tauri-apps/plugin-dialog";

import { lightTheme, darkTheme, qualityTones } from "./theme";
import { useApp } from "./store";
import {
  engine, onEngineMessage, onEngineClosed, runConversion,
  formatBytes, formatDuration, qualityBadge,
} from "./engine";
import "./App.css";

const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;

export default function App() {
  const s = useApp();

  // Boot the engine once and wire the event stream.
  useEffect(() => {
    let unlistenMsg: (() => void) | undefined;
    let unlistenClosed: (() => void) | undefined;

    (async () => {
      unlistenMsg = await onEngineMessage((msg) => {
        if ("event" in msg && msg.event === "ready") {
          useApp.getState().setEngineReady(true, msg.engineVersion);
          engine.capabilities()
            .then(useApp.getState().setCapabilities)
            .catch(() => undefined);
        } else if ("event" in msg && msg.event === "log") {
          useApp.getState().addLog(msg.line);
        }
      });
      unlistenClosed = await onEngineClosed(() =>
        useApp.getState().setEngineReady(false));
      await engine.start();
    })();

    return () => { unlistenMsg?.(); unlistenClosed?.(); };
  }, []);

  const pickFile = async () => {
    const picked = await open({
      multiple: false,
      filters: [{ name: "PDF book", extensions: ["pdf"] }],
    });
    if (typeof picked === "string") analyze(picked);
  };

  const analyze = async (path: string) => {
    s.startAnalysis(path);
    try {
      s.setAnalysis(await engine.analyze(path));
    } catch (e) {
      s.fail(String(e));
    }
  };

  const convert = async () => {
    if (!s.file) return;
    s.beginConversion();
    const outDir = s.file.replace(/[/\\][^/\\]+$/, "") + "\\Warraq";
    try {
      const result = await runConversion(
        { path: s.file, outDir, workers: s.capabilities?.defaults.workers ?? 4 },
        (e) => useApp.getState().applyStage(e),
        (w) => useApp.getState().addWarning(w.message),
      );
      s.finish(result);
    } catch (e) {
      s.fail(String(e));
    }
  };

  return (
    <FluentProvider theme={prefersDark ? darkTheme : lightTheme}>
      <div className="app">
        <nav className="rail">
          <div className="brand" title="Warraq">و</div>
          <RailButton
            icon={<Library24Regular />} label="Library"
            active={s.view === "library"} onClick={() => s.setView("library")}
          />
          <RailButton
            icon={<Settings24Regular />} label="Settings"
            active={s.view === "settings"} onClick={() => s.setView("settings")}
          />
        </nav>

        <main className="main">
          {s.view === "library" && <LibraryView onPick={pickFile} />}
          {s.view === "inspect" && <InspectView onConvert={convert} />}
          {s.view === "processing" && <ProcessingView />}
          {s.view === "results" && <ResultsView onAgain={pickFile} />}
          {s.view === "settings" && <SettingsView />}
        </main>

        <footer className="status">
          <StatusDot ok={s.engineReady} />
          <Text size={200}>
            {s.engineReady
              ? `Engine ready · ${s.engineVersion ?? ""}`
              : "Starting engine…"}
          </Text>
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
  { icon, label, active, onClick }:
  { icon: React.ReactElement; label: string; active: boolean; onClick: () => void },
) {
  return (
    <Tooltip content={label} relationship="label" positioning="after">
      <button className={`rail-btn${active ? " active" : ""}`} onClick={onClick}>
        {icon}
      </button>
    </Tooltip>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`dot${ok ? " ok" : ""}`} />;
}

/* ------------------------------------------------------------- Library */
function LibraryView({ onPick }: { onPick: () => void }) {
  const history = useApp((s) => s.history);
  return (
    <div className="view">
      <Text as="h1" size={800} weight="semibold">Library</Text>
      <Text size={300} className="muted">
        Turn scanned Arabic books into Kindle books that actually read like books.
      </Text>

      <button className="dropzone" onClick={onPick}>
        <DocumentAdd24Regular fontSize={32} />
        <Text size={500} weight="semibold">Drop a PDF book here</Text>
        <Text size={300} className="muted">or click to browse</Text>
      </button>

      {history.length > 0 && (
        <>
          <Text as="h2" size={500} weight="semibold" className="section">Recent</Text>
          <div className="grid">
            {history.map((h) => (
              <Card key={h.id} className="book-card">
                <Text weight="semibold" className="rtl-aware">{h.title}</Text>
                <Text size={200} className="muted">{h.author}</Text>
                <Badge appearance="filled"
                       color={h.qualityGate === "PASS" ? "success" : "warning"}>
                  {h.qualityScore}/100
                </Badge>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- Inspect */
function InspectView({ onConvert }: { onConvert: () => void }) {
  const { analysis, analyzing, file, reset } = useApp();

  if (analyzing || !analysis) {
    return (
      <div className="view center">
        <Spinner label="Inspecting the book…" />
        <Text size={200} className="muted">{file}</Text>
      </div>
    );
  }

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
      </Card>

      <div className="actions">
        <Button appearance="secondary" onClick={reset}>Cancel</Button>
        <Button appearance="primary" onClick={onConvert}>Convert</Button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- Processing */
function ProcessingView() {
  const { stages, overallPct, capabilities, warnings } = useApp();
  const labels = Object.fromEntries(
    (capabilities?.stages ?? []).map((s) => [s.id, s.label]),
  );

  return (
    <div className="view">
      <Text as="h1" size={600} weight="semibold">Converting…</Text>
      <ProgressBar value={overallPct} thickness="large" />
      <Text size={200} className="muted">{Math.round(overallPct * 100)}%</Text>

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
function ResultsView({ onAgain }: { onAgain: () => void }) {
  const { result, error, reset } = useApp();

  if (error) {
    return (
      <div className="view center">
        <ErrorCircle20Filled fontSize={40} />
        <Text size={500} weight="semibold">Conversion failed</Text>
        <Text className="muted">{error}</Text>
        <Button appearance="primary" onClick={reset}>Back</Button>
      </div>
    );
  }
  if (!result) return null;

  const badge = qualityBadge(result);
  const tone = qualityTones[badge.tone];
  const ty = result.typography;

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
          <Check ok={ty.issues.length === 0} label="Diacritics preserved" />
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
      </Card>

      <div className="actions">
        <Button icon={<FolderOpen20Regular />}>Open folder</Button>
        <Button icon={<ArrowClockwise20Regular />} onClick={onAgain}>
          Convert another
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

      <Card className="panel">
        <Text weight="semibold">Engine</Text>
        <Check ok={caps.tools.calibre.available} label="Calibre" />
        <Check ok={caps.tools.tesseract.available}
               label={`Tesseract · ${caps.tools.tesseract.languages.join(", ")}`} />
        <Check ok={caps.tools.azure.available}
               label={`Azure Document Intelligence · ${caps.tools.azure.auth}`} />
        <Text size={200} className="muted">{caps.tools.azure.privacy}</Text>
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
