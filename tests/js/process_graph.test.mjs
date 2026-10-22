import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../gateway/process_graph.js',import.meta.url),'utf8');
class Element {
  constructor(tag){this.tag=tag;this.attrs={};this.children=[];this.handlers={};this.textContent='';this.style={};}
  setAttribute(key,value){this.attrs[key]=value;}
  appendChild(value){this.children.push(value);return value;}
  append(...values){this.children.push(...values);}
  addEventListener(name,handler){this.handlers[name]=handler;}
  set innerHTML(value){throw new Error('Graph data must never become HTML');}
  walk(){return [this,...this.children.flatMap(n=>n.walk())];}
}
function fixture(){
 const window={};const document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)};
 vm.runInNewContext(source,{window,document,fetch:()=>{throw new Error('Graph must remain offline');}});
 return window.owgProcessGraph;
}
const model=()=>({activities:[{id:'receive',label:'Receive',kind:'activity'},{id:'decide',label:'Choose',kind:'decision'},
 {id:'accept',label:'Accept',kind:'end'},{id:'reject',label:'Reject',kind:'end'}],entry_activity_id:'receive',
 transitions:[{source:'receive',target:'decide'},{source:'decide',target:'accept',rule_id:'yes'},
 {source:'decide',target:'reject',rule_id:'no'},{source:'decide',target:'receive',rule_id:'retry'},
 {source:'decide',target:'decide',rule_id:'clarify'}]});

test('shared graph preserves branches, back edges and self-loops with bounded finite geometry',()=>{
 const graph=fixture().layout(model());
 assert.equal(graph.positions.size,4);assert.equal(graph.edges.length,5);
 assert.equal(graph.positions.get('receive').level,0);
 assert.equal(graph.positions.get('accept').level,graph.positions.get('reject').level);
 assert.notEqual(graph.positions.get('accept').y,graph.positions.get('reject').y);
 const self=graph.edges.find(e=>e.source===e.target);
 assert(self.points.length>=4);
 for(const edge of graph.edges)assert(edge.points.flat().every(Number.isFinite));
 assert(Number.isFinite(graph.width)&&Number.isFinite(graph.height));
 const bad=model();bad.activities=Array.from({length:41},(_,i)=>({id:String(i)}));
 assert.throws(()=>fixture().layout(bad),/bounded process graph/);
});

test('incomplete edits remain visible and graph labels stay data; keyboard selection uses exact activity identity',()=>{
 const graph=fixture(),input=model();input.activities[1].label='</text><script>sideEffects()</script>';
 input.activities.push({id:'new_activity',label:'New activity',kind:'activity'});
 input.transitions.push({source:'missing',target:'accept'});
 const selected=[];const drawing=graph.draw(input,a=>selected.push(a.id));
 const nodes=drawing.walk().filter(n=>n.tag==='g');
 assert.equal(nodes.length,5);
 assert(drawing.walk().some(n=>n.tag==='title'&&n.textContent.includes('<script>')));
 const node=nodes.find(n=>n.attrs['aria-label']==='Select activity decide');
 node.handlers.click();let prevented=false;
 node.handlers.keydown({key:'Enter',preventDefault(){prevented=true;}});
 assert.deepEqual(selected,['decide','decide']);assert.equal(prevented,true);
 assert.equal(input.activities[1].label,'</text><script>sideEffects()</script>');
});

test('declared responsibilities render as inert labels and preserve node identity',()=>{
 const input=model();input.activities[0].responsibility_label='<img src=x onerror=sideEffects()>';
 const chosen=[];const drawing=fixture().draw(input,activity=>chosen.push(activity.id));
 assert(drawing.walk().some(node=>node.tag==='title'&&node.textContent.includes('Declared responsibility: <img')));
 assert(drawing.walk().some(node=>node.tag==='text'&&node.textContent.startsWith('Responsibility: ')));
 drawing.walk().find(node=>node.attrs['aria-label']==='Select activity receive').handlers.click();
 assert.deepEqual(chosen,['receive']);
});


test('observed transition map preserves forks, joins, loops, counts and source labels without changing design semantics',()=>{
 const graph=fixture();
 const report={activities:[{activity:'Open',event_count:3,case_count:3},
   {activity:'Review',event_count:3,case_count:2},{activity:'Close',event_count:3,case_count:3}],
   cases:[{path:['Open','Close']}],transitions:[
   {from:'Open',to:'Review',transition_count:2,case_count:2,median_elapsed_gap_seconds:60},
   {from:'Open',to:'Close',transition_count:1,case_count:1,median_elapsed_gap_seconds:120},
   {from:'Review',to:'Review',transition_count:1,case_count:1,median_elapsed_gap_seconds:30},
   {from:'Review',to:'Close',transition_count:2,case_count:2,median_elapsed_gap_seconds:60}]};
 const selected=[],drawing=graph.drawObserved(report,activity=>selected.push(activity.label));
 const svg=drawing.walk().find(node=>node.tag==='svg');
 assert.match(svg.attrs['aria-label'],/not causal dependencies/);
 assert.equal(drawing.walk().filter(node=>node.tag==='path').length,8);
 assert.equal(drawing.walk().filter(node=>node.tag==='g').length,3);
 assert(drawing.walk().some(node=>node.tag==='title'&&node.textContent.includes('median elapsed gap 60')));
 const review=drawing.walk().find(node=>node.attrs['aria-label']==='Inspect observed activity Review');
 review.handlers.keydown({key:'Enter',preventDefault(){}});
 assert.deepEqual(selected,['Review']);
 assert(drawing.walk().some(node=>node.tag==='text'&&node.textContent==='3 events · 2 cases'));
 assert.equal(report.activities[1].activity,'Review');
 const design=graph.draw(model(),()=>{});
 assert.match(design.walk().find(node=>node.tag==='svg').attrs['aria-label'],/Proposed process graph/);
 assert.throws(()=>graph.drawObserved({...report,activities:[...report.activities,report.activities[0]]}),/unique/);
 assert.throws(()=>graph.drawObserved({...report,transitions:[{from:'unknown',to:'Close'}]}),/endpoints/);
});

test('dense observed maps stay bounded and finite while keeping their own larger read-only limits',()=>{
 const graph=fixture();const activities=Array.from({length:64},(_,i)=>({activity:'Activity '+i,event_count:64,case_count:64}));
 const transitions=activities.flatMap(a=>activities.map(b=>({from:a.activity,to:b.activity,transition_count:1,case_count:1,median_elapsed_gap_seconds:0})));
 const drawing=graph.drawObserved({activities,transitions},()=>{});
 assert.equal(drawing.walk().filter(node=>node.tag==='g').length,64);
 assert.equal(drawing.walk().filter(node=>node.tag==='path').length,8192);
 const svg=drawing.walk().find(node=>node.tag==='svg');
 assert(Number.isFinite(Number(svg.attrs.width))&&Number.isFinite(Number(svg.attrs.height)));
 assert(Number(svg.attrs.height)<10000);
 assert.throws(()=>graph.drawObserved({activities:[...activities,{activity:'Too many'}],transitions:[]}),/bounded/);
 assert.throws(()=>graph.drawObserved({activities,transitions:[...transitions,transitions[0]]}),/bounded/);
});

test('zoom and fit preserve finite geometry, exact identity and original process data without I/O',()=>{
 const graph=fixture(),input=model(),before=JSON.stringify(input),selected=[];
 const drawing=graph.draw(input,activity=>selected.push(activity.id));
 const find=label=>drawing.walk().find(node=>node.tag==='button'&&node.textContent===label);
 const svg=drawing.walk().find(node=>node.tag==='svg'),viewport=drawing.walk().find(node=>node.className==='owg-graph-viewport');
 const width=Number(svg.attrs.width);assert.equal(viewport.attrs.tabindex,'0');
 assert.match(viewport.attrs['aria-label'],/arrow keys to pan/);
 find('Zoom in').handlers.click();assert(Number(svg.attrs.width)>width);
 find('Actual size').handlers.click();assert.equal(Number(svg.attrs.width),width);
 viewport.clientWidth=180;find('Fit graph').handlers.click();assert(Number(svg.attrs.width)<=156.000001);
 assert.equal(viewport.scrollLeft,0);assert.equal(viewport.scrollTop,0);
 for(let i=0;i<100;i++)find('Zoom out').handlers.click();
 assert.equal(find('Zoom out').disabled,true);assert.equal(Number(svg.attrs.width),width*.01);
 for(let i=0;i<100;i++)find('Zoom in').handlers.click();
 assert.equal(find('Zoom in').disabled,true);assert.equal(Number(svg.attrs.width),width*3);
 assert(Number.isFinite(Number(svg.attrs.height)));
 drawing.walk().find(node=>node.attrs['aria-label']==='Select activity accept').handlers.click();
 assert.deepEqual(selected,['accept']);assert.equal(JSON.stringify(input),before);
 assert.equal(drawing.walk().filter(node=>node.tag==='select').length,0);
});

test('observed emphasis uses numerical counts and gaps, preserves zero/unknown and hides no data',()=>{
 const input={activities:[{activity:'Open',event_count:20,case_count:10},{activity:'Close',event_count:10,case_count:10}],
  transitions:[{from:'Open',to:'Close',transition_count:10,case_count:10,median_elapsed_gap_seconds:120},
   {from:'Open',to:'Open',transition_count:2,case_count:2,median_elapsed_gap_seconds:0},
   {from:'Close',to:'Open',transition_count:null,case_count:null,median_elapsed_gap_seconds:null}]};
 const before=JSON.stringify(input),drawing=fixture().drawObserved(input,()=>{});
 const select=drawing.walk().find(node=>node.tag==='select');
 const paths=drawing.walk().filter(node=>node.tag==='path');
 const legend=drawing.walk().find(node=>node.className==='muted owg-graph-legend');
 assert.equal(paths.length,6);assert(paths.every(node=>node.attrs['stroke-width']===2));
 select.value='frequency';select.handlers.change();
 assert(paths[0].attrs['stroke-width']>paths[2].attrs['stroke-width']);
 assert.equal(paths[4].attrs.stroke,'#8a8a8a');
 assert.match(legend.textContent,/supplied event logs/);
 select.value='elapsed';select.handlers.change();
 assert.equal(paths[0].attrs['stroke-width'],8);assert.equal(paths[2].attrs['stroke-width'],2);
 assert.notEqual(paths[2].attrs.stroke,paths[4].attrs.stroke);
 assert.equal(paths[4].attrs.stroke,'#8a8a8a');assert.match(legend.textContent,/not verified waiting times/);
 assert(drawing.walk().some(node=>node.tag==='title'&&node.textContent.includes('gap unavailable')));
 select.value='none';select.handlers.change();assert(paths.every(node=>node.attrs['stroke-width']===2));
 assert.equal(drawing.walk().filter(node=>node.tag==='g').length,2);
 assert.equal(JSON.stringify(input),before);
});

test('candidate projections offer frequency only and invalid numerical values stay unknown',()=>{
 const input=model();input.activities.forEach(node=>{node.observed_count=1;node.observed_summary='1 projected occurrence';});
 input.transitions.forEach((edge,index)=>{edge.observed_count=index?NaN:3;edge.observed_median_gap_seconds=index?undefined:Infinity;});
 const drawing=fixture().draw(input,()=>{},{observed:true,projected:true});
 const select=drawing.walk().find(node=>node.tag==='select');
 assert.deepEqual(select.children.map(node=>node.value),['none','frequency']);
 select.value='frequency';select.handlers.change();
 assert.match(drawing.walk().find(node=>node.className==='muted owg-graph-legend').textContent,/normalized candidate projections/);
 assert.equal(drawing.walk().filter(node=>node.tag==='path')[2].attrs.stroke,'#8a8a8a');
});
