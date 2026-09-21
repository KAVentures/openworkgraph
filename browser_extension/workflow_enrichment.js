const WORKFLOW_CLIPBOARD_KEY = "openworkgraph_last_clipboard_transfer_v2";
const WORKFLOW_CLIPBOARD_MAX_AGE_MS = 2 * 60 * 60 * 1000;

function workflowTabContextId(tabId) {
  if (tabId === null || tabId === undefined || tabId === "") return "";
  return `${BROWSER_RUNTIME_ID}:${tabId}`;
}

function workflowSemanticAction(action, target = {}) {
  const rawAction = String(action || "").toLowerCase();
  if (["copy", "cut", "paste"].includes(rawAction)) {
    return {name: rawAction, confidence: "direct_event"};
  }
  if (rawAction === "form_submit") {
    return {name: "submit", confidence: "direct_event"};
  }

  const label = `${target?.label || ""} ${target?.name || ""}`.toLowerCase().replace(/\s+/g, " ").trim();
  if (!label) return null;

  const rules = [
    ["send", /\b(send|skicka)\b/],
    ["save", /\b(save|spara)\b/],
    ["deploy", /\b(deploy|publish|publicera)\b/],
    ["export", /\b(export|download|ladda ner)\b/],
    ["connect", /\b(connect|pair|anslut|koppla)\b/],
    ["search", /\b(search|find|sök|hitta)\b/],
    ["create", /\b(create|new|add|skapa|ny|lägg till)\b/],
    ["delete", /\b(delete|remove|trash|ta bort|radera)\b/],
    ["merge", /\bmerge\b/],
    ["approve", /\bapprove|godkänn\b/],
  ];
  for (const [name, pattern] of rules) {
    if (pattern.test(label)) return {name, confidence: "label_heuristic"};
  }
  return null;
}

async function workflowReadClipboardTransfer() {
  try {
    const stored = await ext.storage.local.get(WORKFLOW_CLIPBOARD_KEY);
    const value = stored?.[WORKFLOW_CLIPBOARD_KEY];
    return value && typeof value === "object" ? value : null;
  } catch (_) {
    return null;
  }
}

async function workflowWriteClipboardTransfer(value) {
  try {
    await ext.storage.local.set({[WORKFLOW_CLIPBOARD_KEY]: value});
  } catch (_) {}
}

async function workflowEnrichEvent(body) {
  const enriched = {...(body || {})};
  const action = String(enriched.action || "browser_event");
  const metadata = {...(enriched.metadata || {})};
  const tabContextId = workflowTabContextId(metadata.tab_id);
  if (tabContextId) metadata.tab_context_id = tabContextId;

  const semantic = workflowSemanticAction(action, enriched.target || {});
  if (semantic) {
    metadata.semantic_action = semantic.name;
    metadata.semantic_action_confidence = semantic.confidence;
  }

  if (["copy", "cut", "paste"].includes(action)) {
    metadata.evidence_channel = metadata.evidence_channel || "browser_dom";
    metadata.clipboard_contents_captured = false;
  }

  if (action === "copy" || action === "cut") {
    const transfer = {
      transfer_id: uuid(),
      observed_at_ms: Date.now(),
      source_tab_context_id: tabContextId,
    };
    metadata.clipboard_transfer_id = transfer.transfer_id;
    metadata.clipboard_source_observed = true;
    await workflowWriteClipboardTransfer(transfer);
  } else if (action === "paste") {
    const transfer = await workflowReadClipboardTransfer();
    const ageMs = transfer ? Date.now() - Number(transfer.observed_at_ms || 0) : Number.POSITIVE_INFINITY;
    if (transfer?.transfer_id && ageMs >= 0 && ageMs <= WORKFLOW_CLIPBOARD_MAX_AGE_MS) {
      metadata.clipboard_transfer_id = String(transfer.transfer_id);
      metadata.clipboard_source_observed = true;
      metadata.clipboard_link_age_seconds = Math.round(ageMs) / 1000;
      if (transfer.source_tab_context_id) {
        metadata.clipboard_source_tab_context_id = String(transfer.source_tab_context_id);
      }
    } else {
      metadata.clipboard_source_observed = false;
    }
  }

  enriched.metadata = metadata;
  return enriched;
}

// Load after background.js and wrap its single send path. Existing capture,
// retry/authentication and privacy sanitization remain unchanged.
const originalSendBrowserEvent = sendBrowserEvent;
sendBrowserEvent = async function enrichedSendBrowserEvent(body) {
  return originalSendBrowserEvent(await workflowEnrichEvent(body));
};
