# Reviewer-label sensitivity (R22)

Open the existing communications paired classifier evaluation, select all
reviewed holdout messages or the available lexical-overlap-screened subset,
and optionally select a displayed language. Expand **Reviewer-label error
sensitivity** and explicitly enter a hypothetical budget (basis points, where
100 is 1%). For each intent, at most floor(message count × budget / 10,000)
binary reviewer labels can be flipped; the pooled analysis independently uses
floor(message count × intent count × budget / 10,000) label cells.

Predictions and abstentions are fixed. Flipping one binary reference label
swaps correct and incorrect for decided predictions, leaving abstentions
unchanged. The paired correctness difference changes by −2, −1, 0, +1 or +2.
The existing complete nine-cell paired correctness matrix suffices to compute
those effect populations, without text, individual probabilities or example IDs.
The most adverse or favorable flips of magnitude two precede those of magnitude
one. This yields attainable exact extrema under an at-most budget; not all
integers between them necessarily occur. Minimum flips to erase or reverse
an observed lead use the same ordered effect counts. Erasing means reaching
or crossing zero, not necessarily an exact tie. Empty populations show no data.

Per-intent bounds use separate hypothetical budgets. Pooled bounds use
independent intent-label cells, not distinct messages, and cannot be interpreted
as a message-level correction or complete-message accuracy bound.
The export records source model, training-basis and taxonomy digests, selected
population and budget, exact counts, effect populations and extrema. Those pins
are carried from the existing report; current evidence is not reverified.
The review does not estimate annotation error or statistical uncertainty,
establish external quality, select thresholds, retrain a classifier, send
source data, or authorize deployment. Holdout results must not be used for
tuning. Selection and budget changes invalidate prior results.
