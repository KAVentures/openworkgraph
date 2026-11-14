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
    dossierForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (dossierRequest) dossierRequest.abort();
      const ref = dossierRef.value.trim();
      dossierResults.replaceChildren();
      if (!/^owg:w:[0-9a-f]{32}$/.test(ref) || !scanConsent.checked) {
        dossierRequest = null;
        dossierButton.disabled = false;
        dossierStatus.textContent = 'Select a valid opaque case and explicitly approve the source read.';
        return;
      }
      const controller = new AbortController();
      dossierRequest = controller;
      dossierButton.disabled = true;
      dossierStatus.textContent = 'Checking current declaration and authorization…';
      const current = () => dossierRequest === controller && !controller.signal.aborted;
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
        dossierStatus.textContent = 'Case reviewed. Provider status is not business success.';
        show('Owner goal: ' + String(dossier.owner_declaration?.goal_code || 'unknown').slice(0,80));
        show('Expected outcome: ' + String(dossier.owner_declaration?.expected_outcome_code || 'unknown').slice(0,80));
        show('Reviewed prerequisites: ' + dossier.declared_prerequisite_count);
        show('Provider status: ' + (source.availability === 'source_reported' && source.state === 'open'
          ? 'open (source-reported)' : 'unknown'));
        show('Customer outcome: unknown; no agent action authorized.');
      } catch (_) {
        if (current()) dossierStatus.textContent =
          'Review unavailable, stale or revoked. No source or outcome conclusion can be drawn.';
      } finally {
        if (dossierRequest === controller) {
          dossierRequest = null;
          dossierButton.disabled = false;
          scanConsent.checked = false;
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
