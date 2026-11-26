"""Deterministic diversity ordering of already-ranked training review candidates."""
from collections import defaultdict, deque


def diverse_order(ranked):
    """Round robin languages, then keyword groups within each language.

    Preserve existing priority inside each stratum. Every candidate remains
    selectable; unknown language is a separate declared group, not inferred.
    No label, holdout text or probability is needed by this ordering.
    """
    groups = defaultdict(lambda: defaultdict(deque))
    for row in ranked:
        groups[row["language"]][row["keyword_intent"]].append(row)
    # The first appearance follows the existing global priority ordering.
    # Each language gets one turn before another candidate in that language.
    languages = deque(groups)
    keywords = {language: deque(strata) for language, strata in groups.items()}
    result = []
    while languages:
        language = languages.popleft()
        keyword = keywords[language].popleft()
        queue = groups[language][keyword]
        result.append(queue.popleft())
        if queue:
            keywords[language].append(keyword)
        if keywords[language]:
            languages.append(language)
    return result
