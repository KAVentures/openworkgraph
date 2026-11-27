"""Build and verify a reviewer-only offline ZIP with the exact installed UI.

No private key, ledger, source receipt, or original truth file is accepted.
Verification does not extract the archive or execute its contents.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import zipfile

from . import prospective_pilot_cohort as cohort
from . import pilot_blinded_batches as blinded

FORMAT="owg.pilot-reviewer-package.v1"
ASSETS=("index.html","reviewer.css","reviewer.js")
PACKET="reviewer-packet.json"
MANIFEST="manifest.json"
README="README.txt"
INSTRUCTIONS=(
    "OWG offline reviewer\n\n"
    "Extract this authorized package to a private local folder. Open index.html "
    "in a supported browser and select reviewer-packet.json. Review only "
    "approved material. Export saved judgments to an authorized private "
    "destination. Incomplete judgments remain unknown. Do not send the "
    "package or sheet to an unauthorized recipient. No browser storage or "
    "network request is used by the supplied UI. The browser does not verify "
    "identity, consent, blinding, or underlying source truth. Follow your "
    "study's retention and deletion instructions.\n"
).encode("utf-8")
MAX_PACKAGE_BYTES=cohort.MAX_BYTES+1024*1024
MAX_ASSET_BYTES=256*1024


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read_bounded(path,limit):
    with Path(path).open("rb") as handle:
        data=handle.read(limit+1)
    if len(data)>limit:
        raise ValueError("file exceeds package limit")
    return data


def _assets():
    root=Path(__file__).resolve().with_name("pilot_reviewer")
    return {name:_read_bounded(root/name,MAX_ASSET_BYTES) for name in ASSETS}


def _validate_packet(packet,expected_packet_sha256):
    if cohort.pin(expected_packet_sha256)!=cohort.digest(packet):
        raise ValueError("packet differs from independent pin")
    if not isinstance(packet,dict):
        raise ValueError("review packet object required")
    if packet.get("format")==blinded.FORMAT:
        blinded.template(packet)
    elif packet.get("format")==blinded.ADJUDICATION:
        blinded.resolution_template(packet)
    else:
        raise ValueError("only public reviewer or disagreement packets allowed")


def _contents(packet,expected_packet_sha256):
    _validate_packet(packet,expected_packet_sha256)
    data=cohort.canonical(packet)+b"\n"
    if len(data)>cohort.MAX_BYTES:
        raise ValueError("packet exceeds package size limit")
    files={**_assets(),PACKET:data,README:INSTRUCTIONS}
    body={"format":FORMAT,"packet_sha256":expected_packet_sha256,
          "files":{name:{"sha256":_sha(value),"bytes":len(value)} for name,value in sorted(files.items())}}
    files[MANIFEST]=cohort.canonical({**body,"manifest_sha256":cohort.digest(body)})+b"\n"
    return files


def _zip(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,value in sorted(files.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.create_system=3
            info.external_attr=0o100600<<16
            archive.writestr(info,value)
    data=out.getvalue()
    if len(data)>MAX_PACKAGE_BYTES:
        raise ValueError("archive exceeds package size limit")
    return data


def _receipt(data,files,packet_pin):
    manifest=cohort._parse(files[MANIFEST])
    return {"format":FORMAT,"package_sha256":_sha(data),"package_bytes":len(data),
            "packet_sha256":packet_pin,"manifest_sha256":manifest["manifest_sha256"],
            "files":sorted(files),"private_key_included":False,
            "consent_or_blinding_verified":False}


def build(packet,path,*,expected_packet_sha256,confirmed):
    if confirmed is not True:
        raise ValueError("explicit approved-material confirmation required")
    files=_contents(packet,expected_packet_sha256)
    data=_zip(files)
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"wb") as handle:
        handle.write(data)
    return _receipt(data,files,expected_packet_sha256)


def verify(path,*,expected_packet_sha256,expected_package_sha256,confirmed):
    if confirmed is not True:
        raise ValueError("explicit private package verification required")
    data=_read_bounded(path,MAX_PACKAGE_BYTES)
    if _sha(data)!=cohort.pin(expected_package_sha256):
        raise ValueError("archive differs from independent package pin")
    files={}
    expected={*ASSETS,PACKET,MANIFEST,README}
    with zipfile.ZipFile(io.BytesIO(data),"r") as archive:
        infos=archive.infolist()
        if len(infos)!=len(expected) or {i.filename for i in infos}!=expected:
            raise ValueError("unexpected archive members")
        for info in infos:
            limit=cohort.MAX_BYTES if info.filename==PACKET else MAX_ASSET_BYTES
            mode=info.external_attr>>16
            if (info.is_dir() or info.flag_bits&1 or info.file_size>limit or
                    info.compress_type!=zipfile.ZIP_DEFLATED or mode!=0o100600):
                raise ValueError("unexpected archive member metadata")
            with archive.open(info) as member:
                content=member.read(limit+1)
            if len(content)>limit or len(content)!=info.file_size:
                raise ValueError("archive member exceeds limit")
            files[info.filename]=content
    packet=cohort._parse(files[PACKET])
    reference=_contents(packet,expected_packet_sha256)
    if files!=reference:
        raise ValueError("package differs from current installed UI or exact manifest")
    return _receipt(data,files,expected_packet_sha256)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    b=sub.add_parser("build");b.add_argument("--packet",type=Path,required=True)
    v=sub.add_parser("verify");v.add_argument("--package-sha256",required=True)
    for cmd in (b,v):
        cmd.add_argument("--packet-sha256",required=True)
        cmd.add_argument("--package",type=Path,required=True)
        cmd.add_argument("--receipt",type=Path,required=True)
        cmd.add_argument("--confirm",action="store_true",required=True)
    args=parser.parse_args(argv)
    try:
        if args.receipt.exists() or args.receipt.resolve()==args.package.resolve():
            raise ValueError("choose a separate new receipt file")
        if args.command=="build":
            result=build(cohort._load(args.packet),args.package,
                         expected_packet_sha256=args.packet_sha256,confirmed=args.confirm)
        else:
            result=verify(args.package,expected_packet_sha256=args.packet_sha256,
                          expected_package_sha256=args.package_sha256,confirmed=args.confirm)
        cohort._write(args.receipt,result)
    except (ValueError,TypeError,KeyError,OSError,zipfile.BadZipFile,RuntimeError,RecursionError,OverflowError):
        parser.exit(2,"Reviewer package refused or output unavailable; inspect files before retrying.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
