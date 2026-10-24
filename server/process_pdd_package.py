"""Process Definition Document package with reviewed, redacted step screenshots.

Input is the exported human-reviewed design (the same contract as the agent
starter), screenshots the user attached and masked in the browser, and an
optional narrative. Images are decoded, bounded and re-encoded without
metadata. The package is returned for download and nothing is stored.
"""
from __future__ import annotations

import base64
import binascii
from hashlib import sha256
import html
from io import BytesIO
import json
import re
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .process_agent_starter import AgentStarterError, build_agent_starter
from .process_design_documents import build_pdd
from shared.process_catalog import process_bpmn, process_document
from shared.process_model import ProcessModel

FORMAT = "openworkgraph.pdd-package-request.v1"
MAX_REQUEST_BYTES = 48 * 1024 * 1024
MAX_SCREENSHOTS = 60
MAX_PER_STEP = 3
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_PIXELS = 4096 * 4096
MAX_EDGE = 2400
MAX_CAPTION = 300
MAX_NARRATIVE = 20000
_EPOCH = (1980, 1, 1, 0, 0, 0)


class PddPackageError(ValueError):
    """Safe user-facing error that never echoes request content."""


def process_reference(request: Any) -> dict[str, Any] | None:
    """Validate an explicit catalog source without accepting a client model copy."""
    if not isinstance(request, dict) or "process_reference" not in request:
        return None
    ref = request["process_reference"]
    if "design" in request or not isinstance(ref, dict) or set(ref) != {"workflow_id", "revision", "content_sha256"}:
        raise PddPackageError("choose either an exported proposed design or an exact saved process reference")
    if (not isinstance(ref["workflow_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ref["workflow_id"])
            or type(ref["revision"]) is not int or not 1 <= ref["revision"] <= 2**63 - 1
            or not isinstance(ref["content_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", ref["content_sha256"])):
        raise PddPackageError("a saved process requires its exact ID, revision and SHA256")
    return dict(ref)


def _spec(design: Any) -> dict[str, Any]:
    try:
        with ZipFile(BytesIO(build_agent_starter(design))) as archive:
            return json.loads(archive.read("workflow.spec.json"))
    except AgentStarterError as exc:
        raise PddPackageError(str(exc)) from None


def _image(raw: Any) -> tuple[bytes, int, int]:
    from PIL import Image, UnidentifiedImageError

    if not isinstance(raw, str) or len(raw) > MAX_IMAGE_BYTES * 4 // 3 + 8:
        raise PddPackageError("each screenshot must be a base64 image of at most 6 MB")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise PddPackageError("a screenshot is not valid base64") from None
    if not data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
        raise PddPackageError("screenshots must be PNG or JPEG")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(BytesIO(data)) as probe:
            probe.verify()
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise PddPackageError("a screenshot is too large")
            image = image.convert("RGB")
            image.thumbnail((MAX_EDGE, MAX_EDGE))
            out = BytesIO()
            # A fresh PNG carries no EXIF, text chunks, ICC profile or other source metadata.
            image.save(out, format="PNG", optimize=True)
            return out.getvalue(), image.width, image.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise PddPackageError("a screenshot could not be read as an image") from None


def _text(value: Any, limit: int, what: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise PddPackageError(f"{what} must be text of at most {limit} characters")
    return value.replace("\r\n", "\n").strip()


def _md_cell(text: str) -> str:
    return html.escape(text.replace("\n", " ")).replace("|", "\\|")


def build_pdd_package(request: Any, process_packet: dict[str, Any] | None = None) -> bytes:
    if not isinstance(request, dict) or request.get("format") != FORMAT:
        raise PddPackageError(f"request must use format {FORMAT}")
    if set(request) - {"format", "design", "process_reference", "screenshots", "narrative", "screenshots_reviewed_and_redacted_by_user"}:
        raise PddPackageError("unknown request fields")
    reference = process_reference(request)
    artifacts: dict[str, bytes] = {}
    if reference is not None:
        if (not process_packet or any(process_packet.get(k) != v for k, v in reference.items())
                or process_packet.get('format') != 'openworkgraph.versioned-process-export.v1'
                or any(process_packet.get(k) is not False for k in
                       ('organization_policy', 'execution_authorization', 'business_outcomes_verified'))):
            raise PddPackageError("the saved process reference no longer matches this revision")
        try:
            model = ProcessModel.model_validate(process_packet["process_model"])
            original = process_document(process_packet)
            artifacts = {"process.json": (json.dumps(process_packet, indent=2, allow_nan=False) + "\n").encode(),
                         "process.bpmn": process_bpmn(process_packet).encode(), "process.txt": original.encode()}
        except (ValueError, KeyError, TypeError):
            raise PddPackageError("the saved process cannot be exported as a document") from None
        steps = [{"id": node.id, "activity": node.label} for node in model.activities]
        # Preserve the real graph as text and BPMN, never a flattened runtime route.
        pdd = ["# Saved process design (draft)", "",
               "Node order below is an attachment index, not an execution sequence.", "",
               "```text", original.replace("```", "'''"), "```", "",
               "## 7. Sign-off", "", "Review against current business rules and source systems before use.",
               "This document does not approve policy, authorize execution or verify outcomes."]
    else:
        spec = _spec(request.get("design"))
        steps = spec["to_be"]["steps"]
        pdd = build_pdd(spec).rstrip("\n").split("\n")
    shots = request.get("screenshots") or []
    if not isinstance(shots, list) or len(shots) > MAX_SCREENSHOTS:
        raise PddPackageError(f"at most {MAX_SCREENSHOTS} screenshots")
    if shots and request.get("screenshots_reviewed_and_redacted_by_user") is not True:
        raise PddPackageError("confirm that every screenshot was reviewed and redacted before including it")
    images: list[dict[str, Any]] = []
    per_step: dict[str, int] = {}
    for item in shots:
        if not isinstance(item, dict) or set(item) - {"step_id", "image_base64", "caption", "masked_regions"}:
            raise PddPackageError("each screenshot has a step_id, image_base64, caption and masked_regions")
        step_id = item.get("step_id")
        if not isinstance(step_id, str) or step_id not in {s["id"] for s in steps}:
            raise PddPackageError("a screenshot refers to an unknown step")
        per_step[step_id] = per_step.get(step_id, 0) + 1
        if per_step[step_id] > MAX_PER_STEP:
            raise PddPackageError(f"at most {MAX_PER_STEP} screenshots per step")
        masked = item.get("masked_regions", 0)
        if isinstance(masked, bool) or not isinstance(masked, int) or not 0 <= masked <= 500:
            raise PddPackageError("masked_regions must be a small whole number")
        png, width, height = _image(item.get("image_base64"))
        name = f"images/{step_id}_{per_step[step_id]}.png"
        images.append({"step_id": step_id, "path": name, "png": png, "width": width, "height": height,
                       "caption": _text(item.get("caption"), MAX_CAPTION, "caption"), "masked_regions": masked,
                       "sha256": sha256(png).hexdigest()})
    narrative = request.get("narrative") or {}
    if not isinstance(narrative, dict) or set(narrative) - {"text", "ai_drafted"}:
        raise PddPackageError("narrative has text and ai_drafted")
    story = _text(narrative.get("text"), MAX_NARRATIVE, "narrative")
    ai_drafted = narrative.get("ai_drafted") is True

    sign_off = pdd.index("## 7. Sign-off")
    extra: list[str] = []
    if story:
        extra += ["## 8. Narrative (draft)", ""]
        extra += (["> **AI-drafted text, not reviewed or verified.** Check every statement against the "
                   "tables above and the live process before relying on it.", ""] if ai_drafted else
                  ["> Reviewer-written description. It does not change the reviewed steps above.", ""])
        extra += ["```text", story.replace("```", "'''"), "```", ""]
    if images:
        extra += ["## 9. Step screenshots", "",
                  "Screenshots were attached and masked by the reviewer; masking is manual and may miss "
                  "information. They illustrate observed screens, not required behavior.", ""]
        by_id = {s["id"]: s["activity"] for s in steps}
        for image in images:
            caption = image["caption"] or by_id[image["step_id"]]
            extra += [f"### {image['step_id']}: {_md_cell(by_id[image['step_id']])}", "",
                      f"![{_md_cell(caption)}]({image['path']})", "",
                      f"*{_md_cell(caption)}* (masked regions: {image['masked_regions']})", ""]
    document = "\n".join(pdd[:sign_off] + extra + pdd[sign_off:]).replace("## 7. Sign-off", "## 10. Sign-off") + "\n"
    manifest = {
        "format": "openworkgraph.pdd-package.v1",
        "process_steps": len(steps),
        "screenshots": [{"step_id": i["step_id"], "path": i["path"], "width": i["width"], "height": i["height"],
                         "sha256": i["sha256"], "masked_regions_declared": i["masked_regions"]} for i in images],
        "screenshots_reviewed_and_redacted_by_user": bool(images),
        "image_metadata_removed": True,
        "narrative_included": bool(story),
        "narrative_ai_drafted": ai_drafted and bool(story),
        "approved_business_policy": False,
        "stored_by_owg": False,
    }
    if reference is not None:
        manifest.update(process_reference=reference, source="saved_local_process_revision",
                        execution_authorized=False, business_outcomes_verified=False)
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        def put(name: str, data: bytes) -> None:
            info = ZipInfo(name, date_time=_EPOCH)
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
        put("PDD.md", document.encode("utf-8"))
        put("PDD.html", _html(document, images).encode("utf-8"))
        put("manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        for name, data in artifacts.items():
            put(name, data)
        for image in images:
            put(image["path"], image["png"])
    return out.getvalue()


def _html(markdown: str, images: list[dict[str, Any]]) -> str:
    """A self-contained, script-free HTML rendering for sharing and printing."""
    embedded = {i["path"]: "data:image/png;base64," + base64.b64encode(i["png"]).decode() for i in images}
    body: list[str] = []
    in_code = in_table = False
    for line in markdown.split("\n"):
        if line.startswith("```"):
            body.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            body.append(html.escape(line))
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split(" | ")]
            if set("".join(cells)) <= {"-", "|", " "}:
                continue
            if not in_table:
                body.append("<table>")
                in_table = True
            body.append("<tr>" + "".join(f"<td>{html.escape(html.unescape(c))}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            body.append("</table>")
            in_table = False
        if line.startswith("!["):
            alt, _, rest = line[2:].partition("](")
            src = embedded.get(rest.rstrip(")"))
            if src:
                body.append(f'<p><img src="{src}" alt="{html.escape(html.unescape(alt))}"></p>')
            continue
        level = len(line) - len(line.lstrip("#"))
        if level and line[level:level + 1] == " ":
            body.append(f"<h{level}>{html.escape(html.unescape(line[level + 1:]))}</h{level}>")
        elif line.startswith("> "):
            body.append(f"<blockquote>{html.escape(line[2:].replace('**', ''))}</blockquote>")
        elif line.startswith("- "):
            body.append(f"<p>• {html.escape(line[2:])}</p>")
        elif line.strip():
            body.append(f"<p>{html.escape(html.unescape(line).replace('*', ''))}</p>")
    if in_table:
        body.append("</table>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">"
            "<title>Process Definition Document (draft)</title><style>body{font-family:system-ui,sans-serif;max-width:960px;"
            "margin:24px auto;padding:0 16px;line-height:1.45;overflow-wrap:anywhere}table{border-collapse:collapse;margin:8px 0;max-width:100%;display:block;overflow-x:auto}td{border:1px solid #ccc;"
            "padding:4px 8px}img{max-width:100%;border:1px solid #ccc}pre{white-space:pre-wrap;background:#f5f5f5;padding:8px}"
            "blockquote{border-left:4px solid #b80;margin:8px 0;padding:4px 12px;background:#fff8e6}</style></head><body>"
            + "\n".join(body) + "</body></html>\n")
