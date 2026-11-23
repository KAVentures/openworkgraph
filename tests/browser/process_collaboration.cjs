const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const { spawn, spawnSync } = require("node:child_process");
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
    const handoffPublishedResponse = alice.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/v1/human/processes");
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
    // Overlapping authenticated selections: a late first response must not
    // replace the newer selection, even when both target the same process.
    const selectionRacePage = await login("alice");
    let releaseStaleRead;
    let staleReadIntercepted;
    const staleReadSeen = new Promise(resolve => { staleReadIntercepted = resolve; });
    let processReads = 0;
    await selectionRacePage.route(/\/v1\/human\/processes\/process_[^/?]+(?:\?.*)?$/, async route => {
      processReads++;
      if (processReads === 1) {
        await new Promise(resolve => { releaseStaleRead = resolve; staleReadIntercepted(); });
        const upstream = await route.fetch();
        const data = await upstream.json();
        data.process.title = "STALE RESPONSE MUST NOT RENDER";
        await route.fulfill({ response: upstream, json: data });
      } else await route.continue();
    });
    await selectionRacePage.getByRole("button", {name:"Shared support process · r1",exact:true}).click();
    await staleReadSeen;
    await selectionRacePage.getByRole("button", {name:"Shared support process · r1",exact:true}).click();
    await selectionRacePage.locator("#selectedTitle").filter({hasText:"Shared support process · revision 1"}).waitFor();
    releaseStaleRead();
    await selectionRacePage.waitForTimeout(150);
    assert.doesNotMatch(await selectionRacePage.locator("#selectedTitle").textContent(), /STALE RESPONSE/);
    await selectionRacePage.close();
    // Browser acceptance of the real authenticated handoff preflight against the
    // synthetic Gateway. The packet is kept in Chromium, never posted to it.
    const handoffPublished = await (await handoffPublishedResponse).json();
    assert.ok(handoffPublished.process?.workflow_id, "publication returns a process identity");
    const handoffProcess = handoffPublished.process;
    const handoffPacket = (overrides = {}) => ({
      format: "openworkgraph.evidence-handoff-review.v1",
      scope: "human_review_only",
      execution_authorized: false,
      source_read_authorized: false,
      process: {
        workflow_id: handoffProcess.workflow_id,
        revision: handoffProcess.revision,
        content_sha256: handoffProcess.content_sha256,
        ...overrides,
      },
    });
    const setHandoffPacket = async packet => {
      await alice.locator("#handoffPacket").setInputFiles({
        name: "local-review.json", mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(packet)),
      });
      assert.equal(await alice.locator("#handoffConsent").isChecked(), false,
        "changing the packet revokes prior inspection consent");
      await alice.locator("#handoffConsent").check();
    };
    let uploadedHandoff = false;
    alice.on("request", request => {
      if (request.method() !== "GET" && request.postData()?.includes("openworkgraph.evidence-handoff-review.v1")) uploadedHandoff = true;
    });
    await setHandoffPacket(handoffPacket());
    await alice.locator("#handoffConsent").uncheck();
    await alice.locator("#handoffCheck").click();
    assert.match(await alice.locator("#handoffStatus").textContent(), /Explicit authorization/);
    await alice.locator("#handoffConsent").check();
    await alice.locator("#handoffCheck").click();
    await alice.locator("#handoffStatus").filter({hasText: "MATCH:"}).waitFor();
    assert.match(await alice.locator("#handoffStatus").textContent(), /Eligible for human design review only/);
    assert.match(await alice.locator("#handoffStatus").textContent(), /execution authorization remain unverified/i);
    assert.equal(await alice.locator("#handoffReview").isDisabled(), false,
      "an authorized, revision-pinned inspection must enable review navigation without execution");
    assert.equal(uploadedHandoff, false, "review packet bytes must remain local");
    await setHandoffPacket(handoffPacket({revision: handoffProcess.revision + 1}));
    await alice.locator("#handoffCheck").click();
    await alice.waitForFunction(() => document.querySelector("#handoffStatus")?.textContent?.includes("REFUSED:"));
    assert.equal(await alice.locator("#handoffReview").isDisabled(), true,
      "rejected packet must never enable review navigation, including when the selection is hidden");
    // Real Gateway 403 from a valid identity without process scope; do not mock a response.
    // Denial can revoke the session: isolate it from the rest of the lifecycle.
    const deniedHandoffPage = await login("alice");
    await deniedHandoffPage.getByRole("button", {name:"Shared support process · r1", exact:true}).click();
    await deniedHandoffPage.locator("#selectedTitle").filter({hasText:"revision 1"}).waitFor();
    await deniedHandoffPage.locator("#handoffPacket").setInputFiles({
      name:"denied-review.json", mimeType:"application/json",
      buffer:Buffer.from(JSON.stringify(handoffPacket())),
    });
    await deniedHandoffPage.locator("#handoffConsent").check();
    const detailUrl = "**/v1/human/processes/" + handoffProcess.workflow_id;
    await deniedHandoffPage.route(detailUrl, async route => route.continue({headers:{...route.request().headers(),authorization:"Bearer no-process"}}), {times:1});
    await deniedHandoffPage.locator("#handoffCheck").click();
    await deniedHandoffPage.waitForFunction(() => document.querySelector("#selected")?.hidden === true && /REFUSED:|Session cleared/.test(document.querySelector("#handoffStatus")?.textContent || ""));
    assert.equal(await deniedHandoffPage.locator("#selected").isHidden(), true,
      "denied Gateway access must invalidate the entire selected process and its actions");
    assert.match(await deniedHandoffPage.locator("#handoffStatus").textContent(), /REFUSED:|Session cleared/,
      "denial must retain a diagnostic even when the selection is hidden");
    assert.equal(uploadedHandoff, false);

    // Recovery is an explicit fresh login and fresh server verification, never
    // reuse of the denied result or an automatic grant after HTTP 403.
    const recoveredHandoffPage = await login("alice");
    await recoveredHandoffPage.getByRole("button", {name:"Shared support process · r1",exact:true}).click();
    await recoveredHandoffPage.locator("#selectedTitle").filter({hasText:"revision 1"}).waitFor();
    await recoveredHandoffPage.locator("#handoffPacket").setInputFiles({
      name:"recovered-review.json",mimeType:"application/json",
      buffer:Buffer.from(JSON.stringify(handoffPacket())),
    });
    await recoveredHandoffPage.locator("#handoffConsent").check();
    const recoveredRead = recoveredHandoffPage.waitForResponse(response =>
      response.request().method()==="GET" &&
      new URL(response.url()).pathname==="/v1/human/processes/"+handoffProcess.workflow_id);
    await recoveredHandoffPage.locator("#handoffCheck").click();
    assert.equal((await recoveredRead).status(),200,
      "recovery must independently read the current authorized Gateway revision");
    await recoveredHandoffPage.locator("#handoffStatus")
      .filter({hasText:"MATCH:"}).waitFor();
    assert.match(await recoveredHandoffPage.locator("#handoffStatus").textContent(),
      /Eligible for human design review only/i);
    assert.equal(await recoveredHandoffPage.locator("#handoffReview").isDisabled(),false,
      "a fresh authorized recovery permits human review but not execution");
    assert.equal(uploadedHandoff,false,
      "neither denied nor recovered packet may be posted to Gateway");

    // Real Gateway verifier rejects the expired bearer (401), without a mocked response.
    // An expired delegated session (401) must fail closed rather than reuse a
    // previously authenticated process selection or display a false match.
    const expiredHandoffPage = await login("alice");
    await expiredHandoffPage.getByRole("button", {name:"Shared support process · r1",exact:true}).click();
    await expiredHandoffPage.locator("#selectedTitle").filter({hasText:"revision 1"}).waitFor();
    await expiredHandoffPage.locator("#handoffPacket").setInputFiles({
      name:"expired-review.json",mimeType:"application/json",
      buffer:Buffer.from(JSON.stringify(handoffPacket())),
    });
    await expiredHandoffPage.locator("#handoffConsent").check();
    await expiredHandoffPage.route(detailUrl, async route => route.continue({headers:{...route.request().headers(),authorization:"Bearer expired-session"}}), {times:1});
    await expiredHandoffPage.locator("#handoffCheck").click();
    await expiredHandoffPage.locator("#handoffStatus").filter({hasText:/REFUSED:|Session cleared/}).waitFor({state:"attached"});
    assert.doesNotMatch(await expiredHandoffPage.locator("#handoffStatus").textContent(),/^MATCH:/);

    // A fresh authenticated read may return a newer server revision even when
    // the selected packet was valid at publication. That is a refusal, not an
    // implicit update or approval of the old review packet.
    const driftHandoffPage = await login("alice");
    await driftHandoffPage.getByRole("button", {name:"Shared support process · r1",exact:true}).click();
    await driftHandoffPage.locator("#selectedTitle").filter({hasText:"revision 1"}).waitFor();
    await driftHandoffPage.locator("#handoffPacket").setInputFiles({
      name:"drift-review.json",mimeType:"application/json",
      buffer:Buffer.from(JSON.stringify(handoffPacket())),
    });
    await driftHandoffPage.locator("#handoffConsent").check();
    await driftHandoffPage.route(detailUrl, async route => {
      const response=await route.fetch();
      const latest=await response.json();
      assert.equal(response.status(),200,"drift fixture starts from an authorized read");
      latest.process.revision=handoffProcess.revision+1;
      latest.latest_revision=handoffProcess.revision+1;
      await route.fulfill({response,json:latest});
    }, {times:1});
    await driftHandoffPage.locator("#handoffCheck").click();
    await driftHandoffPage.locator("#handoffStatus").filter({hasText:"REFUSED:"}).waitFor();
    assert.doesNotMatch(await driftHandoffPage.locator("#handoffStatus").textContent(),/^MATCH:/);
    assert.equal(uploadedHandoff,false);

    // An in-flight authorized detail read must never re-validate a packet after
    // the reviewer clears or replaces the local selection. Hold the actual
    // Gateway request until the local generation has changed.
    const racePage = await login("alice");
    await racePage.getByRole("button", {name:"Shared support process · r1",exact:true}).click();
    await racePage.locator("#selectedTitle").filter({hasText:"revision 1"}).waitFor();
    await racePage.locator("#handoffPacket").setInputFiles({
      name:"race-review.json",mimeType:"application/json",
      buffer:Buffer.from(JSON.stringify(handoffPacket())),
    });
    await racePage.locator("#handoffConsent").check();
    let releaseHandoffRead;
    const heldHandoffRead = new Promise(resolve => {releaseHandoffRead=resolve;});
    await racePage.route(detailUrl, async route => {
      await heldHandoffRead;
      await route.continue();
    }, {times:1});
    const pendingHandoffRead = racePage.waitForRequest(request =>
      request.method()==="GET" && new URL(request.url()).pathname==="/v1/human/processes/"+handoffProcess.workflow_id);
    await racePage.locator("#handoffCheck").click();
    await pendingHandoffRead;
    await racePage.locator("#handoffClear").click();
    assert.equal(await racePage.locator("#handoffPacket").inputValue(),"");
    const completedHandoffRead = racePage.waitForResponse(response =>
      new URL(response.url()).pathname==="/v1/human/processes/"+handoffProcess.workflow_id);
    releaseHandoffRead();
    await completedHandoffRead;
    await racePage.evaluate(() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await racePage.locator("#handoffStatus").textContent(),"No packet checked.");
    await racePage.unroute(detailUrl);
    assert.equal(uploadedHandoff,false);

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
    let readinessRequests=0;alice.on('request',request=>{if(new URL(request.url()).pathname==='/v1/human/process-readiness')readinessRequests++;});
    assert.equal(readinessRequests,0);
    await alice.getByRole('button',{name:'Load design readiness',exact:true}).click();
    await waitStatus(alice,'Loaded organization design readiness. Page counts are not organization totals.');
    assert.equal(readinessRequests,1);
    assert.match(await alice.locator('#readinessReport').textContent(),/1 saved design on this page/);
    assert.match(await alice.locator('#readinessReport').textContent(),/Activities without declared responsibility: 1 design/);
    const readinessDownload=alice.waitForEvent('download');
    await alice.getByRole('button',{name:'Download current readiness page',exact:true}).click();
    const readinessArtifact=await readinessDownload,readinessPath=pathModule.join(scratch,'readiness-page.json');
    await readinessArtifact.saveAs(readinessPath);const readinessJSON=JSON.parse(await fs.readFile(readinessPath,'utf8'));
    assert.equal(readinessJSON.page_process_count,1);assert.equal(readinessJSON.processes[0].revision,1);
    assert.equal(readinessJSON.execution_authorized,false);assert.equal(readinessJSON.deployment_health_checked,false);
    await alice.getByLabel('Readiness owner actor ID (optional)',{exact:true}).fill('nobody');
    assert.equal(await alice.locator('#readinessReport').textContent(),'');assert.equal(readinessRequests,1);
    await alice.getByRole('button',{name:'Load design readiness',exact:true}).click();
    await alice.locator('#readinessReport').getByText('No shared designs match these filters on this page.',{exact:true}).waitFor();
    await alice.getByLabel('Readiness owner actor ID (optional)',{exact:true}).fill('alice');
    await alice.getByRole('button',{name:'Load design readiness',exact:true}).click();
    await alice.locator('#readinessReport').getByText(/^1 saved design on this page/).waitFor();
    await alice.locator('#readinessReport > details > summary').click();
    await alice.setViewportSize({width:390,height:844});
    assert.equal(await alice.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await alice.setViewportSize({width:1280,height:900});
    await alice.getByRole('button',{name:'Open readiness design '+readinessJSON.processes[0].workflow_id,exact:true}).click();
    await waitStatus(alice,'Opened exact saved revision 1.');
    let releaseReadiness;const readinessGate=new Promise(resolve=>releaseReadiness=resolve);
    await alice.route('**/v1/human/process-readiness?*',async route=>{await readinessGate;await route.continue();});
    const pendingReadiness=alice.waitForRequest(request=>new URL(request.url()).pathname==='/v1/human/process-readiness');
    await alice.locator('#loadReadiness').click();await pendingReadiness;
    await alice.getByLabel('Readiness owner actor ID (optional)',{exact:true}).fill('changed-while-pending');
    const lateReadiness=alice.waitForResponse(response=>new URL(response.url()).pathname==='/v1/human/process-readiness');
    releaseReadiness();await lateReadiness;await alice.unroute('**/v1/human/process-readiness?*');
    await alice.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await alice.locator('#readinessReport').textContent(),'');assert(await alice.locator('#downloadReadiness').isDisabled());
    await alice.getByLabel('Readiness owner actor ID (optional)',{exact:true}).fill('𐀀'.repeat(512));
    const unicodeReadiness=alice.waitForResponse(response=>new URL(response.url()).pathname==='/v1/human/process-readiness');
    await alice.locator('#loadReadiness').click();assert.equal((await unicodeReadiness).status(),200);
    await alice.locator('#readinessReport').getByText('No shared designs match these filters on this page.',{exact:true}).waitFor();
    const beforeOverlong=readinessRequests;
    await alice.getByLabel('Readiness owner actor ID (optional)',{exact:true}).fill('a'.repeat(513));
    await alice.locator('#loadReadiness').click();await waitStatus(alice,'Use an owner actor ID of at most 512 characters.');
    assert.equal(readinessRequests,beforeOverlong);


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
    // The real Gateway now reports independent review for this exact revision.
    // Re-check from the owner's browser; the local packet is still never uploaded.
    await setHandoffPacket(handoffPacket());
    await alice.locator("#handoffCheck").click();
    await alice.locator("#handoffStatus")
      .filter({hasText:"MATCH: Fresh authorized process revision"}).waitFor();
    assert.match(await alice.locator("#handoffStatus").textContent(), /execution authorization remain unverified/);
    assert.equal(uploadedHandoff, false);
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
    const predicateEditor=alice.locator('.owg-rule-predicate').filter({hasText:'Structured conditions · rule_1'});
    await predicateEditor.locator('summary').click();
    await predicateEditor.getByLabel('Predicate field rule_1',{exact:true}).fill('/evidence/sufficient');
    await predicateEditor.getByLabel('Predicate expected value rule_1',{exact:true}).fill('true');
    await predicateEditor.getByRole('button',{name:'Add declared condition for rule_1',exact:true}).click();
    assert.equal(await alice.getByLabel('I confirm declared rule rule_1',{exact:true}).isChecked(),false);
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
    assert.match(await alice.locator('#design').textContent(), /Declared predicate.*evidence\/sufficient/);
    const factPayload=Buffer.from(JSON.stringify({cases:[
      {case_id:'case_1',facts:{evidence:{sufficient:true},unused:'PRIVATE_SHARED_CASE_FACT'}},
      {case_id:'case_2',facts:{evidence:{sufficient:false}}},{case_id:'case_3',facts:{}}]}));
    let sharedFactChecks=0;alice.on('request',request=>{if(new URL(request.url()).pathname.endsWith('/check-rules'))sharedFactChecks++;});
    await alice.getByLabel('Authorized shared case-facts JSON',{exact:true}).setInputFiles({name:'owned-shared-facts.json',mimeType:'application/json',buffer:factPayload});
    assert.equal(sharedFactChecks,0);assert(await alice.locator('#checkRuleFacts').isDisabled());
    await alice.getByLabel("I authorize sending this selected case-facts file to this organization's Gateway.",{exact:true}).check();
    assert.equal(sharedFactChecks,0);
    const checkedFacts=alice.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/check-rules'));
    await alice.locator('#checkRuleFacts').click();const sharedCheck=await (await checkedFacts).json();
    assert.equal(sharedCheck.process_reference.revision,2);assert.equal(sharedCheck.organization_id,'acme');
    assert.equal(sharedCheck.rule_result_counts.matched,1);assert.equal(sharedCheck.rule_result_counts.not_matched,1);
    assert.equal(sharedCheck.rule_result_counts.unknown,1);assert(!JSON.stringify(sharedCheck).includes('PRIVATE_SHARED_CASE_FACT'));
    await alice.locator('#ruleFactsResult').getByText('3 supplied case-fact reports',{exact:true}).waitFor();
    assert(!(await alice.locator('#ruleFactsResult').textContent()).includes('PRIVATE_SHARED_CASE_FACT'));
    const sharedRuleDownload=alice.waitForEvent('download');
    await alice.getByRole('button',{name:'Download minimized shared rule-check report',exact:true}).click();
    const downloadedRules=await sharedRuleDownload,downloadedRulesPath=pathModule.join(scratch,'shared-rule-check.json');
    await downloadedRules.saveAs(downloadedRulesPath);
    const downloadedRuleText=await fs.readFile(downloadedRulesPath,'utf8');
    assert(!downloadedRuleText.includes('PRIVATE_SHARED_CASE_FACT'));
    assert.equal(JSON.parse(downloadedRuleText).process_reference.revision,2);
    await alice.locator('#clearRuleFacts').click();assert.equal(await alice.locator('#ruleFactsResult').textContent(),'');
    assert.equal(await alice.locator('#ruleFactsConsent').isChecked(),false);
    assert.equal(await alice.locator('#ruleFactsFile').inputValue(),'');
    // Clearing while a permitted request is pending must also discard its late result.
    let releaseRuleRequest;const pendingRuleGate=new Promise(resolve=>releaseRuleRequest=resolve);
    await alice.route('**/check-rules?*',async route=>{await pendingRuleGate;await route.continue();});
    await alice.getByLabel('Authorized shared case-facts JSON',{exact:true}).setInputFiles({name:'owned-shared-facts.json',mimeType:'application/json',buffer:factPayload});
    await alice.locator('#ruleFactsConsent').check();
    const pendingRequest=alice.waitForRequest(request=>new URL(request.url()).pathname.endsWith('/check-rules'));
    await alice.locator('#checkRuleFacts').click();await pendingRequest;
    await alice.locator('#clearRuleFacts').click();
    const lateResponse=alice.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/check-rules'));
    releaseRuleRequest();await lateResponse;
    await alice.unroute('**/check-rules?*');
    await alice.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await alice.locator('#ruleFactsResult').textContent(),'');

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
    // Alice saved revision 2 while Bob still held the earlier selection. The
    // Gateway correctly refuses review against that stale authorization snapshot.
    // Refresh the actual authenticated selection before the successful review.
    await bob.getByRole("button", { name: "Load shared processes", exact: true }).click();
    await bob.getByRole("button", { name: "Shared support process · r2", exact: true }).click();
    await bob.locator("#selectedTitle").filter({ hasText: "revision 2" }).waitFor();
    await bob.locator("#reviewConfirm").check();
    await bob
      .getByRole("button", { name: "Record design review", exact: true })
      .click();
    // A current, authorized reviewer should receive a durable Gateway review
    // acknowledgment; design review never grants agent execution permission.
    // Surface the actual Gateway acknowledgment or refusal instead of a
    // 30-second opaque locator timeout when an authorization guard fires.
    await bob.waitForFunction(() => {
      const message = document.querySelector("#reviewOutcome")?.textContent || "";
      return message.includes("Gateway acknowledged") ||
        message.includes("Reopen") || message.includes("reopen") ||
        message.includes("changed") || message.includes("uncertain");
    });
    assert.match(
      await bob.locator("#reviewOutcome").textContent(),
      /Gateway acknowledged reviewed design for revision 2.*Human design review only; no agent handoff or execution occurred\./,
    );
    assert.equal(await bob.locator("#reviewConfirm").isChecked(), false);
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
    // The fresh authenticated preflight rejects the revoked reviewer before\n    // any review POST. The UI now reports this as a revision/permission conflict\n    // rather than relying on the old server-side POST rejection message.\n    await bob.locator("#reviewOutcome").filter({\n      hasText: "Review permission or revision changed",\n    }).waitFor();\n    assert.equal(await bob.locator("#reviewConfirm").isChecked(), false);
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
    assert.equal(await alice.locator('#readinessReport').textContent(),'');
    assert(await alice.locator('#downloadReadiness').isDisabled());
    assert.equal(await alice.locator('#readinessOwner').inputValue(),'');
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
    // A reviewed parallel design stays descriptive: organization review cannot
    // make a linear implementation exporter silently choose one branch.
    const parallelOwner=await login("alice"),parallelPath=pathModule.join(scratch,"parallel.json");
    await fs.writeFile(parallelPath,JSON.stringify({format:"openworkgraph.versioned-process-export.v1",title:"Shared parallel checks",
      process_model:{goal:"Complete both independent checks",baseline_source:"user_declared",baseline_coverage:"user_declared_design",
        supporting_case_count:0,business_rules:[],baseline_support_unit:"declared_cases",
        baseline_path:["User declared parallel design"],activities:[{id:"fork",label:"Run checks",kind:"parallel_split"},
          {id:"a",label:"Check A"},{id:"b",label:"Check B"},{id:"join",label:"All checks",kind:"parallel_join"},{id:"close",label:"Close"}].map(node=>({kind:"activity",implementation:"unmapped",...node})),
        transitions:[["fork","a"],["fork","b"],["a","join"],["b","join"],["join","close"]].map(([source,target])=>({source,target})),
        entry_activity_id:"fork",terminal_activity_ids:["close"]}}));
    await upload(parallelOwner,parallelPath);
    await parallelOwner.getByRole("button",{name:"Publish as a new shared process",exact:true}).click();
    await parallelOwner.locator("#selectedTitle").filter({hasText:"Shared parallel checks · revision 1"}).waitFor();
    await parallelOwner.getByRole("button",{name:"Edit selected design",exact:true}).click();
    assert.equal(await parallelOwner.getByLabel("Draft kind fork",{exact:true}).inputValue(),"parallel_split");
    assert.equal(await parallelOwner.getByLabel("Draft kind join",{exact:true}).inputValue(),"parallel_join");
    await parallelOwner.getByRole("button",{name:"Add collaborator",exact:true}).click();
    await parallelOwner.getByLabel("Collaborator 1 actor ID",{exact:true}).fill("bob");
    await parallelOwner.getByLabel("Collaborator 1 role",{exact:true}).selectOption("reviewer");
    await parallelOwner.locator("#accessConfirm").check();
    await parallelOwner.getByRole("button",{name:"Save access",exact:true}).click();
    await waitStatus(parallelOwner,"Ownership and collaborator roles saved.");
    const parallelReviewer=await login("bob");
    await parallelReviewer.getByRole("button",{name:"Shared parallel checks · r1",exact:true}).click();
    await parallelReviewer.locator("#selectedTitle").filter({hasText:"Shared parallel checks · revision 1"}).waitFor();
    await parallelReviewer.locator("#reviewConfirm").check();
    await parallelReviewer.getByRole("button",{name:"Record design review",exact:true}).click();
    await parallelReviewer.locator("#reviewState").filter({hasText:"Design review: reviewed design."}).waitFor();
    assert(await parallelReviewer.getByRole("button",{name:"Download SDK implementation project",exact:true}).isEnabled());
    assert((await parallelReviewer.locator("#exports").textContent()).includes("preserve every parallel branch and join"));
    const [parallelSDK]=await Promise.all([parallelReviewer.waitForEvent("download"),parallelReviewer.getByRole("button",{name:"Download SDK implementation project",exact:true}).click()]);
    const parallelSDKPath=pathModule.join(scratch,"shared-parallel-sdk.zip");await parallelSDK.saveAs(parallelSDKPath);
    const parallelSDKCheck=spawnSync(process.env.OWG_BROWSER_PYTHON||"python",['-c',`import json,sys,zipfile
z=zipfile.ZipFile(sys.argv[1]);s=json.loads(z.read('workflow.spec.json'))
assert s['control_flow_contract']=='openworkgraph.parallel-node-firing-plan.v1'
assert s['execution_schedule']=='serial_token_valid_node_order' and not s['execution_authorization']
assert s['organization_catalog_reference']['organization_id']
assert len([n for n in s['process_model']['activities'] if n['kind'].startswith('parallel_')])==2
assert json.loads(z.read('tool-bindings.example.json'))['enabled'] is False
`,parallelSDKPath],{encoding:'utf8'});assert.equal(parallelSDKCheck.status,0,parallelSDKCheck.stdout+parallelSDKCheck.stderr);
    const parallelDownload=parallelReviewer.waitForEvent("download");
    await parallelReviewer.getByRole("button",{name:"Export saved BPMN",exact:true}).click();
    const parallelArtifact=await parallelDownload,parallelXMLPath=pathModule.join(scratch,"shared-parallel.bpmn");
    await parallelArtifact.saveAs(parallelXMLPath);
    assert.equal(((await fs.readFile(parallelXMLPath,"utf8")).match(/<bpmn:parallelGateway/g)||[]).length,2);
    await parallelReviewer.setViewportSize({width:390,height:844});
    assert.equal(await parallelReviewer.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    assert.deepEqual(errors, []);
    console.log(
      "Browser collaboration acceptance passed: explicit publication, independent review, revision invalidation, immediate revocation, exact historical export, tenant switch, reviewed parallel SDK archive with independent customer intent, mobile and no persistent credentials.",
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
