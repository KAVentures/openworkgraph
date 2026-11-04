"""Functional navigation regression for the dashboard's consent-neutral journey guide."""
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class JourneyGuideTests(unittest.TestCase):
    def test_navigation_and_refusal_in_real_javascript_runtime(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is required for dashboard JS execution")
        source = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="owgJourneyGuide"', source)
        self.assertIn('id="owgJourneyStatus"', source)
        marker = "/* Navigation only. Never infer consent"
        self.assertEqual(source.count(marker), 1)
        script = marker + source.split("<script>\n" + marker, 1)[1].split("</script>", 1)[0]
        harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const listeners={}, elements={}, controls=[], events=[], state={active:'overview'};
function element(id) {
  return elements[id] ||= {
    id,hidden:false,attributes:{},setAttribute(k,v){this.attributes[k]=v},
    focus(){events.push('focus:'+id)},scrollIntoView(){events.push('scroll:'+id)}
  };
}
for(const id of ['owgJourneyStatus','tab-settings','tab-evidence','tab-connect',
 'panel-settings','panel-evidence','panel-connect','owgEventLogCard',
 'owgCatalogEditor','owgPipelineCard'])element(id);
const guide=element('owgJourneyGuide');
const reviewInputs={};
for(const stage of ['privacy','discovery','map','review']){
  const handlers={};
  reviewInputs[stage]={dataset:{owgReview:stage},checked:false,disabled:true,
    addEventListener(k,fn){handlers[k]=fn},change(value){this.checked=value;handlers.change()}};
}
const review=element('owgJourneyReview');
review.querySelector=selector=>{
  const match=selector.match(/data-owg-review="([^"]+)"/);
  return match?reviewInputs[match[1]]:null;
};
review.querySelectorAll=()=>Object.values(reviewInputs);
for(const id of ['owgJourneyReviewStatus','owgJourneyReviewHandoff','owgJourneyReviewReset']){
  const el=element(id),handlers={};
  el.addEventListener=(k,fn)=>{handlers[k]=fn};
  el.click=()=>handlers.click();
}
guide.querySelector=selector=>{
  if(selector==='#owgJourneyReview')return review;
  if(selector.startsWith('#'))return elements[selector.slice(1)]||null;
  const match=selector.match(/data-owg-journey="([^"]+)"/);
  return match?controls.find(c=>c.dataset.owgJourney===match[1]):null;
};
guide.querySelectorAll=()=>controls;
for(const stage of ['privacy','discovery','map','review','handoff']){
  const handlers={};
  controls.push({dataset:{owgJourney:stage},addEventListener(k,fn){handlers[k]=fn},
    click(){handlers.click()}});
}
const document={
  addEventListener(k,fn){listeners[k]=fn},
  getElementById(id){return elements[id]||null}
};
const sandbox={document,window:{activateTab(tab){
  state.active=tab;
  for(const key of ['settings','evidence','connect'])element('panel-'+key).hidden=key!==tab;
}}};
vm.runInNewContext(process.argv[1],sandbox);
listeners.DOMContentLoaded();
for(const [i,expectedTab,focusId] of [
 [0,'settings','tab-settings'],[1,'evidence','owgEventLogCard'],
 [2,'evidence','owgCatalogEditor'],[3,'evidence','owgPipelineCard'],
 [4,'connect','tab-connect']]){
  controls[i].click();
  assert.equal(state.active,expectedTab);
  assert.ok(events.includes('focus:'+focusId));
  assert.match(element('owgJourneyStatus').textContent,/No consent, approval or execution/);
}
delete elements.owgCatalogEditor;
const before=events.length,active=state.active;
controls[2].click();
assert.equal(state.active,active);
assert.equal(events.length,before);
assert.match(element('owgJourneyStatus').textContent,/not available/);
assert.equal(controls.length,5);
element('owgCatalogEditor');
assert.equal(element('owgJourneyReviewHandoff').disabled,true);
controls[0].click();
assert.equal(reviewInputs.privacy.disabled,false);
reviewInputs.privacy.change(true);
controls[1].click();
assert.equal(reviewInputs.discovery.disabled,false);
reviewInputs.discovery.change(true);
controls[2].click();
reviewInputs.map.change(true);
controls[3].click();
reviewInputs.review.change(true);
assert.equal(element('owgJourneyReviewHandoff').disabled,false);
controls[0].click();
assert.equal(element('owgJourneyReviewHandoff').disabled,true);
assert.equal(reviewInputs.review.checked,false);
"""
        result = subprocess.run(
            ["node", "-e", harness, script], capture_output=True, text=True,
            timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
