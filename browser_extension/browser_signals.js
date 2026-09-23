const owgSignalExt = globalThis.browser ?? globalThis.chrome;
const OWG_CONTENT_SIGNAL_SETTINGS_KEY = "openworkgraph_browser_signal_settings_v1";
let owgContentSignalSettings = {performance_timing: true, file_upload_category: false};

function owgNormalizeContentSignalSettings(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    performance_timing: source.performance_timing !== undefined ? !!source.performance_timing : true,
    file_upload_category: source.file_upload_category !== undefined ? !!source.file_upload_category : false,
  };
}

async function owgLoadContentSignalSettings() {
  try {
    const stored = await owgSignalExt.storage.local.get(OWG_CONTENT_SIGNAL_SETTINGS_KEY);
    owgContentSignalSettings = owgNormalizeContentSignalSettings(stored?.[OWG_CONTENT_SIGNAL_SETTINGS_KEY]);
  } catch (_) {}
}

if (owgSignalExt.storage?.onChanged) {
  owgSignalExt.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes?.[OWG_CONTENT_SIGNAL_SETTINGS_KEY]) return;
    owgContentSignalSettings = owgNormalizeContentSignalSettings(changes[OWG_CONTENT_SIGNAL_SETTINGS_KEY].newValue);
  });
}
owgLoadContentSignalSettings();

function owgRoundTiming(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) return 0;
  return Math.min(10 * 60 * 1000, Math.round(number / 50) * 50);
}

function owgEmitPerformanceTiming() {
  if (!isTopFrame() || !owgContentSignalSettings.performance_timing) return;
  try {
    const nav = performance.getEntriesByType("navigation")?.[0];
    if (!nav) return;
    const metadata = {
      timing_source: "navigation_timing",
      rounded_to_ms: 50,
      response_wait_ms: owgRoundTiming(Number(nav.responseStart) - Number(nav.requestStart)),
      dom_ready_ms: owgRoundTiming(Number(nav.domContentLoadedEventEnd) - Number(nav.startTime)),
      load_complete_ms: owgRoundTiming(Number(nav.loadEventEnd) - Number(nav.startTime)),
      resource_urls_captured: false,
      page_contents_captured: false,
    };
    send("performance_timing", {}, metadata);
  } catch (_) {}
}

function owgFileCategory(file) {
  const mime = String(file?.type || "").toLowerCase();
  if (!mime) return "unknown";
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (mime.includes("zip") || mime.includes("gzip") || mime.includes("tar") || mime.includes("rar") || mime.includes("7z")) return "archive";
  if (mime.includes("spreadsheet") || mime.includes("excel") || mime === "text/csv") return "spreadsheet";
  if (mime.includes("json") || mime.includes("xml") || mime.includes("yaml")) return "structured-data";
  if (mime.startsWith("text/") || mime.includes("pdf") || mime.includes("word") || mime.includes("document") || mime.includes("presentation") || mime.includes("powerpoint")) return "document";
  return "other";
}

addEventListener("change", (event) => {
  if (!owgContentSignalSettings.file_upload_category) return;
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || String(input.type || "").toLowerCase() !== "file") return;
  const files = Array.from(input.files || []);
  if (!files.length) return;
  const counts = {};
  for (const file of files) {
    const category = owgFileCategory(file);
    counts[category] = Number(counts[category] || 0) + 1;
  }
  send("file_upload_category", {tag: "input", role: "", type: "file", name: "", label: "", contenteditable: false}, {
    file_count: files.length,
    categories: counts,
    filename_captured: false,
    path_captured: false,
    exact_size_captured: false,
    file_contents_captured: false,
    classification_source: "browser_file_mime_type",
  });
}, true);

addEventListener("load", () => setTimeout(owgEmitPerformanceTiming, 0), {once: true});
