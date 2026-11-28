"""Reviewer-only package reproducibility, independent pin and member safety."""
from copy import deepcopy
import io
import zipfile

import pytest

from evals import prospective_pilot_cohort as cohort
from evals import pilot_reviewer_package as package


def packet():
    return {"format":"owg.pilot-blinded-batch.v1",
            "entries":[{"review_id":"opaque","prompt":"approved prompt","review_text":"approved response"}],
            "limitations":"Operator-approved free text; blinding is not guaranteed."}


def build(tmp_path,name="reviewer.zip"):
    p=packet();path=tmp_path/name
    receipt=package.build(p,path,expected_packet_sha256=cohort.digest(p),confirmed=True)
    return path,receipt


def test_reproducible_review_only_archive(tmp_path):
    a,r=build(tmp_path);b,s=build(tmp_path,"second.zip")
    assert a.read_bytes()==b.read_bytes() and r==s
    assert package.verify(a,expected_packet_sha256=r["packet_sha256"],
                          expected_package_sha256=r["package_sha256"],confirmed=True)==r
    with zipfile.ZipFile(a) as z:
        assert set(z.namelist())=={*package.ASSETS,package.PACKET,package.MANIFEST,package.README}
        assert cohort._parse(z.read(package.PACKET))==packet()
        assert all(i.external_attr>>16==0o100600 for i in z.infolist())
    assert not r["private_key_included"] and not r["consent_or_blinding_verified"]
    with pytest.raises(FileExistsError):build(tmp_path)


@pytest.mark.parametrize("kind",["wrong_pin","private_key","extra_packet_field","duplicate_review_id","no_confirmation"])
def test_build_refuses_unapproved_or_wrong_scope(tmp_path,kind):
    p=packet();pin=cohort.digest(p);confirmed=True
    if kind=="wrong_pin":pin="0"*64
    elif kind=="private_key":
        p={"format":"owg.pilot-blinded-batch.v1","mapping":{},"batch_sha256":"a"*64};pin=cohort.digest(p)
    elif kind=="extra_packet_field":p["key"]={"private":True};pin=cohort.digest(p)
    elif kind=="duplicate_review_id":p["entries"]*=2;pin=cohort.digest(p)
    else:confirmed=False
    out=tmp_path/"refused.zip"
    with pytest.raises(ValueError):package.build(p,out,expected_packet_sha256=pin,confirmed=confirmed)
    assert not out.exists()


@pytest.mark.parametrize("kind",["extra_member","missing_member","path_traversal","modified_js","modified_packet","modified_manifest","symlink","duplicate_name"])
def test_verify_rejects_rehashed_invalid_archives(tmp_path,kind):
    p=packet();files=package._contents(p,cohort.digest(p))
    if kind=="extra_member":files["private-key.json"]=b"{}"
    elif kind=="missing_member":files.pop(package.README)
    elif kind=="path_traversal":files["../index.html"]=files.pop("index.html")
    elif kind=="modified_js":files["reviewer.js"]+=b"\nalert(1)"
    elif kind=="modified_packet":files[package.PACKET]=b"{}"
    elif kind=="modified_manifest":files[package.MANIFEST]=b"{}"
    data=package._zip(files)
    if kind in ("symlink","duplicate_name"):
        out=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as original,zipfile.ZipFile(out,"w") as new:
            for info in original.infolist():
                body=original.read(info)
                if kind=="symlink" and info.filename=="index.html":info.external_attr=0o120777<<16
                new.writestr(info,body)
            if kind=="duplicate_name":
                with pytest.warns(UserWarning):new.writestr("index.html",b"duplicate")
        data=out.getvalue()
    path=tmp_path/"tampered.zip";path.write_bytes(data)
    with pytest.raises(ValueError):
        package.verify(path,expected_packet_sha256=cohort.digest(p),
                       expected_package_sha256=package._sha(data),confirmed=True)


def test_disagreement_packet_and_cli(tmp_path):
    p=packet()
    p["format"]="owg.pilot-blinded-adjudication.v1"
    p["limitations"]="Operator-reviewed material; independence and blinding are not verified."
    # No disputes is a valid result of independent reviewer agreement.
    p["entries"]=[]
    inp=tmp_path/"packet.json";cohort._write(inp,p)
    out=tmp_path/"reviewer.zip";r=tmp_path/"receipt.json";v=tmp_path/"verify.json"
    assert package.main(["build","--packet",str(inp),"--packet-sha256",cohort.digest(p),
                         "--package",str(out),"--receipt",str(r),"--confirm"])==0
    receipt=cohort._load(r)
    assert package.main(["verify","--packet-sha256",cohort.digest(p),
                        "--package-sha256",receipt["package_sha256"],"--package",str(out),
                        "--receipt",str(v),"--confirm"])==0
    assert cohort._load(v)==receipt
