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
    await upload(alice, pathModule.join(scratch, "process-v2.json"));
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
    assert.match(
      await alice.locator("#reviewState").textContent(),
      /needs review/,
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
    await reader.setViewportSize({ width: 390, height: 844 });
    await reader.screenshot({
      path: pathModule.join(scratch, "mobile.png"),
      fullPage: true,
    });
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
