"""Exact document-title distribution of indexed policy statement occurrences."""
from collections import Counter, defaultdict


def summarize(before, after):
    """Return title-level counts and per-text evidence, without asserting identity.

    Among maximum-cardinality exact-text matches, maximize same-title matches.
    Remaining matched occurrences necessarily cross title labels. The resulting
    minimum is not evidence that a specific statement moved or changed authority.
    """
    left, right = defaultdict(Counter), defaultdict(Counter)
    for rows, groups in ((before, left), (after, right)):
        for row in rows:
            groups[row['text']][row['document_title']] += 1
    by_title = defaultdict(Counter)
    by_text = {}
    same_total = cross_total = retained_total = 0
    for statement in sorted(left.keys() | right.keys()):
        a, b = left[statement], right[statement]
        retained = min(sum(a.values()), sum(b.values()))
        same = sum(min(a[t], b[t]) for t in a.keys() & b.keys())
        cross = retained - same
        same_total += same
        cross_total += cross
        retained_total += retained
        labels = []
        for title in sorted(a.keys() | b.keys()):
            shared = min(a[title], b[title])
            by_title[title]['baseline_occurrences'] += a[title]
            by_title[title]['candidate_occurrences'] += b[title]
            by_title[title]['same_title_retained'] += shared
            labels.append({'document_title': title, 'baseline_occurrences': a[title],
                           'candidate_occurrences': b[title]})
        by_text[statement] = {'same_title_retained_maximum': same,
                              'cross_title_retained_minimum': cross,
                              'title_distribution_changed': a != b,
                              'title_counts': labels}
    summary = {'same_title_retained_maximum': same_total,
               'cross_title_retained_minimum': cross_total,
               'retained_occurrences': retained_total,
               'distinct_text_groups_with_title_distribution_changes': sum(
                   row['title_distribution_changed'] for row in by_text.values()),
               'distinct_text_groups_requiring_cross_title_match': sum(
                   row['cross_title_retained_minimum'] > 0 for row in by_text.values()),
               'titles': [{'document_title': title, **counts,
                           'baseline_unmatched_within_title': counts['baseline_occurrences'] - counts['same_title_retained'],
                           'candidate_unmatched_within_title': counts['candidate_occurrences'] - counts['same_title_retained']}
                          for title, counts in sorted(by_title.items())],
               'document_identity_verified': False, 'policy_authority_verified': False}
    return summary, by_text
