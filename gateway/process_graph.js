/* Shared process-design and observed-transition visualization. Data only; no network, storage or execution. */
(() => {
  "use strict";
  function layout(model, observed = false) {
    if (
      !Array.isArray(model.activities) ||
      model.activities.length > (observed ? 64 : 40) ||
      !Array.isArray(model.transitions) ||
      model.transitions.length > (observed ? 4096 : 100)
    ) {
      throw new Error("A bounded process graph is required.");
    }
    const nodes = new Map(model.activities.map((a) => [a.id, a]));
    const outgoing = new Map([...nodes.keys()].map((id) => [id, []]));
    for (const edge of model.transitions) {
      if (nodes.has(edge.source) && nodes.has(edge.target))
        outgoing.get(edge.source).push(edge.target);
    }
    const levels = new Map(),
      pending = [];
    if (nodes.has(model.entry_activity_id)) {
      levels.set(model.entry_activity_id, 0);
      pending.push(model.entry_activity_id);
    }
    for (let i = 0; i < pending.length; i++) {
      const source = pending[i];
      for (const target of outgoing.get(source))
        if (!levels.has(target)) {
          levels.set(target, levels.get(source) + 1);
          pending.push(target);
        }
    }
    const fallback = levels.size ? Math.max(...levels.values()) + 1 : 0;
    const rows = new Map(),
      positions = new Map(),
      edges = [];
    const top =
      70 + Math.min(model.transitions.length, observed ? 24 : 100) * 8;
    for (const id of nodes.keys()) {
      const level = levels.has(id) ? levels.get(id) : fallback;
      const row = rows.get(level) || 0;
      rows.set(level, row + 1);
      positions.set(id, { x: 35 + level * 260, y: top + row * 130, level });
    }
    model.transitions.forEach((edge, index) => {
      const from = positions.get(edge.source),
        to = positions.get(edge.target);
      if (!from || !to) return; // Keep incomplete edits visible; saving validates the model.
      const start = [from.x + 190, from.y + 32],
        end = [to.x, to.y + 32];
      let points;
      if (edge.source === edge.target) {
        points = [
          [from.x + 190, from.y + 20],
          [from.x + 220, from.y + 20],
          [from.x + 220, from.y + 90],
          [from.x + 95, from.y + 90],
          [from.x + 95, from.y + 64],
        ];
      } else if (to.level === from.level + 1) {
        const middle = (start[0] + end[0]) / 2;
        points = [start, [middle, start[1]], [middle, end[1]], end];
      } else {
        const lane = 25 + (observed ? index % 24 : index) * 8;
        points = [
          start,
          [start[0] + 24, start[1]],
          [start[0] + 24, lane],
          [end[0] - 24, lane],
          [end[0] - 24, end[1]],
          end,
        ];
      }
      edges.push({ ...edge, points });
    });
    return {
      positions,
      edges,
      width: Math.max(300, ...[...positions.values()].map((p) => p.x + 240)),
      height: Math.max(150, ...[...positions.values()].map((p) => p.y + 110)),
    };
  }
  function draw(model, onSelect, options = {}) {
    const geometry = layout(model, options.observed === true),
      ns = "http://www.w3.org/2000/svg";
    const svg = (tag, attrs = {}) => {
      const element = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) =>
        element.setAttribute(key, String(value)),
      );
      return element;
    };
    const wrap = document.createElement("div");
    wrap.className = "owg-process-graph";
    const drawing = svg("svg", {
      width: geometry.width,
      height: geometry.height,
      role: "group",
      "aria-label": options.observed
        ? "Observed activity transitions across supplied cases. Arrows show recorded order, not causal dependencies or approved routes."
        : "Proposed process graph. Arrows represent designed transitions.",
    });
    geometry.edges.forEach((edge) => {
      const path = svg("path", {
        d: edge.points.map((p, i) => (i ? "L " : "M ") + p.join(" ")).join(" "),
        fill: "none",
        stroke: "#557e70",
        "stroke-width": 2,
      });
      const title = svg("title");
      title.textContent =
        (options.observed && edge.observed_summary) ||
        edge.source +
          " → " +
          edge.target +
          (edge.rule_id ? " · " + edge.rule_id : "");
      path.appendChild(title);
      drawing.appendChild(path);
      const end = edge.points.at(-1),
        before = edge.points.at(-2);
      const angle = Math.atan2(end[1] - before[1], end[0] - before[0]);
      const p1 = [
        end[0] - 9 * Math.cos(angle - 0.45),
        end[1] - 9 * Math.sin(angle - 0.45),
      ];
      const p2 = [
        end[0] - 9 * Math.cos(angle + 0.45),
        end[1] - 9 * Math.sin(angle + 0.45),
      ];
      drawing.appendChild(
        svg("path", {
          d: "M " + p1.join(" ") + " L " + end.join(" ") + " L " + p2.join(" "),
          fill: "none",
          stroke: "#557e70",
          "stroke-width": 2,
        }),
      );
    });
    model.activities.forEach((activity) => {
      const p = geometry.positions.get(activity.id),
        group = svg("g", {
          role: "button",
          tabindex: 0,
          "aria-label": options.observed
            ? "Inspect observed activity " + activity.label
            : "Select activity " + activity.id,
        });
      const rect = svg("rect", {
        x: p.x,
        y: p.y,
        width: 190,
        height: 64,
        rx: activity.kind === "decision" ? 25 : 6,
        fill: "#f1f7f4",
        stroke: "#557e70",
        "stroke-width": 2,
      });
      const title = svg("title");
      title.textContent =
        (options.observed &&
          activity.label + " · " + activity.observed_summary) ||
        activity.label + " · " + activity.id + " · " + activity.kind;
      const label = svg("text", {
        x: p.x + 10,
        y: p.y + 25,
        "font-size": 13,
        fill: "#25372e",
      });
      label.textContent = String(activity.label).slice(0, 24);
      const detail = svg("text", {
        x: p.x + 10,
        y: p.y + 47,
        "font-size": 11,
        fill: "#526358",
      });
      detail.textContent =
        (options.observed && activity.observed_summary) ||
        activity.id +
          " · " +
          String(activity.kind || "activity").replaceAll("_", " ");
      const select = () => onSelect?.(activity);
      group.addEventListener("click", select);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
      group.append(rect, title, label, detail);
      drawing.appendChild(group);
    });
    wrap.appendChild(drawing);
    return wrap;
  }
  function drawObserved(report, onSelect) {
    if (
      !Array.isArray(report.activities) ||
      report.activities.length > 64 ||
      !Array.isArray(report.transitions) ||
      report.transitions.length > 4096
    ) {
      throw new Error("A bounded observed transition map is required.");
    }
    const labels = new Map();
    const activities = report.activities.map((row, index) => {
      if (typeof row.activity !== "string" || labels.has(row.activity))
        throw new Error("Observed activity labels must be unique.");
      const id = "observed_" + index;
      labels.set(row.activity, id);
      return {
        id,
        label: row.activity,
        kind: "activity",
        observed_summary:
          row.event_count + " events · " + row.case_count + " cases",
      };
    });
    const transitions = report.transitions.map((row) => {
      if (!labels.has(row.from) || !labels.has(row.to))
        throw new Error("Observed transition endpoints must exist.");
      return {
        source: labels.get(row.from),
        target: labels.get(row.to),
        observed_summary:
          row.from +
          " → " +
          row.to +
          " · " +
          row.transition_count +
          " recorded transitions across " +
          row.case_count +
          " cases · median elapsed gap " +
          row.median_elapsed_gap_seconds +
          " s; not verified waiting",
      };
    });
    const first = report.cases?.[0]?.path?.[0];
    return draw(
      {
        activities,
        transitions,
        entry_activity_id: labels.get(first) || activities[0]?.id,
      },
      onSelect,
      { observed: true },
    );
  }
  window.owgProcessGraph = Object.freeze({ layout, draw, drawObserved });
})();
