/* R9: explicitly requested, session-authenticated analysis of co-observed object attributes.
   This is intentionally separate from the metric builder until the R9 builder PR merges. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  };
  const field = (title, control) => {
    const wrap = node('div');
    const label = node('label', title);
    label.htmlFor = control.id;
    wrap.append(label, control);
    return wrap;
  };
  const button = (label, id) => {
    const el = node('button', label, 'secondary');
    el.type = 'button';
    el.id = id;
    return el;
  };
  const number = n => Number(n || 0).toLocaleString();
  const duration = seconds => seconds === null || seconds === undefined
    ? 'unknown' : Number(seconds).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' s';
  const csvCell = value => {
    let text = String(value === null || value === undefined ? '' : value);
    // Excel/Sheets treat even quoted leading =,+,-,@ as formulas.
    if (/^\s*[=+\-@\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  };
  function download(filename, mime, body) {
    const url = URL.createObjectURL(new Blob([body], {type: mime}));
    const a = node('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function renderTable(headers, rows) {
    const wrap = node('div', null, 'table-wrap');
    const table = node('table');
    const head = node('thead'), tr = node('tr');
    for (const title of headers) tr.appendChild(node('th', title));
    head.appendChild(tr);
    const body = node('tbody');
    for (const row of rows) {
      const line = node('tr');
      for (const value of row) line.appendChild(node('td', value));
      body.appendChild(line);
    }
    table.append(head, body);
    wrap.appendChild(table);
    return wrap;
  }
  function build() {
    const pick = $('owgEventLogPick'), casePick = $('owgEventLogCaseType');
    const logId = pick?.value || '', caseType = casePick?.value || '';
    const section = node('section');
    section.id = 'owgLinkedAttributes';
    section.style.marginTop = '12px';
    section.appendChild(node('h3', 'Analyze linked business attributes'));
    section.appendChild(node('p',
      'Compare observed case counts and elapsed durations by a supplied attribute of one uniquely co-observed related object. Multiple links, missing attributes and conflicting definitions stay unknown; these are not verified business joins or outcomes.',
      'muted'));
    const related = node('select');
    related.id = 'owgLinkedType';
    for (const option of [...(casePick?.options || [])]) {
      if (!option.value || option.value === caseType) continue;
      const choice = node('option', option.value);
      choice.value = option.value;
      related.appendChild(choice);
    }
    const attribute = node('input');
    attribute.id = 'owgLinkedAttribute';
    attribute.type = 'text';
    attribute.maxLength = 120;
    attribute.placeholder = 'e.g. region';
    const limit = node('input');
    limit.id = 'owgLinkedLimit';
    limit.type = 'number';
    limit.min = '1';
    limit.max = '100';
    limit.step = '1';
    limit.value = '50';
    const run = button('Analyze linked attributes', 'owgLinkedRun');
    const json = button('Download linked analysis JSON', 'owgLinkedJSON');
    const csv = button('Download linked analysis CSV', 'owgLinkedCSV');
    json.disabled = csv.disabled = true;
    const controls = node('div', null, 'row');
    controls.style.alignItems = 'end';
    controls.append(field('Related object type', related), field('Attribute on related object', attribute),
      field('Maximum groups', limit), run, json, csv);
    const status = node('p', related.options.length
      ? 'Choose an attribute and request a read-only local analysis.'
      : 'This log needs a second object type to analyze linked attributes.', 'muted');
    status.id = 'owgLinkedStatus';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const output = node('div');
    output.id = 'owgLinkedResults';
    section.append(controls, status, output);
    let last = null, request = 0;
    const current = () => section.isConnected && pick.value === logId && casePick.value === caseType &&
      $('owgLinkedAttributes') === section;
    const clear = () => {
      request++;
      last = null;
      json.disabled = csv.disabled = true;
      output.replaceChildren();
      // An edit while a request is in flight supersedes it; allow a new run
      // rather than leaving the button disabled by the old request's finally.
      run.disabled = !related.options.length || !logId || !caseType;
      if (current()) status.textContent = 'Parameters changed. Run the analysis again.';
    };
    related.addEventListener('change', clear);
    attribute.addEventListener('input', clear);
    limit.addEventListener('input', clear);
    run.disabled = !related.options.length || !logId || !caseType;
    run.addEventListener('click', async () => {
      const name = attribute.value.trim(), cap = Number(limit.value);
      if (!current()) return;
      if (!name || name.length > 120 || !Number.isInteger(cap) || cap < 1 || cap > 100 ||
          !related.value || related.value === caseType) {
        clear();
        status.textContent = 'Choose a different related object type, an attribute name and 1–100 groups.';
        return;
      }
      const ticket = ++request;
      last = null;
      json.disabled = csv.disabled = true;
      output.replaceChildren();
      run.disabled = true;
      status.textContent = 'Analyzing the selected local event log…';
      try {
        await window.__owgAuthReady;
        if (!current() || ticket !== request) return;
        const response = await fetch('/v1/event-logs/' + encodeURIComponent(logId) + '/linked-attribute-analysis', {
          method: 'POST', cache: 'no-store',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({case_type: caseType, related_object_type: related.value, attribute: name, limit: cap})
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail :
          'Linked-attribute analysis failed (' + response.status + ').');
        if (!current() || ticket !== request) return;
        last = payload;
        const counts = payload.status_counts || {};
        output.appendChild(node('p',
          number(payload.cases) + ' observed cases: ' + number(counts.resolved) + ' resolved, ' +
          number(counts.unlinked) + ' unlinked, ' + number(counts.ambiguous) + ' ambiguous, ' +
          number(counts.missing_attribute) + ' missing attribute, ' +
          number(counts.oversize_attribute) + ' oversized attribute.'));
        output.appendChild(renderTable(
          ['Supplied ' + name, 'Observed cases', 'Median observed elapsed time'],
          (payload.rows || []).map(row => [row.value, number(row.cases), duration(row.median_duration_seconds)])));
        output.appendChild(node('p',
          number(payload.groups_returned) + ' of ' + number(payload.groups_total) + ' groups shown; ' +
          number(payload.groups_omitted) + ' groups and ' +
          number(payload.resolved_cases_omitted_by_group_limit) + ' resolved cases omitted by display limit. ' +
          'Examined ' + number(payload.work?.selected_case_event_rows) + ' case-event rows and ' +
          number(payload.work?.selected_event_object_links) + ' object links.',
          'muted'));
        output.appendChild(node('p',
          'Only exactly one same-log, same-event related object can resolve. Attributes are supplied, not source-verified; duration is observed elapsed time, not verified work time. Results are not permission for any action.',
          'muted'));
        json.disabled = csv.disabled = false;
        status.textContent = 'Read-only linked-attribute analysis complete.';
      } catch (error) {
        if (current() && ticket === request) status.textContent = String(error.message || 'Analysis failed.');
      } finally {
        if (current() && ticket === request) run.disabled = false;
      }
    });
    json.addEventListener('click', () => {
      if (!current() || !last) return;
      download('linked-attribute-analysis.json', 'application/json', JSON.stringify(last, null, 2) + '\n');
    });
    csv.addEventListener('click', () => {
      if (!current() || !last) return;
      const lines = [
        ['attribute', 'value', 'cases', 'median_duration_seconds'].map(csvCell).join(','),
        ...(last.rows || []).map(row => [last.attribute, row.value, row.cases,
          row.median_duration_seconds].map(csvCell).join(','))
      ];
      download('linked-attribute-analysis.csv', 'text/csv;charset=utf-8', lines.join('\r\n') + '\r\n');
    });
    return section;
  }
  function install() {
    const results = $('owgEventLogResults');
    if (!results) return;
    const observer = new MutationObserver(() => {
      const anchor = results.querySelector('#owgEventLogAnalysis');
      if (anchor && !results.querySelector('#owgLinkedAttributes')) {
        anchor.insertAdjacentElement('afterend', build());
      }
    });
    observer.observe(results, {childList: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})();
