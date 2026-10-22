"""Owned company admin Gateway, no collector or customer account."""
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from gateway_identity_helpers import make_app
scratch=Path(os.environ['OWG_BROWSER_TEST_DIR'])
app,db=make_app(scratch)
