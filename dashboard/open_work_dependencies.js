/* Read-only operator view of reviewed dependencies. No agent approval or source write. */
(() => {
  'use strict';
  function mount() {
    const host = document.getElementById('panel-evidence');
    if (!host || document.getElementById('owgDurableDependencyView')) return;
    const card = document.createElement('section');
    card.id = 'owgDurableDependencyView';
    card.setAttribute('aria-label', 'Reviewed case dependencies');
    card.style.cssText = 'margin:1rem 0;padding:1rem;border:1px solid var(--border,#777);border-radius:8px';
    const heading = document.createElement('h3');
    heading.textContent = 'Reviewed case dependencies';
    const note = document.createElement('p');
    note.textContent = 'Read-only, owner-declared prerequisites. Source status and business outcomes remain unknown until independently verified. No agent action is authorized here.';
    const form = document.createElement('form');
    const label = document.createElement('label');
    label.textContent = 'Opaque work-item reference (optional): ';
    const field = document.createElement('input');
    field.type = 'text';
    field.name = 'work_item_ref';
    field.placeholder = 'owg:w:…';
    field.autocomplete = 'off';
    field.maxLength = 38;
    field.setAttribute('aria-describedby', 'owgDependencyStatus');
    label.appendChild(field);
    const button = document.createElement('button');
    button.type = 'submit';
    button.textContent = 'Refresh reviewed dependencies';
    form.append(label, document.createTextNode(' '), button);
    const status = document.createElement('p');
    status.id = 'owgDependencyStatus';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const details = document.createElement('div');
    details.id = 'owgDependencyResults';
    const list = document.createElement('ul');
    details.appendChild(list);
    card.append(heading, note, form, status, details);

    // A deliberately separate, opt-in operator flow. Listing local owner
    // declarations is not permission to scan the provider or execute work.
    const dossierForm = document.createElement('form');
    const dossierLabel = document.createElement('label');
    dossierLabel.textContent = 'Reviewed case to inspect: ';
    const dossierRef = document.createElement('input');
    dossierRef.type = 'text';
    dossierRef.maxLength = 38;
    dossierRef.autocomplete = 'off';
    dossierLabel.appendChild(dossierRef);
    const scanLabel = document.createElement('label');
    const scanConsent = document.createElement('input');
    scanConsent.type = 'checkbox';
    scanLabel.append(scanConsent, document.createTextNode(' I authorize a bounded live Stripe read for this case'));
    const dossierButton = document.createElement('button');
    dossierButton.type = 'submit';
    dossierButton.textContent = 'Review case and verify source';
    const dossierStatus = document.createElement('p');
    dossierStatus.setAttribute('role', 'status');
    dossierStatus.setAttribute('aria-live', 'polite');
    const dossierResults = document.createElement('ul');
    dossierForm.append(dossierLabel, scanLabel, dossierButton);
    card.append(dossierForm, dossierStatus, dossierResults);
    let dossierRequest = null;
    let dossierApprovalConsumed = false;
    // Withdrawal is effective immediately in this view, including while the
    // declaration lookup or source scan is pending. Server-side grants remain
    // independently enforced by the authenticated route.
    function clearDossier(reason) {
      if (dossierRequest) dossierRequest.abort();
      dossierRequest = null;
      dossierButton.disabled = false;
      dossierResults.replaceChildren();
      dossierStatus.textContent = reason;
    }
    // Revocation also clears completed source evidence, not only pending reads.
    // Changing the selected case invalidates any previously displayed evidence.
    scanConsent.addEventListener('change', () => {
      if (scanConsent.checked) dossierApprovalConsumed = false;
      else clearDossier('Source read approval withdrawn; no result displayed.');
    });
    dossierRef.addEventListener('input', () => {
      scanConsent.checked = false;
      dossierApprovalConsumed = false;
      clearDossier('Case changed; explicitly approve a new source read.');
    });
    dossierForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (dossierRequest) dossierRequest.abort();
      const ref = dossierRef.value.trim();
      dossierResults.replaceChildren();
      if (!/^owg:w:[0-9a-f]{32}$/.test(ref) || !scanConsent.checked || dossierApprovalConsumed) {
        dossierRequest = null;
        dossierButton.disabled = false;
        dossierStatus.textContent = 'Select a valid opaque case and explicitly approve the source read.';
        return;
      }
      const controller = new AbortController();
      dossierRequest = controller;
      dossierButton.disabled = true;
      dossierStatus.textContent = 'Checking current declaration and authorization…';
      const current = () => dossierRequest === controller && !controller.signal.aborted && scanConsent.checked;
      const show = value => {
        const item = document.createElement('li');
        item.textContent = value;
        dossierResults.appendChild(item);
      };
      try {
        if (window.__owgAuthReady) await window.__owgAuthReady;
        if (!current()) return;
        const opts = {credentials:'same-origin', cache:'no-store',
          headers:{Accept:'application/json'}, signal:controller.signal};
        const declarationsResponse = await fetch('/v1/open-work/declarations', opts);
        if (!current()) return;
        if (!declarationsResponse.ok) throw new Error('declaration read denied');
        const declarations = await declarationsResponse.json();
        if (!current()) return;
        if (declarations.format !== 'openworkgraph.local-case-declarations.v1' ||
            declarations.available !== true || !Array.isArray(declarations.items) ||
            declarations.items.length > 10000) throw new Error('invalid declaration inventory');
        const matches = declarations.items.filter(item => item && item.work_item_ref === ref);
        if (matches.length !== 1) throw new Error('case not currently reviewed');
        const selected = matches[0];
        if (!Number.isSafeInteger(selected.revision) || selected.revision < 1 ||
            typeof selected.edit_token !== 'string' ||
            !/^[0-9a-f]{32}$/.test(selected.edit_token)) {
          throw new Error('invalid declaration revision');
        }
        if (!scanConsent.checked || !current()) return;
        dossierStatus.textContent = 'Reading explicitly approved source evidence…';
        // One approval permits one source scan; keep the checkbox visibly
        // checked while its evidence is displayed so withdrawal is possible.
        dossierApprovalConsumed = true;
        const scanned = await fetch('/v1/open-work/declarations/dossier', {
          ...opts, method:'POST',
          headers:{...opts.headers, 'Content-Type':'application/json'},
          body:JSON.stringify({work_item_ref:ref, expected_revision:selected.revision,
            expected_edit_token:selected.edit_token, confirm_source_scan:true, max_pages:1})
        });
        if (!current()) return;
        if (!scanned.ok) throw new Error('source review unavailable');
        const dossier = await scanned.json();
        if (!current()) return;
        const source = dossier && dossier.source_reported_status;
        if (dossier.format !== 'openworkgraph.local-reviewed-case-dossier.v1' ||
            dossier.work_item_ref !== ref || !source ||
            !['source_reported','unknown'].includes(source.availability) ||
            dossier.agent_execution_authorized !== false ||
            dossier.customer_outcome_verified !== false ||
            dossier.expected_outcome_verified !== false ||
            dossier.source_write_performed !== false ||
            !Number.isSafeInteger(dossier.declared_prerequisite_count) ||
            dossier.declared_prerequisite_count < 0) {
          throw new Error('unexpected dossier response');
        }
        // Treat the complete server projection as untrusted before rendering.
        const prereqs = dossier.declared_prerequisites;
        const observations = dossier.retained_observation_summary;
        const validRef = value => typeof value === 'string' && /^owg:w:[0-9a-f]{32}$/.test(value);
        const validRecord = value => typeof value === 'string' && /^owg:r:[0-9a-f]{32}$/.test(value);
        const validCount = value => Number.isSafeInteger(value) && value >= 0 && value <= 10000000;
        if (!Array.isArray(prereqs) || prereqs.length !== dossier.declared_prerequisite_count ||
            prereqs.length > 10000 || !prereqs.every(p => p && validRef(p.work_item_ref) &&
              Number.isSafeInteger(p.revision) && p.revision >= 1 &&
              p.provenance === 'explicit_owner_declaration_not_source_verified' &&
              p.expected_outcome_verified === false &&
              p.source_state === 'unknown_until_explicit_recheck') ||
            !Array.isArray(observations) || observations.length > 8 ||
            !observations.every(o => o && validRecord(o.resource_ref) &&
              ['observed_event_count','observed_session_count','observed_actor_count'].every(k => validCount(o[k])) &&
              o.observed_not_verified_handoff === true) ||
            dossier.business_obligation_verified !== false ||
            dossier.absence_of_observation_proves_no_work !== false ||
            source.provider_status_not_customer_outcome !== true ||
            !['open','unknown'].includes(source.state) ||
            (source.availability === 'unknown' && source.state !== 'unknown')) {
          throw new Error('invalid case evidence projection');
        }
        // Join the reviewed durable dependency projection into this same operator
        // review. This read is separately authorized by the server and must not
        // confer source or execution permissions. Fail closed on malformed data.
        const graphResponse = await fetch('/v1/open-work/declarations/durable-dependencies?work_item_ref=' +
          encodeURIComponent(ref), opts);
        if (!current()) return;
        if (!graphResponse.ok) throw new Error('dependency read denied');
        const graph = await graphResponse.json();
        if (!current()) return;
        const graphContext = graph && graph.context;
        const graphRefs = values => Array.isArray(values) && values.length <= 10000 &&
          values.every(validRef) && new Set(values).size === values.length;
        if (!graph || graph.format !== 'openworkgraph.reviewed-durable-dependencies.v1' ||
            graph.agent_execution_authorized !== false ||
            graph.customer_outcome !== 'unknown' ||
            !graphContext || !graphRefs(graphContext.direct) ||
            !graphRefs(graphContext.prerequisites) ||
            typeof graphContext.truncated !== 'boolean' ||
            !graphContext.direct.every(x => graphContext.prerequisites.includes(x))) {
          throw new Error('invalid durable case graph');
        }
        dossierStatus.textContent = 'Case reviewed. Provider status is not business success.';
        show('Owner goal: ' + String(dossier.owner_declaration?.goal_code || 'unknown').slice(0,80));
        show('Expected outcome: ' + String(dossier.owner_declaration?.expected_outcome_code || 'unknown').slice(0,80));
        show('Reviewed prerequisites: ' + dossier.declared_prerequisite_count);
        show('Durable direct dependencies: ' + graphContext.direct.length);
        show('Durable transitive dependencies: ' + graphContext.prerequisites.length);
        if (graphContext.truncated) show('Dependency graph truncated; no completeness claim.');
        prereqs.forEach(p => show('Prerequisite ' + p.work_item_ref +
          ' (revision ' + p.revision + '): owner-declared; outcome unverified'));
        show('Retained observation references: ' + observations.length +
          ' (observations are not verified handoffs)');
        observations.forEach(o => show('Observed record ' + o.resource_ref +
          ': ' + o.observed_event_count + ' events, ' + o.observed_session_count +
          ' sessions; not proof of outcome'));
        show('Provider status: ' + (source.availability === 'source_reported' && source.state === 'open'
          ? 'open (source-reported)' : 'unknown'));
        if (source.availability === 'unknown') {
          show('Source unavailable or incomplete: ' +
            String(source.reason || 'unknown').slice(0,80));
        } else {
          show('Source checked at: ' + String(source.verified_at || 'unknown').slice(0,40) +
            '; inventory complete: ' + (source.bounded_inventory_complete === true ? 'yes' : 'no'));
        }
        show('Customer outcome: unknown; no agent action authorized.');
      } catch (_) {
        if (current()) dossierStatus.textContent =
          'Review unavailable, stale or revoked. No source or outcome conclusion can be drawn.';
      } finally {
        if (dossierRequest === controller) {
          dossierRequest = null;
          dossierButton.disabled = false;
          // Do not silently uncheck while source evidence remains visible.
          // On an unsuccessful review, require a fresh explicit approval.
          if (!dossierResults.children.length) scanConsent.checked = false;
        }
      }
    });


    // Retained attempt receipts are a separate, explicit, read-only review.
    // A journal entry is not independent provider verification or customer success.
    const receiptForm = document.createElement('form');
    receiptForm.setAttribute('aria-label', 'Review bound agent attempt receipts');
    const receiptHeading = document.createElement('h4');
    receiptHeading.textContent = 'Review bound agent attempt receipts';
    const receiptInputs = [
      ['Case reference', 38], ['Deployment ID', 128], ['Run ID', 128]
    ].map(([name, limit]) => {
      const label = document.createElement('label');
      label.textContent = name + ': ';
      const input = document.createElement('input');
      input.type = 'text';
      input.maxLength = limit;
      input.autocomplete = 'off';
      label.appendChild(input);
      receiptForm.appendChild(label);
      return input;
    });
    const receiptApprovalLabel = document.createElement('label');
    const receiptApproval = document.createElement('input');
    receiptApproval.type = 'checkbox';
    receiptApprovalLabel.append(receiptApproval,
      document.createTextNode(' I explicitly authorize historical run comparison for this bound case'));
    const receiptButton = document.createElement('button');
    receiptButton.type = 'submit';
    receiptButton.textContent = 'Inspect retained attempt receipts';
    const receiptStatus = document.createElement('p');
    receiptStatus.setAttribute('role', 'status');
    receiptStatus.setAttribute('aria-live', 'polite');
    const receiptResults = document.createElement('ul');
    receiptForm.append(receiptApprovalLabel, receiptButton);
    card.append(receiptHeading, receiptForm, receiptStatus, receiptResults);
    let receiptController = null;
    let receiptGeneration = 0;
    let receiptApprovalConsumed = false;
    function clearReceipts(message) {
      receiptGeneration += 1;
      if (receiptController) receiptController.abort();
      receiptController = null;
      receiptButton.disabled = false;
      receiptResults.replaceChildren();
      receiptStatus.textContent = message;
    }
    receiptInputs.forEach(input => input.addEventListener('input', () => {
      receiptApproval.checked = false;
      receiptApprovalConsumed = false;
      clearReceipts('Run selection changed; a new explicit review is required.');
    }));
    receiptApproval.addEventListener('change', () => {
      if (receiptApproval.checked) receiptApprovalConsumed = false;
      else clearReceipts('Historical review approval withdrawn.');
    });
    receiptForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (receiptApprovalConsumed && receiptApproval.checked) {
        receiptStatus.textContent = 'This approval has already been used; withdraw it before requesting a new review.';
        return;
      }
      clearReceipts('');
      const [ref, deployment, run] = receiptInputs.map(input => input.value.trim());
      if (!/^owg:w:[0-9a-f]{32}$/.test(ref) || !deployment || !run ||
          !receiptApproval.checked || receiptApprovalConsumed) {
        receiptStatus.textContent = 'A reviewed case, deployment, run and explicit approval are required.';
        return;
      }
      receiptApprovalConsumed = true;
      const generation = receiptGeneration;
      const controller = new AbortController();
      receiptController = controller;
      receiptButton.disabled = true;
      const current = () => generation === receiptGeneration &&
        receiptController === controller && !controller.signal.aborted &&
        receiptApproval.checked;
      const options = {credentials:'same-origin', cache:'no-store',
        headers:{Accept:'application/json'}, signal:controller.signal};
      try {
        if (window.__owgAuthReady) await window.__owgAuthReady;
        if (!current()) return;
        receiptStatus.textContent = 'Checking current case review and run binding…';
        const inventoryResponse = await fetch('/v1/open-work/declarations', options);
        if (!current()) return;
        if (!inventoryResponse.ok) throw new Error('declaration access denied');
        const inventory = await inventoryResponse.json();
        if (!current()) return;
        if (inventory.format !== 'openworkgraph.local-case-declarations.v1' ||
            inventory.available !== true || !Array.isArray(inventory.items) ||
            inventory.items.length > 10000) throw new Error('invalid declarations');
        const matches = inventory.items.filter(item => item && item.work_item_ref === ref);
        if (matches.length !== 1) throw new Error('case not reviewed');
        const selected = matches[0];
        if (!Number.isSafeInteger(selected.revision) || selected.revision < 1 ||
            typeof selected.edit_token !== 'string' ||
            !/^[0-9a-f]{32}$/.test(selected.edit_token)) throw new Error('invalid review');
        const response = await fetch('/v1/open-work/declarations/human-outcomes/attempt-receipts', {
          ...options, method:'POST',
          headers:{...options.headers, 'Content-Type':'application/json'},
          body:JSON.stringify({
            work_item_ref:ref, expected_revision:selected.revision,
            expected_edit_token:selected.edit_token,
            confirm_historical_review:true, confirm_run_comparison:true,
            max_cases:1, deployment_id:deployment, run_id:run
          })
        });
        if (!current()) return;
        if (!response.ok) throw new Error('binding or receipt access denied');
        const result = await response.json();
        if (!current()) return;
        if (!result || result.format !== 'openworkgraph.bound-attempt-receipts.v1' ||
            result.case_ref !== ref ||
            typeof result.run_ref !== 'string' || !result.run_ref ||
            result.receipt_provenance !== 'local_untrusted_journal' ||
            result.provider_event_independently_rechecked_on_this_read !== false ||
            result.customer_outcome_verified !== false ||
            result.agent_execution_authorized !== false ||
            result.source_read_performed !== false ||
            !Array.isArray(result.entries) || result.entries.length > 100 ||
            !result.entries.every(row => row && Number.isSafeInteger(row.id) &&
              row.id > 0 && typeof row.checked_at === 'string' &&
              ['unknown','verified_provider_event','verified_provider_operation'].includes(row.status) &&
              ['unknown','succeeded','failed'].includes(row.provider_operation) &&
              row.customer_outcome === 'unknown')) {
          throw new Error('invalid receipt projection');
        }
        // A retained receipt is useful only with current, independently authorized
        // prerequisite context. Do not display either projection if this second
        // read is denied, revoked, truncated incorrectly, or malformed.
        const dependencyResponse = await fetch(
          '/v1/open-work/declarations/durable-dependencies?work_item_ref=' +
          encodeURIComponent(ref), options);
        if (!current()) return;
        if (!dependencyResponse.ok) throw new Error('dependency access denied');
        const dependency = await dependencyResponse.json();
        if (!current()) return;
        const context = dependency && dependency.context;
        const validRefs = values => Array.isArray(values) && values.length <= 10000 &&
          values.every(value => typeof value === 'string' &&
            /^owg:w:[0-9a-f]{32}$/.test(value)) &&
          new Set(values).size === values.length;
        if (!dependency || dependency.format !== 'openworkgraph.reviewed-durable-dependencies.v1' ||
            dependency.agent_execution_authorized !== false ||
            dependency.customer_outcome !== 'unknown' || !context ||
            !validRefs(context.direct) || !validRefs(context.prerequisites) ||
            typeof context.truncated !== 'boolean' ||
            !context.direct.every(value => context.prerequisites.includes(value))) {
          throw new Error('invalid dependency projection');
        }
        receiptStatus.textContent = 'Historical journal only. Provider evidence was not rechecked; customer outcome unknown.';
        const add = value => {
          const li = document.createElement('li');
          li.textContent = value;
          receiptResults.appendChild(li);
        };
        add('Bound run: ' + result.run_ref.slice(0,128));
        add('Retained entries: ' + result.entries.length);
        add('Reviewed direct prerequisites: ' + context.direct.length);
        add('Reviewed transitive prerequisites: ' + context.prerequisites.length);
        if (context.truncated) add('Dependency context truncated; completeness unknown.');
        result.entries.forEach(row => add('Journal #' + row.id + ': ' + row.status +
          ', provider operation ' + row.provider_operation +
          ', customer outcome unknown'));
      } catch (_) {
        if (current()) {
          receiptResults.replaceChildren();
          receiptStatus.textContent = 'Receipt review denied, stale or unavailable; no outcome established.';
        }
      } finally {
        if (receiptController === controller) {
          receiptController = null;
          receiptButton.disabled = false;
          // Keep consent visibly checked while successful historical evidence
          // remains displayed, so withdrawal can clear it immediately.
          if (!receiptResults.children.length) receiptApproval.checked = false;
        }
      }
    });

    host.appendChild(card);
    let requestController = null;
    function line(value) {
      const li = document.createElement('li');
      li.textContent = value;
      list.appendChild(li);
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const ref = field.value.trim();
      // Cancel the prior request even if this submission fails validation.
      // An older response must never overwrite a newer validation error.
      if (requestController) requestController.abort();
      if (ref && !/^owg:w:[0-9a-f]{32}$/.test(ref)) {
        requestController = null;
        button.disabled = false;
        list.replaceChildren();
        status.textContent = 'Enter a valid opaque work-item reference or leave the field empty.';
        return;
      }
      const controller = new AbortController();
      requestController = controller;
      button.disabled = true;
      list.replaceChildren();
      status.textContent = 'Checking current reviewed declarations…';
      try {
        // Wait for the dashboard's authenticated fetch bootstrap before reading.
        if (typeof window !== 'undefined' && window.__owgAuthReady) {
          await window.__owgAuthReady;
        }
        if (controller.signal.aborted || requestController !== controller) return;
        const query = ref ? '?work_item_ref=' + encodeURIComponent(ref) : '';
        const response = await fetch('/v1/open-work/declarations/durable-dependencies' + query, {
          credentials: 'same-origin', cache: 'no-store',
          headers: {'Accept': 'application/json'}, signal: controller.signal
        });
        if (controller.signal.aborted || requestController !== controller) return;
        if (!response.ok) {
          status.textContent = response.status === 401 || response.status === 403
            ? 'Access unavailable. Reconnect and authorize the source before reviewing.'
            : 'Dependencies unavailable (HTTP ' + response.status + '). No previous result is retained.';
          return;
        }
        const data = await response.json();
        if (controller.signal.aborted || requestController !== controller) return;
        if (data.format !== 'openworkgraph.reviewed-durable-dependencies.v1' ||
            data.agent_execution_authorized !== false || data.customer_outcome !== 'unknown') {
          status.textContent = 'Unexpected review response; no results displayed.';
          return;
        }
        // Treat server responses as untrusted, including malformed success payloads.
        // Validate the entire shape before rendering any part of a review.
        const counts = ['reviewed_case_count', 'reviewed_dependency_count',
          'added_reviewed_edges', 'removed_stale_edges'];
        const validCount = value => Number.isSafeInteger(value) && value >= 0;
        const validRefs = value => Array.isArray(value) && value.length <= 10000 &&
          value.every(item => typeof item === 'string' && /^owg:w:[0-9a-f]{32}$/.test(item));
        if (!counts.every(key => validCount(data[key])) ||
            (data.context && (typeof data.context !== 'object' ||
              !validRefs(data.context.direct) || !validRefs(data.context.prerequisites) ||
              typeof data.context.truncated !== 'boolean'))) {
          status.textContent = 'Unexpected review response; no results displayed.';
          return;
        }
        status.textContent = 'Current reviewed declarations loaded. No source status or business outcome verified.';
        line('Reviewed cases: ' + Number(data.reviewed_case_count || 0));
        line('Declared dependencies: ' + Number(data.reviewed_dependency_count || 0));
        line('Newly mirrored edges: ' + Number(data.added_reviewed_edges || 0));
        line('Stale edges removed: ' + Number(data.removed_stale_edges || 0));
        if (data.context) {
          line('Direct prerequisites: ' + ((data.context.direct || []).join(', ') || 'None'));
          line('All prerequisites: ' + ((data.context.prerequisites || []).join(', ') || 'None'));
          if (data.context.truncated) line('Result truncated; do not treat this as a complete graph.');
        }
      } catch (error) {
        // Fetch implementations may reject a superseded request with a non-AbortError.
        // Only the latest live request is allowed to change the operator view.
        if (!controller.signal.aborted && requestController === controller &&
            error.name !== 'AbortError') {
          status.textContent = 'Could not retrieve current dependencies. No previous result is retained.';
        }
      } finally {
        if (requestController === controller) {
          requestController = null;
          button.disabled = false;
        }
      }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
