const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const { spawn } = require("node:child_process");
const { tmpdir } = require("node:os");
const pathModule = require("node:path");
(async () => {
  const scratch = await fs.mkdtemp(
    pathModule.join(tmpdir(), "owg-process-browser-"),
  );
  const port = process.env.OWG_BROWSER_TEST_PORT || "8791";
  const base = "http://127.0.0.1:" + port;
  const server = spawn(
    process.env.OWG_BROWSER_PYTHON || "python",
    [
      "-m",
      "uvicorn",
      "process_collaboration_app:app",
      "--app-dir",
      "tests/browser",
      "--host",
      "127.0.0.1",
      "--port",
      port,
    ],
    {
      env: { ...process.env, OWG_BROWSER_TEST_DIR: scratch },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let logs = "",
    spawnError;
  server.on("error", (error) => {
    spawnError = error;
  });
  for (const pipe of [server.stdout, server.stderr])
    pipe.on("data", (chunk) => {
      logs = (logs + chunk).slice(-8000);
    });
  let browser;
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      if (spawnError) throw spawnError;
      if (server.exitCode !== null)
        throw new Error("Synthetic Gateway exited: " + logs);
      try {
        const response = await fetch(base + "/processes");
        if (response.ok) {
          ready = true;
          break;
        }
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    if (!ready) throw new Error("Synthetic Gateway failed to start: " + logs);
    browser = await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
        ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
        : {}),
      headless: true,
      args: ["--no-sandbox"],
    });
    const errors = [];
    async function login(token) {
      const context = await browser.newContext();
      const page = await context.newPage();
      page.on("pageerror", (e) => errors.push(e.message));
      await page.goto(base + "/processes");
      await page.getByLabel("Delegated access token").fill(token);
      await page.getByRole("button", { name: "Connect", exact: true }).click();
      await page
        .getByRole("status")
        .filter({ hasText: "Loaded organization process inventory." })
        .waitFor();
      assert.equal(
        await page.getByLabel("Delegated access token").inputValue(),
        "",
      );
      return page;
    }
    async function waitStatus(p, text) {
      await p.getByRole("status").filter({ hasText: text }).waitFor();
    }
    async function upload(p, path) {
      await p.getByLabel("Reviewed process export").setInputFiles(path);
      await waitStatus(
        p,
        "File previewed in this browser. Nothing has been published.",
      );
      await p.locator("#reviewed").check();
      await p.locator("#sharing").check();
    }
    const alice = await login("alice");
    let designWrites = 0;
    alice.on("request", (request) => {
      if (
        request.method() === "POST" &&
        /\/v1\/human\/processes(?:\/process_[A-Za-z0-9_-]+)?$/.test(
          new URL(request.url()).pathname,
        )
      )
        designWrites++;
    });
    await upload(alice, pathModule.join(scratch, "process.json"));
    assert.equal(await alice.locator("#inventory button").count(), 0);
    await alice
      .getByRole("button", {
        name: "Publish as a new shared process",
        exact: true,
      })
      .click();
    await alice
      .locator("#selectedTitle")
      .filter({ hasText: "Shared support process · revision 1" })
      .waitFor();
    assert.match(
      await alice.locator("#design").textContent(),
      /Evidence missing/,
    );
    assert.equal(
      await alice
        .getByRole("button", { name: "Record design review", exact: true })
        .isDisabled(),
      true,
    );
    assert.equal(
      await alice
        .getByRole("button", {
          name: "Download SDK implementation project",
          exact: true,
        })
        .isDisabled(),
      true,
    );
    await alice
      .getByRole("button", { name: "Add collaborator", exact: true })
      .click();
    await alice
      .getByLabel("Collaborator 1 actor ID", { exact: true })
      .fill("bob");
    await alice
      .getByLabel("Collaborator 1 role", { exact: true })
      .selectOption("reviewer");
    await alice.locator("#accessConfirm").check();
    await alice
      .getByRole("button", { name: "Save access", exact: true })
      .click();
    await waitStatus(alice, "Ownership and collaborator roles saved.");
    const bob = await login("bob");
    await bob
      .getByRole("button", { name: "Shared support process · r1", exact: true })
      .click();
    await bob
      .locator("#selectedTitle")
      .filter({ hasText: "revision 1" })
      .waitFor();
    assert.equal(
      await bob
        .getByRole("button", {
          name: "Save as the selected process's next revision",
          exact: true,
        })
        .isDisabled(),
      true,
    );
    await bob
      .getByLabel("Review comment", { exact: true })
      .fill("Clarify exceptional path");
    await bob.locator("#reviewConfirm").check();
    await bob
      .getByRole("button", { name: "Request changes", exact: true })
      .click();
    await bob
      .locator("#reviewState")
      .filter({ hasText: "Design review: changes requested." })
      .waitFor();
    await bob
      .getByLabel("Review comment", { exact: true })
      .fill("Reviewed exact proposed graph");
    await bob.locator("#reviewConfirm").check();
    await bob
      .getByRole("button", { name: "Record design review", exact: true })
      .click();
    await bob
      .locator("#reviewState")
      .filter({ hasText: "Design review: reviewed design." })
      .waitFor();
    await bob
      .getByRole("button", {
        name: "Load reviewed improvement scenarios",
        exact: true,
      })
      .click();
    await bob
      .getByRole("status")
      .filter({ hasText: "Loaded reviewed organization scenarios." })
      .waitFor();
    assert.match(
      await bob.locator("#opportunityReport").textContent(),
      /900 projected net monthly value/,
    );
    const scenarioDownload = bob.waitForEvent("download");
    await bob
      .getByRole("button", { name: "Download scenarios report", exact: true })
      .click();
    const scenarioFile = await scenarioDownload;
    const scenarioPath = pathModule.join(scratch, "scenarios.json");
    await scenarioFile.saveAs(scenarioPath);
    const scenarioReport = JSON.parse(await fs.readFile(scenarioPath, "utf8"));
    assert.equal(scenarioReport.organization_id, "acme");
    assert.equal(scenarioReport.currency, "USD");
    assert.equal(scenarioReport.company_savings_total, null);
    assert.equal(scenarioReport.labor_savings_measured, false);
    assert.equal(scenarioReport.opportunities[0].revision, 1);
    const implementationDownload = bob.waitForEvent("download");
    await bob
      .getByRole("button", {
        name: "Download SDK implementation project",
        exact: true,
      })
      .click();
    const implementation = await implementationDownload;
    const implementationPath = pathModule.join(scratch, "implementation.zip");
    await implementation.saveAs(implementationPath);
    const checked = spawn(
      process.env.OWG_BROWSER_PYTHON || "python",
      [
        "-c",
        `import json,sys
from pathlib import Path
from zipfile import ZipFile
z=ZipFile(sys.argv[1]);assert 'execute.py' in z.namelist()
assert json.loads(z.read('tool-bindings.example.json'))['enabled'] is False
assert json.loads(z.read('customer-intent.example.json'))['customer_authorization_confirmed'] is False
root=Path(sys.argv[2]);root.mkdir();z.extractall(root);sys.path.insert(0,str(root))
from runtime import load_spec
from execute import reference,digest
from evaluate import compare
spec=load_spec()
suite={'format':'openworkgraph.reviewed-shadow-cases.v1','process_reference':reference(spec),'spec_sha256':digest(spec),
'reviewed_by_customer':True,'case_inputs_authorized':True,'cases':[{'id':'case_1','category':'missing_facts',
'input':{'text':'SYNTHETIC_PRIVATE_CASE'},'expected':{'status':'needs_clarification','accepted_routes':[],'review_confirmed':True}}]}
results={'format':'openworkgraph.shadow-plan-results.v1','process_reference':reference(spec),'spec_sha256':digest(spec),
'suite_sha256':digest(suite),'results':[{'case_id':'case_1','plan':{'revision':spec['revision'],
'content_sha256':spec['content_sha256'],'status':'needs_clarification','activity_ids':[],
'missing_business_inputs':['SYNTHETIC_PRIVATE_QUESTION']}}]}
(root/'report.json').write_text(json.dumps(compare(spec,suite,results)),encoding='utf-8')`,
        implementationPath,
        pathModule.join(scratch, "shared-runtime"),
      ],
      { stdio: "ignore" },
    );
    await new Promise((resolve, reject) => {
      checked.on("error", reject);
      checked.on("exit", (code) =>
        code === 0
          ? resolve()
          : reject(
              new Error(
                "Shared implementation ZIP did not preserve disabled grants",
              ),
            ),
      );
    });
    const shadowReport = JSON.parse(
      await fs.readFile(
        pathModule.join(scratch, "shared-runtime", "report.json"),
        "utf8",
      ),
    );
    let shadowRequests = 0;
    bob.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/shadow-review")) {
        shadowRequests++;
        assert(!request.postData().includes("SYNTHETIC_PRIVATE_"));
      }
    });
    for (const invalid of [
      { ...shadowReport, raw_case: "SYNTHETIC_PRIVATE_CASE" },
      {
        ...shadowReport,
        cases: [
          { ...shadowReport.cases[0], case_id: "SYNTHETIC_PRIVATE_CASE" },
        ],
      },
    ]) {
      await bob
        .getByLabel("Minimized shared shadow report", { exact: true })
        .setInputFiles({
          name: "invalid-report.json",
          mimeType: "application/json",
          buffer: Buffer.from(JSON.stringify(invalid)),
        });
      assert.equal(shadowRequests, 0);
      await bob
        .getByRole("button", {
          name: "Send minimized report for organization inspection",
          exact: true,
        })
        .click();
      await waitStatus(
        bob,
        "Choose a valid minimized shadow report without case values, model text or extra fields. Nothing was sent.",
      );
      assert.equal(shadowRequests, 0);
    }
    await bob
      .getByLabel("Minimized shared shadow report", { exact: true })
      .setInputFiles({
        name: "shadow-report.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(shadowReport)),
      });
    assert.equal(shadowRequests, 0);
    await bob
      .getByRole("button", {
        name: "Send minimized report for organization inspection",
        exact: true,
      })
      .click();
    await bob
      .locator("#shadowResult h3")
      .filter({ hasText: "1 of 1 cases match customer expectations" })
      .waitFor();
    assert.equal(shadowRequests, 1);
    assert.match(
      await bob.locator("#shadowResult").textContent(),
      /current independent design review/,
    );
    await bob
      .getByRole("button", { name: "Clear shadow review", exact: true })
      .click();
    assert.equal(await bob.locator("#shadowResult").textContent(), "");
    await alice
      .getByRole("button", { name: "Edit selected design", exact: true })
      .click();
    await alice
      .locator("#preview")
      .getByRole("button", { name: "Select activity open", exact: true })
      .click();
    assert.equal(
      await alice.evaluate(() =>
        document.activeElement.getAttribute("aria-label"),
      ),
      "Draft activity open",
    );
    await alice
      .getByLabel("Draft scenario Monthly cases", { exact: true })
      .fill("120");
    await alice
      .getByLabel("Draft business goal", { exact: true })
      .fill("Resolve the reviewed case after checking its evidence");
    await alice
      .getByLabel("Draft activity open", { exact: true })
      .fill("Inspect case");
    await alice
      .getByLabel("Draft responsibility open", { exact: true })
      .fill("Case reviewer");
    await alice
      .getByRole("button", { name: "Add draft activity", exact: true })
      .click();
    await alice
      .getByLabel("Draft activity step_1", { exact: true })
      .fill("Decide case");
    await alice
      .getByLabel("Draft kind step_1", { exact: true })
      .selectOption("decision");
    await alice
      .getByRole("button", { name: "Add draft activity", exact: true })
      .click();
    await alice
      .getByLabel("Draft activity step_2", { exact: true })
      .fill("Reject case");
    await alice
      .getByLabel("Draft terminal activity IDs (comma separated)", {
        exact: true,
      })
      .fill("close,step_2");
    await alice
      .getByRole("button", { name: "Add draft rule", exact: true })
      .click();
    await alice
      .getByLabel("Draft rule rule_1", { exact: true })
      .fill("Evidence sufficient");
    await alice
      .getByLabel("I confirm declared rule rule_1", { exact: true })
      .check();
    await alice
      .getByLabel("Draft rule rule_1", { exact: true })
      .fill("Reviewed evidence is sufficient");
    assert.equal(
      await alice
        .getByLabel("I confirm declared rule rule_1", { exact: true })
        .isChecked(),
      false,
    );
    await alice
      .getByLabel("I confirm declared rule rule_1", { exact: true })
      .check();
    await alice
      .getByRole("button", { name: "Add draft rule", exact: true })
      .click();
    await alice
      .getByLabel("Draft rule rule_2", { exact: true })
      .fill("Evidence is insufficient");
    await alice
      .getByLabel("I confirm declared rule rule_2", { exact: true })
      .check();
    await alice
      .getByLabel("Draft to 1", { exact: true })
      .selectOption("step_1");
    await alice
      .getByRole("button", { name: "Add draft transition", exact: true })
      .click();
    await alice
      .getByLabel("Draft from 2", { exact: true })
      .selectOption("step_1");
    await alice.getByLabel("Draft to 2", { exact: true }).selectOption("close");
    await alice
      .getByLabel("Draft branch rule 2", { exact: true })
      .selectOption("rule_1");
    await alice
      .getByRole("button", { name: "Add draft transition", exact: true })
      .click();
    await alice
      .getByLabel("Draft from 3", { exact: true })
      .selectOption("step_1");
    await alice
      .getByLabel("Draft to 3", { exact: true })
      .selectOption("step_2");
    await alice
      .getByLabel("Draft branch rule 3", { exact: true })
      .selectOption("rule_2");
    await alice
      .getByRole("button", { name: "Remove draft transition 3", exact: true })
      .click();
    assert.equal(await alice.locator("#reviewed").isDisabled(), true);
    assert.equal(await alice.locator("#sharing").isDisabled(), true);
    assert.match(
      await alice.locator("#selectedTitle").textContent(),
      /revision 1/,
    );
    assert.equal(designWrites, 1);
    await alice
      .getByRole("button", { name: "Review edited design", exact: true })
      .click();
    assert.match(await alice.locator("#preview").textContent(), /Inspect case/);
    await alice.locator("#reviewed").check();
    await alice.locator("#sharing").check();
    await alice
      .getByRole("button", {
        name: "Save as the selected process's next revision",
        exact: true,
      })
      .click();
    await waitStatus(
      alice,
      "Invalid process graph: Decision branches require at least two explicitly referenced rules",
    );
    assert.match(
      await alice.locator("#selectedTitle").textContent(),
      /revision 1/,
    );
    await alice
      .getByRole("button", { name: "Continue editing candidate", exact: true })
      .click();
    await alice
      .getByRole("button", { name: "Add draft transition", exact: true })
      .click();
    await alice
      .getByLabel("Draft from 3", { exact: true })
      .selectOption("step_1");
    await alice
      .getByLabel("Draft to 3", { exact: true })
      .selectOption("step_2");
    await alice
      .getByLabel("Draft branch rule 3", { exact: true })
      .selectOption("rule_2");
    await alice
      .getByRole("button", { name: "Review edited design", exact: true })
      .click();
    await alice.locator("#reviewed").check();
    await alice.locator("#sharing").check();
    await alice
      .getByRole("button", {
        name: "Save as the selected process's next revision",
        exact: true,
      })
      .click();
    await alice
      .locator("#selectedTitle")
      .filter({ hasText: "revision 2" })
      .waitFor();
    assert.match(await alice.locator("#design").textContent(),/Case reviewer/);
    assert((await alice.locator("#design svg title").allTextContents()).some(text=>text.includes("Declared responsibility: Case reviewer")));
    assert.match(
      await alice.locator("#reviewState").textContent(),
      /needs review/,
    );
    assert.equal(
      await alice
        .getByRole("button", {
          name: "Download SDK implementation project",
          exact: true,
        })
        .isDisabled(),
      true,
    );
    await alice
      .getByRole("button", {
        name: "Load reviewed improvement scenarios",
        exact: true,
      })
      .click();
    await alice
      .getByRole("status")
      .filter({ hasText: "Loaded reviewed organization scenarios." })
      .waitFor();
    assert.match(
      await alice.locator("#opportunityReport").textContent(),
      /No reviewed improvement scenarios/,
    );
    assert.equal(designWrites, 3);
    assert.match(await alice.locator("#design").textContent(), /Decide case/);
    assert.equal(
      await alice
        .locator("#design")
        .getByRole("button", { name: "Select activity step_1", exact: true })
        .count(),
      1,
    );
    assert.match(
      await alice.locator("#design").textContent(),
      /Reviewed evidence is sufficient/,
    );
    await bob.locator("#reviewConfirm").check();
    await bob
      .getByRole("button", { name: "Record design review", exact: true })
      .click();
    await waitStatus(
      bob,
      "Process revision changed; reload and review the current design",
    );
    await bob
      .getByRole("button", { name: "Load shared processes", exact: true })
      .click();
    await bob
      .getByRole("button", { name: "Shared support process · r2", exact: true })
      .click();
    await bob
      .locator("#selectedTitle")
      .filter({ hasText: "revision 2" })
      .waitFor();
    await alice
      .getByRole("button", { name: "Remove collaborator 1", exact: true })
      .click();
    await alice.locator("#accessConfirm").check();
    await alice
      .getByRole("button", { name: "Save access", exact: true })
      .click();
    await waitStatus(alice, "Ownership and collaborator roles saved.");
    await bob.locator("#reviewConfirm").check();
    await bob
      .getByRole("button", { name: "Record design review", exact: true })
      .click();
    await waitStatus(bob, "You are not a reviewer for this process");
    const reader = await login("reader");
    assert.equal(
      await reader
        .getByRole("button", {
          name: "Publish as a new shared process",
          exact: true,
        })
        .isDisabled(),
      true,
    );
    await reader
      .getByRole("button", { name: "Shared support process · r2", exact: true })
      .click();
    assert.equal(
      await reader
        .getByRole("button", { name: "Edit selected design", exact: true })
        .isDisabled(),
      true,
    );
    await reader
      .getByRole("button", { name: "Load revision history", exact: true })
      .click();
    await reader.getByRole("button", { name: /^Open revision 1 ·/ }).click();
    await reader
      .locator("#selectedTitle")
      .filter({ hasText: "revision 1" })
      .waitFor();
    assert.equal(
      await reader
        .getByRole("button", { name: "Record design review", exact: true })
        .isDisabled(),
      true,
    );
    const downloadPromise = reader.waitForEvent("download");
    await reader
      .getByRole("button", { name: "Export saved JSON", exact: true })
      .click();
    const download = await downloadPromise;
    const path = pathModule.join(scratch, "shared-r1.json");
    await download.saveAs(path);
    const exported = JSON.parse(await fs.readFile(path, "utf8"));
    assert.equal(exported.revision, 1);
    assert.equal(
      exported.process_model.opportunity_scenario.monthly_cases,
      100,
    );
    assert.equal(exported.collaboration_review.status, "reviewed_design");
    assert.equal(exported.execution_authorization, false);
    // A pending local file read must not repopulate a disconnected identity's page.
    await alice.evaluate(() => {
      const original = File.prototype.text;
      File.prototype.text = function () {
        return new Promise((resolve) => {
          window.resolvePreview = async () =>
            resolve(await original.call(this));
        });
      };
    });
    await alice
      .getByLabel("Reviewed process export")
      .setInputFiles(pathModule.join(scratch, "process.json"));
    await alice.getByLabel("Delegated access token").fill("foreign");
    await alice.getByRole("button", { name: "Connect", exact: true }).click();
    await waitStatus(alice, "Loaded organization process inventory.");
    await alice.evaluate(async () => {
      await window.resolvePreview();
      await new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      );
    });
    assert.equal(await alice.locator("#selected").isHidden(), true);
    assert.equal(
      (await alice.locator("body").textContent()).includes(
        "Shared support process",
      ),
      false,
    );
    assert.equal(await alice.locator("#comment").inputValue(), "");
    assert.equal(await alice.locator("#opportunityReport").textContent(), "");
    assert.equal(
      await alice
        .getByRole("button", { name: "Download scenarios report", exact: true })
        .isDisabled(),
      true,
    );
    await reader.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await reader.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
    );
    await reader.screenshot({
      path: pathModule.join(scratch, "mobile.png"),
      fullPage: true,
    });
    if (process.env.OWG_BROWSER_ARTIFACT_DIR) {
      await fs.mkdir(process.env.OWG_BROWSER_ARTIFACT_DIR, { recursive: true });
      await fs.copyFile(
        pathModule.join(scratch, "mobile.png"),
        pathModule.join(
          process.env.OWG_BROWSER_ARTIFACT_DIR,
          "shared-process-mobile.png",
        ),
      );
      await reader.setViewportSize({ width: 1280, height: 900 });
      await reader.screenshot({
        path: pathModule.join(
          process.env.OWG_BROWSER_ARTIFACT_DIR,
          "shared-process-desktop.png",
        ),
        fullPage: true,
      });
    }
    assert.deepEqual(
      await reader.evaluate(() => [localStorage.length, sessionStorage.length]),
      [0, 0],
    );
    assert.deepEqual(errors, []);
    console.log(
      "Browser collaboration acceptance passed: explicit publication, independent review, revision invalidation, immediate revocation, exact historical export, tenant switch, mobile and no persistent credentials.",
    );
  } finally {
    if (browser) await browser.close();
    server.kill("SIGTERM");
    await new Promise((resolve) => {
      if (server.exitCode !== null || spawnError) resolve();
      else server.once("exit", resolve);
    });
    await fs.rm(scratch, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
