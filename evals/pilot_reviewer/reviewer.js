/* Offline reviewer: no network requests, no browser persistence, no HTML injection. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const fields = ["selected_resources", "selected_agent_runs", "clarification_questions",
    "wrong_assumptions", "continuation_success", "consequential_wrong_action"];
  const arrays = fields.slice(0, 2), counts = fields.slice(2, 4), flags = fields.slice(4);
  const formats = ["owg.pilot-blinded-batch.v1", "owg.pilot-blinded-adjudication.v1"];
  const limitations = ["Operator-approved free text; blinding is not guaranteed.",
    "Operator-reviewed material; independence and blinding are not verified."];
  const byteSize = value => new TextEncoder().encode(value).length;
  const MAX = 32 * 1024 * 1024, PAGE = 25;
  let packet = null, entries = new Map(), saved = new Map(), current = null, page = 0;
  let dual = false, dirty = false, loadVersion = 0;

  function exact(value, keys) {
    if (!value || Array.isArray(value) || typeof value !== "object" ||
        Object.keys(value).length !== keys.length ||
        keys.some(key => !Object.prototype.hasOwnProperty.call(value, key))) {
      throw new Error("Missing or unexpected fields.");
    }
  }
  function text(value, limit = 65536) {
    if (typeof value !== "string" || !value.trim() || byteSize(value) > limit) {
      throw new Error("A bounded, nonempty string is required.");
    }
    return value;
  }
  function token(value) {
    if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value)) {
      throw new Error("Invalid opaque review ID.");
    }
    return value;
  }
  function judgment(value, extra = []) {
    exact(value, [...fields, ...extra]);
    for (const field of arrays) {
      const list = value[field];
      if (!Array.isArray(list) || list.length > 10000 ||
          list.some(item => typeof item !== "string" || !item.trim() || byteSize(item) > 65536) ||
          new Set(list).size !== list.length) {
        throw new Error("Selected evidence must be a JSON array of distinct nonempty strings.");
      }
    }
    for (const field of counts) {
      if (!Number.isSafeInteger(value[field]) || value[field] < 0) {
        throw new Error("Counts must be nonnegative safe integers.");
      }
    }
    for (const field of flags) {
      if (typeof value[field] !== "boolean") throw new Error("Judge both outcome questions explicitly.");
    }
    return value;
  }
  function parsePacket(value) {
    exact(value, ["format", "entries", "limitations"]);
    const mode = formats.indexOf(value.format);
    if (mode < 0 || value.limitations !== limitations[mode] ||
        !Array.isArray(value.entries) || value.entries.length > 1500 ||
        (mode === 0 && value.entries.length === 0)) throw new Error("Unsupported reviewer packet.");
    const index = new Map();
    for (const entry of value.entries) {
      exact(entry, ["review_id", "prompt", "review_text",
        ...(mode === 1 ? ["differing_fields", "first_review", "second_review"] : [])]);
      const id = token(entry.review_id);
      if (index.has(id)) throw new Error("Duplicate review ID.");
      text(entry.prompt); text(entry.review_text);
      if (mode === 1) {
        judgment(entry.first_review); judgment(entry.second_review);
        const differences = entry.differing_fields;
        if (!Array.isArray(differences) || !differences.length ||
            differences.some(field => !fields.includes(field)) ||
            new Set(differences).size !== differences.length) {
          throw new Error("Invalid disagreement fields.");
        }
      }
      index.set(id, entry);
    }
    return {index, dual: mode === 1};
  }
  async function read(file) {
    if (!file || file.size > MAX) throw new Error("Choose a JSON file no larger than 32 MiB.");
    return JSON.parse(await file.text());
  }
  const msg = (id, value) => { $(id).textContent = value; };
  function permitDiscard() {
    return !dirty || window.confirm("Discard the unsaved changes to this judgment?");
  }
  function clearFields() {
    for (const field of [...fields, "resolution_reason"]) $(field).value = "";
    dirty = false;
  }
  function renderEntry(focus = false) {
    const row = entries.get(current);
    $("entry").hidden = !row;
    if (!row) return;
    msg("entry-heading", "Review " + row.review_id);
    msg("prompt", row.prompt); msg("response", row.review_text);
    $("disagreement").hidden = !dual; $("resolution-group").hidden = !dual;
    if (dual) {
      msg("difference", "Differing fields: " + row.differing_fields.join(", "));
      msg("first", JSON.stringify(row.first_review, null, 2));
      msg("second", JSON.stringify(row.second_review, null, 2));
    } else {
      msg("difference", ""); msg("first", ""); msg("second", "");
    }
    clearFields();
    const existing = saved.get(current);
    if (existing) {
      for (const field of arrays) $(field).value = JSON.stringify(existing[field], null, 2);
      for (const field of [...counts, ...flags]) $(field).value = String(existing[field]);
      if (dual) $("resolution_reason").value = existing.resolution_reason;
    }
    $("clear").disabled = !existing;
    msg("entry-message", existing ? "Saved judgment loaded." : "No judgment saved for this entry.");
    if (focus) $("entry-heading").focus();
  }
  function visibleIds() {
    const mode = $("filter").value;
    return [...entries.keys()].filter(id => mode === "all" || (mode === "saved" ? saved.has(id) : !saved.has(id)));
  }
  function renderList() {
    const ids = visibleIds(), pages = Math.max(1, Math.ceil(ids.length / PAGE));
    page = Math.max(0, Math.min(page, pages - 1));
    const host = $("entry-list"); host.replaceChildren();
    for (const id of ids.slice(page * PAGE, (page + 1) * PAGE)) {
      const li = document.createElement("li"), button = document.createElement("button");
      button.type = "button"; button.textContent = id + (saved.has(id) ? " — saved" : " — not saved");
      if (id === current) button.setAttribute("aria-current", "true");
      button.addEventListener("click", () => selectEntry(id)); li.append(button); host.append(li);
    }
    $("page-back").disabled = page === 0; $("page-next").disabled = page + 1 >= pages;
    msg("coverage", saved.size + " of " + entries.size + " judgments saved. " +
      ids.length + " matching entries; page " + (page + 1) + " of " + pages + ".");
    $("export").disabled = !packet;
  }
  function selectEntry(id) {
    if (id === current || !entries.has(id) || !permitDiscard()) return;
    current = id; renderEntry(true); renderList();
  }
  $("packet-file").addEventListener("change", async () => {
    const version = ++loadVersion;
    if ((saved.size || dirty) && !window.confirm("Replace the current packet and discard its in-memory judgments?")) {
      $("packet-file").value = ""; return;
    }
    try {
      const value = await read($("packet-file").files[0]);
      const parsed = parsePacket(value);
      if (version !== loadVersion) return;
      packet = value; entries = parsed.index; dual = parsed.dual; saved = new Map();
      current = entries.keys().next().value || null; page = 0;
      $("reviewer").value = ""; $("sheet-file").value = ""; $("filter").value = "all";
      $("workspace").hidden = false; $("export-section").hidden = false;
      dirty = false; renderEntry(); renderList();
      msg("notice", dual ? "Disagreement packet opened." : "Reviewer packet opened.");
    } catch (error) {
      if (version === loadVersion) msg("notice", "Packet refused: " + error.message);
    }
  });
  $("sheet-file").addEventListener("change", async () => {
    const version = loadVersion;
    try {
      if (!packet || ((saved.size || dirty) && !window.confirm("Replace all current judgments with the selected sheet?"))) return;
      const rows = await read($("sheet-file").files[0]);
      if (version !== loadVersion) return;
      if (!Array.isArray(rows) || rows.length > entries.size) throw new Error("Invalid review sheet size.");
      const next = new Map(); let reviewer = null;
      for (const row of rows) {
        judgment(row, ["review_id", "reviewer_id", ...(dual ? ["resolution_reason"] : [])]);
        const id = token(row.review_id), who = text(row.reviewer_id, 512);
        if (!entries.has(id) || next.has(id) || (reviewer !== null && who !== reviewer)) {
          throw new Error("Sheet has unknown or duplicate IDs or mixed reviewer identities.");
        }
        if (dual) text(row.resolution_reason);
        reviewer = who; next.set(id, row);
      }
      if (reviewer !== null && reviewer.length > 128) throw new Error("Reviewer ID exceeds local form limit.");
      saved = next;
      if (reviewer !== null) $("reviewer").value = reviewer;
      renderEntry(); renderList(); msg("notice", "Saved sheet restored in memory.");
    } catch (error) { msg("notice", "Sheet refused: " + error.message); }
    finally { $("sheet-file").value = ""; }
  });
  $("review-form").addEventListener("input", () => { dirty = true; });
  $("review-form").addEventListener("submit", event => {
    event.preventDefault();
    try {
      if (!current) throw new Error("Select an entry.");
      const reviewer = text($("reviewer").value.trim(), 512);
      if ([...saved.values()].some(row => row.reviewer_id !== reviewer)) {
        throw new Error("Keep the same reviewer ID throughout a sheet; clear the workspace to start another.");
      }
      const row = {review_id: current, reviewer_id: reviewer};
      for (const field of arrays) row[field] = JSON.parse($(field).value);
      for (const field of counts) {
        if (!/^\d+$/.test($(field).value)) throw new Error("Enter both counts explicitly.");
        row[field] = Number($(field).value);
      }
      for (const field of flags) {
        if (!["true", "false"].includes($(field).value)) throw new Error("Judge both outcome questions explicitly.");
        row[field] = $(field).value === "true";
      }
      if (dual) row.resolution_reason = text($("resolution_reason").value);
      judgment(row, ["review_id", "reviewer_id", ...(dual ? ["resolution_reason"] : [])]);
      saved.set(current, row); dirty = false; $("clear").disabled = false;
      msg("entry-message", "Judgment saved in memory. Export to retain it."); renderList();
    } catch (error) { msg("entry-message", "Judgment not saved: " + error.message); }
  });
  $("clear").addEventListener("click", () => {
    if (!saved.has(current) || !window.confirm("Remove this saved judgment? It will be missing from the exported sheet.")) return;
    saved.delete(current); renderEntry(); renderList();
  });
  $("next-entry").addEventListener("click", () => {
    const ids = [...entries.keys()], offset = ids.indexOf(current);
    const next = [...ids.slice(offset + 1), ...ids.slice(0, offset)].find(id => !saved.has(id));
    if (next) selectEntry(next);
    else msg("entry-message", "No other unsaved entry.");
  });
  $("filter").addEventListener("change", () => { page = 0; renderList(); });
  $("page-back").addEventListener("click", () => { page--; renderList(); });
  $("page-next").addEventListener("click", () => { page++; renderList(); });
  $("export").addEventListener("click", () => {
    if (!packet || (dirty && !window.confirm("Export only saved judgments, excluding unsaved edits?"))) return;
    if (!window.confirm("Export this private review sheet to the authorized local destination?")) return;
    const rows = [...entries.keys()].filter(id => saved.has(id)).map(id => saved.get(id));
    const data = JSON.stringify(rows, null, 2) + "\n";
    if (byteSize(data) > MAX) { msg("notice", "Sheet exceeds the 32 MiB output limit."); return; }
    const url = URL.createObjectURL(new Blob([data], {type: "application/json"}));
    const link = document.createElement("a");
    link.href = url; link.download = dual ? "pilot-resolutions.json" : "pilot-review.json";
    document.body.append(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    msg("notice", "Export requested. Confirm the downloaded file before closing this page.");
  });
  $("reset").addEventListener("click", () => {
    if (!window.confirm("Clear all approved material and saved judgments from this page? Exported files are not deleted.")) return;
    loadVersion++; packet = null; entries.clear(); saved.clear(); current = null; dirty = false;
    $("workspace").hidden = true; $("export-section").hidden = true;
    $("packet-file").value = ""; $("sheet-file").value = ""; $("reviewer").value = "";
    $("entry-list").replaceChildren(); clearFields();
    for (const id of ["prompt", "response", "first", "second", "difference", "entry-heading", "entry-message", "coverage"]) msg(id, "");
    msg("notice", "Reviewer workspace cleared. Previously exported files remain on disk.");
  });
})();
