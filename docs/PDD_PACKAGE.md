# Process documentation with screenshots

**Process documentation with screenshots** (Evidence tab) turns a design you
exported from the process designer (`openworkgraph-proposed-process.json`)
into a Process Definition Document package you can share. It is the same
human-reviewed contract as the agent starter: the design must be marked as
reviewed and must not authorize actions or include source identifiers.

## Steps

1. Choose the exported design. The steps of the proposed (TO-BE) process
   appear.
2. Optionally add up to three screenshots per step (PNG or JPEG). Each opens
   in a masking editor: drag over names, email addresses, numbers or anything
   else that should not appear, or mask the whole image. Masks are drawn into
   the pixels in the browser before the image leaves the page. Add a caption
   if it helps.
3. Optionally add a narrative. **Copy a prompt for your AI** copies the goal
   and step names (no screenshots) so you can ask the AI app you already use
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
