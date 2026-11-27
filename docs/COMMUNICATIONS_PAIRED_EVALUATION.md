# Paired keyword and learned intent evaluation (R22)

On the Communications card, enable retained review examples, label examples
and train a current learned model. Choose **Compare learned and keyword
classifiers**. Both methods evaluate the exact same reviewer-labelled held-out
messages. No example is relabelled, used to fit a model, or used to choose
thresholds by this operation.

## Comparison basis

The existing keyword classifier emits a single primary intent, an ambiguous
tie or no match. For a fair explicit multi-label comparison, project its
unique winner to present and other intents to absent; tied highest-scoring
intents abstain, and no match is all absent. The learned model uses its
existing fixed present/absent/uncertain thresholds. Untrained declared intents
abstain rather than silently predicting absence.

For each intent, report true positives, false positives, true negatives, false
negatives, positive and negative abstentions; accuracy over all examples;
accuracy among decided examples; decision coverage; precision; recall over all
reviewed positives; and F1 including positive abstentions in the recall
denominator. Empty denominators are unknown. The micro result pools intent
decisions, not messages. Strict message correctness requires all declared
intents to be correctly decided. A wrong decision makes a message incorrect;
otherwise any abstention prevents complete correctness.

The paired 3-by-3 counts show correct, incorrect and abstained results for
both methods on the same examples. Learned-only and keyword-only correct
counts produce the paired correctness difference and a descriptive exact
two-sided McNemar p value. These p values are unadjusted for multiple testing;
the comparison does not establish independent sampling or justify deployment.
Never optimize taxonomy terms or learned thresholds against these held-out
results and then claim they remain independent validation.

## Overlap and language

Both full holdout and the subset without detected lexical overlap against
training data are returned. If the lexical audit reaches its work bound,
screened results are unavailable, not partially checked counts. No detected
lexical overlap does not prove thread, semantic or external independence.
The report shows up to 30 largest language groups, with exact coverage counts
and all languages represented in the aggregate. Languages are reviewer labels,
not independently identified.

## Local endpoint and lifetime

`GET /v1/communications/review/paired-evaluation` requires the local dashboard
session and enabled review examples plus a fresh model. It checks the retained
training basis, model digest and complete keyword taxonomy digest again before
returning. Changes, expiry or revoked review consent prevent a current report.
The report is no-store and does not persist message text, example IDs, or
per-example predictions. Explicit JSON export includes only aggregate results,
digests, labels, and criteria. No external service is called.

Bounds: up to 2,000 total labelled examples, 40 intents, 2 million held-out
characters, and the existing lexical overlap work bound. Invalid or retired
labels require review. No model or keyword configuration is modified. Synthetic
tests cover ties, unknown intents, overlap screening, paired arithmetic,
missing denominators and work limits. Real independent multilingual quality
remains to be demonstrated.

## Learned probability reliability

The same fixed full and overlap-screened holdouts and reviewer language groups
also report per-trained-intent probability quality: Brier score, clipped log
loss, 10-bin expected calibration error, tie-aware AUROC and non-interpolated
average precision. The constant baseline is the intent's training prevalence
(not held-out prevalence). Brier skill is one minus model Brier divided by
baseline Brier; a negative value indicates worse scores than that baseline.
The baseline is evaluated on exactly the model-scored rows. No score is imputed
for missing or untrained intents; coverage is disclosed. Empty or single-class
ranking metrics and zero-denominator skill are unavailable. Macro metrics use
only trained intents with at least five scored positives and negatives and no
missing scores, with per-metric support reported. Ten fixed probability bins
include counts and positive labels; mean probabilities and rates are hidden
for bins with fewer than five observations. Bin boundaries are lower-inclusive
and upper-exclusive, except 1.0 belongs to the last bin. The log-loss clip is
1e-15. These diagnostics do not tune thresholds, certify calibration, establish
external quality or authorize deployment. The existing consent and model-basis
checks apply, and the aggregate JSON export includes the same metrics.
