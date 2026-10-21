"""Synthetic bounded OWG exports for a real external BPMN modeler round trip."""
import json
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from test_process_catalog import model,branched_model
from shared.process_catalog import process_bpmn
from server.process_agent_starter import _review_bpmn

folder=Path(sys.argv[1]);folder.mkdir(exist_ok=True)
graph=branched_model().model_dump()
graph['business_rules'].append({'id':'retry','text':'Additional evidence needs review','user_confirmed':False})
graph['transitions'].append({'source':'decision','target':'b','rule_id':'retry'})
graph['manual_review_conditions']=[{'activity_id':'b','kind':'exception_condition',
    'condition_label':'Evidence missing','response':'pause_for_review'}]
self_loop=json.loads(json.dumps(graph))
self_loop['transitions'][-1]['target']='decision'

def packet(graph):
    return {'title':'Claims review','workflow_id':'claims','revision':7,'content_sha256':'a'*64,'process_model':graph}

fixtures={
 'branches-and-loop':process_bpmn(packet(graph)),
 'self-loop':process_bpmn(packet(self_loop)),
 'linear':process_bpmn(packet(model().model_dump())),
 'manual-starter':_review_bpmn(['Receive','Review','Close'],[{
     'at_step_id':'step_002','condition_label':'Missing approval','response':'pause_for_review'}]),
}
ns={'bpmn':'http://www.omg.org/spec/BPMN/20100524/MODEL'}
manifest=[]
for name,xml in fixtures.items():
    filename=name+'.bpmn';(folder/filename).write_text(xml,encoding='utf-8')
    process=ET.fromstring(xml).find('bpmn:process',ns)
    nodes=[node.attrib['id'] for node in process if node.tag.rsplit('}',1)[-1] in {
        'task','manualTask','exclusiveGateway','startEvent','endEvent'}]
    edges=[{'id':flow.attrib['id'],'source':flow.attrib['sourceRef'],'target':flow.attrib['targetRef']}
           for flow in process.findall('bpmn:sequenceFlow',ns)]
    manifest.append({'name':name,'file':filename,'process_id':process.attrib['id'],'nodes':nodes,'edges':edges})
(folder/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
