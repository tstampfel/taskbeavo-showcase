"""Which tasks TaskBeavo accepts (docs/PLAN.md, "Posting a task and handling offers" and
"\"Drive Mum to the doctor\" is a taxi licence problem"; trust-safety section 1).

The category list is the server's: a task must name one of `core.categories.TaskCategory`.
Rides (driving people), medical care and personal care (bathing, dressing, medication)
are never categories; a category outside the list is refused with CATEGORY_NOT_ALLOWED.
Errands where the helper drives alone, and accompaniment on a bus or in a licensed taxi,
are allowed.

People can still describe an excluded task under an allowed category ("Check-in visit:
drive Mama to the doctor"). `screen_excluded` reads title and description for that and
names the excluded categories the text looks like. It is a keyword heuristic, so it never
refuses on its own: a match holds the task for staff review (Task.HoldReason
EXCLUDED_CATEGORY), and a person decides. A rule matches when all of its patterns occur
in one sentence, in any order (German puts the verb last).

Matching runs on a casefolded copy without diacritics, so "Liječnik", "lijecnik" and
"LIJEČNIKA" read alike. It never logs: the text may hold names and addresses.
"""

import re
import unicodedata
from enum import StrEnum

from core.categories import TaskCategory
from core.errors import ApiError, ErrorCode


class ExcludedCategory(StrEnum):
    RIDES = "RIDES"
    MEDICAL_CARE = "MEDICAL_CARE"
    PERSONAL_CARE = "PERSONAL_CARE"


def require_allowed_category(value: str) -> TaskCategory:
    """The category if TaskBeavo accepts it, otherwise CATEGORY_NOT_ALLOWED."""
    if value not in TaskCategory.values:
        raise ApiError(ErrorCode.CATEGORY_NOT_ALLOWED, "TaskBeavo doesn't accept this category.")
    return TaskCategory(value)


# Family members and pronouns, in hr, en, de and fr. Croatian object forms (mamu, tatu,
# baku) and short pronouns (ga, ju, nju) included; German "sie" is left out because it
# is also the formal "you".
_PERSON = (
    r"\b(me|him|her|them|us|mum|mom|mother|dad|father|grandma|grandmother|grandpa|"
    r"grandfather|parents?|"
    r"mamu|mama|majku|majka|tatu|tata|oca|baku|baka|djeda|djed|roditelje|ga|ju|nju|njega|"
    r"mutter|vater|papa|oma|opa|eltern|ihn|"
    r"maman|mere|pere|grand-mere|grand-pere|lui|elle)\b"
)
_DESTINATION = (
    r"\b(doctor|doctors|gp|hospital|clinic|dentist|airport|station|"
    r"doktor\w*|lijecnik\w*|bolnic\w*|ambulant\w*|zubar\w*|aerodrom\w*|zracn\w* luk\w*|"
    r"kolodvor\w*|"
    r"arzt\w*|krankenhaus\w*|klinik\w*|zahnarzt\w*|flughafen\w*|bahnhof\w*|"
    r"medecin\w*|hopital\w*|clinique\w*|dentiste\w*|aeroport\w*|gare)\b"
)
# Driving someone. Taking or accompanying someone (bus, licensed taxi) is allowed, and so
# is a helper driving alone, so a ride needs a person and a destination too.
_DRIVE = (
    r"\b(drive|driving|"
    r"odvesti|odvezti|odvest|odvezi|odvozit\w*|voziti|vozit|prevesti|prevezti|"
    r"fahren|fahrt|faehrt|chauffieren|"
    r"conduire|conduis|conduisez)\b"
)
_RIDE = r"\b(a ride|a lift|airport transfer|prijevoz\w*|prevoz\w*|fahrdienst|covoiturage)\b"
_MEDICAL_ACT = (
    r"\b(give|giving|administer|inject|change|dress|dati|davati|dat|primiti|previti|"
    r"promijeniti|geben|spritzen|verabreichen|wechseln|setzen|donner|faire|changer|"
    r"administrer|injecter)\b"
)
_MEDICAL_OBJECT = (
    r"\b(injection|injections|insulin|catheter|wound|bandage|dressing|"
    r"injekcij\w*|inzulin\w*|kateter\w*|ran[aeu]|zavoj\w*|"
    r"spritze\w*|katheter\w*|wunde\w*|verband\w*|"
    r"insuline|sonde|pansement\w*)\b"
)
_MEDICAL_CARE = (
    r"\b(nursing care|wound care|previjanj\w*|njeg\w* ran\w*|krankenpflege|wundversorgung|"
    r"verbandswechsel|soins infirmiers|piqures?)\b"
)
_PERSONAL_CARE = (
    r"\b(bathe|bath|shower|wash|dress|undress)\s+(my\s+|our\s+)?"
    r"(mum|mom|mother|dad|father|grandma|grandmother|grandpa|grandfather|parents?)"
    r"\b(?![\'\u2019]s)",
    r"\b(okupati|kupati|oprati|presvuci|presvlaciti|obuci)\s+"
    r"(mamu|majku|tatu|oca|baku|djeda|roditelje|ga|ju|nju|njega)\b",
    r"\b(mutter|vater|mama|papa|oma|opa|eltern)\s+(zu\s+)?"
    r"(baden|duschen|waschen|anziehen|ausziehen|wickeln)\b",
    r"\bbeim\s+(baden|duschen|anziehen|ausziehen|waschen)\b",
    r"\b(laver|doucher|baigner|habiller|deshabiller)\s+(ma|mon|mes)\s+"
    r"(mere|pere|maman|papa|grand-mere|grand-pere|parents)\b",
    r"\bfaire\s+sa\s+toilette\b",
    r"\b(change|changing)\s+(\w+\s+)?(diapers?|nappy|nappies)\b",
    r"\bpromijeniti\s+(\w+\s+)?pelen\w*",
    r"\bwindeln?\s+wechseln\b",
    r"\bchanger\s+(\w+\s+)?couches?\b",
    r"\b(personal hygiene|osobn\w* higijen\w*|korperpflege|koerperpflege|intimpflege|"
    r"toilette intime)\b",
)

_RULES: tuple[tuple[ExcludedCategory, tuple[re.Pattern[str], ...]], ...] = tuple(
    (category, tuple(re.compile(pattern) for pattern in patterns))
    for category, patterns in (
        (ExcludedCategory.RIDES, (_DRIVE, _PERSON, _DESTINATION)),
        (ExcludedCategory.RIDES, (_RIDE, _PERSON)),
        (ExcludedCategory.MEDICAL_CARE, (_MEDICAL_ACT, _MEDICAL_OBJECT)),
        (ExcludedCategory.MEDICAL_CARE, (_MEDICAL_CARE,)),
        *((ExcludedCategory.PERSONAL_CARE, (pattern,)) for pattern in _PERSONAL_CARE),
    )
)
_SENTENCE_END = re.compile(r"[.!?;\n]+")


def screen_excluded(*texts: str) -> frozenset[ExcludedCategory]:
    """The excluded categories `texts` read like; empty when none."""
    found: set[ExcludedCategory] = set()
    for text in texts:
        for sentence in _SENTENCE_END.split(_normalise(text)):
            for category, patterns in _RULES:
                if category not in found and all(p.search(sentence) for p in patterns):
                    found.add(category)
    return frozenset(found)


def _normalise(text: str) -> str:
    # "ß" folds to "ss" and "ö" to "o"; German "ae/oe/ue" spellings are listed separately.
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
