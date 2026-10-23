# Communications mining

Find out what customers and colleagues write about, without keeping what they
wrote. **Communications** (Evidence tab) analyses an email or ticket export
you choose:

| Format | Notes |
| --- | --- |
| mbox | A mailbox export (Gmail Takeout, Thunderbird, Apple Mail). Threads come from `References`/`In-Reply-To`; replies are recognised by **your own email domains**. |
| .eml | A single message. |
| CSV | A ticket export. Choose the text columns (for example subject and body) and, optionally, time (timezone-qualified), thread/ticket ID, direction (`inbound`/`outbound`, `customer`/`agent`) and channel. |

## What happens to the export

1. You tick **Analyse this export on this computer**. The file goes only to the
   local OWG server, which writes it to a private temporary file
   (owner-only permissions) and deletes it at the end of the same request.
2. Quoted reply text is removed. Personal identity numbers, IBANs, payment
   cards and secrets are redacted with the same redactor as captured evidence.
3. Each inbound message is classified, entity types are counted and the
   message is discarded. Outbound replies are counted but not classified.
4. Only aggregates are returned. No message text, entity value or sender
   address is stored or returned. Sender domains are listed only when at
   least 5 messages share them, and phrases only when at least 5 unclassified
   messages share them.

Ticking **Also store an intent-only event log** additionally saves, on this
computer, one event per dated message in a thread: a hash of the thread
ID, the intent, the time and the direction. It appears under **Large event logs**,
where variants, KPIs, cohorts, simulation and conformance apply to the
conversation flow. Delete it there like any event log.

## Results

- **Intents** with message counts and shares among classified messages,
  plus how many were **ambiguous** (tied between intents, which are listed)
  or **unclassified**.
- **Entity types mentioned**: email addresses, URLs, phone numbers, order,
  invoice and ticket references, money amounts, dates, and redacted
  personal identity numbers, IBANs, payment cards and secrets. Each type has
  the number of messages and mentions; overlapping references are counted
  once.
- **Threads**: number of threads, median messages per thread, threads where
  the customer wrote more than once, and the median first response time
  (first inbound to first later outbound message).
- **Weekly volume** with the most common intent per week.
- **Frequent phrases in unclassified messages**: word pairs to consider
  adding to the taxonomy.

## Taxonomy

Intents come from a keyword taxonomy you can edit under **Intent taxonomy**,
one intent per line:

```text
Refund request: refund, money back, återbetalning
Cancellation: cancel, terminate, avsluta, säga upp
```

The default covers refunds, cancellations, billing, order and delivery
status, complaints, technical issues, account access, changes of details,
scheduling and information requests, in English and Swedish. Terms match
whole words in order; a matched term scores its number of words, so specific
phrases outweigh single words. The highest score wins and a tie is
ambiguous. The taxonomy is saved on this computer
(`communications_taxonomy.json`) and can be restored to the default.

Intents are keyword matches, not verified reasons for contact. Check
ambiguous and unclassified volumes before relying on the shares, and adjust
the taxonomy for your vocabulary.

## Limits

At most 200 MB and 50,000 messages per analysis (roughly 2,500 messages per
second on a laptop), 40 intents and 60 terms per intent. All routes need the
local dashboard session; AI bearer tokens and MCP clients cannot run an
analysis or change the taxonomy.

| Route | Purpose |
| --- | --- |
| `POST /v1/communications/analyze?format=mbox\|eml\|csv` | Analyse the request body. Requires `X-OWG-Local-Processing-Confirmed: true`; `save_event_log=true` also requires `X-OWG-Local-Storage-Confirmed: true`. CSV takes `text_columns` (comma-separated), `time_column`, `thread_column`, `direction_column`, `channel_column` and `delimiter`; email formats take `own_domains`. |
| `GET /v1/communications/taxonomy` | The saved or default taxonomy. |
| `PUT /v1/communications/taxonomy` | Save a taxonomy (`openworkgraph.communications-taxonomy.v1`). |
| `POST /v1/communications/taxonomy/reset` | Restore the default taxonomy. |
