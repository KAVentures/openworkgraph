const OWG_SIGNAL_SETTINGS_KEY = "openworkgraph_browser_signal_settings_v1";
const OWG_SIGNAL_DEFAULTS = {performance_timing: true, file_upload_category: false};
let owgSignalSettings = {...OWG_SIGNAL_DEFAULTS};
let owgSignalSettingsAt = 0;
let owgSignalSettingsPromise = null;

function owgNormalizeSignalSettings(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    performance_timing: source.performance_timing !== undefined ? !!source.performance_timing : OWG_SIGNAL_DEFAULTS.performance_timing,
    file_upload_category: source.file_upload_category !== undefined ? !!source.file_upload_category : OWG_SIGNAL_DEFAULTS.file_upload_category,
  };
}

async function owgStoreSignalSettings(settings) {
  owgSignalSettings = owgNormalizeSignalSettings(settings);
  owgSignalSettingsAt = Date.now();
  try { await ext.storage.local.set({[OWG_SIGNAL_SETTINGS_KEY]: owgSignalSettings}); } catch (_) {}
  return owgSignalSettings;
}

async function owgRefreshSignalSettings(force = false) {
  if (!force && Date.now() - owgSignalSettingsAt < 5000) return owgSignalSettings;
  if (owgSignalSettingsPromise) return owgSignalSettingsPromise;
  owgSignalSettingsPromise = (async () => {
    try {
      const fresh = await getJson("/v1/browser-context");
      if (fresh?.signal_settings) return await owgStoreSignalSettings(fresh.signal_settings);
    } catch (_) {}
    try {
      const stored = await ext.storage.local.get(OWG_SIGNAL_SETTINGS_KEY);
      if (stored?.[OWG_SIGNAL_SETTINGS_KEY]) return await owgStoreSignalSettings(stored[OWG_SIGNAL_SETTINGS_KEY]);
    } catch (_) {}
    return owgSignalSettings;
  })();
  try { return await owgSignalSettingsPromise; }
  finally { owgSignalSettingsPromise = null; }
}

const owgSignalOriginalSendBrowserEvent = sendBrowserEvent;
sendBrowserEvent = async function signalAwareSendBrowserEvent(body) {
  const settings = await owgRefreshSignalSettings(false);
  const action = String(body?.action || "");
  if (action === "performance_timing" && !settings.performance_timing) return true;
  if (action === "file_upload_category" && !settings.file_upload_category) return true;
  return owgSignalOriginalSendBrowserEvent(body);
};

owgRefreshSignalSettings(true);
if (ext.alarms?.onAlarm) {
  ext.alarms.onAlarm.addListener(alarm => {
    if (alarm?.name === "openworkgraph-flush") owgRefreshSignalSettings(true);
  });
}
