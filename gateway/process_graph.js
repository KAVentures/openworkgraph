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
    const toolbar = document.createElement("div");
    toolbar.className = "owg-graph-toolbar";
    Object.assign(toolbar.style, {display:"flex", flexWrap:"wrap", gap:"8px", alignItems:"center", padding:"8px"});
    toolbar.setAttribute("role", "group");
    toolbar.setAttribute("aria-label", "Process map view controls");
    const viewport = document.createElement("div");
    viewport.className = "owg-graph-viewport";
    Object.assign(viewport.style, {overflow:"auto", maxHeight:"520px", maxWidth:"100%"});
    viewport.setAttribute("tabindex", "0");
    viewport.setAttribute("role", "region");
    viewport.setAttribute("aria-label", "Scrollable process map; use arrow keys to pan");
    const drawing = svg("svg", {
      width: geometry.width,
      height: geometry.height,
      viewBox: "0 0 " + geometry.width + " " + geometry.height,
      role: "group",
      "aria-label": options.observed
        ? "Observed activity transitions across supplied cases. Arrows show recorded order, not causal dependencies or approved routes."
        : "Proposed process graph. Arrows represent designed transitions.",
    });
    const edgePaint = [], nodePaint = [];
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
      const arrow = svg("path", {
          d: "M " + p1.join(" ") + " L " + end.join(" ") + " L " + p2.join(" "),
          fill: "none",
          stroke: "#557e70",
          "stroke-width": 2,
        });
      drawing.appendChild(arrow);
      edgePaint.push({path, arrow, count:edge.observed_count, gap:edge.observed_median_gap_seconds});
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
        rx: ["decision", "routing"].includes(activity.kind) ? 25 : 6,
        fill: "#f1f7f4",
        stroke: "#557e70",
        "stroke-width": 2,
      });
      const title = svg("title");
      title.textContent =
        (options.observed &&
          activity.label + " · " + activity.observed_summary) ||
        activity.label +
          " · " +
          activity.id +
          " · " +
          activity.kind +
          (activity.responsibility_label
            ? " · Declared responsibility: " + activity.responsibility_label
            : "");
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
        (activity.responsibility_label
          ? ("Responsibility: " + activity.responsibility_label).slice(0, 27)
          : activity.id +
            " · " +
            String(activity.kind || "activity").replaceAll("_", " "));
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
      nodePaint.push({rect, count:activity.observed_count});
    });
    let scale = 1;
    const zoomLabel = document.createElement("span");
    zoomLabel.setAttribute("role", "status");
    function setScale(value) {
      scale = Math.max(.01, Math.min(3, value));
      drawing.setAttribute("width", Math.max(1, geometry.width * scale));
      drawing.setAttribute("height", Math.max(1, geometry.height * scale));
      zoomLabel.textContent = "Zoom " + Math.round(scale * 100) + "%";
      zoomOut.disabled = scale <= .01;
      zoomIn.disabled = scale >= 3;
    }
    function control(label, handler) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = label;
      button.addEventListener("click", handler);
      toolbar.appendChild(button);
      return button;
    }
    const zoomOut = control("Zoom out", () => setScale(scale / 1.25));
    const zoomIn = control("Zoom in", () => setScale(scale * 1.25));
    control("Actual size", () => setScale(1));
    control("Fit graph", () => {
      const width = Math.max(1, (viewport.clientWidth || wrap.clientWidth || 320) - 24);
      setScale(Math.min(1, width / geometry.width, 480 / geometry.height));
      viewport.scrollLeft = viewport.scrollTop = 0;
    });
    toolbar.appendChild(zoomLabel);
    setScale(1);
    let legendNode = null;
    if (options.observed === true) {
      const finiteCount = value => Number.isSafeInteger(value) && value >= 0;
      const finiteGap = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
      const hasCounts = edgePaint.some(row => finiteCount(row.count)) || nodePaint.some(row => finiteCount(row.count));
      const hasGaps = edgePaint.some(row => finiteGap(row.gap));
      if (hasCounts || hasGaps) {
        const label = document.createElement("label");
        label.textContent = "Map emphasis ";
        const select = document.createElement("select");
        select.setAttribute("aria-label", "Observed map emphasis");
        const modes = [["none", "Uniform"], ...(hasCounts ? [["frequency", "Recorded frequency"]] : []),
          ...(hasGaps ? [["elapsed", "Median elapsed gap"]] : [])];
        for (const [value, title] of modes) {
          const option = document.createElement("option");
          option.value = value; option.textContent = title; select.appendChild(option);
        }
        label.appendChild(select);toolbar.appendChild(label);
        const legend = document.createElement("p");
        legend.className = "muted owg-graph-legend";
        Object.assign(legend.style, {margin:"8px", overflowWrap:"anywhere"});
        const maxCount = Math.max(0, ...edgePaint.filter(row => finiteCount(row.count)).map(row => row.count));
        const maxNodeCount = Math.max(0, ...nodePaint.filter(row => finiteCount(row.count)).map(row => row.count));
        const maxGap = Math.max(0, ...edgePaint.filter(row => finiteGap(row.gap)).map(row => row.gap));
        const shade = (ratio, elapsed) => elapsed
          ? (ratio > .66 ? "#994b16" : ratio > .33 ? "#b9742e" : "#d7ad7d")
          : (ratio > .66 ? "#426b5b" : ratio > .33 ? "#719582" : "#b4cdbf");
        const repaint = () => {
          const mode = select.value;
          for (const row of edgePaint) {
            const count = finiteCount(row.count), gap = finiteGap(row.gap);
            let color = "#557e70", width = 2;
            if (mode === "frequency" && count) {
              const ratio = maxCount ? row.count / maxCount : 0;
              color = shade(ratio, false); width = 2 + 6 * Math.sqrt(ratio);
            } else if (mode === "elapsed" && gap) {
              const ratio = maxGap ? row.gap / maxGap : 0;
              color = shade(ratio, true); width = 2 + 6 * Math.sqrt(ratio);
            } else if (mode !== "none") color = "#8a8a8a";
            for (const element of [row.path, row.arrow]) {
              element.setAttribute("stroke", color);element.setAttribute("stroke-width", width);
            }
          }
          for (const row of nodePaint) {
            row.rect.setAttribute("fill", mode === "frequency" && finiteCount(row.count)
              ? (maxNodeCount && row.count / maxNodeCount > .66 ? "#c5dfce" : "#e3efe7") : "#f1f7f4");
          }
          legend.textContent = mode === "frequency"
            ? "Wider, darker arrows show higher recorded transition counts (0 to " + maxCount + ") relative to this displayed map. Node shading uses occurrence counts. " +
              (options.projected ? "Counts are normalized candidate projections, not canonical events or business cases. " : "Counts describe supplied event logs. ") + "Unknown values are gray; no transitions are hidden."
            : mode === "elapsed"
            ? "Wider, darker amber arrows show larger median elapsed gaps (0 to " + maxGap + " seconds) relative to this displayed map. Zero is measured; unknown values are gray. These are not verified waiting times, active labor, bottlenecks or causal dependencies. No transitions are hidden."
            : "Uniform display. Select a frequency or elapsed-gap emphasis; no map data or process model changes.";
        };
        select.value = "none";
        select.addEventListener("change", repaint);repaint();
        legendNode = legend;
      }
    }
    viewport.appendChild(drawing);
    // Controls and legend stay outside the scrollable diagram viewport.
    wrap.appendChild(toolbar);
    if (legendNode) wrap.appendChild(legendNode);
    wrap.appendChild(viewport);
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
        observed_count: row.event_count,
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
        observed_count: row.transition_count,
        observed_median_gap_seconds: row.median_elapsed_gap_seconds,
        observed_summary:
          row.from +
          " → " +
          row.to +
          " · " +
          row.transition_count +
          " recorded transitions across " +
          row.case_count +
          " cases · median elapsed gap " +
          (typeof row.median_elapsed_gap_seconds === "number" && Number.isFinite(row.median_elapsed_gap_seconds) && row.median_elapsed_gap_seconds >= 0
            ? row.median_elapsed_gap_seconds + " s" : "unavailable") +
          "; not verified waiting",
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
