# Independent multi-reviewer product-value pilot (R25)

The existing blinded three-arm product-value pilot can optionally be reviewed
by two independent reviewers, with a third reviewer resolving disagreements.
This is an offline research/acceptance workflow, not a new customer runtime
feature or a claim of demonstrated product benefit.

## Prepare and review

First create the existing frozen packet and private key using
[BLINDED_PRODUCT_REVIEW.md](BLINDED_PRODUCT_REVIEW.md). Give the same public
packet and blank review template separately to two reviewers; never share
the private key, truth, or arm-labeled traces. Each reviewer completes every
entry independently, without access to the other's ratings. Retain both
original completed JSON arrays privately and preserve their exact contents.
Use stable distinct pseudonymous reviewer IDs and maintain an authorized
private identity and conflict-of-interest record.

Prepare the adjudication package:

    python -m evals.multi_reviewer_product_review prepare-adjudication \
      --episodes /private/truth.jsonl --packet /private/packet.json \
      --key /private/key.json --first-review /private/first.json \
      --second-review /private/second.json \
      --review-dir /private/adjudicator-packet \
      --private-dir /private/adjudication-basis \
      --confirm-share-disagreements

Output directories must be separate, non-nested and new. The private directory
contains agreement.json and basis.json with digests of all frozen inputs.
The public adjudication directory contains only the differing entries, the
approved original neutral material, the two anonymous ratings, differing
field names and a blank resolution template. Inspect this package before
sharing: selected evidence IDs and original free text must already be
approved for this recipient and condition-neutral. No automatic redaction
or reviewer identity verification is performed.

For every differing entry, a third reviewer assesses the original evidence
and supplies all six review fields plus a substantive resolution reason.
The third review can disagree with both original ratings. The process never
assumes that the first reviewer is correct or that majority voting establishes
truth. When there are no disagreements, use an empty JSON array of resolutions;
no third reviewer is required.

## Score

    python -m evals.multi_reviewer_product_review score-adjudicated \
      --episodes /private/truth.jsonl --packet /private/packet.json \
      --key /private/key.json --first-review /private/first.json \
      --second-review /private/second.json \
      --adjudication-packet /private/adjudicator-packet/disagreements.json \
      --basis /private/adjudication-basis/basis.json \
      --resolutions /private/resolutions.json --out /private/result.json

All three arms retain identical episode coverage and paired model/source
metadata. Each original sheet must cover every entry and identify one
distinct reviewer. The third sheet covers exactly all disagreements and
must identify a reviewer distinct from both original reviewers and all
declared truth authors. Identity strings are normalized for comparison;
this prevents simple spelling-case/whitespace aliases, not deliberate
misrepresentation. Changed frozen inputs, duplicate, extra or missing
ratings, invalid judgments and blank reasons are refused. Outputs never
overwrite existing files.

The agreement report contains exact agreement by field, binary contingency
counts and Cohen's kappa (null for degenerate marginals), count mean absolute
differences, and evidence-set Jaccard agreement (both-empty sets equal one).
Evidence selection order is ignored; actual ID membership is not. These
statistics describe agreement across blinded review entries, not independent
episode-level sampling or clinical/research validity. Individual reviewer
product decisions are retained beside the adjudicated canonical report to
make sensitivity to reviewer judgments visible. No additional threshold
changes the existing canonical scoring or paired statistics.

Limit: 1–500 frozen episodes, three entries per episode, 32 MiB per CLI
input file. A frozen basis detects accidental changes relative to a trusted
private file, not malicious modification of every input and digest.
The script performs no network, source reads or model calls. A synthetic
study is still pilot-only; the original requirement for at least 20
declared real reviewed episodes remains, and neither that requirement nor
reviewer agreement independently proves a representative or causal result.

Regression command:

    python -m unittest discover -s tests -p test_multi_reviewer_product_review.py

Related: [#370 R25](https://github.com/KAVentures/openworkgraph/issues/370).

## Reviewer-choice robustness after adjudication

The final private score also contains `reviewer_choice_sensitivity`. For each
entry, take the distinct complete six-dimensional decision-score vectors
from both original reviews and the final adjudicated review. Different
judgments with the same decision vector collapse to one alternative. Do not
mix fields from different reviews or invent additional outcomes. The six
metrics are recall, continuation success, clarification questions, wrong
resource rate, wrong assumptions and consequential wrong action. Individual
arm extrema and paired candidate-minus-baseline differences are reported
using the existing scorer's per-episode and aggregate rounding.

For each of the three canonical comparisons, evaluate the adverse and
favorable corners of the marginal metric box using the unchanged canonical
point gate. If the adverse corner passes, every allowed choice passes; if
the favorable corner fails, none passes. The corners need not be jointly
attainable. If the number of distinct joint score-vector assignments for
that pair is at most 4,096, enumerate all complete row combinations and
count the exact decisions. Otherwise use only those sufficient corner
certificates or report unresolved. No random sampling or assignment
probabilities are used. Even an all-choice pass does not change the
canonical decision or bypass the 20-real-reviewed-episode requirement.

This is a private post-unblinding sensitivity report, not a reviewer-facing
packet. It has no reviewer IDs, episode IDs, source facts or model outputs.
It quantifies sensitivity to submitted judgments, not all possible errors,
statistical uncertainty, independence, or verified business impact. Tests
include an independent canonical aggregate oracle, a case where a favorable
marginal corner is unattainable, identical score vectors, bounded large
choice spaces and missing review inputs.
