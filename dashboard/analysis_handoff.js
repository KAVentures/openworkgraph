(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let period = 'week', start = '', end = '', generation = 0, pending = null, activeHandoff = null;
  const staleScope = 'Work period changed. Prepare a new AI handoff for the selected period.';
  const dateInput = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  // Date.parse accepts 24:00:00 and impossible dates such as February 30,
  // rolling both into a different day. No evidence or export-bound aliasing.
  const calendarDay = value => {
    if (typeof value !== 'string') return false;
    const parts = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
    if (!parts) return false;
    const year = +parts[1], month = +parts[2], day = +parts[3];
    const check = new Date(0);
    check.setUTCFullYear(year, month - 1, day);
    return check.getUTCFullYear() === year &&
      check.getUTCMonth() === month - 1 && check.getUTCDate() === day;
  };
  const strictClockInstant = value => typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(value) &&
    calendarDay(value) && Number.isFinite(Date.parse(value));
  // Date.parse truncates sub-millisecond fractions. A returned preview or
  // ticket shifted by even one microsecond must not alias the selected range.
  // Strip the fraction only for the whole-second UTC conversion; compare the
  // entire decimal fraction after removing insignificant trailing zeros.
  function exactInstantParts(value) {
    if (!strictClockInstant(value)) return null;
    const fraction = /\.(\d+)(?=Z|[+-]\d{2}:\d{2}$)/.exec(value);
    const wholeSecond = fraction
      ? value.slice(0, fraction.index) + value.slice(fraction.index + fraction[0].length)
      : value;
    const seconds = Date.parse(wholeSecond);
    return Number.isFinite(seconds)
      ? [seconds, fraction ? fraction[1].replace(/0+$/, '') : ''] : null;
  }
  // Date.parse cannot order observations within the same millisecond.
  // Compare exact UTC whole seconds, then fractional digits with implicit
  // trailing zeros. Invalid instants return NaN so no ordering can pass.
  function compareExactInstants(a, b) {
    const left = exactInstantParts(a), right = exactInstantParts(b);
    if (!left || !right) return NaN;
    if (left[0] !== right[0]) return left[0] < right[0] ? -1 : 1;
    const x = left[1], y = right[1];
    for (let i = 0; i < Math.max(x.length, y.length); i++) {
      const digitX = x.charAt(i) || '0', digitY = y.charAt(i) || '0';
      if (digitX !== digitY) return digitX < digitY ? -1 : 1;
    }
    return 0;
  }
  function sameExactInstant(a, b) {
    return compareExactInstants(a, b) === 0;
  }
  // Lease bounds are provenance disclosures, not authorization. Date.toISOString
  // rounds away microseconds; retain the entire supplied fractional instant
  // while normalizing its UTC offset for a truthful, readable snapshot.
  function canonicalUtcInstant(value) {
    const parts = exactInstantParts(value);
    if (!parts) return null;
    const utcSecond = new Date(parts[0]).toISOString();
    return utcSecond.slice(0, -5) + '.' + parts[1].padEnd(3, '0') + 'Z';
  }
  function selection() {
    if (period === 'current' || period === 'all') return {scope: period};
    const now = new Date(), a = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const b = new Date(a); b.setDate(b.getDate()+1);
    if (period === 'week') a.setDate(a.getDate()-6);
    if (period === 'custom') {
      if (!start || !end) throw new Error('Choose both dates.');
      const aa = new Date(start+'T00:00:00'), bb = new Date(end+'T00:00:00');
      // Date silently rolls impossible days (e.g. February 30) into March.
      if (!Number.isFinite(+aa) || !Number.isFinite(+bb) ||
          dateInput(aa)!==start || dateInput(bb)!==end || aa>bb)
        throw new Error('Choose valid dates, with end on or after start.');
      bb.setDate(bb.getDate()+1);
      return {scope:'range',since:aa.toISOString(),until:bb.toISOString()};
    }
    return {scope:'range',since:a.toISOString(),until:b.toISOString()};
  }
  // Relative calendar selections can change at local midnight without a DOM
  // event. Compare UTC instants, not their serialized strings: the backend
  // echoes +00:00 while the local date picker emits equivalent Z timestamps.
  function selectionStillMatches(chosen) {
    try {
      const now = selection();
      if (now.scope !== chosen?.scope) return false;
      if (now.scope !== 'range') return true;
      return sameExactInstant(now.since, chosen.since) &&
        sameExactInstant(now.until, chosen.until);
    } catch (_) { return false; }
  }
  window.owgEvidenceSelection = selection;
  // A malformed preview cannot authorize a download or become an AI request.
  // Optional legacy fields stay optional; fields used by this UI must be safe.
  function validatePreview(d) {
    const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
    const count = v => Number.isSafeInteger(v) && v >= 0;
    const counts = v => object(v) && Object.values(v).every(count);
    // Shared clock/calendar validation also applies to ticket and lease bounds.
    const instant = strictClockInstant;
    const dateLabel = value => typeof value === 'string' &&
      (/^\d{4}-\d{2}-\d{2}$/.test(value) || instant(value)) &&
      calendarDay(value) && Number.isFinite(Date.parse(value));
    // Date-only chunk labels denote UTC midnight; timestamped labels retain
    // their full fractional precision and explicit offset for comparisons.
    const chunkInstant = value => /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? value + 'T00:00:00Z' : value;
    // Displayed date chunks must not claim observations outside this selection.
    // 'all' is unbounded; 'current' may have a recorder-run start bound.
    const bounds = d?.window;
    const windowStart = bounds?.since == null ? null :
      (instant(bounds.since) ? Date.parse(bounds.since) : NaN);
    const windowEnd = bounds?.until == null ? null :
      (instant(bounds.until) ? Date.parse(bounds.until) : NaN);
    const validWindow = object(bounds) && (
      (bounds.scope === 'all' && windowStart === null && windowEnd === null) ||
      (bounds.scope === 'current' && windowEnd === null &&
        (windowStart === null || Number.isFinite(windowStart))) ||
      (bounds.scope === 'range' && Number.isFinite(windowStart) &&
        Number.isFinite(windowEnd) && compareExactInstants(bounds.since, bounds.until) < 0));
    const first = d?.first_observed_at, last = d?.last_observed_at;
    const observed = (d?.canonical_rows === 0 && first == null && last == null) ||
      (d?.canonical_rows > 0 && instant(first) && instant(last) &&
        compareExactInstants(first, last) <= 0);
    // Observed events must belong to the selected window; the range end is
    // exclusive. Compare instants, not textual timezone representations.
    const observedWithinWindow = (first == null && last == null) ||
      (instant(first) && instant(last) &&
        (windowStart === null || compareExactInstants(first, bounds.since) >= 0) &&
        (windowEnd === null || compareExactInstants(last, bounds.until) < 0));
    const chunks = d?.analysis_plan == null ||
      (object(d.analysis_plan) &&
        // The server uses a positive bounded page size and reports whether
        // its listed dates were clipped to the selected window. Optional
        // legacy fields may be absent, but contradictory claims fail closed.
        (d.analysis_plan.page_size == null ||
          (Number.isSafeInteger(d.analysis_plan.page_size) && d.analysis_plan.page_size > 0)) &&
        (d.analysis_plan.chunks_respect_selected_window == null ||
          d.analysis_plan.chunks_respect_selected_window === true) &&
        (d.analysis_plan.more_dates_not_listed == null ||
          typeof d.analysis_plan.more_dates_not_listed === 'boolean') &&
        (d.analysis_plan.date_chunks == null ||
          (Array.isArray(d.analysis_plan.date_chunks) &&
            // The server lists at most 31 dates. A larger plan is not a
            // trustworthy local preview and must not grow the modal unbounded.
            d.analysis_plan.date_chunks.length <= 31 &&
            d.analysis_plan.date_chunks.every((x, i, list) => {
              if (!object(x) || !dateLabel(x.since) || !dateLabel(x.until) ||
                  !count(x.event_rows)) return false;
              // A displayed stepwise plan must not silently repeat, overlap,
              // reverse or reorder evidence windows. Date-only labels are UTC.
              const sinceInstant = chunkInstant(x.since), untilInstant = chunkInstant(x.until);
              const since = Date.parse(sinceInstant);
              // The backend groups rows by UTC date and clips that one-day
              // window to the selected period. A multi-day or midnight-
              // crossing chunk would falsely advertise daily coverage.
              const nextUtcMidnight = new Date(
                Math.floor(since / 86400000) * 86400000 + 86400000).toISOString();
              // A positive-count chunk must overlap the actual observed span.
              // The chunk end is exclusive; do not present events before the
              // first or after the last observed event as supported evidence.
              if (x.event_rows > 0 &&
                  (!instant(first) || !instant(last) ||
                   compareExactInstants(untilInstant, first) <= 0 ||
                   compareExactInstants(sinceInstant, last) > 0))
                return false;
              return compareExactInstants(sinceInstant, untilInstant) < 0 &&
                compareExactInstants(untilInstant, nextUtcMidnight) <= 0 &&
                (windowStart === null || compareExactInstants(sinceInstant, bounds.since) >= 0) &&
                (windowEnd === null || compareExactInstants(untilInstant, bounds.until) <= 0) &&
                // A daily plan is one entry per UTC calendar date, not
                // several disjoint fragments of the same day. UTC instants
                // (rather than textual timezone labels) define identity.
                (i === 0 || (compareExactInstants(
                  chunkInstant(list[i - 1].until), sinceInstant) <= 0 &&
                  Math.floor(Date.parse(chunkInstant(list[i - 1].since)) / 86400000) <
                    Math.floor(since / 86400000)));
            }))));
    // Both maps partition the selected events; a date plan may list only
    // the first 31 days, but cannot list more events than were selected.
    // Accumulate safely: individually valid integers may overflow in sum.
    const sumSafe = values => {
      let total = 0;
      for (const value of values) {
        if (!count(value) || !Number.isSafeInteger(total + value)) return null;
        total += value;
      }
      return total;
    };
    const partitionCounts = object(d?.event_types) && object(d?.sources) &&
      sumSafe(Object.values(d.event_types)) === d.canonical_rows &&
      sumSafe(Object.values(d.sources)) === d.canonical_rows;
    const plannedRows = Array.isArray(d?.analysis_plan?.date_chunks)
      ? sumSafe(d.analysis_plan.date_chunks.map(x => x?.event_rows)) : 0;
    const conserved = count(d?.canonical_rows) && count(d?.retained_rows) &&
      d.canonical_rows <= d.retained_rows && partitionCounts &&
      plannedRows !== null && plannedRows <= d.canonical_rows;
    // A daily plan is not a proof of complete capture. But when the backend
    // explicitly says all selected UTC dates were listed, the listed rows
    // must account for every selected event. A truncated plan must contain
    // the first 31 UTC dates and leave a later observed date unlisted.
    // Legacy previews without a coverage flag remain conservatively usable.
    const plan = d?.analysis_plan;
    const listed = plan?.date_chunks;
    const coverageFlag = plan?.more_dates_not_listed;
    // Explicit UTC-day plans are ordered from the earliest selected event.
    // A conserved row total alone cannot prove that the displayed dates
    // contain the first/last observed event. The end bound is exclusive.
    const containsInstant = (chunk, value) => chunk && instant(value) &&
      compareExactInstants(chunkInstant(chunk.since), value) <= 0 &&
      compareExactInstants(value, chunkInstant(chunk.until)) < 0;
    const explicitEndpointsValid = coverageFlag == null || d.canonical_rows === 0 ||
      (Array.isArray(listed) && listed.length > 0 &&
        containsInstant(listed[0], first) &&
        (coverageFlag !== false || containsInstant(listed[listed.length - 1], last)));
    const planCoverageValid = plan == null || (
      (plan.complete_history_claimed == null ||
        plan.complete_history_claimed === false) &&
      explicitEndpointsValid &&
      // Server daily chunks are built from dates with observed events. A
      // claimed complete/truncated list cannot contain invented empty days;
      // retain zero-row chunks only for legacy plans without a coverage flag.
      (coverageFlag == null ||
        (Array.isArray(listed) && listed.every(chunk => chunk.event_rows > 0))) &&
      (coverageFlag !== false ||
        (Array.isArray(listed) && plannedRows === d.canonical_rows)) &&
      (coverageFlag !== true ||
        (Array.isArray(listed) && listed.length === 31 &&
          plannedRows !== null && plannedRows < d.canonical_rows &&
          // The omission flag means a 32nd DISTINCT UTC date, not merely
          // an observation after a clipped 31st-day interval.
          instant(last) &&
          Math.floor(Date.parse(last) / 86400000) >
            Math.floor(Date.parse(listed[30].since) / 86400000))));
    // Work and capture-health counts describe the same selected evidence,
    // not a second independent estimate. Legacy previews may omit this block.
    // The server samples at most six selected focus spans. Never show context
    // examples inconsistent with the selected focus evidence or observed span.
    const examplesValid = Array.isArray(d?.redacted_examples) &&
      d.redacted_examples.length <= Math.min(6, d?.event_types?.focus_span ?? 0) &&
      d.redacted_examples.every(sample => object(sample) &&
        // Samples are evidence labels, not arbitrary JSON to stringify into
        // a misleading "[object Object]" or "undefined" in the review UI.
        (sample.app == null ||
          (typeof sample.app === 'string' && !!sample.app.trim())) &&
        (sample.window_title == null || typeof sample.window_title === 'string') &&
        (sample.observed_at == null ||
          (instant(sample.observed_at) && instant(first) && instant(last) &&
           compareExactInstants(first, sample.observed_at) <= 0 &&
           compareExactInstants(sample.observed_at, last) <= 0)));
    // The modal must not promise text exclusion when a supplied preview
    // explicitly contradicts the local export's privacy contract. Legacy
    // previews without this optional attestation remain usable, but uncertain.
    const workText = d?.work_text;
    const workTextValid = workText == null || (object(workText) &&
      (workText.included_in_activity_package == null ||
        workText.included_in_activity_package === false) &&
      (workText.local_capture_enabled == null ||
        typeof workText.local_capture_enabled === 'boolean'));
    const work = d?.work_evidence;
    const healthRows = sumSafe([d?.event_types?.capture_gap ?? 0,
      d?.event_types?.capture_health ?? 0]);
    const workCountsAbsent = work?.ordinary_event_rows == null &&
      work?.health_event_rows == null;
    const workCountsValid = workCountsAbsent ||
      (count(work?.ordinary_event_rows) && count(work?.health_event_rows) &&
        sumSafe([work.ordinary_event_rows, work.health_event_rows]) === d?.canonical_rows &&
        work.health_event_rows === healthRows);
    const lastWork = work?.last_work_observed_at;
    const workValid = work == null || (object(work) && workCountsValid &&
      // The local preview does not measure recorder liveness. Its backend
      // explicitly reports false; a forged affirmative claim cannot become
      // reviewable evidence, even though the UI separately warns of uncertainty.
      (work.recorder_liveness_verified == null ||
        work.recorder_liveness_verified === false) &&
      (lastWork == null || (d?.canonical_rows > healthRows &&
        instant(lastWork) && instant(first) && instant(last) &&
        compareExactInstants(first, lastWork) <= 0 &&
        compareExactInstants(lastWork, last) <= 0)));
    if (!object(d) || !conserved || typeof d.request_text !== 'string' || !d.request_text.trim() ||
        !count(d.canonical_rows) || !count(d.retained_rows) ||
        !counts(d.event_types) || !counts(d.sources) ||
        !examplesValid ||
        !observed || !observedWithinWindow || !validWindow || !chunks ||
        !planCoverageValid || !workValid || !workTextValid) {
      throw new Error('Preview evidence was incomplete or malformed. Nothing was copied or downloaded; prepare again.');
    }
  }
  // Source names are observed labels, not verified provider identities. Keep
  // the review modal bounded even when an import supplies many source labels;
  // disclose every omitted category and its selected-event count.
  // Imported evidence labels are untrusted display text. HTML escaping does
  // not neutralize bidi overrides or invisible line/control separators that
  // can visually spoof a different source or focus observation. Expose their
  // code points in the review only; never rewrite canonical evidence/exports.
  function visibleEvidenceLabel(value, kind) {
    let shown = '', length = 0;
    for (const character of value) {
      if (length === 96) return shown + `… [${kind} label truncated]`;
      length++;
      const cp = character.codePointAt(0);
      const formatting = cp <= 0x1f || cp === 0x7f ||
        (cp >= 0x80 && cp <= 0x9f) || cp === 0x061c ||
        cp === 0x00ad || cp === 0x034f || cp === 0x180e ||
        (cp >= 0x200b && cp <= 0x200f) || cp === 0x2060 ||
        (cp >= 0x202a && cp <= 0x202e) ||
        // Unicode line/paragraph separators can visually create a new
        // evidence row; invisible math/deprecated bidi format controls can
        // conceal distinctions between reported source and sample labels.
        cp === 0x2028 || cp === 0x2029 ||
        (cp >= 0x2061 && cp <= 0x2064) ||
        (cp >= 0x2066 && cp <= 0x206f) || cp === 0xfeff ||
        // A single untrusted source name must not masquerade as multiple
        // "source: count" entries or the "; omitted" disclosure.
        (kind === 'source' && (cp === 0x2c || cp === 0x3a || cp === 0x3b));
      shown += formatting
        ? `[U+${cp.toString(16).toUpperCase().padStart(4, '0')}]`
        : character;
    }
    return shown;
  }
  function sourceSummary(sources) {
    const shown = [];
    let omittedCategories = 0, omittedEvents = 0;
    // Source-map insertion order is not evidence importance: a long tail of
    // empty/rare labels must not hide the largest observed source in a bounded
    // review. Counts are already validated as a conserved safe-integer partition.
    // Literal-name ties make the same source map reproducible across import order.
    const ranked = Object.entries(sources).sort((a, b) =>
      b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    // Two distinct reported source keys can render identically after the
    // 96-codepoint bound or control-character escaping. A reviewer must not
    // mistake those separate categories for a single verified source.
    const visible = ranked.slice(0, 8).map(([name]) =>
      name.trim() ? visibleEvidenceLabel(name, 'source') : '[blank source label]');
    const occurrences = new Map(), ordinals = new Map();
    for (const label of visible)
      occurrences.set(label, (occurrences.get(label) || 0) + 1);
    for (let i = 0; i < ranked.length; i++) {
      const count = ranked[i][1];
      if (i < visible.length) {
        const label = visible[i], total = occurrences.get(label);
        const ordinal = (ordinals.get(label) || 0) + 1;
        ordinals.set(label, ordinal);
        const distinct = total > 1 ? ` [distinct reported category ${ordinal}/${total}]` : '';
        shown.push(`${label}${distinct}: ${count}`);
      } else { omittedCategories++; omittedEvents += count; }
    }
    return (shown.join(', ') || 'none') + (omittedCategories
      ? `; ${omittedCategories} additional source categories (${omittedEvents} selected events) omitted from this preview`
      : '');
  }
  function matchesRequestedWindow(requested, returned) {
    if (!returned || returned.scope!==requested.scope) return false;
    if (requested.scope!=='range') return true;
    // Equivalent UTC offsets are valid; submillisecond shifts are not.
    return sameExactInstant(requested.since,returned.since) &&
      sameExactInstant(requested.until,returned.until);
  }
  async function api(path, options, stillCurrent=()=>true) {
    await window.__owgAuthReady;
    // An old auth wait must not start a read/export for a superseded scope.
    if(!stillCurrent())throw new Error(staleScope);
    const r = await fetch(path,{cache:'no-store',...options});
    const d = await r.json().catch(()=>({}));
    if (!r.ok) throw new Error(d.detail || 'Could not prepare evidence.');
    return d;
  }
  function checkedExportUrl(response, fmt, detailed, redacted, chosen) {
    // Ticket responses are not permission grants. Do not let a response mix-up
    // silently switch period, detail level, redaction, or download origin.
    const invalid=()=>new Error('Export ticket did not match the selected evidence and privacy options. Prepare or retry the download.');
    const path='/v1/export/'+fmt;
    const value=response?.url;
    if(typeof value!=='string' || value.length>8192 || !value.startsWith(path+'?'))
      throw invalid();
    let parsed;
    try { parsed=new URL(value,window.location.origin); }
    catch(_) { throw invalid(); }
    if(parsed.origin!==window.location.origin || parsed.pathname!==path || parsed.hash ||
       parsed.username || parsed.password) throw invalid();
    const params=parsed.searchParams;
    const expected={scope:chosen.scope,include_raw:String(!!detailed),
      redact_names:String(!!redacted)};
    if(chosen.scope==='range') {
      expected.since=chosen.since;
      expected.until=chosen.until;
    } else if(chosen.scope!=='all' && chosen.scope!=='current') throw invalid();
    const keys=[...params.keys()], allowed=new Set([...Object.keys(expected),'ticket']);
    if(keys.length!==allowed.size || new Set(keys).size!==keys.length ||
       keys.some(key=>!allowed.has(key))) throw invalid();
    const ticket=params.get('ticket');
    if(typeof response.ticket!=='string' || !ticket || !ticket.trim() ||
       response.ticket!==ticket) throw invalid();
    for(const [key,wanted] of Object.entries(expected)) {
      const actual=params.get(key);
      if(key==='since' || key==='until') {
        if(!sameExactInstant(actual,wanted)) throw invalid();
      } else if(actual!==wanted) throw invalid();
    }
    return value;
  }
  async function download(fmt, detailed, redacted=true, chosen=selection(), stillCurrent=()=>true) {
    const d = await api('/v1/export-ticket',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({format:fmt,...chosen,include_raw:!!detailed,redact_names:!!redacted})},stillCurrent);
    // A delayed ticket response cannot initiate a download after period change.
    if(!stillCurrent())throw new Error(staleScope);
    const url=checkedExportUrl(d,fmt,detailed,redacted,chosen);
    const a = document.createElement('a');a.href=url;a.download='';a.hidden=true;
    document.body.appendChild(a);a.click();a.remove();
  }
  function syncControls() {
    document.querySelectorAll('.owg-period').forEach(node => {
      node.querySelector('select').value=period;
      node.querySelector('[data-custom]').hidden=period!=='custom';
      node.querySelector('[data-start]').value=start;node.querySelector('[data-end]').value=end;
    });
  }
  async function refresh() {
    const seq=++generation;
    // An open handoff is only valid for its prepared evidence generation.
    // Mark it stale immediately, including for keyboard/screen-reader users.
    if(activeHandoff && activeHandoff.seq!==seq){
      activeHandoff.copy.disabled=true;
      activeHandoff.download.disabled=true;
      activeHandoff.detailed.disabled=true;
      activeHandoff.status.textContent=staleScope+' Nothing was copied or downloaded by this change.';
      activeHandoff=null;
    }
    pending?.abort(); pending=new AbortController();
    document.querySelectorAll('[data-period-summary]').forEach(x=>{
      x.setAttribute('aria-busy','true');
      x.innerHTML='<progress aria-label="Checking selected evidence"></progress> Checking selected evidence…';
    });
    try {
      const requested=selection();
      const d=await api('/v1/analysis-preview?'+new URLSearchParams(requested),{signal:pending.signal},()=>seq===generation);
      if(seq!==generation)return null;
      if(!selectionStillMatches(requested))
        throw new Error('Work period changed while evidence was loading. Prepare again; no evidence was exported.');
      if(!matchesRequestedWindow(requested,d.window))
        throw new Error('Preview period did not match the selected work period. Prepare again; no evidence was exported.');
      validatePreview(d);
      // No selected observations is not proof that no work occurred, nor
      // evidence of the recorder's current consent or capture state.
      // Localized Date formatting drops submillisecond precision and the
      // reported timezone offset. Keep the familiar local-time overview, but
      // disclose exact source-reported ISO instants so two microsecond-apart
      // observations cannot appear to have identical verified timestamps.
      const text=d.canonical_rows
        ? `${d.canonical_rows} recorded events selected (${d.retained_rows} retained rows reported; capture completeness unverified) · observed locally ${new Date(d.first_observed_at).toLocaleString()} – ${new Date(d.last_observed_at).toLocaleString()} · reported ISO times ${d.first_observed_at} – ${d.last_observed_at} (offsets preserved)`
        : d.retained_rows
          ? 'No events recorded in this selection. Other retained evidence exists; inactivity and capture completeness are unverified.'
          : 'No retained events reported. Recording and consent status are unverified; this does not establish inactivity.';
      document.querySelectorAll('[data-period-summary]').forEach(x=>{x.textContent=text;x.removeAttribute('aria-busy');});
      return d;
    } catch(e) {
      if(seq!==generation)return null;
      document.querySelectorAll('[data-period-summary]').forEach(x=>{x.textContent=e.message;x.removeAttribute('aria-busy');});
      return null;
    }
  }
  async function prepare() {
    const seq=generation+1;
    const d=await refresh();
    // The response must belong to this click, not a newer period refresh.
    if(seq!==generation)return;
    if(!d){window.toast?.('Could not prepare selected evidence. Check the period and dashboard connection.');return;}
    // The generic server request does not describe whether this particular
    // local preview contains any ordinary work evidence. A copyable handoff
    // must not invite an AI to infer workflows from zero observations or
    // capture-health/gap records alone. Other authorized sources may differ.
    const ordinaryRows = d.work_evidence?.ordinary_event_rows ??
      (d.canonical_rows - (d.event_types.capture_gap || 0) -
        (d.event_types.capture_health || 0));
    const evidenceWarning = ordinaryRows === 0
      ? d.canonical_rows === 0
        ? "\n\nEvidence limitation: No recorded events in this local preview's selected period. This is not proof of inactivity or complete capture. Do not infer a workflow, business completion, time savings, or ROI from absent ordinary work evidence. If other currently authorized evidence is available, evaluate it separately and disclose its source and limits; otherwise report insufficient evidence."
        : "\n\nEvidence limitation: This local preview contains only capture-health or gap records, not ordinary work events for the selected period. Health and gap records are not workflow steps. Do not infer a workflow, business completion, time savings, or ROI from absent ordinary work evidence. If other currently authorized evidence is available, evaluate it separately and disclose its source and limits; otherwise report insufficient evidence."
      : '';
    // Evidence availability is not workflow confirmation. Capture-health and
    // gap records can be downloaded for diagnostics, but cannot substantiate
    // an automation opportunity or completed business process.
    const diagnosticOnly=d.canonical_rows>0 && ordinaryRows===0;
    const evidenceAssessment=ordinaryRows===0
      ? d.canonical_rows===0
        ? 'Evidence assessment: insufficient for workflow review — no recorded events in this selection. Capture coverage and consent status remain unverified.'
        : 'Evidence assessment: insufficient for workflow review — only capture-health or gap records are present. These are diagnostics, not observed workflow steps.'
      : 'Evidence assessment: ordinary work observations are present, but no workflow, business completion, or automation opportunity is verified. Human review is required.';
    const request=d.request_text+evidenceWarning;
    const chosen={scope:d.window.scope,...(d.window.scope==='range'?{since:d.window.since,until:d.window.until}:{})};
    if(!selectionStillMatches(chosen)){
      window.toast?.('Work period changed while preparing the handoff. Prepare again; nothing was copied or downloaded.');
      return;
    }
    const access=d.local_ai_history_access;
    // This is a snapshot of local AI history consent, not an export grant.
    // Do not turn malformed lease metadata into an apparent permission.
    const leaseObject=access!==null && typeof access==='object' && !Array.isArray(access);
    const leaseExpiry=canonicalUtcInstant(access?.expires_at);
    const grantedAt=canonicalUtcInstant(access?.granted_at);
    const nowInstant=new Date().toISOString();
    // The local clock is millisecond-resolution, but a lease expiring one
    // microsecond after that instant is still future-dated. Do not round it
    // into an apparently expired grant or overstate its actual expiry time.
    const expiryValid=access?.expires_at==null ||
      (leaseExpiry!==null && compareExactInstants(access.expires_at, nowInstant) > 0);
    // Production policy records granted_at on every new saved-history grant.
    // An impossible future/malformed grant timestamp is not evidence of
    // permission. Legacy previews may omit it, but remain snapshot-only.
    // Compare full fractional seconds so a microsecond reversal is not hidden.
    const grantedValid=access?.granted_at==null ||
      (grantedAt!==null &&
        compareExactInstants(access.granted_at, nowInstant) <= 0 &&
        (access.expires_at==null ||
          (leaseExpiry!==null &&
            compareExactInstants(access.granted_at, access.expires_at) < 0)));
    let localRead='Local AI saved-history permission could not be verified from this preview; check current settings before using an AI.';
    if(d.local_ai_enabled===false) localRead='Local AI access is off.';
    else if(d.local_ai_enabled===true && leaseObject && expiryValid && grantedValid){
      const until=leaseExpiry?' Grant expiry: '+leaseExpiry+'.':'';
      if(access.mode==='off')
        localRead='Local AI can read the current run only; saved-history access is off.';
      else if(access.mode==='all_saved')
        localRead='Local saved-history grant: all saved work (snapshot only; current consent still applies).'+until;
      else if(access.mode==='selected_range'){
        const since=canonicalUtcInstant(access.since), end=canonicalUtcInstant(access.until);
        if(since && end && compareExactInstants(access.since, access.until) < 0)
          localRead='Local saved-history grant: '+since+' to '+end+
            ' (end exclusive; snapshot only, may not cover this selected work period).'+until;
      }
    }
    const workTextStatement = d.work_text?.included_in_activity_package === false
      ? 'Visible work text is not included in this activity package. Typed values and clipboard contents are not included.'
      : 'This preview does not confirm that visible work text, typed values or clipboard contents are excluded. Inspect the package before sharing it.';
    // An omitted latest-work timestamp is not proof that no ordinary work was
    // observed. The server may omit it for excluded apps or legacy previews.
    // Health/gap rows are not ordinary work; neither absence proves inactivity.
    const latestWorkStatement = d.work_evidence?.last_work_observed_at
      ? `Latest work event: ${d.work_evidence.last_work_observed_at}.`
      : ordinaryRows > 0
        ? `Latest work event timestamp not supplied (${ordinaryRows} ordinary selected events); this does not imply no work occurred.`
        : 'No ordinary work events recorded in this selection; this does not establish that no work occurred.';
    // A current recorder run is not the same as all retained history. Missing
    // daily-plan metadata means unknown coverage, not zero recorded events.
    const selectedPeriod = d.window.scope === 'range'
      ? `Selected range: ${d.window.since} to ${d.window.until} (end exclusive, UTC).`
      : d.window.scope === 'current'
        ? `Current recorder run${d.window.since ? ` (from ${d.window.since})` : ' (start not supplied by preview)'}.`
        : 'All retained history (unbounded date selection).';
    // A scope identifies what to read, not an immutable evidence snapshot.
    // Even fixed date bounds can change after consent revocation, deletion,
    // retention or newly synchronized events. Never promise preview/file parity.
    const scopeFreshnessStatement = d.window.scope === 'current'
      ? 'This recorder-run selection can change as events arrive or the recorder restarts. The downloaded file may differ from this preview; review it before sharing.'
      : d.window.scope === 'all'
        ? 'All retained history can change as events arrive, are deleted, or become inaccessible. The downloaded file may differ from this preview; review it before sharing.'
        : 'The selected date bounds are fixed, but retained events and access can change. The downloaded file may differ from this preview; review it before sharing.';
    // Daily plans are useful for workflow review only when ordinary work was
    // observed. Health-only dates are diagnostics; zero rows cannot support
    // instructions to an AI to discover repeated work or automation.
    const dateCoverageTitle = ordinaryRows > 0
      ? 'Analyze a large history in smaller parts'
      : diagnosticOnly ? 'Review capture-health diagnostic dates' : 'Review missing date coverage';
    const dateCoverageIntro = ordinaryRows > 0
      ? 'Ask your AI to process one day at a time, follow every page, and keep track of what it has examined. Repeated-work maps are optional.'
      : diagnosticOnly
        ? 'The listed dates contain capture-health/gap diagnostics only, not workflow steps. They cannot substantiate repeated-work maps or automation opportunities.'
        : 'No recorded events are available to analyze. Missing dates do not establish inactivity, complete capture or current consent.';
    const dateCoverageDownload = d.canonical_rows
      ? diagnosticOnly
        ? 'The optional diagnostic download contains the full selected period; inspect it locally before sharing.'
        : 'The file download still contains the full selected period.'
      : 'No evidence package is available for this zero-event selection; only the explanatory request can be copied.';
    const dailyChunks = d.analysis_plan?.date_chunks;
    const dailyBreakdown = !Array.isArray(dailyChunks)
      ? '<p>No daily breakdown was provided; date coverage is unknown.</p>'
      : dailyChunks.length
        ? `<ul>${dailyChunks.map(x=>`<li>${esc(x.since)} to ${esc(x.until)}: ${esc(x.event_rows)} events</li>`).join('')}</ul>`
        : '<p>No daily chunks were listed; this does not prove complete recording or zero events.</p>';
    // Focus-span totals and optional redacted examples are different facts.
    // An empty example list is not evidence that no focus events were observed.
    const focusSpanCount = d.event_types.focus_span || 0;
    const focusExampleCount = d.redacted_examples.length;
    const focusExampleSummary = focusExampleCount
      ? `${focusExampleCount} redacted context example(s) supplied; ${focusSpanCount} selected focus-span events counted. Examples are a bounded sample, not proof of complete context capture.`
      : focusSpanCount
        ? `No redacted context examples were supplied, although ${focusSpanCount} selected focus-span events are counted. Missing examples do not mean no focus was observed.`
        : 'No focus-span events recorded in this selection; this does not prove inactivity or complete capture.';
    // A sample with no observation timestamp must not look as though its
    // provenance time was verified. Keep the supplied offset-aware instant
    // verbatim rather than silently converting it to the viewer's timezone.
    // A missing label is not the same as a literal reported "Unknown app" or
    // "Untitled activity". Empty/whitespace-only titles are supplied but
    // uninformative; do not silently substitute an invented activity title.
    const focusExampleItems = focusExampleCount
      ? d.redacted_examples.map(x => {
          const appLabel = x.app == null
            ? 'app not supplied'
            : 'reported app: ' + visibleEvidenceLabel(x.app, 'sample');
          const titleLabel = x.window_title == null
            ? 'title not supplied'
            : x.window_title === ''
              ? 'reported title: [empty title label]'
              : !x.window_title.trim()
                ? 'reported title: [whitespace-only title label]'
                : 'reported title: ' + visibleEvidenceLabel(x.window_title, 'sample');
          const observed = x.observed_at == null
            ? 'observation time not supplied'
            : `observed at <time datetime="${esc(x.observed_at)}">${esc(x.observed_at)}</time>`;
          return `<li>${esc(appLabel)} · ${esc(titleLabel)} · ${observed}</li>`;
        }).join('')
      : '<li>No redacted context examples supplied.</li>';
    window.openModal?.('Prepare for AI',ordinaryRows>0?'Review selected work evidence (workflow unverified)':'Review evidence limitations (no verified workflow)',`
      <p>${ordinaryRows>0?'No workflow description or repeated pattern is required. An AI may help review observations after you explicitly share evidence; this panel does not run a model.':'No ordinary work evidence is available for workflow review. Do not infer a workflow from missing observations or capture-health diagnostics; this panel does not run a model.'}</p>
      <p><strong>${esc(evidenceAssessment)}</strong></p>
      <div class="note"><strong>Selected local evidence preview — not sent to an AI</strong>
        <p>${esc(d.canonical_rows)} recorded events · ${esc(d.event_types.focus_span||0)} focus spans · ${esc(d.event_types.capture_gap||0)} unobserved-gap records.</p>
        <p>${esc(selectedPeriod)} Observed: ${esc(d.first_observed_at||'none')} to ${esc(d.last_observed_at||'none')}.</p>
         <p>Coverage accounting: ${esc(d.canonical_rows)} selected recorded events out of ${esc(d.retained_rows)} retained rows reported. Retained rows do not prove complete capture; unselected retained rows may be outside this period or otherwise excluded, not proof of inactivity or missed work.</p>
        <p>${esc(scopeFreshnessStatement)} This preview is not an immutable export snapshot or proof of complete capture.</p>
        <p>Reported sources (bounded preview; labels are not verified identities): ${esc(sourceSummary(d.sources))}. Highest selected-event counts appear first (literal-label tie break); omitted totals are disclosed. ${esc(workTextStatement)}</p>
        <p>${esc(latestWorkStatement)} Capture health and gaps are shown separately; they do not confirm the recorder is running.</p>
        <p>${esc(localRead)} Hosted AI availability must be checked separately: it only sees previously shared evidence. Preparing or downloading a local file does not grant AI history access.</p>
      </div>
      <details><summary>${esc(dateCoverageTitle)}</summary><p>${esc(dateCoverageIntro)} ${esc(dateCoverageDownload)}</p>${dailyBreakdown}${d.analysis_plan?.more_dates_not_listed?'<p>Only the first 31 dates are listed. Continue through the remaining selected dates.</p>':''}</details>
      <details><summary>Preview redacted context examples</summary><p>${esc(focusExampleSummary)}</p><ul>${focusExampleItems}</ul><p>Filtering is best effort; inspect any downloaded package before sharing it.</p></details>
      <label style="display:flex;gap:8px;margin:12px 0"><input id="analysisDetailed" type="checkbox" ${d.canonical_rows?'':'disabled'}>Include detailed chronological evidence (raw local event rows, more labels and context)</label>
      <p>Copy request copies only the prepared request text. Download saves a separate local evidence file; an AI will not receive that file unless you inspect and attach or share it yourself.</p>
      <p>Name redaction is best effort, not anonymization. Even summary exports may contain sensitive titles, URLs or metadata; detailed chronology adds raw local evidence that may contain more sensitive content. Inspect the file before sharing it. Gaps and foreground time are not measured lost work or savings.</p>
      ${d.canonical_rows?'':'<p class="muted">No recorded events in the selected period. An evidence package is unavailable; you can still copy the explanatory request. This does not establish inactivity or complete capture.</p>'}
      <div class="modal-actions"><button type="button" id="analysisDownload" ${d.canonical_rows?'':'disabled'}>${d.canonical_rows?(diagnosticOnly?'Download capture-health diagnostic package':'Download AI evidence package'):'Evidence download unavailable'}</button><button type="button" class="secondary" id="analysisCopy">Copy request for my AI</button></div>
      <details style="margin-top:12px"><summary>Read the request</summary><pre style="white-space:pre-wrap" id="analysisRequest"></pre></details>
      <div class="muted" id="analysisActionStatus" role="status">Nothing has been sent to an AI. ${d.canonical_rows?(diagnosticOnly?'Only capture-health/gap diagnostics are available; no workflow is verified.':'Downloads work with any capable AI, without an OWG cloud account.'):'There is no recorded-event package to download for this selection.'}</div>`);
    const requestNode=$('#analysisRequest'), statusNode=$('#analysisActionStatus');
    const copyNode=$('#analysisCopy'), downloadNode=$('#analysisDownload'), detailedNode=$('#analysisDetailed');
    requestNode.textContent=request;
    copyNode.disabled=false;
    downloadNode.disabled=!d.canonical_rows;
    detailedNode.checked=false;
    // With no selected rows there is no downloadable chronology to configure.
    detailedNode.disabled=!d.canonical_rows;
    activeHandoff={seq,copy:copyNode,download:downloadNode,detailed:detailedNode,status:statusNode};
    const current=()=>seq===generation && selectionStillMatches(chosen);
    const expired=()=>{
      if(current())return false;
      copyNode.disabled=true;
      downloadNode.disabled=true;
      detailedNode.disabled=true;
      statusNode.textContent=staleScope+' Nothing was copied or downloaded by this action.';
      return true;
    };
    // Clipboard permission prompts can remain pending. Repeated Copy clicks
    // must not queue duplicate writes or race to announce an older result.
    // Copy and Download share one status region: serialize both operations so
    // one completion cannot overwrite the other action's progress or result.
    let actionInFlight=false;
    copyNode.onclick=async()=>{
      if(expired()||actionInFlight)return;
      actionInFlight=true;
      copyNode.disabled=true;
      downloadNode.disabled=true;
      statusNode.textContent='Copying the prepared request locally… No AI was invoked.';
      try{
        await navigator.clipboard.writeText(request);
        statusNode.textContent=current()
          ? 'Only the prepared request text was copied. No evidence file was attached or sent; review and share any downloaded file separately. No model was invoked.'
          : 'The work period changed during copying. The copied request refers to the previous period; prepare again.';
      }catch(_){statusNode.textContent=current()
        ? 'Clipboard unavailable. Open “Read the request” and copy the text.'
        : staleScope+' Clipboard write failed; prepare again.';}
      finally{
        actionInFlight=false;
        // A superseded handoff must not regain either action button.
        if(current()){
          copyNode.disabled=false;
          downloadNode.disabled=!d.canonical_rows;
        }
      }
    };
    // A user may double-click while the local ticket is pending. Only one
    // ticket/download may be started for this prepared handoff at a time.
    downloadNode.onclick=async()=>{
      if(expired()||actionInFlight||!d.canonical_rows)return;
      // Freeze the exact option shown to the user before the async ticket read.
      // A later checkbox change cannot rewrite an already-issued request.
      const includeDetailed=!!detailedNode.checked;
      actionInFlight=true;
      copyNode.disabled=true;
      downloadNode.disabled=true;
      detailedNode.disabled=true;
      statusNode.textContent=includeDetailed
        ? 'Preparing the redacted evidence package with detailed chronological evidence locally…'
        : 'Preparing the redacted evidence package without detailed chronological evidence locally…';
      try{
        await download('csvzip',includeDetailed,true,chosen,current);
        statusNode.textContent=current()
          ? `Download started (${includeDetailed?'detailed chronology included':'summary evidence only'}). Inspect the file, then attach it to your AI. No data was uploaded.`
          : staleScope;
      }catch(e){statusNode.textContent=current()?e.message:staleScope;}
      finally{
        actionInFlight=false;
        // A superseded modal must never regain either action control.
        if(current()){
          copyNode.disabled=false;
          downloadNode.disabled=!d.canonical_rows;
          detailedNode.disabled=false;
        }
      }
    };
  }
  function install() {
    const style=document.createElement('style');style.textContent='.owg-period{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:14px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:#fff}.owg-period select,.owg-period input{font:inherit;padding:7px;border:1px solid var(--line);border-radius:7px}.owg-period [data-period-summary]{flex-basis:100%;font-size:12px;color:var(--muted)}.owg-period [data-custom]{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.owg-period [hidden]{display:none!important}';document.head.appendChild(style);
    const today=new Date();start=dateInput(new Date(today.getFullYear(),today.getMonth(),today.getDate()-6));end=dateInput(today);
    for(const id of ['overview','evidence','connect','export']) {
      const panel=$('#panel-'+id);if(!panel)continue;
      const bar=document.createElement('div');bar.className='owg-period';
      bar.innerHTML=`<strong>Work period</strong><select aria-label="Work period"><option value="today">Today</option><option value="week">Last 7 days</option><option value="custom">Custom dates</option><option value="current">This run</option><option value="all">All retained work</option></select><span data-custom hidden><label>From <input type="date" data-start></label><label>Through <input type="date" data-end></label></span><span class="muted">${esc(Intl.DateTimeFormat().resolvedOptions().timeZone||'local time')}</span><button type="button" class="secondary">Prepare for AI</button><span data-period-summary role="status"></span>`;
      // Scope applies to handoff/exports; existing current-run dashboards retain their labels.
      const note=document.createElement('small');note.className='muted';note.textContent='Select the period to view in Activity or prepare for AI. Activity uses this period; current-run charts keep their labeled scope.';bar.appendChild(note);
      panel.insertBefore(bar,panel.firstChild);
      bar.querySelector('select').onchange=e=>{period=e.target.value;syncControls();refresh();document.dispatchEvent(new Event('owg:period-change'));};
      bar.querySelectorAll('input').forEach(x=>x.onchange=()=>{start=bar.querySelector('[data-start]').value;end=bar.querySelector('[data-end]').value;syncControls();refresh();document.dispatchEvent(new Event('owg:period-change'));});
      bar.querySelector('button').onclick=prepare;
    }
    syncControls();refresh();document.dispatchEvent(new Event('owg:period-change'));
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
