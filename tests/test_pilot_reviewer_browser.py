"""Real Chromium local-file reviewer journey; no network or private key required."""
import json
from pathlib import Path
import os

import pytest

playwright=pytest.importorskip("playwright.sync_api")
ROOT=Path(__file__).resolve().parents[1]
PAGE=(ROOT/"evals"/"pilot_reviewer"/"index.html").as_uri()


def make_packet(dual=False):
    first={"selected_resources":["resource"],"selected_agent_runs":[],
           "clarification_questions":0,"wrong_assumptions":0,
           "continuation_success":True,"consequential_wrong_action":False}
    rows=[{"review_id":f"id-{i}","prompt":"<img src=x onerror=alert(1)>",
           "review_text":"Approved neutral response"} for i in range(2)]
    if dual:
        for row in rows:
            row.update(differing_fields=["continuation_success"],first_review=first,
                       second_review={**first,"continuation_success":False})
    return {"format":"owg.pilot-blinded-adjudication.v1" if dual else "owg.pilot-blinded-batch.v1",
            "entries":rows,"limitations":("Operator-reviewed material; independence and blinding are not verified."
                if dual else "Operator-approved free text; blinding is not guaranteed.")}


def upload(page,selector,value):
    page.locator(selector).set_input_files({"name":"approved.json","mimeType":"application/json",
                                           "buffer":json.dumps(value).encode()})


def fill(page,dual=False):
    page.locator("#selected_resources").fill('["exact, resource"]')
    page.locator("#selected_agent_runs").fill("[]")
    page.locator("#clarification_questions").fill("0")
    page.locator("#wrong_assumptions").fill("1")
    page.locator("#continuation_success").select_option("true")
    page.locator("#consequential_wrong_action").select_option("false")
    if dual:page.locator("#resolution_reason").fill("Independent assessment")


@pytest.mark.parametrize("dual",[False,True])
def test_local_review_export_resume_and_safe_text(dual):
    with playwright.sync_playwright() as runtime:
        options={"headless":True}
        if os.environ.get("OWG_TEST_CHROMIUM"):options["executable_path"]=os.environ["OWG_TEST_CHROMIUM"]
        browser=runtime.chromium.launch(**options)
        page=browser.new_page(accept_downloads=True)
        page.on("dialog",lambda d:d.accept())
        errors=[];external=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.on("request",lambda req:external.append(req.url) if req.url.startswith(("http:","https:")) else None)
        page.goto(PAGE)
        upload(page,"#packet-file",make_packet(dual))
        page.get_by_text("Disagreement packet opened." if dual else "Reviewer packet opened.",exact=True).wait_for()
        assert page.locator("#prompt").inner_text()=="<img src=x onerror=alert(1)>"
        assert page.locator("#prompt img").count()==0
        page.locator("#reviewer").fill("reviewer-X")
        page.locator("#save").click()
        assert "not saved" in page.locator("#entry-message").inner_text()
        fill(page,dual);page.locator("#save").click()
        assert "1 of 2 judgments saved" in page.locator("#coverage").inner_text()
        page.locator("#next-entry").click()
        assert page.locator("#entry-heading").inner_text()=="Review id-1"
        assert page.locator("#continuation_success").input_value()==""
        with page.expect_download() as downloaded:page.locator("#export").click()
        rows=json.loads(downloaded.value.path().read_text())
        assert len(rows)==1 and rows[0]["review_id"]=="id-0"
        assert rows[0]["continuation_success"] is True and rows[0]["consequential_wrong_action"] is False
        assert rows[0]["selected_resources"]==["exact, resource"]
        assert ("resolution_reason" in rows[0])==dual
        assert "prompt" not in rows[0] and "review_text" not in rows[0]
        page.locator("#reset").click()
        upload(page,"#packet-file",make_packet(dual))
        page.get_by_text("Disagreement packet opened." if dual else "Reviewer packet opened.",exact=True).wait_for()
        upload(page,"#sheet-file",rows)
        page.get_by_text("Saved sheet restored in memory.",exact=True).wait_for()
        assert "1 of 2 judgments saved" in page.locator("#coverage").inner_text()
        assert page.locator("#reviewer").input_value()=="reviewer-X"
        upload(page,"#sheet-file",[{**rows[0],"review_id":"unplanned"}])
        page.get_by_text("Sheet refused:",exact=False).wait_for()
        assert "1 of 2 judgments saved" in page.locator("#coverage").inner_text()
        assert page.evaluate("Object.keys(localStorage).length")==0
        assert not errors and not external
        browser.close()
