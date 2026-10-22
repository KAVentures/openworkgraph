/* Read-only, additive workflow explorer over existing derived OWG evidence. */
(() => {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const MAX_VARIANTS = 6;
  const MAX_STEPS = 24;
  const LIMIT = 30;
  let candidates = [];
  let selectedId = '';
  let latestRequest = 0;
  let chosenPeriod = '7';
  let selectedExample = null;
  let activeBundle = null;
  let currentWindow = {};

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function svgElement(tag, attrs) {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }
  function count(value) {
    const n = Number(value);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : 0;
  }
  function safeArray(value) { return Array.isArray(value) ? value : []; }
  function readable(step, labels) {
    const s = String(step || '').slice(0, 240);
    if (s.startsWith('surface:')) return String((labels || {})[s] || s.slice(8).replace(/-/g, ' ')).slice(0, 100);
    if (s.startsWith('action:')) return s.slice(7).replace(/_/g, ' ');
    if (s.startsWith('tool:')) return 'Agent tool · ' + s.slice(5).replace(/_/g, ' ');
    return s.replace(/_/g, ' ');
  }
  function titleFor(cluster) {
    if(cluster.reviewed_label)return String(cluster.reviewed_label).slice(0,120);
    const labels = cluster.surface_labels || {};
    const steps = safeArray(cluster.core_steps).length ? cluster.core_steps : cluster.core_structural_steps;
    return safeArray(steps).slice(0, 3).map(step => readable(step, labels)).filter(Boolean).join(' → ') || 'Observed workflow candidate';
  }
  function buildLanes(cluster) {
    const variants = safeArray(cluster.exact_variants);
    const labels = cluster.surface_labels || {};
    const rows = variants.length ? variants : [{
      readable_steps: cluster.core_steps || cluster.core_structural_steps || [],
      execution_count: cluster.execution_count || 0,
      execution_ids: cluster.execution_ids || []
    }];
    return rows.slice(0, MAX_VARIANTS).map((variant, index) => {
      const steps = safeArray(variant.readable_steps).length ?
        variant.readable_steps : variant.structural_steps || [];
      const ids = safeArray(variant.execution_ids).filter(id => /^execution:[a-zA-Z0-9_-]+$/.test(String(id))).slice(0, 12);
      return {
        index,
        steps: safeArray(steps).slice(0, MAX_STEPS).map(step => readable(step, labels)),
        omittedSteps: Math.max(0, safeArray(steps).length - MAX_STEPS),
        count: count(variant.execution_count),
        ids,
        exact: variants.length > 0,
        inferred: true
      };
    }).filter(row => row.steps.length);
  }

  function sinceWindow(period) {
    if (period === 'all') return {};
    const days = Number(period);
    return {since: new Date(Date.now() - days * 86400000).toISOString()};
  }
  function button(label, handler, secondary) {
    const b = element('button', label);
    b.type = 'button';
    if (secondary) b.className = 'secondary';
    b.addEventListener('click', handler);
    return b;
  }
  function append(parent, ...children) {
    children.forEach(child => parent.appendChild(child));
    return parent;
  }
  function status(text) {
    const host = document.getElementById('owgExplorerStatus');
    if (host) host.textContent = text;
  }
  function install() {
    const overview = document.getElementById('panel-overview');
    if (!overview || document.getElementById('owgWorkflowExplorer')) return;
    const card = element('section', null, 'card');
    card.id = 'owgWorkflowExplorer';
    card.setAttribute('aria-label', 'Workflow explorer');
    const heading = element('h2', 'Explore observed workflows');
    const explanation = element('p', 'Explore repeated paths, variations and their supporting executions. Candidate groupings are inferred, not verified business processes.', 'muted');
    const controls = element('div', null, 'owg-explorer-controls');
    const selectLabel = element('label', 'Period ');
    const period = element('select');
    period.id = 'owgExplorerPeriod';
    [['7', 'Last 7 days'], ['30', 'Last 30 days'], ['all', 'All retained history (bounded)']].forEach(([value, caption]) => {
      const opt = element('option', caption);
      opt.value = value;
      period.appendChild(opt);
    });
    period.value = chosenPeriod;
    period.addEventListener('change', () => { chosenPeriod = period.value; });
    selectLabel.appendChild(period);
    const refreshButton = button('Explore workflows', load);
    refreshButton.id = 'owgExplorerRefresh';
    const search = element('input');
    search.id = 'owgExplorerSearch';
    search.type = 'search';
    search.placeholder = 'Filter observed steps';
    search.setAttribute('aria-label', 'Filter workflow candidates');
    search.addEventListener('input', renderCandidates);
    append(controls, selectLabel, refreshButton, search);
    const notice = element('div', 'Select a period and explore. This reads existing local workflow evidence; it does not send data to an AI.', 'muted');
    notice.id = 'owgExplorerStatus';
    notice.setAttribute('role', 'status');
    const layout = element('div', null, 'owg-explorer-layout');
    const list = element('div');
    list.id = 'owgExplorerCandidates';
    list.setAttribute('aria-label', 'Workflow candidates');
    const detail = element('div');
    detail.id = 'owgExplorerDetail';
    detail.appendChild(element('p', 'Choose a workflow candidate to inspect its observed variants.', 'muted'));
    append(layout, list, detail);
    append(card, heading, explanation, controls, notice, layout);
    const style = element('style');
    style.textContent = [
      '#owgWorkflowExplorer .owg-explorer-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:end;margin:12px 0}',
      '#owgWorkflowExplorer .owg-explorer-controls label{display:grid;gap:3px}',
      '#owgWorkflowExplorer .owg-explorer-controls input{min-width:180px;max-width:260px}',
      '#owgWorkflowExplorer .owg-explorer-layout{display:grid;grid-template-columns:minmax(195px,1fr) minmax(0,2.5fr);gap:16px;margin-top:12px}',
      '#owgExplorerCandidates{max-height:650px;overflow:auto}',
      '#owgExplorerCandidates button{width:100%;display:block;text-align:left;white-space:normal;margin-bottom:6px;padding:10px;border:1px solid var(--border,#d9dfd4);border-radius:8px;background:transparent;color:inherit}',
      '#owgExplorerCandidates button[aria-pressed=true]{outline:2px solid #3b7462;outline-offset:-2px}',
      '#owgExplorerDetail{min-width:0}',
      '#owgWorkflowExplorer .owg-map-scroll{max-width:100%;overflow:auto;border:1px solid var(--border,#d9dfd4);border-radius:8px}',
      '#owgWorkflowExplorer .owg-map-scroll svg{display:block;max-height:730px}',
      '#owgWorkflowExplorer .owg-explorer-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}',
      '#owgWorkflowExplorer .owg-map-node:focus rect{stroke-width:3}',
      '@media(max-width:800px){#owgWorkflowExplorer .owg-explorer-layout{grid-template-columns:minmax(0,1fr)}}'
    ].join('\n');
    card.prepend(style);
    const anchor = document.getElementById('patternCard');
    if (anchor && anchor.parentNode === overview) anchor.insertAdjacentElement('afterend', card);
    else overview.appendChild(card);
  }

  async function load() {
    const refreshButton = document.getElementById('owgExplorerRefresh');
    const requestId = ++latestRequest;
    if (refreshButton) refreshButton.disabled = true;
    candidates = [];
    activeBundle = null;
    selectedId = '';
    selectedExample = null;
    status('Reading workflow candidates…');
    renderCandidates();
    clearDetail('Loading candidate workflows…');
    try {
      await window.__owgAuthReady;
      currentWindow = sinceWindow(chosenPeriod);
      const params = new URLSearchParams({
        min_runs: '2', limit: String(LIMIT), rank_by_quality: 'true',
        source_event_limit: '25000', include_transition_maps:'true', ...currentWindow
      });
      const response = await fetch('/v1/workflow-evidence/families?' + params, {cache: 'no-store'});
      if (!response.ok) throw new Error('The workflow evidence read failed (' + response.status + ').');
      const payload = await response.json();
      if (requestId !== latestRequest) return;
      candidates = safeArray(payload.candidate_clusters);
      status(
        candidates.length + ' candidate groups shown' +
        (count(payload.total) > candidates.length ? ' of ' + count(payload.total) + ' returned groups.' : '.') +
        (payload.source_limit_reached ? ' Source event limit reached: earlier work may be missing. Narrow the period.' : '') +
        ' Group quality reflects structure, not task identity.'
      );
      renderCandidates();
      if (candidates.length) choose(candidates[0].candidate_cluster_id);
      else clearDetail('No repeated workflow candidates found for this period. This does not mean no work occurred. Try another period or inspect the Activity view.');
    } catch (error) {
      if (requestId !== latestRequest) return;
      status('Explorer unavailable: ' + String(error.message || error));
      clearDetail('Existing evidence, discovery, and export views remain available.');
    } finally {
      if (requestId === latestRequest && refreshButton) refreshButton.disabled = false;
    }
  }

  function clearDetail(message) {
    const host = document.getElementById('owgExplorerDetail');
    if (host) host.replaceChildren(element('p', message, 'muted'));
  }
  function renderCandidates() {
    const host = document.getElementById('owgExplorerCandidates');
    const search = document.getElementById('owgExplorerSearch');
    if (!host) return;
    host.replaceChildren();
    const term = String(search && search.value || '').trim().toLowerCase();
    const matches = candidates.filter(candidate => {
      const labels = safeArray(candidate.core_steps).join(' ') + ' ' +
        safeArray(candidate.core_structural_steps).join(' ') + ' ' + titleFor(candidate);
      return labels.toLowerCase().includes(term);
    });
    matches.forEach(candidate => {
      const b = button('', () => choose(candidate.candidate_cluster_id));
      b.setAttribute('aria-pressed', String(selectedId === candidate.candidate_cluster_id));
      append(b, element('strong', titleFor(candidate)),
        element('div', count(candidate.execution_count) + ' observed runs · ' +
          count(candidate.exact_variant_count) + ' variants · median ' +
          Math.round(Number(candidate.median_duration_seconds) || 0) + 's', 'muted'));
      host.appendChild(b);
    });
    if (!matches.length && candidates.length) host.appendChild(element('p', 'No candidates match this filter.', 'muted'));
  }
  function choose(id) {
    const candidate = candidates.find(item => item.candidate_cluster_id === id);
    if (!candidate) return;
    selectedId = id;
    selectedExample = null;
    activeBundle = null;
    renderCandidates();
    renderDetail(candidate);
  }

  function renderDetail(candidate) {
    const host = document.getElementById('owgExplorerDetail');
    if (!host) return;
    host.replaceChildren();
    host.appendChild(element('h3',(candidate.reviewed_label?'Reviewed group · ':'')+titleFor(candidate)));
    host.appendChild(element('p',candidate.reviewed_label?
      'User-declared classification · '+count(candidate.execution_count)+' selected retained executions · '+count(candidate.exact_variant_count)+' observed variants. This does not verify common business cases, intent or handoffs.':
      'Observed candidate · ' + count(candidate.execution_count) + ' executions · ' +
      count(candidate.exact_variant_count) + ' exact sequence variants · structural specificity ' +
      Number(candidate.cluster_quality || 0).toFixed(2) + ' (not task confidence)', 'muted'));
    const lanes = buildLanes(candidate);
    if (!lanes.length) {
      host.appendChild(element('p', 'No readable steps are available for this candidate.', 'muted'));
      return;
    }
    const drawing = element('div', null, 'owg-map-scroll');
    drawing.setAttribute('aria-label', 'Scrollable process variant map');
    const width = 160 + Math.max(...lanes.map(lane => lane.steps.length)) * 184;
    const height = lanes.length * 106 + 35;
    const svg = svgElement('svg', {width, height, viewBox: '0 0 ' + width + ' ' + height,
      role: 'group', 'aria-label': 'Observed sequences in separate lanes; not a single confirmed business process'});
    lanes.forEach((lane, row) => {
      const y = 24 + row * 106;
      const caption = svgElement('text', {x: 10, y: y + 31, 'font-size': 12, fill: '#61726b'});
      caption.textContent = 'V' + (row + 1) + ' · ' + lane.count + ' runs';
      svg.appendChild(caption);
      lane.steps.forEach((step, col) => {
        const x = 145 + col * 184;
        if (col > 0) {
          const connector = svgElement('path', {d: 'M ' + (x - 30) + ' ' + (y + 25) +
            ' H ' + (x - 5), stroke: '#70877d', 'stroke-width': 2, fill: 'none'});
          const tip = svgElement('path', {d: 'M ' + (x - 11) + ' ' + (y + 20) + ' L ' +
            (x - 5) + ' ' + (y + 25) + ' L ' + (x - 11) + ' ' + (y + 30),
            stroke: '#70877d', fill: 'none', 'stroke-width': 2});
          append(svg, connector, tip);
        }
        const group = svgElement('g', {role: 'button', tabindex: '0', class: 'owg-map-node',
          'aria-label': 'Variant ' + (row + 1) + ' step ' + (col + 1) + ': ' + step});
        const rect = svgElement('rect', {x, y, width: 154, height: 52, rx: 8,
          fill: '#f3f7f5', stroke: '#6d9281', 'stroke-width': 1.5});
        const label = svgElement('text', {x: x + 9, y: y + 21, fill: '#244337', 'font-size': 12});
        label.textContent = step.length > 25 ? step.slice(0, 24) + '…' : step;
        const sublabel = svgElement('text', {x: x + 9, y: y + 38, fill: '#66766e', 'font-size': 10});
        sublabel.textContent = 'Observed step ' + (col + 1);
        const title = svgElement('title');
        title.textContent = step;
        append(group, rect, label, sublabel, title);
        group.addEventListener('click', () => explainStep(lane, col));
        group.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            explainStep(lane, col);
          }
        });
        group.style.cursor = 'pointer';
        svg.appendChild(group);
      });
      if (lane.omittedSteps) {
        const more = svgElement('text', {x: 155 + lane.steps.length * 184, y: y + 29, 'font-size': 12});
        more.textContent = '+' + lane.omittedSteps + ' steps not drawn';
        svg.appendChild(more);
      }
    });
    drawing.appendChild(svg);
    host.appendChild(drawing);
    const limited = count(candidate.exact_variant_count) - lanes.length;
    host.appendChild(element('p',
      'Each lane is a distinct observed sequence. Identical steps in different lanes are not assumed to be the same decision or case.' +
      (limited > 0 ? ' ' + limited + ' additional variants are not drawn.' : '') +
      ' The map shows no unobserved actions, values, or inferred authorization.', 'muted'));
    if(candidate.observed_transition_map){
      const combined=element('div');combined.id='owgExplorerCombinedMap';
      host.appendChild(button('Explore combined candidate transitions',()=>renderCombinedMap(candidate,lanes,combined),true));
      host.appendChild(combined);
    }
    const actions = element('div', null, 'owg-explorer-actions');
    actions.appendChild(button('Review selected examples', () => inspect(candidate, selectedExample), true));
    const regroup=button('Name or regroup selected examples',()=>{
      if(!window.owgWorkflowGroups?.stage){status('Reviewed grouping controls unavailable. Reopen the local dashboard.');return;}
      window.owgWorkflowGroups.stage({execution_ids:selectedIds(candidate,selectedExample),title:titleFor(candidate),...currentWindow});
    },true);
    regroup.disabled=!!candidate.reviewed_label;
    if(regroup.disabled)regroup.title='Use the reviewed group editor to rename, split or move these classifications.';
    actions.appendChild(regroup);
    actions.appendChild(button('Draft selected variant in process catalog',()=>{
      const lane=selectedExample;
      if(!lane||!lane.exact||lane.omittedSteps){status('Select a node in a complete displayed exact variant before drafting. Core summaries and clipped lanes cannot be used.');return;}
      try{
        if(!window.owgProcessCatalog?.draftObserved)throw new Error('Open the current local process catalog first.');
        window.owgProcessCatalog.draftObserved({path:lane.steps,count:lane.count,basis:'selected_observed_variant',evidenceRefs:lane.ids});
      }catch(error){status(String(error.message||'Could not draft this selected variant.'));}
    },true));
    actions.appendChild(button('Copy AI handoff request', () => copyHandoff(candidate, selectedExample), true));
    host.appendChild(actions);
    const stepInfo = element('div', 'Select a map node to inspect its supporting variant.', 'muted');
    stepInfo.id = 'owgExplorerStepInfo';
    stepInfo.setAttribute('role', 'status');
    host.appendChild(stepInfo);
    const examples = element('div');
    examples.id = 'owgExplorerEvidence';
    host.appendChild(examples);
  }
  function renderCombinedMap(candidate,lanes,host){
    host.replaceChildren();
    const map=candidate.observed_transition_map;
    if(!window.owgProcessGraph?.draw||map?.format!=='openworkgraph.candidate-transition-map.v1'){
      host.appendChild(element('p','Combined map unavailable. Use the separate observed variant lanes.','muted'));return;
    }
    const activities=safeArray(map.activities).map(node=>({...node,
      label:readable(node.display_step||node.structural_step,candidate.surface_labels),kind:'activity',
      observed_count:node.projected_occurrence_count,
      observed_summary:count(node.projected_occurrence_count)+' projected steps · '+count(node.supporting_execution_count)+' executions'}));
    const byId=new Map(activities.map(node=>[node.id,node]));
    const transitions=safeArray(map.transitions).map(edge=>({...edge,
      observed_count:edge.projected_transition_count,
      observed_summary:(byId.get(edge.source)?.label||'')+' → '+(byId.get(edge.target)?.label||'')+' · '+
        count(edge.projected_transition_count)+' projected transitions in '+count(edge.supporting_execution_count)+' executions; step timing unavailable'}));
    host.appendChild(element('h4','Combined candidate transition map'));
    host.appendChild(element('p',count(map.execution_count)+' candidate executions across all cluster projections, including variants omitted from the lanes. '+
      'Counts describe normalized structural steps: adjacent repeats collapse and each sequence is limited to '+count(map.maximum_projected_steps_per_execution)+' steps. '+
      'They are not canonical event counts or verified business cases. Combined paths can be absent from every execution; no step timing, policy or authorization is inferred.','muted'));
    if(count(map.omitted_activity_count)||count(map.omitted_transition_type_count))host.appendChild(element('p',
      count(map.omitted_activity_count)+' activity types and '+count(map.omitted_transition_type_count)+' transition types omitted by map limits. Separate variants remain available.','muted'));
    const info=element('div','Select a node to inspect projection counts and displayed supporting variants.','muted');
    info.id='owgExplorerCombinedDetails';info.setAttribute('role','status');info.tabIndex=-1;
    try{
      const drawing=window.owgProcessGraph.draw({activities,transitions,entry_activity_id:map.layout_anchor_activity_id||activities[0]?.id},selected=>{
        const node=byId.get(selected.id);if(!node)return;
        // A combined selection does not silently choose a complete execution.
        selectedExample=null;
        const stepInfo=document.getElementById('owgExplorerStepInfo');
        if(stepInfo)stepInfo.textContent='Combined activity selected. Choose a supporting variant before drafting an exact route.';
        info.replaceChildren(element('p',node.label+' · '+count(node.projected_occurrence_count)+' projected occurrences in '+
          count(node.supporting_execution_count)+' candidate executions. Supporting examples below are limited to displayed variants.'));
        let found=0;
        for(const lane of lanes){
          if(!safeArray(candidate.exact_variants?.[lane.index]?.structural_steps).includes(node.structural_step))continue;
          found++;
          info.appendChild(button('Select supporting variant '+(lane.index+1),()=>{
            selectedExample=lane;
            const detail=document.getElementById('owgExplorerStepInfo');
            if(detail)detail.textContent='Selected variant '+(lane.index+1)+' supporting '+node.label+'. '+lane.count+' candidate executions; inspect evidence before inferring intent.';
          },true));
        }
        if(!found)info.appendChild(element('p','No supporting variant is included in the displayed lane sample. Narrow the period or inspect the source evidence.'));
        info.focus();
      },{observed:true,projected:true});
      host.appendChild(drawing);host.appendChild(info);
    }catch{
      host.appendChild(element('p','This combined projection exceeds the renderer limits. Use the separate observed variant lanes.','muted'));
    }
  }
  function explainStep(lane, position) {
    selectedExample = lane;
    const host = document.getElementById('owgExplorerStepInfo');
    if (host) host.textContent =
      'Variant ' + (lane.index + 1) + ', step ' + (position + 1) + ': ' +
      lane.steps[position] + '. Supported by ' + lane.count +
      ' candidate executions; up to ' + lane.ids.length +
      ' evidence references available. This is an observation, not a required step.';
  }
  function selectedIds(candidate, lane) {
    const ids = lane && lane.ids.length ? lane.ids : safeArray(candidate.execution_ids);
    return ids.filter(id => /^execution:[a-zA-Z0-9_-]+$/.test(String(id))).slice(0, 12);
  }
  async function inspect(candidate, lane) {
    const output = document.getElementById('owgExplorerEvidence');
    if (!output) return;
    output.replaceChildren(element('p', 'Reading bounded supporting evidence…', 'muted'));
    const ids = selectedIds(candidate, lane);
    if (!ids.length) {
      output.replaceChildren(element('p', 'No selectable execution references available.', 'muted'));
      return;
    }
    try {
      await window.__owgAuthReady;
      const params = new URLSearchParams({
        execution_ids: ids.join(','), max_runs: String(ids.length),
        source_event_limit: '25000', max_events_per_run: '12',
        include_canonical_evidence: 'false', ...currentWindow
      });
      const response = await fetch('/v1/workflow-evidence?' + params, {cache: 'no-store'});
      if (!response.ok) throw new Error('Evidence unavailable (' + response.status + ').');
      const bundle = await response.json();
      if (selectedId !== candidate.candidate_cluster_id) return;
      activeBundle = bundle;
      output.replaceChildren();
      output.appendChild(element('h4', 'Evidence for ' +
        count(bundle.selector && bundle.selector.selected_execution_count) + ' selected executions'));
      const rows = safeArray(bundle.structural_alignment && bundle.structural_alignment.high_support_steps);
      if (rows.length) {
        const list = element('ul');
        rows.slice(0, 12).forEach(row => list.appendChild(element('li',
          readable(row.step, bundle.structural_alignment.surface_labels) + ' · ' +
          count(row.support_runs) + '/' + count(row.runs_total) + ' observed runs')));
        output.appendChild(list);
      }
      output.appendChild(element('p',
        'Selected examples are bounded. ' +
        (bundle.provenance && bundle.provenance.source_limit_reached ?
          'Source limit reached; earlier evidence may be absent. ' : '') +
        'Observed completion does not verify the real business outcome. Use the existing Evidence and AI export views for canonical traces.', 'muted'));
    } catch (error) {
      output.replaceChildren(element('p', String(error.message || error), 'muted'));
    }
  }
  function copyHandoff(candidate, lane) {
    const ids = selectedIds(candidate, lane);
    const instruction = ids.length
      ? 'Use OpenWorkGraph get_workflow_evidence(execution_ids="' + ids.join(',') + '"). '
      : 'Find and inspect the exact executions behind this OpenWorkGraph workflow candidate. ';
    const reviewed = candidate.reviewed_workflow_id ?
      'Read get_workflow_knowledge(workflow_id="'+candidate.reviewed_workflow_id+'"); require revision '+candidate.reviewed_revision+' before using that user-declared classification. If it changed, reload and clarify. It does not verify intent, business-case joins or permission. ' : '';
    const prompt = reviewed + instruction +
      'Reconstruct what was observed using canonical evidence, verify business case identity and outcomes with current authorized source tools, ' +
      'identify workflow variations and unknown rules, and propose an improved workflow plus an agent implementation plan. ' +
      'Do not treat observed steps as required policy, do not infer permission to write, and do not invent missing values or results.';
    if (typeof window.copyText === 'function') window.copyText(prompt);
    else if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(prompt);
    if (typeof window.toast === 'function') window.toast('Workflow handoff request copied.');
  }

  // Read-only model exposed for pure Node regression tests; no sensor or server changes.
  window.__owgWorkflowExplorerModel = {buildLanes, titleFor, readable};
  window.owgWorkflowExplorer=Object.freeze({
    showReviewedGroup({bundle,label,workflow_id,revision,window:filter={}}){
      const clusters=safeArray(bundle.structural_alignment?.structural_sequence_variants);
      const variants=clusters.flatMap(cluster=>safeArray(cluster.exact_variants));
      const candidate={candidate_cluster_id:'reviewed_'+workflow_id+'_r'+revision,reviewed_label:label,reviewed_workflow_id:workflow_id,reviewed_revision:revision,
        execution_ids:safeArray(bundle.selector?.execution_ids),execution_count:count(bundle.selector?.selected_execution_count),
        exact_variants:variants,exact_variant_count:variants.length,surface_labels:bundle.structural_alignment?.surface_labels||{},
        core_steps:clusters[0]?.core_steps||[]};
      selectedId=candidate.candidate_cluster_id;selectedExample=null;activeBundle=bundle;currentWindow=filter;
      renderCandidates();renderDetail(candidate);
    },
    clearReviewed(label){
      selectedId='';selectedExample=null;activeBundle=null;renderCandidates();
      clearDetail(label?'Reviewed group · '+label+'. Loadable source details are required to draw its observed variants.':'Choose an inferred candidate or load a reviewed classification to inspect observed variants.');
    }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})();
