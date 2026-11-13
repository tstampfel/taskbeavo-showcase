"""The contact-detail filter for user-written text (docs/PLAN.md, "Posting a task and handling
offers"; trust-safety skill section 2).

Posters and helpers may not swap phone numbers, emails, links or messenger handles before a
booking exists. Every write path that stores such text calls one function:

    result = filter_contact_details(text, locale)

- `text` is what the author wrote; `locale` is the author's saved language (hr, en, de, fr;
  regional or unknown values are fine). It picks which spelled digit words count, so a French
  author's "un deux trois" is a number and a Croatian author's is not.
- `result.text` is the text with every clear match replaced by `MASK`. Store and show that.
- `result.verdict` is one of, from mildest to strictest (the strictest match decides):
  - `CLEAN`: nothing found. Publish.
  - `FLAGGED`: only a bare messaging-app name with no handle, number or email next to it
    ("Pišite mi na WhatsApp", "Ich habe kein WhatsApp"). Publish now, unchanged, and log the
    kinds for staff; don't tell the author anything.
  - `MASKED`: clear matches, all masked. Publish the masked text and tell the author why.
  - `NEEDS_REVIEW`: at least one doubtful match, left unmasked. Hold the content for the
    review queue instead of publishing it. This is the only verdict that holds content.
- `result.kinds` is which kinds of contact detail were found; `masked_count`,
  `review_count` and `flagged_count` count the clear, doubtful and flag-only matches.

The result never holds the matched text, and this module never logs: the input is user text
and may hold names, phone numbers and addresses, which must stay out of logs, Sentry and
Honeycomb (CLAUDE.md, Observability). Kinds and counts are safe to record.

Booked posters and helpers chatting after acceptance are not filtered; the caller decides that.

Matching runs on a normalised copy of the text: compatibility forms are folded (fullwidth
at signs and digits, circled letters), diacritics dropped and invisible format characters
(zero-width spaces, soft hyphens) removed, so they can't hide a match. Masks cover the
original text.
Every pattern runs in linear time; text longer than `MAX_TEXT_LENGTH` isn't scanned and comes
back as `NEEDS_REVIEW`, so callers should cap input well below it.

Limits, all on the cautious side:
- Two phone numbers with only spaces between them form one run that is too long to be sure
  of, so the text goes to review unmasked.
- Long ID numbers look like phone numbers: an OIB (11 digits) is masked as PHONE and an IBAN
  goes to review.
- Digit groups split by line breaks, commas, "|" or a word like "i"/"und" are only flagged
  for review, and only when they add up to a phone-like number (9+ digits starting 0, 00 or +).
- A domain with a capitalised ending ("Merci.De rien") is probably a missing space, so it
  goes to review instead of being masked.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

MASK = "•••"
# Longer text isn't scanned; it is held for review. Callers cap input well below this.
MAX_TEXT_LENGTH = 50_000


class Verdict(StrEnum):
    """Mildest first; the strictest match in a text decides its verdict."""

    CLEAN = "CLEAN"
    FLAGGED = "FLAGGED"
    MASKED = "MASKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class MatchKind(StrEnum):
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    URL = "URL"
    MESSENGER = "MESSENGER"


@dataclass(frozen=True)
class FilterResult:
    text: str
    verdict: Verdict
    kinds: frozenset[MatchKind]
    masked_count: int
    review_count: int
    flagged_count: int


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    kind: MatchKind
    clear: bool


def _strip_marks(text: str) -> str:
    """Compatibility forms folded, diacritics and format characters dropped."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c) and unicodedata.category(c) != "Cf"
    )


def _fold(word: str) -> str:
    """Lowercase without diacritics, so "četiri", "Cetiri" and "cetiri" compare equal."""
    return _strip_marks(word.casefold())


def _digit_words(words: str) -> dict[str, str]:
    """ "nula jedan/jedna dva …": the words for 0 to 9 in order, "/" between alternatives."""
    return {
        _fold(word): str(digit)
        for digit, alternatives in enumerate(words.split())
        for word in alternatives.split("/")
    }


# Spelled digits by locale. English is always added, since anyone may write it.
_SPELLED_DIGITS: dict[str, dict[str, str]] = {
    "en": _digit_words("zero one two three four five six seven eight nine"),
    "hr": _digit_words(
        "nula jedan/jedna/jedno/jednu dva/dvije tri četiri pet šest sedam osam devet"
    ),
    "de": _digit_words("null eins zwei/zwo drei vier fünf/fuenf sechs sieben acht neun"),
    "fr": _digit_words("zéro un deux trois quatre cinq six sept huit neuf"),
}

_TLDS = "com|net|org|info|biz|eu|hr|de|at|ch|fr|be|lu|nl|it|si|ba|rs|me|io|co|uk|app|link|ly|online"
_TLD = rf"(?:{_TLDS})(?![\w-])"
_DOT_WORDS = r"dot|punkt|point|tocka|tacka"
_AT_WORDS = r"at|et|majmun|affe|arobase|monkey"
# A plain " at " or " et " is also an ordinary word, so it only counts as "@" next to an
# email-like local part or a known mail provider.
_AMBIGUOUS_AT = {"at", "et"}
_MAIL_PROVIDER = re.compile(
    r"gmail|googlemail|yahoo|hotmail|outlook|live|icloud|me|gmx|web|t-online|orange|free|"
    r"laposte|wanadoo|bluewin|aon|net|inet|tportal|protonmail|proton",
    re.IGNORECASE,
)
# A domain label with at least one letter. The lookahead keeps it unambiguous, so matching
# stays linear.
_LABEL = r"(?=[a-z0-9-]*[a-z])[a-z0-9-]+"

_AT = (
    r"(?:\s*@\s*"
    rf"|\s*[(\[{{]\s*(?:{_AT_WORDS}|@)\s*[)\]}}]\s*"
    rf"|\s+(?P<pat>{_AT_WORDS})\s+)"
)
_DOT = rf"(?:\s*\.\s*|\s*[(\[{{]\s*(?:{_DOT_WORDS}|\.)\s*[)\]}}]\s*|\s+(?:{_DOT_WORDS})\s+)"
# The local part starts at a word start, and the domain is greedy so "gmail.co.uk" is masked
# whole.
_EMAIL = re.compile(
    rf"(?<![\w.+-])(?P<local>[\w+-]+(?:\.[\w+-]+)*){_AT}(?P<domain>[\w-]+(?:\.[\w-]+)*){_DOT}{_TLD}",
    re.IGNORECASE,
)

_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"]+", re.IGNORECASE)
_BARE_DOMAIN = re.compile(
    rf"(?<![\w@.-])(?:{_LABEL}\.)+(?P<tld>{_TLDS})(?![\w-])(?P<path>/[^\s<>\"]*)?",
    re.IGNORECASE,
)
# "mojeusluge točka hr": written-out dots. Bracketed or with www it's clear; plain is doubtful.
_SPELLED_DOMAIN = re.compile(
    rf"(?<![\w.-])(?P<www>www\s*(?:\.|[(\[{{]?\s*(?:{_DOT_WORDS})\s*[)\]}}]?)\s*)?"
    rf"{_LABEL}"
    rf"(?:\s*[(\[{{]\s*(?P<bdot>{_DOT_WORDS})\s*[)\]}}]\s*|\s+(?P<pdot>{_DOT_WORDS})\s+)"
    rf"(?P<tld>{_TLDS})(?![\w-])",
    re.IGNORECASE,
)
# French "point de …" ("point de vue", "point de rendez-vous") is not a .de domain.
_SPELLED_DOMAIN_EXCEPTIONS = {("point", "de")}
_TRAILING_PUNCTUATION = ".,;:!?)]}'\""

_MESSENGER_HOSTS = re.compile(
    r"(?:^|[/.])(?:wa\.me|t\.me|m\.me|telegram\.me|whatsapp\.com|instagram\.com|"
    r"facebook\.com|fb\.com|viber\.com|snapchat\.com|tiktok\.com|threema\.id)\b",
    re.IGNORECASE,
)
# A bare "@name" counts only with a digit, "_" or inner "." in it, so "@home" stays text.
_HANDLE = re.compile(
    r"(?<![\w.@])@(?=[A-Za-z0-9_]*(?:[0-9_]|\.[A-Za-z0-9]))[A-Za-z0-9_][A-Za-z0-9_.]{2,29}"
)
# Messenger names, with Croatian case endings ("na Viberu", "WhatsAppom"). A bare mention of
# a messenger used for chatting is flagged for staff but published; the others ("ig", "snap",
# "messenger bag", "facebook marketplace") only count right before a handle or another match.
_CASE_ENDING = r"(?:u|om|a|e|i)?"
_STRONG_MESSENGER = re.compile(
    rf"\b(?:whats\s?app|viber|telegram|instagram|insta|snapchat|skype|threema|wechat){_CASE_ENDING}\b",
    re.IGNORECASE,
)
_WEAK_MESSENGER = re.compile(
    rf"\b(?:ig|snap|fb|facebook|messenger|tiktok){_CASE_ENDING}\b", re.IGNORECASE
)
_AFTER_MESSENGER = re.compile(r"[\s:\u2013-]*(?:(?:is|je|ist|est)[\s:]+)?", re.IGNORECASE)
# A handle after a messenger name: "@name", or a name with a digit, "_" or "." in it. The
# length bound keeps matching linear; `_handle_after` checks the rest.
_HANDLE_AFTER_MESSENGER = re.compile(r"@?[A-Za-z0-9_.]{2,30}")

# Dates, times and year ranges look like digit runs ("12.03.2026", "9:00", "2025-2026"). A
# "date" that the same kind of separator continues ("06.12.34.56.78") is a phone number.
_NOT_AFTER_GROUP = r"(?<!\d)(?<!\d[./-])(?<!\d\s[./-])(?<!\d[./-]\s)"
_NOT_BEFORE_GROUP = r"(?!\d)(?!\s?[./-]\s?\d)"
_DATE = re.compile(
    rf"{_NOT_AFTER_GROUP}(?P<d>\d{{1,2}})\s?[./-]\s?(?P<m>\d{{1,2}})\s?[./-]\s?(?:\d{{4}}|\d{{2}})"
    rf"{_NOT_BEFORE_GROUP}"
    rf"|{_NOT_AFTER_GROUP}(?:19|20)\d{{2}}-\d{{2}}-\d{{2}}{_NOT_BEFORE_GROUP}"
    rf"|{_NOT_AFTER_GROUP}(?:19|20)\d{{2}}\s?[-\u2013/]\s?(?:19|20)\d{{2}}{_NOT_BEFORE_GROUP}"
)
_TIME = re.compile(r"(?<!\d)(?P<h>\d{1,2})[:h](?P<min>\d{2})(?![\d:])")
_CURRENCY_AFTER = re.compile(
    r"\s*(?:€|\$|(?:eur|euro|eura|eure|kn|kuna|kune|chf|fr|usd|franken)\b)", re.IGNORECASE
)
_CURRENCY_BEFORE = re.compile(r"(?:€|\$|\b(?:eur|euro|kn|chf|usd))\s*$", re.IGNORECASE)
# How far back a currency before a number is looked for.
_CURRENCY_WINDOW = 8

# Separators inside a phone number. The strict ones are what people write in a number; the
# loose ones (line breaks, commas, "|", a joining word) are what people use to hide one.
_STRICT_SEP = r"[ \t\-./()]+"
_LOOSE_SEP = r"(?:[ \t\r\n,_|:*;\-./()]{1,3}|[ \t]+(?:i|und|et|and)[ \t]+)"
_PROTECTED = "\x00"


def _locale_words(locale: str) -> dict[str, str]:
    language = re.split(r"[-_]", locale.strip().lower(), maxsplit=1)[0]
    return {**_SPELLED_DIGITS["en"], **_SPELLED_DIGITS.get(language, {})}


def _run_pattern(words: dict[str, str], separator: str) -> re.Pattern[str]:
    spelled = "|".join(sorted(map(re.escape, words), key=len, reverse=True))
    unit = rf"(?:\d+|(?<![^\W\d_])(?:{spelled})(?![^\W\d_]))"
    return re.compile(rf"(?:\+[ \t]?|\()?{unit}(?:{separator}{unit})*", re.IGNORECASE)


class _Found:
    """The matches so far, with a coverage map so overlap checks stay cheap."""

    def __init__(self, length: int) -> None:
        self.matches: list[_Match] = []
        self.covered = bytearray(length)

    def overlaps(self, start: int, end: int) -> bool:
        return any(self.covered[start:end])

    def add(self, match: _Match) -> bool:
        if self.overlaps(match.start, match.end):
            return False
        self.matches.append(match)
        self.covered[match.start : match.end] = b"\x01" * (match.end - match.start)
        return True


def _strip_trailing(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in _TRAILING_PUNCTUATION:
        end -= 1
    return end


def _find_emails(scan: str, found: _Found) -> None:
    for m in _EMAIL.finditer(scan):
        plain_at = m.group("pat")
        if plain_at and _fold(plain_at) in _AMBIGUOUS_AT:
            local, domain = m.group("local"), m.group("domain")
            if not re.search(r"[._\d+]", local) and not _MAIL_PROVIDER.fullmatch(domain):
                continue
        found.add(_Match(m.start(), m.end(), MatchKind.EMAIL, clear=True))


def _find_urls(scan: str, found: _Found) -> set[int]:
    """Adds links; returns the start of each link to a messenger (wa.me, t.me, …)."""
    messenger_links: set[int] = set()
    for m in _URL.finditer(scan):
        end = _strip_trailing(scan, m.start(), m.end())
        added = found.add(_Match(m.start(), end, MatchKind.URL, clear=True))
        if added and _MESSENGER_HOSTS.search(scan[m.start() : end]):
            messenger_links.add(m.start())
    for m in _BARE_DOMAIN.finditer(scan):
        end = _strip_trailing(scan, m.start(), m.end())
        # "Merci.De rien": a capitalised ending is most likely a missing space after a full
        # stop, so it is doubtful rather than masked.
        tld = m.group("tld")
        clear = bool(m.group("path")) or not tld[0].isupper() or tld.isupper()
        added = found.add(_Match(m.start(), end, MatchKind.URL, clear=clear))
        if added and _MESSENGER_HOSTS.search(scan[m.start() : end]):
            messenger_links.add(m.start())
    for m in _SPELLED_DOMAIN.finditer(scan):
        dot = m.group("bdot") or m.group("pdot")
        if (_fold(dot), m.group("tld").lower()) in _SPELLED_DOMAIN_EXCEPTIONS:
            continue
        clear = bool(m.group("www") or m.group("bdot"))
        found.add(_Match(m.start(), m.end(), MatchKind.URL, clear=clear))
    return messenger_links


def _find_handles(scan: str, found: _Found) -> None:
    for m in _HANDLE.finditer(scan):
        end = _strip_trailing(scan, m.start(), m.end())
        found.add(_Match(m.start(), end, MatchKind.MESSENGER, clear=True))


def _phone_verdict(digits: str, has_plus: bool, spelled: bool) -> bool | None:
    """True for a clear phone number, False for a doubtful one, None for not a phone."""
    n = len(digits)
    if spelled:
        # Spelled digits are almost always someone getting round the filter.
        if n >= 9:
            return True
        return False if n >= 5 else None
    if has_plus or digits.startswith("00"):
        if n >= 8:
            return True
        return False if n >= 5 else None
    if digits.startswith("0"):
        if 9 <= n <= 12:
            return True
        return False if n >= 6 else None
    if 9 <= n <= 12:
        return True
    return False if n >= 7 else None


def _protect(scan: str, found: _Found) -> str:
    """The scan text with dates, times and earlier matches blanked out."""
    chars = list(scan)

    def blank(start: int, end: int) -> None:
        chars[start:end] = _PROTECTED * (end - start)

    for m in _DATE.finditer(scan):
        if m.group("d") and not (1 <= int(m.group("d")) <= 31 and 1 <= int(m.group("m")) <= 12):
            continue
        blank(m.start(), m.end())
    for m in _TIME.finditer(scan):
        if int(m.group("h")) <= 24 and int(m.group("min")) <= 59:
            blank(m.start(), m.end())
    for match in found.matches:
        blank(match.start, match.end)
    return "".join(chars)


def _run_digits(run: str, words: dict[str, str]) -> tuple[str, list[str], bool]:
    """The run's digits, its digit groups, and whether any digit was spelled out."""
    groups: list[str] = []
    spelled = False
    for unit in re.finditer(r"\d+|[^\W\d_]+", run):
        token = unit.group()
        if token.isdigit():
            groups.append(token)
        elif _fold(token) in words:
            groups.append(words[_fold(token)])
            spelled = True
    return "".join(groups), groups, spelled


def _find_phones(scan: str, locale: str, found: _Found) -> None:
    words = _locale_words(locale)
    protected = _protect(scan, found)
    for m in _run_pattern(words, _STRICT_SEP).finditer(protected):
        start, end = m.start(), m.end()
        digits, groups, spelled = _run_digits(m.group(), words)
        has_plus = m.group().startswith("+")
        prefixed = has_plus or digits.startswith("0")
        # "Ante Starčevića 24 20000": a house number and a postcode.
        house_and_postcode = len(groups) == 2 and len(groups[0]) <= 4 and len(groups[1]) == 5
        if house_and_postcode and not prefixed and not spelled:
            continue
        clear = _phone_verdict(digits, has_plus, spelled)
        if clear is None:
            continue
        if not spelled and _beside_currency(protected, start, end):
            # A price, unless it is long or starts like a phone number.
            if not prefixed and len(digits) < 9:
                continue
            clear = clear and prefixed
        if m.group().startswith("(") and ")" not in m.group():
            start += 1
        found.add(_Match(start, end, MatchKind.PHONE, clear=clear))

    # Digit groups hidden behind line breaks, commas or joining words: doubtful only, and only
    # when they add up to a phone-like number, so lists like "10, 20, 30" stay clean.
    protected = _protect(scan, found)
    for m in _run_pattern(words, _LOOSE_SEP).finditer(protected):
        digits, _, spelled = _run_digits(m.group(), words)
        prefixed = m.group().startswith("+") or digits.startswith("0")
        if (prefixed or spelled) and len(digits) >= 9:
            found.add(_Match(m.start(), m.end(), MatchKind.PHONE, clear=False))


def _beside_currency(scan: str, start: int, end: int) -> bool:
    return bool(
        _CURRENCY_AFTER.match(scan, end)
        or _CURRENCY_BEFORE.search(scan, max(0, start - _CURRENCY_WINDOW), start)
    )


def _find_messengers(
    scan: str, found: _Found, messenger_links: set[int]
) -> tuple[list[_Match], list[_Match]]:
    """Messenger names. Returns the ones that only add a kind (a name right before a match
    already found, "WhatsApp 091 …", or a link to a messenger), and the bare names of chat
    apps ("javi se na Viber"), which are flagged but not masked or held. A name followed by a
    handle ("insta: marko.dbk") masks the handle."""
    mentions = [_Match(s, s, MatchKind.MESSENGER, clear=True) for s in messenger_links]
    flagged: list[_Match] = []
    match_starts = {m.start for m in found.matches}
    for pattern, strong in ((_STRONG_MESSENGER, True), (_WEAK_MESSENGER, False)):
        for m in pattern.finditer(scan):
            if found.overlaps(m.start(), m.end()):
                continue
            after = _AFTER_MESSENGER.match(scan, m.end())
            next_at = after.end() if after else m.end()
            if next_at in match_starts:
                mentions.append(_Match(m.start(), m.start(), MatchKind.MESSENGER, clear=True))
                continue
            handle_end = _handle_after(scan, next_at)
            if handle_end is not None:
                found.add(_Match(next_at, handle_end, MatchKind.MESSENGER, clear=True))
            elif strong:
                flagged.append(_Match(m.start(), m.end(), MatchKind.MESSENGER, clear=False))
    return mentions, flagged


def _handle_after(scan: str, start: int) -> int | None:
    """The end of a handle starting at `start`, or None when there isn't one."""
    m = _HANDLE_AFTER_MESSENGER.match(scan, start)
    if not m or scan[m.end() : m.end() + 1].isalnum():
        return None
    end = _strip_trailing(scan, start, m.end())
    handle = scan[start:end]
    if handle.startswith("@"):
        return end if len(handle) >= 3 else None
    if len(handle) < 3 or not re.search(r"[0-9_.]", handle):
        return None
    return end


def _normalise(text: str) -> tuple[str, list[int]]:
    """The text to match on, and for each of its characters the index it came from."""
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        folded = char if char.isascii() else _strip_marks(char)
        chars.append(folded)
        origin.extend([index] * len(folded))
    return "".join(chars), origin


def filter_contact_details(text: str, locale: str) -> FilterResult:
    """Masks clear contact details in `text` and flags doubtful ones; see the module doc."""
    if len(text) > MAX_TEXT_LENGTH:
        return FilterResult(text, Verdict.NEEDS_REVIEW, frozenset(), 0, 1, 0)

    scan, origin = _normalise(text)
    found = _Found(len(scan))
    _find_emails(scan, found)
    messenger_links = _find_urls(scan, found)
    _find_handles(scan, found)
    _find_phones(scan, locale, found)
    mentions, flagged = _find_messengers(scan, found, messenger_links)

    masked = sorted((m for m in found.matches if m.clear), key=lambda m: m.start)
    doubtful = [m for m in found.matches if not m.clear]
    parts: list[str] = []
    position = 0
    for match in masked:
        start, end = origin[match.start], origin[match.end - 1] + 1
        parts.append(text[position:start])
        parts.append(MASK)
        position = end
    parts.append(text[position:])

    if doubtful:
        verdict = Verdict.NEEDS_REVIEW
    elif masked:
        verdict = Verdict.MASKED
    elif flagged:
        verdict = Verdict.FLAGGED
    else:
        verdict = Verdict.CLEAN
    return FilterResult(
        text="".join(parts),
        verdict=verdict,
        kinds=frozenset(m.kind for m in [*found.matches, *mentions, *flagged]),
        masked_count=len(masked),
        review_count=len(doubtful),
        flagged_count=len(flagged),
    )
