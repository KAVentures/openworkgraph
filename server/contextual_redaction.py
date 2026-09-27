from __future__ import annotations

"""Contextual name redaction for AI context: replace, don't remove.

Runs *after* the established presentation pipeline (emails, phones,
personnummer, owner aliases, learned identities, mail rows) and adds recall for
people mentioned in free text: window titles, UI labels, page titles.

Only the sensitive span is replaced with a typed, stable token; every other word
and all punctuation stay, so an AI still sees "Re: Contract for PERSON_1A2B3C -
Gmail" instead of losing the title.

Detection is local and deterministic (no network, no model):

* bundled first-name / surname lists (US Census 1990, public domain; Statistics
  Sweden 2022, CC0) in ``server/name_lexicon``;
* context cues in English and Swedish (``from``, ``chat with``, ``Dr.``,
  ``från``, ``möte med`` ...);
* structure: ``First Surname`` pairs, ``First and First``, ``Surname, First``,
  a title segment that is only a first name;
* identities already learned by the presentation layer (hashed registry).

Names that are also everyday words, places or products (May, Bill, Grace,
Paris, Claude ...) need a stronger signal: an adjacent listed surname or a
person cue. App/site names, UI vocabulary, month/day names and the user's
"never redact" list are never replaced.
"""

import functools
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

LEXICON_DIR = Path(__file__).resolve().parent / "name_lexicon"


def _load_names(name: str) -> frozenset[str]:
    try:
        lines = (LEXICON_DIR / name).read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


FIRST_NAMES = _load_names("first_names.txt")
SURNAMES = _load_names("surnames.txt")

# First names that are also common words, places or product names in work
# titles. They count as a name only next to a listed surname or after a strong
# person cue.
AMBIGUOUS_NAMES = frozenset("""
amber angel april art august aurora autumn bill blair brain candy can carol chase cherry clay cliff
crystal dale dawn dean drew earnest else faith fay fern flora frank gay gene german ginger grace grant
guy hardy hazel heather holly hope hunter ion iris ivy jack jade jewel joy june junior kid kit lance
lane lava lily line lion love luna mark max may melody miles misty nova odd olive opal pat pearl penny
per prince queen rain ray reed rex river rob robin rocky rod rose ruby rusty sage said sandy set sol
son sonny sterling summer tea tone urban van viking violet ward will young win
bo dag rut maj sten saga tove gun mi my lo lee tor ester sol vera juni ros liv alva nova ella elsa
paris florence jordan victoria georgia virginia austin dallas sydney madison chelsea brooklyn india asia
lima sierra dakota carolina savannah phoenix denver houston charlotte lincoln jackson washington orlando
claude julia siri alexa cortana gemini mercedes tesla jaguar porsche jenkins oracle harmony skye sky
""".split())

MONTHS_DAYS = frozenset("""
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
monday tuesday wednesday thursday friday saturday sunday
januari februari mars maj juni juli augusti oktober
måndag tisdag onsdag torsdag fredag lördag söndag
""".split())

# Capitalized words that are never a person on their own in work titles:
# app/site names, UI and business vocabulary, English/Swedish function words.
STOPWORDS = frozenset("""
inbox outbox sent drafts draft spam trash archive starred important unread read all mail email e-mail
account accounts contract contracts invoice invoices budget budgets meeting meetings report reports
calendar chat chats channel channels thread threads message messages dm direct conversation home
settings dashboard overview profile search results new open close edit view compose reply forward
fwd fw re sv vb ang aw tr subject untitled document documents doc docs sheet sheets spreadsheet slides
presentation file files folder folders drive shared notes note task tasks ticket tickets issue issues
pull request requests commit commits branch branches repository repo code review reviews project
projects pipeline tracker board boards backlog sprint roadmap release releases deal deals lead leads
opportunity opportunities contact contacts company companies case cases record records lab labs results
result patient patients journal journals referral referrals prescription prescriptions visit visits
booking bookings appointment appointments order orders quote quotes proposal proposals payment
payments customer customers client clients vendor vendors supplier suppliers team teams group groups
department sales marketing finance legal support hr it ops operations admin administration
q1 q2 q3 q4 fy ytd kpi okr okrs agenda minutes summary plan planning strategy weekly monthly daily
annual quarterly update updates status review template templates welcome hello hi hej dear team's
the a an and or of for to from with by about in on at as is are was be it this that your my our re:
och eller för till från med av om hos på i är att det den en ett som ny nytt nya
faktura fakturor avtal möte möten rapport rapporter kalender inkorg utkast skickat skickade
ärende ärenden journalanteckning remiss remisser recept besök bokning bokningar kund kunder
leverantör projekt uppdrag offert offerter beställning beställningar
gmail outlook google microsoft teams slack salesforce hubspot jira github gitlab bitbucket confluence
notion trello asana monday linear figma miro zoom webex meet docs sheets drive excel word powerpoint
onenote onedrive sharepoint dropbox box chrome safari firefox edge arc brave opera vivaldi finder
terminal xcode cursor vscode code chatgpt openai claude anthropic copilot gemini lovable perplexity
whatsapp telegram signal messenger facebook linkedin twitter instagram youtube reddit wikipedia
cosmic cambio epic cerner melior takecare take care pmo journalen 1177 inera
acme corp inc ltd llc gmbh ab aktiebolag oy as asa plc co group holding holdings
""".split()) | MONTHS_DAYS

_COMPANY_SUFFIXES = frozenset("ab inc ltd llc gmbh oy as asa plc corp co aktiebolag".split())

# Large organizations whose name is also a surname ("Report for Ericsson").
ORGANIZATIONS = frozenset("""
ericsson volvo scania saab skanska telia spotify ikea nordea swedbank handelsbanken seb klarna
electrolux husqvarna sandvik securitas vattenfall northvolt tele2 ica coop atlas copco
ford dell disney boeing siemens philips nokia bosch porsche ferrari mcdonald mcdonalds
""".split())

# A name-like word directly followed by one of these is a place or an
# organization ("Hope Street", "Grace Hopper Room", "Chase Bank").
PLACE_ORG_NOUNS = frozenset("""
street st road rd avenue ave lane boulevard square station airport park garden gardens hall room
building tower center centre museum hotel hospital clinic university college school library
bank capital partners foundation institute group stadium arena bridge island beach
gatan vägen gata väg torg torget plan station stationen sjukhus sjukhuset vårdcentral skola
skolan universitet parken hotell museet bank banken stiftelse
""".split())

HONORIFICS = r"(?:Dr|Doktor|Mr|Mrs|Ms|Miss|Prof|Professor|Fru|Herr|Sir|Madam)\.?"

# Strong cues: the next name-shaped words refer to a person.
STRONG_CUES = (
    r"chat\s+with|chatting\s+with|meeting\s+with|meet\s+with|call\s+with|call|called|calling|"
    r"message\s+from|email\s+from|mail\s+from|reply\s+to|cc|bcc|attendee|assigned\s+to|assignee|"
    r"owner|contact|dear|hi|hello|hej|hejsan|möte\s+med|samtal\s+med|ring|ringa|"
    r"skickat\s+av|tilldelad|mottagare|avsändare|patient|"
    r"reporter|lead|interview|interviewing|candidate|1:1|one-on-one|"
    r"wife|husband|daughter|son|mother|father|colleague|"
    r"dotter|son|mor|mamma|far|pappa|make|maka|sambo|kollega|anhörig"
)
# Weak cues: common prepositions; the next word must already be a listed name.
WEAK_CUES = (
    r"from|for|to|with|by|about|re|regarding|per|"
    r"från|för|till|med|av|hos|om|åt|inför|angående"
)
_STRONG_CUE_RE = re.compile(rf"(?:^|(?<=[\s:(\[\-–—|·,]))(?:{STRONG_CUES})\s*:?\s*$", re.IGNORECASE)
_WEAK_CUE_RE = re.compile(rf"(?:^|(?<=[\s:(\[\-–—|·,]))(?:{WEAK_CUES})\s*:?\s*$", re.IGNORECASE)
_HONORIFIC_RE = re.compile(rf"(?:^|(?<=[\s:(\[\-–—|·,])){HONORIFICS}\s*$")

_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*")
_TOKEN_RE = re.compile(r"\b(?:OWNER(?:_EMAIL|_PHONE)?|PERSON|[A-Z][A-Z_]*_[0-9A-F]{4,})\b")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_LONG_ID_RE = re.compile(r"(?<![\w-])\d{10,}(?![\w-])")
_SEGMENT_SPLIT_RE = re.compile(r"\s+[-–—|·]\s+|\s*[|·]\s*|:\s+")


@dataclass(frozen=True)
class Settings:
    never_redact: tuple[str, ...] = ()
    always_redact: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Word:
    start: int
    end: int
    text: str

    @property
    def key(self) -> str:
        return _base(self.text)


def _base(word: str) -> str:
    return word.strip("'’-").casefold()


def _possessive_base(word: str) -> str:
    low = _base(word)
    for suffix in ("'s", "’s"):
        if low.endswith(suffix):
            return low[: -len(suffix)]
    return low


def _is_capitalized(word: str) -> bool:
    return bool(word) and word[0].isupper() and not (len(word) > 1 and word.isupper() and len(word) <= 3)


class Detector:
    """Find person-name spans in one string. Thread-safe and side-effect free."""

    def __init__(
        self,
        *,
        token: Callable[[str, str], str],
        is_learned: Callable[[str], bool] = lambda _name: False,
        learned_token: Callable[[str], str | None] = lambda _name: None,
        owner_aliases: frozenset[str] = frozenset(),
        settings: Settings = Settings(),
    ) -> None:
        self._token = token
        self._is_learned = is_learned
        self._learned_token = learned_token
        self._owner = owner_aliases
        self._settings = settings
        self._never = tuple(p.casefold() for p in settings.never_redact if p.strip())
        self._always = tuple(sorted({p.strip() for p in settings.always_redact if p.strip()}, key=len, reverse=True))
        self._never_words = frozenset(w for p in self._never for w in p.split())

    # -- word classification --------------------------------------------------------

    def _stop(self, key: str) -> bool:
        return key in STOPWORDS or key in self._never_words

    def _first(self, word: str) -> bool:
        key = _possessive_base(word)
        if not _is_capitalized(word) or self._stop(key):
            return False
        if key in FIRST_NAMES or self._is_learned(key):
            return True
        # Hyphenated given names: "Per-Olof", "Anna-Karin".
        parts = [p for p in key.split("-") if p]
        return len(parts) > 1 and all(p in FIRST_NAMES for p in parts)

    def _surname(self, word: str) -> bool:
        key = _possessive_base(word)
        if not _is_capitalized(word) or self._stop(key) or key in ORGANIZATIONS or key in PLACE_ORG_NOUNS:
            return False
        if key in SURNAMES:
            return True
        # Swedish genitive "Svenssons", compound "Lindqvist-Berg".
        if key.endswith("s") and key[:-1] in SURNAMES:
            return True
        return "-" in key and all(part in SURNAMES for part in key.split("-") if part)

    def _ambiguous(self, word: str) -> bool:
        key = _possessive_base(word)
        return key in AMBIGUOUS_NAMES or key in ORGANIZATIONS

    def _before_place_or_org(self, words: list[_Word], end: int, text: str) -> bool:
        return (
            end < len(words)
            and text[words[end - 1].end:words[end].start] == " "
            and _base(words[end].text) in PLACE_ORG_NOUNS
        )

    def _plain_name_word(self, word: str) -> bool:
        """Capitalized, alphabetic and not vocabulary: acceptable inside a name."""
        key = _possessive_base(word)
        return (
            _is_capitalized(word)
            and not self._stop(key)
            and key not in _COMPANY_SUFFIXES
            and not any(ch.isdigit() for ch in word)
        )

    # -- span finding ------------------------------------------------------------------

    def _extend_surnames(self, words: list[_Word], j: int, text: str, *, loose: bool) -> int:
        """Return the index after the name that starts at j (first name already accepted)."""
        end = j + 1
        while end < len(words) and end - j < 3 and text[words[end - 1].end:words[end].start] == " ":
            nxt = words[end].text
            if self._surname(nxt) or (self._first(nxt) and not self._ambiguous(nxt)):
                end += 1
            elif loose and self._plain_name_word(nxt) and not self._ambiguous(nxt):
                end += 1
            else:
                break
        # A company suffix right after means "Anna Svensson AB" is a company.
        if end < len(words) and _base(words[end].text) in _COMPANY_SUFFIXES:
            return j
        return end

    def spans(self, text: str) -> list[tuple[int, int]]:
        words = [_Word(m.start(), m.end(), m.group(0)) for m in _WORD_RE.finditer(text)]
        if not words:
            return []
        blocked = self._blocked_ranges(text)
        found: list[tuple[int, int]] = []
        taken = [False] * len(words)

        def free(i: int) -> bool:
            w = words[i]
            return not taken[i] and not any(a <= w.start < b for a, b in blocked)

        def add(i: int, j: int) -> None:
            if j <= i or not all(free(k) for k in range(i, j)):
                return
            for k in range(i, j):
                taken[k] = True
            found.append((words[i].start, words[j - 1].end))

        def prefix(i: int) -> str:
            return text[: words[i].start]

        for i, w in enumerate(words):
            if not free(i) or not _is_capitalized(w.text):
                continue
            before = prefix(i)
            strong = bool(_STRONG_CUE_RE.search(before) or _HONORIFIC_RE.search(before))
            weak = bool(_WEAK_CUE_RE.search(before))
            key = _possessive_base(w.text)

            # 1. Honorific / strong cue: "Dr. Lindqvist", "Chat with Sara Ek".
            if strong and (self._first(w.text) or self._surname(w.text) or (
                _HONORIFIC_RE.search(before) and self._plain_name_word(w.text)
            )):
                end = self._extend_surnames(words, i, text, loose=True)
                if not self._before_place_or_org(words, end, text):
                    add(i, end)
                continue
            # 1b. Strong cue + two unlisted name-shaped words: "Chat with Priya Patel".
            if strong and self._plain_name_word(w.text) and i + 1 < len(words):
                nxt = words[i + 1]
                if (
                    text[w.end:nxt.start] == " "
                    and self._plain_name_word(nxt.text)
                    and not self._ambiguous(w.text)
                    and not (i + 2 < len(words) and _base(words[i + 2].text) in _COMPANY_SUFFIXES)
                    and not self._before_place_or_org(words, i + 2, text)
                ):
                    add(i, i + 2)
                    continue

            if self._first(w.text):
                end = self._extend_surnames(words, i, text, loose=not self._ambiguous(w.text))
                has_surname = end > i + 1
                if self._before_place_or_org(words, end, text):
                    continue
                if self._ambiguous(w.text):
                    # "Rose Svensson" yes; "Rose garden", "May report" no.
                    if has_surname and self._surname(words[i + 1].text):
                        add(i, end)
                    elif not has_surname and self._coordinated(words, i, text):
                        # "Mark / Jessica": paired with an unambiguous first name.
                        add(i, end)
                    continue
                # 2. "First Surname" pair anywhere, or a listed first name after a cue.
                if has_surname or weak:
                    add(i, end)
                    continue
                # 3. "Johan and Anna" / "Johan och Anna".
                if self._coordinated(words, i, text):
                    add(i, end)
                    continue
                # 4. A title segment that is just a first name: "Johan | Microsoft Teams".
                if self._alone_in_segment(text, w):
                    add(i, end)
                    continue
                # 4b. Possessive first name: "Rachel's onboarding checklist".
                if w.text.endswith(("'s", "’s")):
                    add(i, end)
                    continue
                continue

            # 5. "Surname, Firstname" / "Surname Firstname" (EHR / directory order).
            if self._surname(w.text) and not self._ambiguous(w.text) and i + 1 < len(words):
                gap = text[w.end:words[i + 1].start]
                if gap.strip() == "," and self._first(words[i + 1].text):
                    add(i, i + 2)
                    continue
                nxt = words[i + 1].text
                if gap == " " and self._first(nxt) and not self._ambiguous(nxt) and not self._surname(nxt) and (
                    i + 2 >= len(words) or not self._surname(words[i + 2].text)
                ):
                    add(i, i + 2)
                    continue
                if weak and key not in AMBIGUOUS_NAMES:
                    add(i, i + 1)
                    continue

            # 6. Already learned full identities: "Koyar Afrasyab".
            if i + 1 < len(words) and text[w.end:words[i + 1].start] == " ":
                pair = f"{key} {_possessive_base(words[i + 1].text)}"
                if self._is_learned(pair) and self._plain_name_word(words[i + 1].text):
                    add(i, i + 2)

        return sorted(found)

    def _coordinated(self, words: list[_Word], i: int, text: str) -> bool:
        """True for either side of "Johan and Anna" / "Johan och Anna" / "Johan & Anna"."""
        joiners = {"and", "och", "og", "und"}

        # At least one side must be an unambiguous first name: "Mark / Jessica"
        # counts, "May / June" does not.
        this_clear = not self._ambiguous(words[i].text)

        def partner(j: int) -> bool:
            if not (0 <= j < len(words) and self._first(words[j].text)):
                return False
            return this_clear or not self._ambiguous(words[j].text)

        # "&" is not a word token, so it shows up as the gap between neighbours.
        symbols = {"&", "/", "+"}
        if i + 1 < len(words) and text[words[i].end:words[i + 1].start].strip() in symbols and partner(i + 1):
            return True
        if i > 0 and text[words[i - 1].end:words[i].start].strip() in symbols and partner(i - 1):
            return True
        if i + 2 < len(words) and words[i + 1].text.casefold() in joiners and partner(i + 2):
            return True
        return i >= 2 and words[i - 1].text.casefold() in joiners and partner(i - 2)

    def _alone_in_segment(self, text: str, w: _Word) -> bool:
        pieces = _SEGMENT_SPLIT_RE.split(text)
        return any(piece.strip(" ,;()[]") == w.text for piece in pieces)

    def _blocked_ranges(self, text: str) -> list[tuple[int, int]]:
        ranges = [(m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]
        ranges += [(m.start(), m.end()) for m in _URL_RE.finditer(text)]
        low = text.casefold()
        for phrase in self._never:
            for m in re.finditer(rf"(?<!\w){re.escape(phrase)}(?!\w)", low):
                ranges.append((m.start(), m.end()))
        return ranges

    # -- replacement --------------------------------------------------------------------

    def person_token(self, name: str) -> str:
        normalized = re.sub(r"\s+", " ", name).strip().casefold()
        for suffix in ("'s", "’s"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
        if normalized in self._owner:
            return "OWNER"
        return self._learned_token(normalized) or self._token("PERSON", normalized)

    def redact(self, text: str) -> str:
        if not text or not any(ch.isalpha() or ch.isdigit() for ch in text):
            return text
        out = text
        # User "always redact" phrases first (whole-word, case-insensitive).
        for phrase in self._always:
            out = re.sub(
                rf"(?<!\w){re.escape(phrase)}(?!\w)",
                lambda m: self._token("PERSON", m.group(0)),
                out,
                flags=re.IGNORECASE,
            )
        # Long reference numbers (10+ digits) that storage hardening kept.
        blocked = self._blocked_ranges(out)
        out = _LONG_ID_RE.sub(
            lambda m: m.group(0) if any(a <= m.start() < b for a, b in blocked) else self._token("ID", m.group(0)),
            out,
        )
        spans = self.spans(out)
        for start, end in reversed(spans):
            name = out[start:end]
            suffix = ""
            for poss in ("'s", "’s"):
                if name.endswith(poss):
                    name, suffix = name[: -len(poss)], poss
            out = out[:start] + self.person_token(name) + suffix + out[end:]
        return out


class CachedRedactor:
    """Per-string memoization keyed by the inputs that change the answer."""

    def __init__(self, maxsize: int = 50_000) -> None:
        self._lock = threading.Lock()
        self._maxsize = maxsize
        self._state: tuple[Any, ...] | None = None
        self._detector: Detector | None = None
        self._cache: dict[str, str] = {}

    def get(self, state: tuple[Any, ...], factory: Callable[[], Detector]) -> Callable[[str], str]:
        with self._lock:
            if state != self._state or self._detector is None:
                self._state = state
                self._detector = factory()
                self._cache = {}
            detector, cache = self._detector, self._cache

        def redact(text: str) -> str:
            hit = cache.get(text)
            if hit is not None:
                return hit
            value = detector.redact(text)
            if len(cache) < self._maxsize:
                cache[text] = value
            return value

        return redact


@functools.lru_cache(maxsize=1)
def lexicon_stats() -> dict[str, int]:
    return {"first_names": len(FIRST_NAMES), "surnames": len(SURNAMES), "ambiguous": len(AMBIGUOUS_NAMES)}
