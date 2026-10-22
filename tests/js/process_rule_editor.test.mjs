import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.attrs={};}
  append(...nodes){this.children.push(...nodes);}
  appendChild(node){this.append(node);return node;}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,fn){this.handlers[k]=fn;}
  walk(){return [this,...this.children.flatMap(n=>n.walk())];}
}
function fixture(){
  const window={};vm.runInNewContext(fs.readFileSync(new URL('../../gateway/process_rule_editor.js',import.meta.url),'utf8'),{
    window,document:{createElement:tag=>new Element(tag)},fetch:()=>assert.fail('Offline editor must not call a provider'),
    localStorage:{setItem:()=>assert.fail('Editor must not persist values')}});
  return window.owgProcessRuleEditor;
}
test('bounded declarations parse values as data with no code or scalar coercion',()=>{
  const {parseCondition:parse}=fixture();
  assert.equal(parse('/a~1b/0','eq','true').expected,true);
  assert.equal(parse('/field','eq','"<script>do_not_execute()</script>"').expected,'<script>do_not_execute()</script>');
  assert.equal(parse('/field','exists','not JSON').expected,null);
  assert.equal(parse('/field','in','[true,1,"1",null]').expected.length,4);
  for(const [path,op,value] of [['$.x','eq','1'],['/bad~2','eq','1'],['/x','eval','1'],['/x','gt','true'],
    ['/x','eq','{}'],['/x','in','[]'],['/x','eq','1e16'],['/x','eq','"\\ud800"'],['/x','eq','NaN']])assert.throws(()=>parse(path,op,value));
});
test('only explicit valid edits change a definition and clear confirmation',()=>{
  const api=fixture(),host=new Element('main'),rule={id:'rule_1',user_confirmed:true};let changes=0;
  api.render(rule,host,()=>changes++);
  const byLabel=label=>host.walk().find(n=>n.attrs['aria-label']===label);
  const button=text=>host.walk().find(n=>n.tag==='button'&&n.textContent===text);
  byLabel('Predicate field rule_1').value='/status';byLabel('Predicate expected value rule_1').value='{}';
  button('Add declared condition for rule_1').handlers.click();assert.equal(changes,0);assert.equal(rule.user_confirmed,true);
  byLabel('Predicate expected value rule_1').value='"approved"';
  button('Add declared condition for rule_1').handlers.click();assert.equal(changes,1);assert.equal(rule.user_confirmed,false);
  assert.equal(rule.predicate.conditions[0].expected,'approved');
  rule.user_confirmed=true;byLabel('Predicate combination rule_1').value='any';byLabel('Predicate combination rule_1').handlers.change();
  assert.equal(rule.predicate.match,'any');assert.equal(rule.user_confirmed,false);
  button('Remove condition 1 for rule_1').handlers.click();assert.equal(rule.predicate,undefined);assert.equal(changes,3);
});
