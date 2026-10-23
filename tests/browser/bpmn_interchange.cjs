/* Actual third-party BPMN import/export; no business systems or customer data. */
const { chromium } = require("playwright");
const fs = require("node:fs/promises");
const path = require("node:path");
const { tmpdir } = require("node:os");
const { spawnSync } = require("node:child_process");
const assert = require("node:assert/strict");
(async () => {
  const scratch = await fs.mkdtemp(
    path.join(tmpdir(), "owg-bpmn-interchange-"),
  );
  let browser;
  try {
    const generated = spawnSync(
      process.env.OWG_BROWSER_PYTHON || "python",
      ["tests/browser/bpmn_interchange_fixtures.py", scratch],
      { encoding: "utf8" },
    );
    assert.equal(generated.status, 0, generated.stdout + generated.stderr);
    const manifest = JSON.parse(
      await fs.readFile(path.join(scratch, "manifest.json"), "utf8"),
    );
    const toolkit = require.resolve("bpmn-js/dist/bpmn-modeler.development.js");
    browser = await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
        ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
        : {}),
      headless: true,
      args: ["--no-sandbox"],
    });
    const page = await browser.newPage({
      viewport: { width: 1280, height: 900 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("**/*", (route) => route.abort());
    await page.setContent(
      '<!doctype html><title>OWG BPMN interoperability test</title><div id="modeler" style="width:1200px;height:800px"></div>',
    );
    await page.addScriptTag({ path: toolkit });
    for (const fixture of manifest) {
      const xml = await fs.readFile(path.join(scratch, fixture.file), "utf8");
      const result = await page.evaluate(
        async ({ xml, fixture }) => {
          const modeler = new BpmnJS({ container: "#modeler" });
          try {
            const imported = await modeler.importXML(xml);
            modeler.get("canvas").zoom("fit-viewport");
            const registry = modeler.get("elementRegistry");
            const nodes = fixture.nodes.map((id) => {
              const n = registry.get(id);
              return {
                id: n.id,
                type: n.type,
                x: n.x,
                y: n.y,
                width: n.width,
                height: n.height,
              };
            });
            const edges = fixture.edges.map((e) => {
              const n = registry.get(e.id);
              return {
                id: n.id,
                source: n.source.id,
                target: n.target.id,
                waypoints: n.waypoints.length,
              };
            });
            const executable = registry.get(fixture.process_id).businessObject
              .isExecutable;
            const { xml: roundtrip } = await modeler.saveXML({ format: true });
            const { svg } = await modeler.saveSVG();
            const second = await modeler.importXML(roundtrip);
            return {
              warnings: imported.warnings.map((w) => w.message),
              roundtripWarnings: second.warnings.map((w) => w.message),
              nodes,
              edges,
              executable,
              roundtrip,
              svg,
            };
          } finally {
            modeler.destroy();
          }
        },
        { xml, fixture },
      );
      assert.deepEqual(result.warnings, [], fixture.name);
      assert.deepEqual(result.roundtripWarnings, [], fixture.name);
      assert.equal(result.executable, false, fixture.name);
      assert.deepEqual(
        result.edges.map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
        })),
        fixture.edges,
        fixture.name,
      );
      assert.equal(result.nodes.length, fixture.nodes.length);
      for (const n of result.nodes)
        assert(
          [n.x, n.y, n.width, n.height].every(Number.isFinite) &&
            n.width > 0 &&
            n.height > 0,
          JSON.stringify(n),
        );
      for (const e of result.edges) assert(e.waypoints >= 2, JSON.stringify(e));
      assert.match(result.svg, /<svg/);
      assert.match(result.roundtrip, /isExecutable="false"/);
      if (fixture.name === "paired-parallel") {
        assert.equal(result.nodes.filter(n=>n.type==="bpmn:ParallelGateway").length,2);
        assert.match(result.roundtrip,/gatewayDirection="Diverging"/);
        assert.match(result.roundtrip,/gatewayDirection="Converging"/);
      }
      if (fixture.name === "branches-and-loop") {
        assert.match(result.roundtrip, /Evidence missing/);
        assert.match(result.roundtrip, /sha256 a{64}/);
        assert.equal(result.roundtrip.includes("conditionExpression"), false);
      }
    }
    assert.deepEqual(errors, []);
    console.log(
      "External BPMN modeler round trips passed: linear, branches, paired parallel gateways, cycles, self-loop and manual-review starter; every activity/edge rendered and execution stayed disabled.",
    );
  } finally {
    if (browser) await browser.close();
    await fs.rm(scratch, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
