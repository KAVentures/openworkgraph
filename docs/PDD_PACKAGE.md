# Process documentation with screenshots

**Process documentation with screenshots** (Evidence tab) packages a saved
process revision or an exported proposed design with optional illustrations.
Proposed-design files (`openworkgraph-proposed-process.json`) keep the existing
agent-starter input contract: reviewed, non-executing, without source identifiers.
Saved graphs use the existing catalog revision, rules and topology directly;
their document export does not require a supported agent runtime.

## Steps

1. In the local process catalog, open a saved revision and choose **Add
   screenshots to saved PDD**. Alternatively select its exported JSON, or an
   exported proposed design. Saved JSON identifies a local revision; OWG loads
   that revision rather than trusting the model copy in the file.
2. Optionally add up to three screenshots per step (PNG or JPEG). Each opens
   in a masking editor: drag over names, email addresses, numbers or anything
   else that should not appear, or mask the whole image. Masks are drawn into
   the pixels in the browser before the image leaves the page. Add a caption
   if it helps.
3. Optionally add a narrative. **Copy a prompt for your AI** copies the goal
   and step names, or the saved graph's nodes, transitions and declared rules
   (no screenshots or evidence pointers), so you can ask the AI app you already use
   to draft one. Tick **drafted with an AI tool** if it was; the document then
   labels the narrative as AI-drafted and not reviewed.
4. If you added screenshots, confirm that you reviewed every one and masked
   anything sensitive. Then **Download PDD package**.

Masking is manual. OpenWorkGraph does not detect text in images, so check
each screenshot yourself.

## The package

| File | Contents |
| --- | --- |
| `PDD.md` | The reviewed PDD (purpose, AS-IS, TO-BE with changes, decision points, open questions) plus the narrative and a section per step with its screenshots, then sign-off. |
| `PDD.html` | The same document as one self-contained, script-free page with images embedded, for sharing or printing. Its Content-Security-Policy allows only embedded images. |
| `images/step_NNN_K.png` | Each screenshot, decoded and re-encoded as PNG: EXIF, GPS, text chunks and colour profiles are removed and images are scaled to at most 2,400 px. |
| `manifest.json` | Steps, image sizes and SHA-256 hashes, declared mask counts, whether the narrative is AI-drafted, and `stored_by_owg: false`. |

The server builds the ZIP in memory and returns it; nothing is stored. Limits:
60 screenshots (3 per step), 6 MB per image, 16 megapixels per image, 48 MB
per request, 20,000 characters of narrative. All text you enter is escaped and
treated as data.

`POST /v1/process-design/pdd-package` takes
`{"format": "openworkgraph.pdd-package-request.v1", "design": {...},
"screenshots": [{"step_id": "step_002", "image_base64": "...", "caption": "...",
"masked_regions": 2}], "narrative": {"text": "...", "ai_drafted": true},
"screenshots_reviewed_and_redacted_by_user": true}` and needs the local
dashboard session.

## Exact saved revisions

For a saved graph, the same endpoint takes `process_reference` instead of
`design`: `{"workflow_id":"process_id","revision":1,"content_sha256":"..."}`.
The reference must match the owner-scoped local catalog. A historical revision
remains explicitly selectable after newer revisions are saved; unsaved editor
changes are excluded. A missing/deleted revision or digest mismatch fails, and
the reference is checked again after image processing. Changing sources clears
old screenshots and narrative; delayed loads cannot replace the newer selection.

The package also contains the existing catalog `process.json`, `process.bpmn`
and `process.txt` exports. Decisions, loops, every parallel branch and join,
declared rules and responsibilities remain intact. The node list is an
attachment index, not an execution sequence. `images/NODE_ID_K.png` uses the
actual saved node ID. The manifest pins the same revision/hash and explicitly
grants no policy or execution authority and verifies no outcomes.

These catalog files include the same declared model and evidence pointers as
the regular catalog export; review them before sharing. Building a package
retains no new images, narrative or documents. Previously saved process records
remain in the catalog, and downloaded copies are separate from it. The route
requires the local dashboard session; AI bearer tokens and MCP clients cannot
build or read these packages.
