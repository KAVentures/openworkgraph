/* Standalone, local-only OCEL relationship timing inspector (R24).
 * This page accesses saved data only using the current OWG dashboard session.
 * It creates DOM nodes via textContent, never renders source strings as HTML.
 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const sessionKey = 'owg_dashboard_session_v1';
  let activeLog = '', groups = [], generation = 0, queryGeneration = 0;
  const activityGeneration = {source: 0, target: 0};

  const integer = value => Number.isInteger(value) && Number.isFinite(value);
  const logIdIsValid = value => /^log_[a-f0-9]{16}$/.test(String(value || ''));
  const fmt = value => Number(value || 0).toLocaleString();
  const elapsed = value => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'Not observed';
    const seconds = Number(value);
    if (seconds >= 86400) return (seconds / 86400).toFixed(2) + ' days';
    if (seconds >= 3600) return (seconds / 3600).toFixed(2) + ' h';
    if (seconds >= 60) return (seconds / 60).toFixed(1) + ' min';
    return seconds.toFixed(1) + ' s';
  };

  function session() {
    try { return sessionStorage.getItem(sessionKey) || ''; }
    catch (_) { return ''; }
  }

  async function api(path, init = {}) {
    const token = session();
    if (!token) throw new Error('No active dashboard session. Reopen OWG from its launcher, then use this page in that same tab.');
    const headers = new Headers(init.headers || {});
    headers.set('Authorization', 'OWG-Session ' + token);
    const response = await fetch(path, {...init, headers, cache: 'no-store',
      credentials: 'same-origin'});
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) {
      if ([401, 403].includes(response.status))
        throw new Error('Your local dashboard session is missing or expired. Reopen OWG from its launcher.');
      throw new Error(String(body.detail || body.error || 'Local analysis was unavailable.').slice(0, 400));
    }
    return body;
  }

  function options(select, items, emptyLabel, current = '') {
    select.replaceChildren();
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = emptyLabel;
    select.appendChild(empty);
    items.forEach(({value, label}) => {
      const option = document.createElement('option');
      option.value = String(value);
      option.textContent = String(label);
      select.appendChild(option);
    });
    if (items.some(item => item.value === current)) select.value = current;
    else if (items.length) select.value = String(items[0].value);
    select.disabled = !items.length;
  }

  function dataOptions(name, values) {
    const target = $(name);
    target.replaceChildren();
    [...new Set(values)].sort((a,b) => a.localeCompare(b)).slice(0, 500).forEach(value => {
      const option = document.createElement('option');
      option.value = value;
      target.appendChild(option);
    });
  }

  function status(message) { $('status').textContent = String(message); }
  function clearResult(message = '') {
    queryGeneration++;
    $('resultSection').hidden = true;
    $('metrics').replaceChildren();
    $('sampleRows').replaceChildren();
    $('provenance').textContent = '';
    if (message) status(message);
  }

  function revealQualifier() {
    $('qualifierValueLabel').hidden = $('qualifierMode').value !== 'literal';
  }

  async function refreshLogs() {
    const gen = ++generation;
    activeLog = '';
    groups = [];
    clearResult('Reading authorized local event logs…');
    options($('logSelect'), [], 'Loading saved OCEL logs…');
    options($('relationshipSelect'), [], 'Choose an OCEL log first');
    $('coverage').textContent = '';
    try {
      const data = await api('/v1/event-logs');
      if (gen !== generation) return;
      const logs = (Array.isArray(data.logs) ? data.logs : []).filter(log =>
        log && logIdIsValid(log.log_id) && ['ocel', 'ocelxml'].includes(log.source_format));
      options($('logSelect'), logs.map(log => ({
        value: log.log_id,
        label: String(log.name || log.log_id) + ' · ' + fmt(log.objects) + ' objects · ' + fmt(log.events) + ' events'
      })), 'No locally retained OCEL logs');
      if (!logs.length) {
        status('No locally imported OCEL logs. Use OWG’s Event logs workspace to import one with permission.');
        return;
      }
      await selectLog(gen);
    } catch (error) {
      if (gen !== generation) return;
      options($('logSelect'), [], 'Local logs unavailable');
      status(error.message || 'Local logs unavailable');
    }
  }

  async function selectLog(gen = ++generation) {
    const id = $('logSelect').value;
    clearResult('Reading supplied object relationships…');
    activeLog = logIdIsValid(id) ? id : '';
    if (!activeLog) {
      options($('relationshipSelect'), [], 'Choose an OCEL log first');
      return;
    }
    const captured = activeLog;
    try {
      const [relationships, types] = await Promise.all([
        api('/v1/event-logs/' + encodeURIComponent(captured) + '/object-relationships'),
        api('/v1/event-logs/' + encodeURIComponent(captured) + '/object-types'),
      ]);
      if (gen !== generation || captured !== activeLog) return;
      groups = (Array.isArray(relationships.groups) ? relationships.groups : [])
        .filter(row => row && typeof row.source_type === 'string' && typeof row.target_type === 'string');
      const typeNames = (types.object_types || []).map(row => String(row.object_type || '')).filter(Boolean);
      dataOptions('knownTypes', typeNames);
      const labels = groups.map((row, index) => ({
        value: String(index + 1),
        label: row.source_type + ' → ' + row.target_type +
          ' · qualifier: ' + (row.qualifier || '(empty)') + ' · ' +
          fmt(row.relationships) + ' supplied links',
      }));
      options($('relationshipSelect'), labels, 'No supplied object relationships');
      $('coverage').textContent = fmt(relationships.retained_relationships) +
        ' supplied relationships. ' +
        (relationships.groups_truncated ? 'This list shows only the largest 200 groups; type an exact pair manually.' :
          'Relationships are caller-supplied and not independently verified.');
      if (groups.length) setFromGroup();
      else status('No directed object relationships were supplied in this log.');
    } catch (error) {
      if (gen !== generation) return;
      groups = [];
      options($('relationshipSelect'), [], 'Relationships unavailable');
      status(error.message || 'Could not read local relationships.');
    }
  }

  function setFromGroup() {
    const index = Number($('relationshipSelect').value) - 1;
    const row = groups[index];
    if (!row) return;
    $('sourceType').value = row.source_type;
    $('targetType').value = row.target_type;
    if (!row.qualifier) {
      $('qualifierMode').value = 'empty';
      $('qualifierValue').value = '';
    } else {
      $('qualifierMode').value = 'literal';
      $('qualifierValue').value = row.qualifier;
    }
    revealQualifier();
    dataOptions('knownQualifiers', groups.map(g => String(g.qualifier || '')).filter(Boolean));
    clearResult('Selected the supplied object-pair group. Choose activities to compare.');
    loadActivities('source');
    loadActivities('target');
  }

  async function loadActivities(which) {
    const gen = ++activityGeneration[which];
    const type = $(which + 'Type').value.trim();
    const captured = activeLog;
    if (!captured || !type) return;
    try {
      const body = await api('/v1/event-logs/' + encodeURIComponent(captured) +
        '/activities?case_type=' + encodeURIComponent(type));
      if (gen !== activityGeneration[which] || captured !== activeLog || $(which + 'Type').value.trim() !== type)
        return;
      const names = (body.activities || []).map(row => String(row.activity || '')).filter(Boolean);
      dataOptions(which + 'ActivityList', names);
      const field = $(which + 'Activity');
      if (!field.value || !names.includes(field.value)) field.value = names[0] || '';
    } catch (_) {
      if (gen === activityGeneration[which]) dataOptions(which + 'ActivityList', []);
    }
  }

  function metric(title, value) {
    const node = document.createElement('div');
    node.className = 'metric';
    const val = document.createElement('strong');
    val.textContent = String(value);
    const label = document.createElement('span');
    label.textContent = title;
    node.append(val, label);
    return node;
  }

  function render(result) {
    const duration = result.forward_lead_time_seconds || {};
    const stats = [
      ['Distinct related pairs', fmt(result.pairs)],
      ['Forward observed pairs', fmt(result.forward_pairs)],
      ['Reversed ordering', fmt(result.reversed_pairs)],
      ['Missing source activity', fmt(result.source_activity_missing_pairs)],
      ['Missing target activity', fmt(result.target_activity_missing_pairs)],
      ['Both activities absent', fmt(result.both_activities_missing_pairs)],
      ['Repeated selected activity', fmt(result.pairs_with_repeated_selected_activity)],
      ['Same timestamp', fmt(result.same_timestamp_pairs)],
      ['Median forward elapsed time', elapsed(duration.p50)],
      ['90th percentile forward time', elapsed(duration.p90)],
    ];
    $('metrics').replaceChildren(...stats.map(([k, v]) => metric(k, v)));
    $('basis').textContent = 'Exact selected directed object pairs · relationship revision ' +
      String(result.relationship_revision) + ' · imported observations only';
    $('limitations').textContent = 'Only the first matching event on each object is compared. Reversed or absent events ' +
      'are not included in forward duration percentiles. Supplied links have no proven validity period; these ' +
      'elapsed times do not establish a causal handoff or authorize business actions.';
    const rows = Array.isArray(result.samples) ? result.samples : [];
    const fields = ['source_object_id', 'target_object_id', 'status', 'source_first_at',
      'target_first_at', 'lead_seconds'];
    $('sampleRows').replaceChildren(...rows.map(row => {
      const tr = document.createElement('tr');
      fields.forEach(field => {
        const td = document.createElement('td');
        td.textContent = field === 'lead_seconds' ? elapsed(row[field]) : String(row[field] ?? '—');
        tr.appendChild(td);
      });
      return tr;
    }));
    $('sampleStatus').textContent = rows.length ?
      'Displaying ' + fmt(rows.length) + ' local object-pair examples' +
        (result.sample_truncated ? ' (more pairs exist; aggregates are complete).' : '.') :
      'No pair examples requested or available.';
    $('provenance').textContent = 'Relationship SHA-256: ' +
      String(result.relationship_digest || 'unknown') + '\nObject index SHA-256: ' +
      String(result.object_index_digest || 'unknown') +
      '\nComplete for selected pairs: ' + String(result.computation_complete_for_selected_pairs === true) +
      '\nSource completeness verified: false\nIndependent causality verification: false\nExecution authorized: false';
    $('resultSection').hidden = false;
  }

  async function submit(event) {
    event.preventDefault();
    const id = activeLog;
    if (!logIdIsValid(id)) { status('Select an authorized retained OCEL log first.'); return; }
    const limit = Number($('sampleLimit').value);
    if (!integer(limit) || limit < 0 || limit > 50) {
      status('Example limit must be a whole number from 0 to 50.');
      return;
    }
    const spec = {
      source_type: $('sourceType').value.trim(),
      target_type: $('targetType').value.trim(),
      source_activity: $('sourceActivity').value.trim(),
      target_activity: $('targetActivity').value.trim(),
      sample_limit: limit,
    };
    if (['empty', 'literal'].includes($('qualifierMode').value)) {
      spec.qualifier = $('qualifierMode').value === 'empty' ? '' : $('qualifierValue').value;
    }
    const turn = ++queryGeneration;
    $('runQuery').disabled = true;
    status('Analyzing exact relationship pairs locally…');
    $('resultSection').hidden = true;
    try {
      const result = await api('/v1/event-logs/' + encodeURIComponent(id) +
        '/object-relationships/lead-times',
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(spec)});
      if (turn !== queryGeneration || id !== activeLog) return;
      render(result);
      status('Local relationship timing analysis complete. Counts describe supplied data, not verified business handoffs.');
    } catch (error) {
      if (turn === queryGeneration) status(error.message || 'Could not analyze this relationship.');
    } finally {
      if (turn === queryGeneration) $('runQuery').disabled = false;
    }
  }

  function init() {
    $('refreshLogs').addEventListener('click', refreshLogs);
    $('logSelect').addEventListener('change', () => selectLog());
    $('relationshipSelect').addEventListener('change', setFromGroup);
    $('qualifierMode').addEventListener('change', () => {revealQualifier();clearResult('Selection changed. Rerun analysis.');});
    $('sourceType').addEventListener('change', () => {clearResult();loadActivities('source');});
    $('targetType').addEventListener('change', () => {clearResult();loadActivities('target');});
    $('queryForm').addEventListener('submit', submit);
    $('resetReport').addEventListener('click', () => clearResult('Results cleared. Your saved log remains unchanged.'));
    if (!session()) {
      status('Open this page from the same browser tab as the authenticated OWG dashboard.');
      return;
    }
    refreshLogs();
  }
  if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', init);
  if (typeof window !== 'undefined') window.__owgObjectLeadTimes = {elapsed, logIdIsValid};
})();
