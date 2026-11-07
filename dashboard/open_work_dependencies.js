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
        if (error.name !== 'AbortError') status.textContent = 'Could not retrieve current dependencies. No previous result is retained.';
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
