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

## Learned intents from reviewed examples

Keyword taxonomies are transparent, but they miss phrasings and languages nobody
wrote terms for. A local model can learn from messages you label. This is
separate from the analysis above, because it is the one place where message text
is kept.

**Review examples (off by default).** Turn them on under *Review examples and
learned model* by ticking the acknowledgement and choosing a retention of 1–90 days.
Then:

- When analysing an export, tick *Keep the least certain inbound messages* to keep
  up to 200 texts. With a model, these are the messages it is least sure about;
  without one, ambiguous and unclassified keyword matches come first. You can
  also add texts through the API.
- Kept texts are redacted before storage: the standard identifier redaction plus
  email addresses, URLs, phone numbers and Luhn-valid card numbers. Duplicates
  are skipped, and at most 2,000 examples are kept.
- Each example expires after the retention. Shortening the retention applies to
  examples already kept. Turning review examples off deletes every example and the
  model.
- Label each example with zero or more intents from the current taxonomy, plus an
  optional language code. No ticks means "no intent", and those examples matter:
  they teach the model what is not an intent.
- One in five examples, chosen by a hash of its ID, is held out. Held-out examples
  are never used for training and only measure quality.

Kept examples live in `communications_review.json` in the local data folder (mode
0600). A local backup copies them like other data. Expired examples are purged
again when a restored store is read, but the backup file itself keeps them until
you delete it.

**The model.** *Train intent model* fits one classifier per intent, in pure
Python and on this computer:

- Features are hashed words, word pairs and character 3–5-grams, so it works across
  languages without a tokenizer per language.
- Each classifier is a class-weighted logistic regression.
- Each intent has its own Platt calibration, fit on 5-fold out-of-fold scores.
- Intents with fewer than 5 positive or 5 negative training examples are skipped
  and listed.

The model expires with the retention and is deleted with the examples.

For each message and intent the model gives a probability and a decision:

| Decision | Calibrated probability |
|---|---|
| `present` | at least 0.7 |
| `absent` | at most 0.3 |
| `uncertain` | between the two |

A message can have several intents or none.

**Quality.** Each training run evaluates on the held-out examples, overall and for
each language:

- precision and recall with Wilson 95% intervals, and F1;
- Brier score and expected calibration error;
- uncertain rate;
- whether there were at least 5 positive and 5 negative examples.

The macro F1 covers only intents with enough support. These measure agreement with
your own labels, on your own messages; they are not verified reasons for contact.

**Learned analysis.** Choose *Learned model* under *Intents from*.

- The report adds `learned_intents`: present and uncertain counts, and the share
  of inbound messages, for each intent.
- `model_reference` names the model's SHA-256 and training time.
- The single-label views (volumes, event log) use the one present intent,
  `multiple intents`, `uncertain` or `unclassified`.
- Outbound replies are still counted, not classified.
- No message text appears in any report. Message text is always data, never
  instructions.

Routes require the local dashboard session; the API token is refused.

| Route | Purpose |
|---|---|
| `GET`/`PUT /v1/communications/review/settings` | Settings; turning on needs `acknowledgement_version` |
| `GET /v1/communications/review/examples?status=all\|labelled\|unlabelled` | List kept examples |
| `POST /v1/communications/review/examples` | Add examples; needs `X-OWG-Review-Examples-Confirmed: true` |
| `POST /v1/communications/review/label` | Label one example |
| `POST /v1/communications/review/delete` | Delete examples |
| `POST /v1/communications/review/train` | Train the model |
| `GET /v1/communications/review/model` | Model status and evaluation |
| `POST /v1/communications/review/model/delete` | Delete the model |

`POST /v1/communications/analyze` accepts `classifier=learned` and
`review_sample=1–200`; sampling also needs the confirmation header.
