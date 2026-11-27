"""Synthetic browser fixture only: create quality report from a portable file."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from shared.connector_bundle_import import decode_value
from shared.connector_quality_verification import Policy,SourceDeclaration,calculate

package=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
policy=Policy(min_events=1,min_cases=1)
declarations=[SourceDeclaration.model_validate({**pin,"policy":policy,"policy_origin":"default"})
              for pin in package["pins"]]
report=calculate(package["organization_id"],policy,declarations,decode_value(package["source_bundle"]))
assert report["all_selected_sources_pass"]
Path(sys.argv[2]).write_text(json.dumps(report,ensure_ascii=True),encoding="utf-8")
