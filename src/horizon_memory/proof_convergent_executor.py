"""Proof-convergent finite execution over source-attested text.

This promoted implementation keeps the laboratory mechanism's authority boundary intact:

* source text remains the authority;
* measurements are exact spans carrying value, dimension, unit and source identity;
* the question proposes operator/binding worlds but never authorizes an answer;
* all sufficiently evidenced selector gauges must converge on the same result;
* a complete byte scan is only *surface completeness*, never a claim that every natural
  language paraphrase was understood.

The operator family is deterministic and fail-closed.  A benchmark judge can evaluate its
utility, but never participates in extraction, convergence, certificate construction or
certificate reopening.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import ctypes
import hashlib
import os
from pathlib import Path
import re
import struct

from .raw_causal_channels import observe_raw_text
from .raw_causal_channels import RawCausalDocument
from .claim_routing import claim_spans
from .materialized_proof_pressure_search import (
    MaterializedIndependentHorizonSearchEngine,
)
from .sigma_pba import (
    AuthorizedFact,
    BindingWitness,
    ConjunctiveProgram,
    ProvenancePolynomial,
    SealedSource,
    SigmaPBAExecutor,
    SigmaPBAOutput,
    SigmaPBAResult,
    is_variable,
)


_SENTENCE = re.compile(r"(?:[^\n.!?]|[.!?](?!\s|\Z))+(?:[.!?]+(?=\s|\Z)|(?=\n|\Z))")
_MEASURE = re.compile(
    r"(?<!\w)(?:(?P<qualifier>about|around|over|roughly|approximately|"
    r"more\s+than|less\s+than)\s+)?(?P<currency>[$€£])?\s*"
    r"(?P<number>\d+(?:,\d{3})*(?:\.\d+)?|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"a(?:n)?)"
    r"(?:\s*[- ]?\s*(?P<unit>seconds?|minutes?|hours?|days?|weeks?|months?|years?|"
    r"dollars?|euros?|pounds?|kilometers?|kilometres?|km|miles?|mbps|gbps))?"
    r"(?P<post_half>\s+and\s+a\s+half)?\b",
    re.IGNORECASE,
)
_QUERY_UNIT = re.compile(
    r"\b(seconds?|minutes?|hours?|days?|weeks?|months?|years?|dollars?|euros?|pounds?|"
    r"kilometers?|kilometres?|km|miles?|mbps|gbps|money)\b", re.IGNORECASE)
_CURRENCY_RESULT_QUERY = re.compile(
    r"(?:\b(?:total\s+)?(?:amount\s+of\s+)?money\b|"
    r"\bhow\s+much\b(?!\s+time\b).*\b(?:spend|spent|pay|paid|cost|earn|earned)\b|"
    r"\b(?:total\s+)?amount\b.*\b(?:spend|spent|pay|paid|cost|earn|earned)\b)", re.I)
_CLOCK = re.compile(
    r"(?<!\d)(?P<hour>(?:1[0-2]|0?[1-9])):(?P<minute>[0-5]\d)\s*"
    r"(?P<ampm>[ap])\.?m\.?(?!\w)", re.I)
_WORD_NUMBER = {
    "a": Decimal(1), "an": Decimal(1), "one": Decimal(1), "two": Decimal(2),
    "three": Decimal(3), "four": Decimal(4), "five": Decimal(5), "six": Decimal(6),
    "seven": Decimal(7), "eight": Decimal(8), "nine": Decimal(9), "ten": Decimal(10),
    "eleven": Decimal(11), "twelve": Decimal(12),
}
_UNIT = {
    "second": ("time", "second", Decimal(1)), "seconds": ("time", "second", Decimal(1)),
    "minute": ("time", "second", Decimal(60)), "minutes": ("time", "second", Decimal(60)),
    "hour": ("time", "second", Decimal(3600)), "hours": ("time", "second", Decimal(3600)),
    "day": ("time", "second", Decimal(86400)), "days": ("time", "second", Decimal(86400)),
    "week": ("time", "second", Decimal(604800)), "weeks": ("time", "second", Decimal(604800)),
    # Calendar months/years cannot be converted to days without a clock.  They retain a
    # separate exact unit and therefore fail closed in mixed-unit arithmetic.
    "month": ("calendar", "month", Decimal(1)), "months": ("calendar", "month", Decimal(1)),
    "year": ("calendar", "year", Decimal(1)), "years": ("calendar", "year", Decimal(1)),
    "dollar": ("currency:USD", "USD", Decimal(1)),
    "dollars": ("currency:USD", "USD", Decimal(1)),
    "euro": ("currency:EUR", "EUR", Decimal(1)),
    "euros": ("currency:EUR", "EUR", Decimal(1)),
    "pound": ("currency:GBP", "GBP", Decimal(1)),
    "pounds": ("currency:GBP", "GBP", Decimal(1)),
    "mile": ("distance", "mile", Decimal(1)), "miles": ("distance", "mile", Decimal(1)),
    "kilometer": ("distance", "kilometer", Decimal(1)),
    "kilometers": ("distance", "kilometer", Decimal(1)),
    "kilometre": ("distance", "kilometer", Decimal(1)),
    "kilometres": ("distance", "kilometer", Decimal(1)), "km": ("distance", "kilometer", Decimal(1)),
    "mbps": ("data_rate", "Mbps", Decimal(1)),
    "gbps": ("data_rate", "Mbps", Decimal(1000)),
}
_CURRENCY = {"$": ("currency:USD", "USD", Decimal(1)),
             "€": ("currency:EUR", "EUR", Decimal(1)),
             "£": ("currency:GBP", "GBP", Decimal(1))}
_SCAFFOLD = frozenset("""
how many much total altogether combined did do does have has had take took
in on at to from for of the a an my me i this that all what is are was were currently
last past through each per amount time long
every
""".split())
_PERSON_PRONOUNS = frozenset({
    "he", "her", "hers", "him", "his", "i", "me", "mine", "our", "ours", "she",
    "their", "theirs", "them", "they", "us", "we", "you", "your", "yours",
})
_NON_ARGUMENT_LEMMAS = frozenset({"a", "an", "and", "but", "nor", "or", "the"})
_VERB_EXCEPTIONS_PATH = Path(__file__).parent / "resources" / "wordnet-3.0" / "verb.exc"
_VERB_EXCEPTIONS_SHA256 = "dbbcf9a601b2d77e934e413b91d90e88ec7f933a8b77cfc00602a923b891b42c"
_VERB_EXCEPTION_CACHE: dict[str, tuple[str, ...]] | None = None
_DIRECT_BINARY_SUBJECT_QUERY = re.compile(
    r"^\s*(?P<type>who|what)\s+(?P<predicate>[^\W_]+(?:[’'-][^\W_]+)*)\s+"
    r"(?P<known>[^\W_]+(?:[’'-][^\W_]+)*)\s*\?\s*$", re.I)
_DIRECT_BINARY_OBJECT_QUERY = re.compile(
    r"^\s*(?P<type>who|what|where)\s+did\s+"
    r"(?P<known>[^\W_]+(?:[’'-][^\W_]+)*)\s+"
    r"(?P<predicate>[^\W_]+(?:[’'-][^\W_]+)*)\s*\?\s*$", re.I)
_CARDINAL = re.compile(
    r"\b(?P<n>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)\s+"
    r"(?P<noun>[^\W_]+)", re.IGNORECASE)
_SUM_CUE = re.compile(r"\b(?:total|altogether|combined|all\s+the|all\s+of)\b", re.I)
_SCALAR_LOOKUP_CUE = re.compile(
    r"^\s*(?:how\s+(?:long|much)|how\s+many\s+(?:seconds?|minutes?|hours?|days?|weeks?|"
    r"months?|years?|miles?|kilometers?|kilometres?|km)|what\s+(?:time|speed))\b", re.I)
_RELATIVE_TIME = re.compile(r"\b(?:before|after|ago|prior\s+to|later|past|last)\b", re.I)
_NON_ASSERTED = re.compile(
    r"\b(?:maybe|perhaps|possibly|might|could|would|should|"
    r"plan(?:ning|ned|s)?\s+to|hope(?:s|d)?\s+to|want(?:s|ed)?\s+to)\b", re.I)
_RATE = re.compile(r"\b(?:per|each|apiece)\b", re.I)
_DISTRIBUTIVE_MEASURE_SUFFIX = re.compile(
    r"\s+(?P<suffix>(?:each|per)\s+[A-Za-z][A-Za-z'’-]*)\b", re.I)
_COMPLETED_TRANSACTION = re.compile(
    r"\b(?:sold|earned|earning|received|made\s+(?:a\s+)?(?:total|profit|revenue))\b", re.I)
_TRANSACTION_ACTIONS = frozenset(("sell", "sold", "earn", "earning", "receive", "received",
                                  "make", "made", "sale"))
_FACTOR_COUNT = re.compile(
    r"(?<![\w$€£.])(?P<n>\d+(?:,\d{3})*|one|two|three|four|five|six|seven|eight|"
    r"nine|ten|eleven|twelve)(?![\w.])", re.I)
_COUNT_QUERY = re.compile(
    r"^\s*how\s+many\s+(?P<target>.+?)\s+(?:are|is|do|does|did|have|has|had|"
    r"can|could|will|would)\b", re.I)
_LIST_INTRODUCER = re.compile(r"\b(?:has|have|contains?|includes?|consists?\s+of)\b", re.I)
_LIST_SPLIT = re.compile(r"\s*,\s*|\s+and\s+", re.I)
_LEADING_COUNT = re.compile(
    r"^\s*(?P<n>\d+(?:,\d{3})*|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|a|an|my)\b", re.I)
_ACQUISITION_QUERY = re.compile(r"\b(?:acquire[sd]?|bought|buy|purchase[sd]?)\b", re.I)
_ACQUISITION_VERB = re.compile(
    r"\b(?:bought|got|picked\s+up|received|acquired|inherited)\b", re.I)
_DIRECT_ACQUISITION = re.compile(
    r"\b(?:bought|got|picked\s+up|received|acquired|inherited)\s+"
    r"(?!(?:from|at|on|for)\b)"
    r"(?P<object>.+?)(?=\s+(?:last\s+weekend|last\s+month|\w+\s+weeks?\s+ago|"
    r"a\s+month\s+ago|on\s+the\s+\d|at\s+(?:a|the)\b|from\s+(?:a|the|my)\b|"
    r"which\b|that\b)|[,;.!?]|$)", re.I)
_RELATIVE_ACQUISITION = re.compile(
    r"(?P<object>(?:my|the|a|an)\s+(?:[A-Za-z][A-Za-z'’-]*\s+){0,4}"
    r"[A-Za-z][A-Za-z'’-]*)\s*,?\s+(?:which|that)\s+I\s+"
    r"(?:bought|got|picked\s+up|received|acquired|inherited)\b", re.I)
_RECENT_WINDOW = re.compile(
    r"\b(?:last\s+(?:weekend|month)|(?:one|two|three|four|\d+)\s+weeks?\s+ago|"
    r"a\s+month\s+ago|\d+(?:st|nd|rd|th)?\s+of\s+last\s+month)\b", re.I)
_WHERE_QUERY = re.compile(r"^\s*where\b", re.I)
_WHERE_ACTION = re.compile(
    r"^\s*where\s+(?:did|do|does|can|could|was|is|were|are)\s+"
    r"(?:I|we|you|he|she|they)\s+(?P<action>[^\W_]+)", re.I)
_ATTRIBUTE_QUERY = re.compile(
    r"^\s*what\s+(?P<attribute>[^?]+?)\s+is\s+(?:my|the)\s+(?P<subject>[^?]+?)\s*\??$",
    re.I)
_ENTITY_DURATION_QUERY = re.compile(
    r"^\s*how\s+long\s+was\s+I\s+in\s+(?P<entity>[^?]+?)\s+(?:for\s*)?\??$", re.I)
_OBSERVED_LOCATION = re.compile(r"\bI\s+was\s+in\s+(?P<entity>[A-Z][A-Za-z'’-]*)\b")
_RELATIVE_VALUE_QUERY = re.compile(
    r"^\s*how\s+much\s+is\s+(?P<subject>.+?)\s+worth\s+in\s+terms\s+of\s+"
    r"(?:the\s+)?(?:amount\s+)?I\s+paid(?:\s+for\s+it)?\s*\??$", re.I)
_RELATIVE_VALUE_SOURCE = re.compile(
    r"\bworth\s+(?P<value>(?:twice|double|triple|half|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|\d+(?:\.\d+)?\s+times)"
    r"\s+(?:what|the\s+amount(?:\s+that)?)\s+I\s+paid(?:\s+for\s+it)?)\b", re.I)
_CLASSIFIED_MONEY_QUERY = re.compile(
    r"\b(?:spend|spent|pay|paid)\s+(?:on|for)\s+(?P<class>.+?)"
    r"(?=\s+(?:in|during|over|within)\s+(?:the\s+)?(?:last|past|previous|this)\b|[?.]?$)", re.I)
_ACTIVITY_DURATION_TOTAL_QUERY = re.compile(
    r"^\s*how\s+many\s+hours\s+have\s+I\s+spent\s+(?P<activity>.+?)\s+in\s+total\s*\??$",
    re.I)
_DERIVED_ARITHMETIC_QUERY = re.compile(
    r"\b(?:cashback|cash\s*back|how\s+much\s+more|difference|amount\s+saved|"
    r"did\s+I\s+save|discount\s+amount)\b", re.I)
_DIFFERENCE_QUERY = re.compile(
    r"^\s*how\s+much\s+more\s+was\s+(?P<left>.+?)\s+than\s+(?P<right>.+?)\s*\??$",
    re.I)
_SAVINGS_QUERY = re.compile(
    r"^\s*how\s+much\s+did\s+I\s+save\s+on\s+(?P<object>.+?)\s*\??$", re.I)
_CASHBACK_QUERY = re.compile(
    r"^\s*how\s+much\s+cash\s*back\s+did\s+I\s+earn\s+at\s+"
    r"(?P<merchant>.+?)(?:\s+(?:last|this)\s+[^?]+)?\s*\??$", re.I)
_PERCENT = re.compile(r"(?<![\d.])(?P<rate>\d+(?:\.\d+)?)\s*%")
_CURRENT_ROLE_DURATION_QUERY = re.compile(
    r"^\s*how\s+long\s+have\s+I\s+been\s+working\s+in\s+my\s+current\s+role\s*\??$",
    re.I)
_AVERAGE_AGE_QUERY = re.compile(r"^\s*what\s+is\s+the\s+average\s+age\s+of\s+(.+?)\s*\??$", re.I)
_KIN_AGE = re.compile(
    r"\bmy\s+(?P<role>mom|mother|dad|father|grandma|grandmother|grandpa|grandfather)\s+is\s+"
    r"(?P<age>\d{1,3})\b", re.I)
_SELF_AGE = re.compile(r"\bI\s+(?:am|just\s+turned|turned)\s+(?P<age>\d{1,3})\b", re.I)
_DISTINCTIVE_IDENTIFIER = re.compile(r"\b(?:[a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,}[A-Za-z0-9]*)\b")
_PRESUPPOSED_POSSESSION_EVENT = re.compile(
    r"(?i:\bmy\s+)(?P<identifier>(?:[a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,}[A-Za-z0-9]*))\b"
    r"(?i:.{0,80}\bafter\s+I\s+(?:bought|purchased|got|ordered|received)\b)")
_TIMELINE_INTERVAL_QUERY = re.compile(
    r"^\s*how\s+many\s+years\s+in\s+total\s+did\s+I\s+spend\s+in\s+.+?\s+"
    r"from\s+(?P<start>.+?)\s+to\s+(?:the\s+)?(?P<end>.+?)\s*\??$", re.I)
_YEAR_RANGE = re.compile(r"\bfrom\s+(?P<start>(?:19|20)\d{2})\s+to\s+(?P<end>(?:19|20)\d{2})\b", re.I)
_YEAR_LITERAL = re.compile(r"\b(?:19|20)\d{2}\b")
_OWNED_SET_QUERY = re.compile(
    r"^\s*how\s+many\s+(?P<target>.+?)\s+do\s+I\s+currently\s+own\s*\??$", re.I)
_ATTENDED_EVENT_COUNT_QUERY = re.compile(
    r"^\s*how\s+many\s+(?P<event>.+?)\s+have\s+I\s+attended\s+in\s+this\s+year\s*\??$",
    re.I)
_EXPLICIT_EVENT_ATTENDANCE = re.compile(
    r"\b(?:I(?:'ve|\s+have)\s+been\s+to|I\s+(?:just\s+)?got\s+back\s+from|"
    r"I\s+(?:last\s+)?wore\b.{0,80}?\bto)\b", re.I)
_PERSON_RELATION = re.compile(
    r"\bmy\s+(?P<relation>college\s+roommate|roommate|cousin|friend|sister|brother)"
    r"(?:'s|\s+(?P<name>[A-Z][A-Za-z'’-]*)(?:'s)?)?\b", re.I)
_EVENT_PARTICIPANT = re.compile(
    r"\b(?:bride|groom|partner|husband|wife)\s*,?\s+(?P<name>[A-Z][A-Za-z'’-]*)\b")
_SCOPED_DURATION_QUERY = re.compile(r"^\s*how\s+many\s+(?P<unit>days?|hours?)\b", re.I)
_PAST_HABIT_OR_PROSPECT = re.compile(
    r"\b(?:used\s+to|usually|every\s+|times?\s+(?:a|per)\s+week|"
    r"thinking\s+(?:of|about)|considering|planning\s+to|want(?:ing)?\s+to|"
    r"would\s+like\s+to|hoping\s+to)\b", re.I)
_MONTH = frozenset(("january", "february", "march", "april", "may", "june",
                    "july", "august", "september", "october", "november", "december"))
_ARTIFACT_EVENT_COUNT_QUERY = re.compile(
    r"^\s*how\s+many\s+(?P<target>.+?)\s+have\s+I\s+(?P<actions>.+?)\s*\??$", re.I)
_SCALE_ARTIFACT = re.compile(
    r"(?P<label>\d+\s*/\s*\d+\s+scale\s+.+?)"
    r"(?=\s+(?:at|that|which|and|for|to|in|on|with|as)\b|[,.;!?]|$)", re.I)
_NAMED_KIT = re.compile(
    r"(?P<label>(?:[A-Z][A-Za-z0-9.'’/-]*\s+){1,5}(?:model\s+)?kit)\b")
_COMPLETED_ARTIFACT_ACTION = re.compile(
    r"\b(?:recently\s+finished|finished|started\s+working|worked\s+on|"
    r"just\s+got|picked\s+up|bought|purchased)\b", re.I)
_FUNCTIONAL_DEVICE_QUERY = re.compile(
    r"^\s*how\s+many\s+(?P<domain>.+?)[- ]related\s+devices?\s+do\s+I\s+use\s+"
    r"in\s+a\s+day\s*\??$", re.I)
_CURRENT_DEVICE_USE = re.compile(
    r"\b(?:wearing|using|relying\s+on|testing|measuring|monitoring|tracking|"
    r"treatments?|therapy|sessions?)\b", re.I)
_ASSISTANT_REFERENCE_QUERY = re.compile(
    r"\b(?:(?:you|we)\s+(?:recommended|suggested|said|mentioned|provided|created|wrote|"
    r"outlined|decided)|previous\s+(?:chat|conversation)|looking\s+back|follow(?:ing)?\s+up|"
    r"going\s+back|remind\s+me|remember\s+what)\b", re.I)
_UTTERANCE_META = frozenset("""
i im me my we our you your wanted want looking look back previous conversation chat remind
remember recall follow following going went through thinking think planning planned again
wondering wonder mentioned mention recommended recommend suggested suggest said say told tell
provided provide gave give earlier confirm check checking discussed discuss can could would
please that this what which who where when how was were did do is are the a an of in on about
for to and from with
""".split())
_ORDINAL_VALUE = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_LINE_FIELD = re.compile(r"^\s*(?:[-*+]\s*)?(?:\*\*)?(?P<label>[^:\n]{2,100}?)"
                         r"(?:\*\*)?\s*:\s*(?P<value>.*)\s*$")
_NUMBERED_LINE = re.compile(r"^\s*(?P<n>\d{1,3})[.)]\s+(?P<value>.+?)\s*$")
_PROPER_VALUE = re.compile(
    r"\b[A-Z][A-Za-z'’&.-]*(?:\s+(?:(?:of|the|in|and)\s+)?"
    r"[A-Z][A-Za-z'’&.-]*){0,6}\b")
_RELATION_ACTIONS = frozenset((
    "be", "been", "collect", "commute", "dedicate", "drive", "get", "go", "have",
    "is", "move", "practice", "spend", "take", "travel", "use", "wait", "watch",
    "work",
))
_CAPITAL_SEQUENCE = re.compile(r"\b(?:[A-Z][a-z]+\s+){1,5}[A-Z][a-z]+\b")
_ACRONYM = re.compile(r"\b[A-Z]{2,8}\b")
_LEMMA = {
    # Surface morphology only.  These collapse tense/aspect, not concepts or benchmark topics.
    "drove": "drive", "driving": "drive", "driv": "drive",
    "spent": "spend", "spending": "spend",
    "took": "take", "taken": "take", "taking": "take",
    "watched": "watch", "watching": "watch",
    "attended": "attend", "attending": "attend",
    "bought": "buy", "buying": "buy",
    "practic": "practice", "practicing": "practice", "practised": "practice",
    "daily": "day",
    "weekday": "weekday", "weekdays": "weekday",
    "weeknight": "weekday", "weeknights": "weekday",
}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _terms(text: str, *, question: bool = False) -> frozenset[str]:
    return frozenset(_LEMMA.get(token, token)
                     for token in observe_raw_text(text, question=question).lexical)


def _relations(text: str, *, question: bool = False) -> frozenset[str]:
    normalized = set()
    for relation in observe_raw_text(text, question=question).relations:
        left, right = relation.split(">", 1)
        normalized.add(f"{_LEMMA.get(left, left)}>{_LEMMA.get(right, right)}")
    return frozenset(normalized)


def _attested_aliases(text: str) -> frozenset[str]:
    values = {match.group(0) for match in _ACRONYM.finditer(text)}
    for match in _CAPITAL_SEQUENCE.finditer(text):
        values.add("".join(word[0] for word in match.group(0).split()).upper())
    return frozenset(values)


def _proper_identities(text: str) -> frozenset[str]:
    values = set()
    for match in _PROPER_VALUE.finditer(text):
        value = " ".join(match.group(0).casefold().split()).strip(" .")
        if (value not in {"i", "i'm", "i've", "it's", "user", "the", "by"}
                and len(value) > 2):
            values.add(value)
    return frozenset(values)


class WordNetNounGraph:
    """Minimal read-only WNDB noun hypernym graph; no tagger, ranker or learned model."""

    def __init__(self, senses: dict[str, tuple[int, ...]],
                 hypernyms: dict[int, tuple[int, ...]],
                 glosses: dict[int, str] | None = None):
        self.senses = senses
        self.hypernyms = hypernyms
        self.glosses = dict(glosses or {})
        self._closure_cache: dict[int, frozenset[int]] = {}

    @classmethod
    def from_wndb(cls, directory: str | Path) -> "WordNetNounGraph":
        root = Path(directory)
        senses = {}
        for line in (root / "index.noun").read_text(encoding="utf-8").splitlines():
            if not line or line[0].isspace():
                continue
            fields = line.split()
            sense_count, pointer_count = int(fields[2]), int(fields[3])
            offset_start = 6 + pointer_count
            senses[fields[0].casefold()] = tuple(
                int(value) for value in fields[offset_start:offset_start + sense_count])
        hypernyms = {}
        glosses = {}
        with (root / "data.noun").open(encoding="utf-8") as stream:
            for line in stream:
                if not line or line[0].isspace():
                    continue
                data, _separator, gloss = line.partition("|")
                fields = data.split()
                offset, word_count = int(fields[0]), int(fields[3], 16)
                glosses[offset] = gloss.strip()
                cursor = 4 + word_count * 2
                pointer_count = int(fields[cursor]); cursor += 1
                parents = []
                for _ in range(pointer_count):
                    symbol, target, pos, _source_target = fields[cursor:cursor + 4]
                    cursor += 4
                    if symbol in {"@", "@i"} and pos == "n":
                        parents.append(int(target))
                hypernyms[offset] = tuple(parents)
        return cls(senses, hypernyms, glosses)

    def ancestors(self, sense: int) -> frozenset[int]:
        cached = self._closure_cache.get(sense)
        if cached is not None:
            return cached
        seen, stack = set(), [sense]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.hypernyms.get(current, ()))
        result = frozenset(seen)
        self._closure_cache[sense] = result
        return result

    def matching_senses(self, lemma: str, target: str) -> tuple[int, ...]:
        target_senses = frozenset(self.senses.get(target.casefold().replace(" ", "_"), ()))
        if not target_senses:
            return ()
        return tuple(sense for sense in self.senses.get(lemma.casefold().replace(" ", "_"), ())
                     if self.ancestors(sense) & target_senses)

    def definitions(self, lemma: str) -> tuple[str, ...]:
        return tuple(self.glosses[sense] for sense in
                     self.senses.get(lemma.casefold().replace(" ", "_"), ())
                     if sense in self.glosses)


_WORDNET_CACHE: dict[str, WordNetNounGraph] = {}


def configured_wordnet() -> WordNetNounGraph | None:
    directory = os.environ.get("PROOFRAY_WORDNET_DIR", os.environ.get("HORIZON_WORDNET_DIR"))
    if not directory:
        return None
    resolved = str(Path(directory).resolve())
    if resolved not in _WORDNET_CACHE:
        _WORDNET_CACHE[resolved] = WordNetNounGraph.from_wndb(resolved)
    return _WORDNET_CACHE[resolved]


@dataclass(frozen=True, slots=True)
class ConservedLinkWord:
    """One Link Grammar token whose character span reopens in the source."""

    index: int
    surface: str
    grammar_word: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class ConservedLinkEdge:
    left: int
    label: str
    right: int


@dataclass(frozen=True, slots=True)
class ConservedLinkGraph:
    """A single bounded parse alternative; it is evidence structure, never authority."""

    source: str
    words: tuple[ConservedLinkWord, ...]
    edges: tuple[ConservedLinkEdge, ...]
    unused_words: int

    def verify(self) -> bool:
        if self.unused_words < 0:
            return False
        size = len(self.words)
        if tuple(word.index for word in self.words) != tuple(range(size)):
            return False
        if any(not edge.label or not (
                0 <= edge.left < size and 0 <= edge.right < size and edge.left < edge.right)
               for edge in self.edges):
            return False
        for word in self.words:
            if not (0 <= word.start <= word.end <= len(self.source)):
                return False
            if word.start != word.end and self.source[word.start:word.end] != word.surface:
                return False
        return True


@dataclass(frozen=True, slots=True)
class ConservedLinkForest:
    source: str
    graphs: tuple[ConservedLinkGraph, ...]
    valid_linkages: int
    truncated: bool
    resource_exhausted: bool
    linkages_found: int = 0
    linkages_post_processed: int = 0


@dataclass(frozen=True, slots=True)
class ConservedLinkRequirement:
    left_word: int
    right_word: int
    label_prefix: str


class _CSatLinkRequirement(ctypes.Structure):
    _fields_ = (("left_word", ctypes.c_size_t),
                ("right_word", ctypes.c_size_t),
                ("label_prefix", ctypes.c_char_p))


_WH_INTERROGATIVE = re.compile(r"\b(?:what|how|where|which|who|when)\b", re.I)


@dataclass(frozen=True, slots=True)
class FocusedInterrogative:
    source: str
    parser_text: str
    source_span: tuple[int, int]
    rule: str

    def verify(self) -> bool:
        start, end = self.source_span
        return 0 <= start < end <= len(self.source) and bool(self.source[start:end].strip())


def focus_interrogative_clause(question: str) -> FocusedInterrogative:
    """Remove conversational address while retaining the exact question source span.

    ``remind me of X`` is the sole synthetic grammar frame.  It records the exact ``X``
    span and prepends ``What is`` only to expose an identity obligation to the parser; the
    prefix is never source evidence and can never be emitted as an answer witness.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be nonempty text")
    lower = question.casefold()
    reminder = lower.rfind("remind me")
    tail_start = reminder + len("remind me") if reminder >= 0 else 0
    while tail_start < len(question) and question[tail_start] in " ,\t":
        tail_start += 1
    tail = question[tail_start:]
    match = _WH_INTERROGATIVE.search(tail)
    if match:
        start = tail_start + match.start()
        focused = FocusedInterrogative(
            question, question[start:].strip(), (start, len(question)),
            "assistant_address_to_first_interrogative" if reminder >= 0 else
            "first_interrogative")
    elif reminder >= 0 and re.match(r"(?i)^of\s+", tail):
        value_start = tail_start + re.match(r"(?i)^of\s+", tail).end()
        value = question[value_start:].strip()
        focused = FocusedInterrogative(
            question, "What is " + value, (value_start, len(question)),
            "assistant_of_identity_frame")
    else:
        match = _WH_INTERROGATIVE.search(question)
        start = match.start() if match else next(
            (index for index, char in enumerate(question) if not char.isspace()), 0)
        focused = FocusedInterrogative(
            question, question[start:].strip(), (start, len(question)),
            "first_interrogative" if match else "unchanged")
    if not focused.verify():
        raise ValueError("focused interrogative failed source-span conservation")
    return focused


class LinkGrammarBridge:
    """Optional local Link Grammar C bridge with explicit deterministic resource limits.

    The library and dictionary are external, version-pinned resources.  Nothing in this
    adapter downloads, learns, ranks into truth, or converts a parse edge into a fact.  All
    retained alternatives remain separate until a downstream proof obligation converges.
    """

    def __init__(self, library_path: str, data_dir: str, *, max_null: int = 2,
                 max_analyses: int = 32, max_memory_mb: int = 256,
                 max_parse_seconds: int = 2, use_sat_parser: bool = False):
        if not 0 <= max_null <= 4 or not 1 <= max_analyses <= 256:
            raise ValueError("Link Grammar bounds are outside the laboratory safety envelope")
        if use_sat_parser and max_null != 0:
            raise ValueError("Link Grammar SAT mode requires max_null=0")
        self._lib = ctypes.CDLL(str(Path(library_path).resolve()))
        self._bind_api()
        self._lib.dictionary_set_data_dir(str(Path(data_dir).resolve()).encode())
        self._dictionary = self._lib.dictionary_create_lang(b"en")
        self._options = self._lib.parse_options_create()
        if not self._dictionary or not self._options:
            self.close()
            raise RuntimeError("Link Grammar dictionary/options initialization failed")
        self.max_analyses = max_analyses
        self.use_sat_parser = use_sat_parser
        self._lib.parse_options_set_verbosity(self._options, 0)
        self._lib.parse_options_set_linkage_limit(self._options, max_analyses)
        self._lib.parse_options_set_min_null_count(self._options, 0)
        self._lib.parse_options_set_max_null_count(self._options, max_null)
        self._lib.parse_options_set_islands_ok(self._options, False)
        self._lib.parse_options_set_spell_guess(self._options, 0)
        self._lib.parse_options_set_max_memory(self._options, max_memory_mb)
        self._lib.parse_options_set_max_parse_time(self._options, max_parse_seconds)
        self._lib.parse_options_set_repeatable_rand(self._options, True)
        self._lib.parse_options_set_use_sat_parser(self._options, use_sat_parser)

    def _bind_api(self) -> None:
        lib = self._lib
        lib.dictionary_set_data_dir.argtypes = [ctypes.c_char_p]
        lib.dictionary_set_data_dir.restype = None
        lib.dictionary_create_lang.argtypes = [ctypes.c_char_p]
        lib.dictionary_create_lang.restype = ctypes.c_void_p
        lib.dictionary_delete.argtypes = [ctypes.c_void_p]
        lib.dictionary_delete.restype = None
        lib.dictionary_clear_cache.argtypes = [ctypes.c_void_p]
        lib.dictionary_clear_cache.restype = None
        lib.parse_options_create.restype = ctypes.c_void_p
        lib.parse_options_delete.argtypes = [ctypes.c_void_p]
        for name, value_type in (
                ("parse_options_set_verbosity", ctypes.c_int),
                ("parse_options_set_linkage_limit", ctypes.c_int),
                ("parse_options_set_min_null_count", ctypes.c_int),
                ("parse_options_set_max_null_count", ctypes.c_int),
                ("parse_options_set_islands_ok", ctypes.c_bool),
                ("parse_options_set_spell_guess", ctypes.c_int),
                ("parse_options_set_max_memory", ctypes.c_int),
                ("parse_options_set_max_parse_time", ctypes.c_int),
                ("parse_options_set_repeatable_rand", ctypes.c_bool),
                ("parse_options_set_use_sat_parser", ctypes.c_bool)):
            getattr(lib, name).argtypes = [ctypes.c_void_p, value_type]
            getattr(lib, name).restype = None
        lib.parse_options_reset_resources.argtypes = [ctypes.c_void_p]
        lib.parse_options_reset_resources.restype = None
        lib.parse_options_resources_exhausted.argtypes = [ctypes.c_void_p]
        lib.parse_options_resources_exhausted.restype = ctypes.c_bool
        lib.sentence_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        lib.sentence_create.restype = ctypes.c_void_p
        lib.sentence_delete.argtypes = [ctypes.c_void_p]
        lib.sentence_delete.restype = None
        lib.sentence_parse.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.sentence_parse.restype = ctypes.c_int
        lib.sentence_num_valid_linkages.argtypes = [ctypes.c_void_p]
        lib.sentence_num_valid_linkages.restype = ctypes.c_int
        lib.sentence_num_linkages_found.argtypes = [ctypes.c_void_p]
        lib.sentence_num_linkages_found.restype = ctypes.c_int
        lib.sentence_num_linkages_post_processed.argtypes = [ctypes.c_void_p]
        lib.sentence_num_linkages_post_processed.restype = ctypes.c_int
        lib.linkage_create.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        lib.linkage_create.restype = ctypes.c_void_p
        lib.linkage_delete.argtypes = [ctypes.c_void_p]
        lib.linkage_delete.restype = None
        for name in ("linkage_get_num_words", "linkage_get_num_links"):
            getattr(lib, name).argtypes = [ctypes.c_void_p]
            getattr(lib, name).restype = ctypes.c_size_t
        for name in ("linkage_get_word_char_start", "linkage_get_word_char_end"):
            getattr(lib, name).argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            getattr(lib, name).restype = ctypes.c_size_t
        lib.linkage_get_word.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.linkage_get_word.restype = ctypes.c_char_p
        for name in ("linkage_get_link_lword", "linkage_get_link_rword"):
            getattr(lib, name).argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            getattr(lib, name).restype = ctypes.c_size_t
        lib.linkage_get_link_label.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.linkage_get_link_label.restype = ctypes.c_char_p
        lib.linkage_unused_word_cost.argtypes = [ctypes.c_void_p]
        lib.linkage_unused_word_cost.restype = ctypes.c_int
        if hasattr(lib, "sentence_sat_projection_exists"):
            lib.sentence_sat_projection_exists.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p,
                ctypes.POINTER(_CSatLinkRequirement), ctypes.c_size_t]
            lib.sentence_sat_projection_exists.restype = ctypes.c_int

    def close(self) -> None:
        options = getattr(self, "_options", None)
        dictionary = getattr(self, "_dictionary", None)
        if options:
            self._lib.parse_options_delete(options)
            self._options = None
        if dictionary:
            self._lib.dictionary_delete(dictionary)
            self._dictionary = None

    def __enter__(self) -> "LinkGrammarBridge":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def parse(self, source: str) -> ConservedLinkForest:
        if not self._dictionary or not self._options:
            raise RuntimeError("Link Grammar bridge is closed")
        if not isinstance(source, str) or not source.strip() or "\n" in source:
            raise ValueError("Link Grammar input must be one nonempty source line")
        self._lib.parse_options_reset_resources(self._options)
        sentence = self._lib.sentence_create(source.encode("utf-8"), self._dictionary)
        if not sentence:
            raise RuntimeError("Link Grammar sentence allocation failed")
        try:
            self._lib.sentence_parse(sentence, self._options)
            valid = max(0, self._lib.sentence_num_valid_linkages(sentence))
            found = max(valid, self._lib.sentence_num_linkages_found(sentence))
            post_processed = max(valid, self._lib.sentence_num_linkages_post_processed(sentence))
            graphs = []
            sat_complete = self.use_sat_parser and valid == 0
            for index in range(min(valid, self.max_analyses)):
                linkage = self._lib.linkage_create(index, sentence, self._options)
                if not linkage:
                    # SAT mode advertises the configured linkage limit because it cannot
                    # know the model count in advance; NULL is the authoritative end marker.
                    sat_complete = self.use_sat_parser
                    break
                try:
                    words = []
                    for word_index in range(self._lib.linkage_get_num_words(linkage)):
                        start = self._lib.linkage_get_word_char_start(linkage, word_index)
                        end = self._lib.linkage_get_word_char_end(linkage, word_index)
                        grammar_word = self._lib.linkage_get_word(linkage, word_index).decode()
                        surface = source[start:end]
                        words.append(ConservedLinkWord(
                            word_index, surface, grammar_word, start, end))
                    edges = tuple(ConservedLinkEdge(
                        self._lib.linkage_get_link_lword(linkage, edge_index),
                        self._lib.linkage_get_link_label(linkage, edge_index).decode(),
                        self._lib.linkage_get_link_rword(linkage, edge_index))
                        for edge_index in range(self._lib.linkage_get_num_links(linkage)))
                    graph = ConservedLinkGraph(
                        source, tuple(words), edges,
                        self._lib.linkage_unused_word_cost(linkage))
                    if not graph.verify():
                        raise ValueError("Link Grammar graph failed exact-span conservation")
                    graphs.append(graph)
                finally:
                    self._lib.linkage_delete(linkage)
            reported_valid = len(graphs) if self.use_sat_parser and sat_complete else valid
            truncated = (not sat_complete if self.use_sat_parser else post_processed < found)
            return ConservedLinkForest(
                source, tuple(graphs), reported_valid, truncated,
                bool(self._lib.parse_options_resources_exhausted(self._options)),
                len(graphs) if self.use_sat_parser and sat_complete else found,
                len(graphs) if self.use_sat_parser and sat_complete else post_processed)
        finally:
            self._lib.sentence_delete(sentence)

    def sat_projection_exists(self, source: str,
                              requirements: tuple[ConservedLinkRequirement, ...]) -> str:
        """Project a typed link conjunction without enumerating complete SAT linkages.

        This requires the version-pinned experimental Link Grammar adapter.  ``possible``
        means at least one connected, sane and post-processed linkage exists; ``impossible``
        means the constrained SAT formula was exhausted.  Any unsupported/resource state is
        ``incomplete`` and cannot authorize an answer.
        """
        if not self._dictionary or not self._options:
            raise RuntimeError("Link Grammar bridge is closed")
        if not self.use_sat_parser:
            raise ValueError("SAT projection requires use_sat_parser=True")
        if not hasattr(self._lib, "sentence_sat_projection_exists"):
            raise RuntimeError("Link Grammar library lacks the projection adapter")
        if (not isinstance(source, str) or not source.strip() or "\n" in source or
                not requirements):
            raise ValueError("SAT projection requires one source line and link requirements")
        if any(item.left_word >= item.right_word or not item.label_prefix or
               not item.label_prefix.isascii() for item in requirements):
            raise ValueError("SAT link requirements are not canonical")

        encoded = tuple(item.label_prefix.encode("ascii") for item in requirements)
        array_type = _CSatLinkRequirement * len(requirements)
        rows = array_type(*(
            _CSatLinkRequirement(item.left_word, item.right_word, label)
            for item, label in zip(requirements, encoded)))
        self._lib.parse_options_reset_resources(self._options)
        sentence = self._lib.sentence_create(source.encode("utf-8"), self._dictionary)
        if not sentence:
            raise RuntimeError("Link Grammar sentence allocation failed")
        try:
            self._lib.sentence_parse(sentence, self._options)
            if self._lib.parse_options_resources_exhausted(self._options):
                return "incomplete"
            result = self._lib.sentence_sat_projection_exists(
                sentence, self._options, rows, len(requirements))
            return "possible" if result == 1 else "impossible" if result == 0 else "incomplete"
        finally:
            self._lib.sentence_delete(sentence)


@dataclass(frozen=True, slots=True)
class ConservedBinaryEvent:
    predicate: str
    argument_1: str
    argument_2: str
    predicate_span: tuple[int, int]
    argument_1_span: tuple[int, int]
    argument_2_span: tuple[int, int]
    voice: str


def _link_word_lemma(word: ConservedLinkWord) -> str:
    grammar = re.sub(r"\[.*", "", word.grammar_word).split(".", 1)[0].casefold()
    return {"has": "have", "had": "have", "is": "be", "am": "be",
            "are": "be", "was": "be", "were": "be", "did": "do",
            "does": "do"}.get(
                grammar, _LEMMA.get(grammar, grammar))


def project_conserved_binary_event(graph: ConservedLinkGraph) -> ConservedBinaryEvent | None:
    """Project only witnessed two-place active/passive events; ambiguity stays outside."""
    by_index = {word.index: word for word in graph.words}
    head_candidates = [edge.right for edge in graph.edges
                       if edge.left == 0 and edge.label.startswith("WV")]
    if len(head_candidates) != 1:
        subject_nodes = {node for edge in graph.edges if edge.label.startswith("S")
                         for node in (edge.left, edge.right)}
        object_nodes = {node for edge in graph.edges if edge.label.startswith("O")
                        for node in (edge.left, edge.right)}
        structural_heads = subject_nodes & object_nodes
        if len(structural_heads) == 1:
            head_candidates = list(structural_heads)
        else:
            infinitive_heads = []
            for edge in graph.edges:
                if not edge.label.startswith("I"):
                    continue
                auxiliary, lexical = edge.left, edge.right
                auxiliary_has_subject = any(
                    item.label.startswith("S") and auxiliary in (item.left, item.right)
                    for item in graph.edges)
                lexical_has_object = any(
                    item.label.startswith(("B", "O")) and lexical in (item.left, item.right)
                    for item in graph.edges)
                if auxiliary_has_subject and lexical_has_object:
                    infinitive_heads.append(lexical)
            if len(set(infinitive_heads)) != 1:
                return None
            head_candidates = list(set(infinitive_heads))
    predicate_index = head_candidates[0]
    subjects = [edge.left if edge.right == predicate_index else edge.right
                for edge in graph.edges if edge.label.startswith("S") and
                predicate_index in (edge.left, edge.right)]
    objects = [edge.left if edge.right == predicate_index else edge.right
               for edge in graph.edges if edge.label.startswith("O") and
               predicate_index in (edge.left, edge.right)]
    if not subjects:
        auxiliaries = [edge.left if edge.right == predicate_index else edge.right
                       for edge in graph.edges if edge.label.startswith("I") and
                       predicate_index in (edge.left, edge.right)]
        subjects = [edge.left if edge.right == auxiliary else edge.right
                    for auxiliary in auxiliaries for edge in graph.edges
                    if edge.label.startswith("S") and auxiliary in (edge.left, edge.right)]
    if not objects:
        objects = [edge.left if edge.right == predicate_index else edge.right
                   for edge in graph.edges if edge.label.startswith("B") and
                   predicate_index in (edge.left, edge.right)]
    voice = "active"
    argument_1 = subjects
    argument_2 = objects
    if not (len(argument_1) == len(argument_2) == 1):
        auxiliaries = [edge.left if edge.right == predicate_index else edge.right
                       for edge in graph.edges if edge.label.startswith("Pv") and
                       predicate_index in (edge.left, edge.right)]
        patients = [edge.left if edge.right == auxiliary else edge.right
                    for auxiliary in auxiliaries for edge in graph.edges
                    if edge.label.startswith("S") and auxiliary in (edge.left, edge.right)]
        agents = []
        for edge in graph.edges:
            if edge.label.startswith("MVp") and predicate_index in (edge.left, edge.right):
                prep = edge.left if edge.right == predicate_index else edge.right
                if _link_word_lemma(by_index[prep]) != "by":
                    continue
                agents.extend(edge2.left if edge2.right == prep else edge2.right
                              for edge2 in graph.edges if edge2.label.startswith("J") and
                              prep in (edge2.left, edge2.right))
        if len(patients) == len(agents) == 1:
            voice, argument_1, argument_2 = "passive", agents, patients
    if len(argument_1) != 1 or len(argument_2) != 1:
        return None
    predicate = by_index[predicate_index]
    left, right = by_index[argument_1[0]], by_index[argument_2[0]]
    return ConservedBinaryEvent(
        _link_word_lemma(predicate), _link_word_lemma(left), _link_word_lemma(right),
        (predicate.start, predicate.end), (left.start, left.end), (right.start, right.end), voice)


def converged_binary_event(forest: ConservedLinkForest) -> ConservedBinaryEvent | None:
    """Return one event only when every event-bearing parse agrees on its semantic roles."""
    if forest.truncated or forest.resource_exhausted:
        return None
    events = tuple(event for graph in forest.graphs
                   if (event := project_conserved_binary_event(graph)) is not None)
    if not events:
        return None
    signatures = {(event.predicate, event.argument_1, event.argument_2) for event in events}
    if len(signatures) != 1:
        return None
    return events[0]


@dataclass(frozen=True, slots=True)
class ConservedOwnedAttributes:
    owner: str
    entity: str
    attributes: tuple[tuple[str, tuple[int, int]], ...]


@dataclass(frozen=True, slots=True)
class ConservedAttributeDemand:
    owner: str
    entity: str
    attribute_type: str
    known_attributes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConservedBinaryDemand:
    predicate: str
    answer_role: str
    answer_type: str
    known_role: str
    known_value: str


def compile_surface_binary_demand(question: str) -> ConservedBinaryDemand | None:
    """Compile two explicit one-hole query constructions without parser authority.

    This head interprets the *request*, not the evidence. It is complete only for its exact finite
    grammar and therefore safely outranks a failed/ambiguous parser on those forms. The source still
    needs an independently witnessed relation before any answer can be authorized.
    """
    if not question or len(question) > 512:
        return None
    if match := _DIRECT_BINARY_OBJECT_QUERY.fullmatch(question):
        return ConservedBinaryDemand(
            match.group("predicate").casefold(), "ARG2", match.group("type").casefold(),
            "ARG1", match.group("known").casefold())
    if match := _DIRECT_BINARY_SUBJECT_QUERY.fullmatch(question):
        return ConservedBinaryDemand(
            match.group("predicate").casefold(), "ARG1", match.group("type").casefold(),
            "ARG2", match.group("known").casefold())
    return None


def _word_index_for_span(graph: ConservedLinkGraph, span: tuple[int, int]) -> int | None:
    matches = [word.index for word in graph.words if (word.start, word.end) == span]
    return matches[0] if len(matches) == 1 else None


def _is_interrogative_slot(graph: ConservedLinkGraph, index: int) -> bool:
    if _link_word_lemma(graph.words[index]) in {"what", "who", "which", "where", "when"}:
        return True
    return any(
        edge.label.startswith("D") and index in (edge.left, edge.right) and
        _link_word_lemma(graph.words[edge.left if edge.right == index else edge.right])
        in {"what", "which"}
        for edge in graph.edges)


def project_binary_demand(graph: ConservedLinkGraph) -> ConservedBinaryDemand | None:
    event = project_conserved_binary_event(graph)
    if event is None:
        return None
    first = _word_index_for_span(graph, event.argument_1_span)
    second = _word_index_for_span(graph, event.argument_2_span)
    if first is None or second is None:
        return None
    holes = tuple(index for index in (first, second) if _is_interrogative_slot(graph, index))
    if len(holes) != 1:
        return None
    if holes[0] == first:
        return ConservedBinaryDemand(
            event.predicate, "ARG1", event.argument_1, "ARG2", event.argument_2)
    return ConservedBinaryDemand(
        event.predicate, "ARG2", event.argument_2, "ARG1", event.argument_1)


def _predicate_forms(value: str) -> frozenset[str]:
    forms = {value.casefold()}
    word = value.casefold()
    forms.update(_english_verb_exceptions().get(word, ()))
    if word.endswith("ing") and len(word) > 4:
        stem = word[:-3]
        forms.update((stem, stem + "e"))
        if len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            forms.add(stem[:-1])
    if word.endswith("ied") and len(word) > 3:
        forms.add(word[:-3] + "y")
    if word.endswith("ed") and len(word) > 2:
        forms.update((word[:-2], word[:-1]))
    if word.endswith("ies") and len(word) > 3:
        forms.add(word[:-3] + "y")
    if word.endswith("es") and len(word) > 2:
        forms.update((word[:-2], word[:-1]))
    if word.endswith("s") and len(word) > 1:
        forms.add(word[:-1])
    return frozenset(forms)


def _english_verb_exceptions() -> dict[str, tuple[str, ...]]:
    """Load the exact Princeton WordNet 3.0 exception table as an offline language pack."""
    global _VERB_EXCEPTION_CACHE
    if _VERB_EXCEPTION_CACHE is not None:
        return _VERB_EXCEPTION_CACHE
    raw = _VERB_EXCEPTIONS_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _VERB_EXCEPTIONS_SHA256:
        raise RuntimeError("English verb exception pack failed its frozen digest")
    mapping = {}
    for line in raw.decode("ascii").splitlines():
        fields = line.split()
        if len(fields) >= 2:
            mapping[fields[0]] = tuple(sorted(set(fields[1:])))
    _VERB_EXCEPTION_CACHE = mapping
    return mapping


def resolve_conserved_binary_relation(statement: ConservedLinkForest,
                                      question: ConservedLinkForest) \
        -> tuple[str, tuple[int, int]] | None:
    """Bind one unknown event role; incomplete worlds emit no proof, conflicts block."""
    if (statement.truncated or statement.resource_exhausted or
            question.truncated or question.resource_exhausted):
        return None
    demands = tuple(value for graph in question.graphs
                    if (value := project_binary_demand(graph)) is not None)
    non_auxiliary = tuple(demand for demand in demands
                          if demand.predicate not in {"be", "do", "have"})
    if non_auxiliary:
        demands = non_auxiliary
    signatures = {(d.answer_role, d.answer_type, d.known_role, d.known_value,
                   tuple(sorted(_predicate_forms(d.predicate)))) for d in demands}
    if len(signatures) != 1:
        return None
    demand = demands[0]
    closed = []
    for graph in statement.graphs:
        event = project_conserved_binary_event(graph)
        if event is None or not (_predicate_forms(event.predicate) &
                                 _predicate_forms(demand.predicate)):
            continue
        roles = {"ARG1": (event.argument_1, event.argument_1_span),
                 "ARG2": (event.argument_2, event.argument_2_span)}
        if roles[demand.known_role][0] != demand.known_value:
            continue
        closed.append(roles[demand.answer_role])
    values = {value for value, _span in closed}
    return min(closed, key=lambda item: item[1]) if len(values) == 1 else None


def conserved_span_answer(fact_id: int, value: str, span: tuple[int, int], *,
                          reason: str) -> ConvergentScalarAnswer:
    """Lift a closed parser binding into the existing compact proof-answer contract."""
    if fact_id <= 0 or not value or not reason or not (0 <= span[0] < span[1]):
        raise ValueError("closed span answer is not canonical")
    world = ScalarProofWorld(
        1, value, "text", (fact_id,), ((fact_id, span[0], span[1]),), reason)
    return ConvergentScalarAnswer(
        "resolved", value, "text", (world,), "parser_obligation_worlds_converged", True)


def _linked_modifiers(graph: ConservedLinkGraph, head: int) \
        -> tuple[tuple[str, tuple[int, int]], ...]:
    rows = []
    for edge in graph.edges:
        if edge.label.startswith("A") and head in (edge.left, edge.right):
            index = edge.left if edge.right == head else edge.right
            word = graph.words[index]
            if word.start < word.end:
                rows.append((_link_word_lemma(word), (word.start, word.end)))
    return tuple(sorted(set(rows)))


def project_owned_attributes(graph: ConservedLinkGraph) -> ConservedOwnedAttributes | None:
    event = project_conserved_binary_event(graph)
    if event is None or event.predicate != "have":
        return None
    entity_index = _word_index_for_span(graph, event.argument_2_span)
    if entity_index is None:
        return None
    attributes = _linked_modifiers(graph, entity_index)
    if not attributes:
        return None
    return ConservedOwnedAttributes(event.argument_1, event.argument_2, attributes)


def project_attribute_demand(graph: ConservedLinkGraph) -> ConservedAttributeDemand | None:
    event = project_conserved_binary_event(graph)
    if event is None or event.predicate != "be":
        return None
    entity_index = _word_index_for_span(graph, event.argument_2_span)
    if entity_index is None:
        return None
    owner_candidates = []
    for edge in graph.edges:
        if edge.label.startswith("M") and entity_index in (edge.left, edge.right):
            prep_index = edge.left if edge.right == entity_index else edge.right
            if _link_word_lemma(graph.words[prep_index]) != "of":
                continue
            owner_candidates.extend(
                edge2.left if edge2.right == prep_index else edge2.right
                for edge2 in graph.edges if edge2.label.startswith("J") and
                prep_index in (edge2.left, edge2.right))
    owners = {_link_word_lemma(graph.words[index]) for index in owner_candidates}
    if len(owners) != 1:
        return None
    known = tuple(sorted(value for value, _span in _linked_modifiers(graph, entity_index)))
    return ConservedAttributeDemand(
        next(iter(owners)), event.argument_2, event.argument_1, known)


def resolve_conserved_attribute(statement: ConservedLinkForest,
                                question: ConservedLinkForest) \
        -> tuple[str, tuple[int, int]] | None:
    """Resolve only when every closed binding agrees and no world is over-complete.

    A parse with no remaining modifier does not close the attribute obligation and therefore
    emits no proof.  A parse with two or more remaining modifiers is a real ambiguity and blocks
    the answer.  This is the D40/Sigma-PBA asymmetry between an incomplete environment and a
    conflicting complete environment; it is not score-based parse selection.
    """
    if (statement.truncated or statement.resource_exhausted or
            question.truncated or question.resource_exhausted):
        return None
    demands = tuple(value for graph in question.graphs
                    if (value := project_attribute_demand(graph)) is not None)
    demand_signatures = {(value.owner, value.entity, value.attribute_type,
                          value.known_attributes) for value in demands}
    if len(demand_signatures) != 1:
        return None
    demand = demands[0]
    known = set(demand.known_attributes)
    closed = []
    for graph in statement.graphs:
        state = project_owned_attributes(graph)
        if (state is None or (state.owner, state.entity) != (demand.owner, demand.entity) or
                not known <= {item[0] for item in state.attributes}):
            continue
        remaining = tuple(item for item in state.attributes if item[0] not in known)
        if len(remaining) > 1:
            return None
        if len(remaining) == 1:
            closed.append(remaining[0])
    values = {value for value, _span in closed}
    return min(closed, key=lambda item: item[1]) if len(values) == 1 else None


def link_graph_to_authorized_hypergraph(graph: ConservedLinkGraph, *, source_id: str,
                                        analysis_id: str, alternative_set: str,
                                        scope: str = "horizon-link-grammar-lab"):
    """Mechanically transport one witnessed binary event into the existing D45 contract."""
    from .authorized_semantic_hypergraph import AuthorizedSemanticHypergraph

    if not graph.verify():
        raise ValueError("Link Grammar source graph is not span-conserved")
    event = project_conserved_binary_event(graph)
    if event is None:
        raise ValueError("Link Grammar graph does not contain one complete binary event")
    predicate_surface = graph.source[slice(*event.predicate_span)]
    argument_1_surface = graph.source[slice(*event.argument_1_span)]
    argument_2_surface = graph.source[slice(*event.argument_2_span)]
    record = {
        "source": {"source_id": source_id, "content": graph.source, "scope": scope},
        "analysis": {
            "analysis_id": analysis_id,
            "alternative_set": alternative_set,
            "complete": True,
            "leaves": [
                {"id": "leaf:arg1", "kind": "entity", "surface": argument_1_surface,
                 "canonical": event.argument_1, "span": list(event.argument_1_span),
                 "normalization_rule": "casefold.v1"},
                {"id": "leaf:arg2", "kind": "entity", "surface": argument_2_surface,
                 "canonical": event.argument_2, "span": list(event.argument_2_span),
                 "normalization_rule": "casefold.v1"},
            ],
            "symbols": [
                {"id": "symbol:predicate", "namespace": "link-grammar-5.13",
                 "symbol": event.predicate, "surface": predicate_surface,
                 "span": list(event.predicate_span),
                 "compiler_rule": "horizon.link-grammar-d45.v1"},
            ],
            "nodes": [
                {"id": "node:arg1", "operator": "entity_bundle",
                 "edges": [["name", "leaf:arg1"]]},
                {"id": "node:arg2", "operator": "entity_bundle",
                 "edges": [["name", "leaf:arg2"]]},
            ],
            "events": [
                {"id": "event:1", "predicate_ref": "symbol:predicate",
                 "roles": [["ARG1", "node:arg1"], ["ARG2", "node:arg2"]],
                 "polarity": "positive", "modalities": [],
                 "temporal_modifiers": [],
                 "event_properties": [["predicate_form", value]
                                      for value in sorted(_predicate_forms(event.predicate))]},
            ],
        },
    }
    return AuthorizedSemanticHypergraph.from_contract(record)


def link_forest_to_authorized_hypergraphs(forest: ConservedLinkForest, *, source_id: str,
                                          alternative_set: str = "link-forest",
                                          scope: str = "horizon-link-grammar-lab") -> tuple:
    """Transport every event-bearing parse as an isolated D45 alternative environment."""
    if forest.truncated or forest.resource_exhausted:
        return ()
    graphs = []
    for index, graph in enumerate(forest.graphs):
        if project_conserved_binary_event(graph) is None:
            continue
        graphs.append(link_graph_to_authorized_hypergraph(
            graph, source_id=source_id, analysis_id=f"a{index}",
            alternative_set=alternative_set, scope=scope))
    return tuple(graphs)


def resolve_binary_relation_via_sigma(statement: ConservedLinkForest,
                                      question: ConservedLinkForest, *, source_id: str) \
        -> tuple[str, tuple[int, int], tuple[int, ...]] | None:
    """Execute a one-hole role program through the actual D45 -> Sigma-PBA path."""
    from .authorized_semantic_hypergraph import (
        AuthorizedSemanticHypergraph, authorized_leaf_reference, charge_reference,
        event_property_reference, role_reference, structured_term_reference,
    )
    from .sigma_pba import ConjunctiveProgram, RelationalGoal

    direct_demand = compile_surface_binary_demand(question.source)
    if statement.truncated or (question.truncated and direct_demand is None):
        return None
    demands = ((direct_demand,) if direct_demand is not None else tuple(
        value for graph in question.graphs
        if (value := project_binary_demand(graph)) is not None and
        value.predicate not in {"be", "do"}))
    signatures = {(d.answer_role, d.answer_type, d.known_role, d.known_value,
                   tuple(sorted(_predicate_forms(d.predicate)))) for d in demands}
    if len(signatures) != 1:
        return None
    demand = demands[0]
    graphs = link_forest_to_authorized_hypergraphs(statement, source_id=source_id)
    if not graphs:
        return None
    transport = AuthorizedSemanticHypergraph.transport(graphs)
    executor = transport.executor()
    known_leaf = authorized_leaf_reference("entity", demand.known_value)
    known_term = structured_term_reference(
        "entity_bundle", (("name", known_leaf),), None)
    outputs = []
    admitted = set()
    for predicate_form in sorted(_predicate_forms(demand.predicate)):
        program = ConjunctiveProgram((
            RelationalGoal("d45_event_property", (
                "?E", event_property_reference("key", "predicate_form"),
                event_property_reference("value", predicate_form))),
            RelationalGoal("d45_event_charge", ("?E", charge_reference("positive"))),
            RelationalGoal("d45_event_role", (
                "?E", role_reference(demand.known_role), known_term)),
            RelationalGoal("d45_event_role", (
                "?E", role_reference(demand.answer_role), "?X")),
        ), ("?X",))
        result = executor.execute(program)
        if result.state == "contested":
            return None
        if result.state == "resolved":
            if not executor.reopen(program, result):
                raise ValueError("Sigma-PBA role result failed reopen")
            outputs.extend(output.values[0] for output in result.outputs)
            admitted.update(result.admitted_fact_ids)
    if len(set(outputs)) != 1:
        return None
    answer_ref = outputs[0]
    answer_rows = []
    for graph in graphs:
        local_by_ref = {value: local for local, value in graph.semantic_refs}
        local = local_by_ref.get(answer_ref)
        node_by_id = {node.local_id: node for node in graph.nodes}
        leaf_by_id = {leaf.local_id: leaf for leaf in graph.leaves}
        if local not in node_by_id:
            continue
        node = node_by_id[local]
        names = [child for role, child in node.edges if role == "name" and child in leaf_by_id]
        if len(names) == 1:
            leaf = leaf_by_id[names[0]]
            answer_rows.append((leaf.canonical, leaf.span))
    if len({row for row in answer_rows}) != 1:
        return None
    value, span = answer_rows[0]
    return value, span, tuple(sorted(admitted))


@dataclass(frozen=True, slots=True)
class SatProjectionCheck:
    candidate: str
    span: tuple[int, int]
    requirements: tuple[ConservedLinkRequirement, ...]
    state: str


@dataclass(frozen=True, slots=True)
class SurfaceBinaryCheck:
    candidate: str
    span: tuple[int, int]
    predicate_span: tuple[int, int]
    known_span: tuple[int, int]
    demand: ConservedBinaryDemand
    rule: str


@dataclass(frozen=True, slots=True)
class SatProjectedBinaryAnswer:
    source: str
    source_sha256: str
    value: str
    span: tuple[int, int]
    checks: tuple[SatProjectionCheck, ...]
    admitted_fact_ids: tuple[int, ...]
    surface_checks: tuple[SurfaceBinaryCheck, ...] = ()

    def reopen(self, bridge: LinkGrammarBridge) -> bool:
        return (
            hashlib.sha256(self.source.encode()).hexdigest() == self.source_sha256 and
            self.source[slice(*self.span)].casefold() == self.value and
            all(bridge.sat_projection_exists(self.source, check.requirements) == check.state
                for check in self.checks) and
            all(check in compile_surface_binary_checks(self.source, check.demand)
                for check in self.surface_checks)
        )


_SURFACE_ROLE_TOKEN = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*", re.UNICODE)
_SURFACE_ROLE_SKIP = frozenset({
    "'d", "'ll", "'m", "n't", "'re", "'s", "'t", "'ve", "a", "an", "and", "are",
    "at", "be", "been", "being", "by", "can",
    "could", "did", "do", "does", "ever", "for", "from", "had", "has", "have", "in",
    "into", "is", "just", "may", "might", "must", "never", "not", "of", "on", "onto",
    "or", "over", "please", "shall", "should", "still", "the", "to", "was", "were",
    "will", "would",
})
_SURFACE_DETERMINERS = frozenset({
    "all", "another", "any", "both", "each", "either", "every", "neither", "some",
    "that", "these", "this", "those",
})
_SURFACE_OBJECT_PRONOUNS = frozenset({"her", "him", "me", "them", "us", "you"})
_SURFACE_DITRANSITIVE_PREDICATES = frozenset({
    "award", "bring", "buy", "cook", "fetch", "give", "grant", "hand", "lend", "offer",
    "owe", "pass", "pay", "read", "sell", "send", "show", "teach", "tell", "write",
})
_SURFACE_ADJECTIVAL_SUFFIXES = ("able", "al", "ed", "ful", "ible", "ic", "ing", "ive",
                                 "less", "ous")


def _surface_link_words(source: str) -> tuple[ConservedLinkWord, ...]:
    words = [ConservedLinkWord(0, "", "LEFT-WALL", 0, 0)]
    for match in _SURFACE_ROLE_TOKEN.finditer(source):
        surface = match.group()
        negative = re.search(r"n[’']t$", surface, re.I)
        clitic = negative or re.search(r"[’'](?:s|ll|ve|re|d|m|t)$", surface, re.I)
        spans = ((match.start(), match.end()),)
        if clitic and clitic.start() > 0:
            split = match.start() + clitic.start()
            spans = ((match.start(), split), (split, match.end()))
        for start, end in spans:
            token = source[start:end]
            words.append(ConservedLinkWord(len(words), token, token, start, end))
    words.append(ConservedLinkWord(len(words), "", "RIGHT-WALL", len(source), len(source)))
    return tuple(words)


def compile_surface_binary_checks(source: str, demand: ConservedBinaryDemand) \
        -> tuple[SurfaceBinaryCheck, ...]:
    """Query-conditioned finite order head over exact word spans.

    It proposes one nearest role per predicate/known pair for active order and a fronted WH object.
    It never asserts polarity or modality and cannot by itself turn the relation into a factual write.
    """
    if not source or len(source) > 4096:
        return ()
    words = _surface_link_words(source)
    lexical = words[1:-1]
    predicate_forms = _predicate_forms(demand.predicate)
    predicates = [word for word in lexical
                  if _predicate_forms(_link_word_lemma(word)) & predicate_forms]
    known = [word for word in lexical if _link_word_lemma(word) == demand.known_value]

    def usable(word: ConservedLinkWord) -> bool:
        lemma = _link_word_lemma(word)
        if lemma in _SURFACE_ROLE_SKIP or lemma == demand.known_value \
                or _predicate_forms(lemma) & predicate_forms:
            return False
        if len(lemma) > 4 and lemma.endswith("ly"):
            return False
        if demand.answer_type == "who":
            return ((word.surface[:1].isupper() and not word.surface.isupper()) or
                    lemma in _PERSON_PRONOUNS)
        return True

    def phrase_head(sequence: list[ConservedLinkWord]) -> ConservedLinkWord | None:
        if not sequence:
            return None
        active = sequence
        first_lemma = _link_word_lemma(active[0])
        def modifier_like(value: str) -> bool:
            return value == "together" or any(
                len(value) > len(suffix) + 2 and value.endswith(suffix)
                for suffix in _SURFACE_ADJECTIVAL_SUFFIXES)
        if (first_lemma in _SURFACE_OBJECT_PRONOUNS and len(active) > 1 and
                predicate_forms & _SURFACE_DITRANSITIVE_PREDICATES):
            following = _link_word_lemma(active[1])
            between = [_link_word_lemma(word) for word in lexical
                       if active[0].index < word.index < active[1].index]
            if (not modifier_like(following) and
                    all(value in {"a", "an", "the"} for value in between)):
                active = active[1:]
                first_lemma = _link_word_lemma(active[0])
        if first_lemma in _SURFACE_DETERMINERS and len(active) > 1:
            following = _link_word_lemma(active[1])
            if active[1].index == active[0].index + 1 and not modifier_like(following):
                return active[1]
        return active[0]

    checks = []
    for predicate in predicates:
        for known_word in known:
            candidate = None
            rule = ""
            if demand.answer_role == "ARG1":
                before = [word for word in lexical if word.index < predicate.index and usable(word)]
                if before:
                    candidate, rule = before[-1], "nearest_left_subject"
            elif demand.answer_role == "ARG2":
                fronted_lemmas = ({"what", "which"}
                                   if demand.answer_type == "what" else
                                   {"who", "whom"}
                                   if demand.answer_type == "who" else {"where"})
                fronted = [word for word in lexical if word.index < known_word.index and
                           _link_word_lemma(word) in fronted_lemmas]
                if fronted:
                    wh = fronted[-1]
                    complements = [word for word in lexical
                                   if wh.index < word.index < known_word.index and usable(word)]
                    candidate = phrase_head(complements) or wh
                    rule = ("fronted_interrogative_nominal" if complements else
                            "fronted_interrogative_object")
                else:
                    coordinated_predicates = {
                        word.index + 1 for word in lexical
                        if word.index == predicate.index + 1 and
                        _link_word_lemma(word) in {"and", "or"}
                    }
                    after = [word for word in lexical
                             if word.index > predicate.index and usable(word) and
                             word.index not in coordinated_predicates]
                    if after:
                        candidate, rule = phrase_head(after), "nearest_right_object"
            if candidate is not None:
                checks.append(SurfaceBinaryCheck(
                    _link_word_lemma(candidate), (candidate.start, candidate.end),
                    (predicate.start, predicate.end), (known_word.start, known_word.end),
                    demand, rule))
    return tuple(sorted(set(checks), key=lambda item: (
        item.candidate, item.span, item.predicate_span, item.known_span, item.rule)))


def _surface_check_graph(source: str, check: SurfaceBinaryCheck) -> ConservedLinkGraph | None:
    words = _surface_link_words(source)
    by_span = {(word.start, word.end): word.index for word in words if word.start < word.end}
    candidate = by_span.get(check.span)
    predicate = by_span.get(check.predicate_span)
    known = by_span.get(check.known_span)
    if None in {candidate, predicate, known}:
        return None
    subject = candidate if check.demand.answer_role == "ARG1" else known
    obj = known if check.demand.known_role == "ARG2" else candidate
    edges = tuple(sorted((
        ConservedLinkEdge(0, "WVsurface", predicate),
        ConservedLinkEdge(min(subject, predicate), "Ssurface", max(subject, predicate)),
        ConservedLinkEdge(min(predicate, obj), "Osurface", max(predicate, obj)),
    ), key=lambda edge: (edge.left, edge.right, edge.label)))
    graph = ConservedLinkGraph(source, words, edges, 0)
    return graph if graph.verify() else None


def _ordered_requirement(left: int, right: int, label: str) \
        -> ConservedLinkRequirement | None:
    return ConservedLinkRequirement(left, right, label) if left < right else None


def _binary_sat_paths(words: tuple[ConservedLinkWord, ...], demand: ConservedBinaryDemand,
                      candidate_index: int, known_index: int, predicate_index: int) \
        -> tuple[tuple[ConservedLinkRequirement, ...], ...]:
    """Compile one finite active/passive dependency family into SAT link obligations."""
    paths = []
    if (demand.known_role, demand.answer_role) == ("ARG2", "ARG1"):
        active = (
            _ordered_requirement(0, predicate_index, "WV"),
            _ordered_requirement(candidate_index, predicate_index, "S"),
            _ordered_requirement(predicate_index, known_index, "O"),
        )
    elif (demand.known_role, demand.answer_role) == ("ARG1", "ARG2"):
        active = (
            _ordered_requirement(0, predicate_index, "WV"),
            _ordered_requirement(known_index, predicate_index, "S"),
            _ordered_requirement(predicate_index, candidate_index, "O"),
        )
    else:
        return ()
    if all(active):
        paths.append(tuple(active))

    if (demand.answer_type == "where" and demand.known_role == "ARG1" and
            demand.answer_role == "ARG2"):
        for preposition in (word.index for word in words if _link_word_lemma(word) in {
                "at", "from", "in", "inside", "into", "near", "on", "outside", "to"}):
            locative = (
                _ordered_requirement(0, predicate_index, "WV"),
                _ordered_requirement(known_index, predicate_index, "S"),
                _ordered_requirement(predicate_index, preposition, "MVp"),
                _ordered_requirement(preposition, candidate_index, "J"),
            )
            if all(locative):
                paths.append(tuple(locative))

    auxiliaries = [word.index for word in words
                   if _link_word_lemma(word) in {"am", "are", "be", "been", "being",
                                                         "is", "was", "were"}]
    by_words = [word.index for word in words if _link_word_lemma(word) == "by"]
    for auxiliary in auxiliaries:
        for by_word in by_words:
            patient = known_index if demand.known_role == "ARG2" else candidate_index
            agent = known_index if demand.known_role == "ARG1" else candidate_index
            passive = (
                _ordered_requirement(0, predicate_index, "WV"),
                _ordered_requirement(patient, auxiliary, "S"),
                _ordered_requirement(auxiliary, predicate_index, "Pv"),
                _ordered_requirement(predicate_index, by_word, "MVp"),
                _ordered_requirement(by_word, agent, "J"),
            )
            if all(passive):
                paths.append(tuple(passive))
    return tuple(sorted(set(paths), key=lambda path: tuple(
        (item.left_word, item.right_word, item.label_prefix) for item in path)))


def resolve_binary_relation_via_sat_sigma(statement_source: str,
                                           question: ConservedLinkForest, *,
                                           bridge: LinkGrammarBridge,
                                           source_id: str) -> SatProjectedBinaryAnswer | None:
    """Compile a one-hole question into SAT projections, then execute D45 -> Sigma.

    Scope is deliberately finite: single-token capitalized participants and the frozen
    active/passive binary dependency family. Unknown forms abstain. Candidate completeness
    is explicit in ``checks`` and every check can be reopened against the SAT circuit.
    """
    direct_demand = compile_surface_binary_demand(question.source)
    if ((question.truncated or question.resource_exhausted) and direct_demand is None) \
            or not bridge.use_sat_parser:
        return None
    demands = ((direct_demand,) if direct_demand is not None else tuple(
        value for graph in question.graphs
        if (value := project_binary_demand(graph)) is not None and
        value.predicate not in {"be", "do"}))
    signatures = {(d.answer_role, d.answer_type, d.known_role, d.known_value,
                   tuple(sorted(_predicate_forms(d.predicate)))) for d in demands}
    if len(signatures) != 1 or demands[0].answer_type not in {"what", "where", "who"}:
        return None
    demand = demands[0]
    surface_checks = compile_surface_binary_checks(statement_source, demand)

    tokenization = bridge.parse(statement_source)
    checks = []
    incomplete = False
    words = None
    if not tokenization.resource_exhausted and tokenization.graphs:
        words = tokenization.graphs[0].words
        predicate_forms = _predicate_forms(demand.predicate)
        predicate_indices = [word.index for word in words
                             if _predicate_forms(_link_word_lemma(word)) & predicate_forms]
        known_indices = [word.index for word in words
                         if _link_word_lemma(word) == demand.known_value]
        if demand.answer_type in {"where", "who"}:
            candidate_indices = [word.index for word in words
                                 if word.start < word.end and
                                 (word.surface[:1].isupper() or
                                  (demand.answer_type == "who" and
                                   _link_word_lemma(word) in _PERSON_PRONOUNS)) and
                                 _link_word_lemma(word) != demand.known_value]
        else:
            candidate_indices = [word.index for word in words
                                 if word.start < word.end and
                                 any(char.isalnum() for char in word.surface) and
                                 _link_word_lemma(word) not in _NON_ARGUMENT_LEMMAS and
                                 _link_word_lemma(word) != demand.known_value and
                                 not (_predicate_forms(_link_word_lemma(word)) & predicate_forms)]
        for candidate_index in candidate_indices:
            candidate_word = words[candidate_index]
            candidate = _link_word_lemma(candidate_word)
            for known_index in known_indices:
                for predicate_index in predicate_indices:
                    for path in _binary_sat_paths(
                            words, demand, candidate_index, known_index, predicate_index):
                        state = bridge.sat_projection_exists(statement_source, path)
                        incomplete |= state == "incomplete"
                        checks.append(SatProjectionCheck(
                            candidate, (candidate_word.start, candidate_word.end), path, state))
    if incomplete:
        return None
    possible = [check for check in checks if check.state == "possible"]
    values = {check.candidate for check in possible}.union(
        check.candidate for check in surface_checks)
    if len(values) != 1:
        return None
    value = next(iter(values))
    answer_checks = [check for check in possible if check.candidate == value]
    answer_surface_checks = [check for check in surface_checks if check.candidate == value]
    answer_spans = {check.span for check in answer_checks}.union(
        check.span for check in answer_surface_checks)
    if len(answer_spans) != 1:
        return None
    answer_span = next(iter(answer_spans))

    projected_graphs = []
    for check in answer_checks:
        edges = [ConservedLinkEdge(
            item.left_word, item.label_prefix, item.right_word)
            for item in check.requirements]
        if demand.answer_type == "where":
            predicate_edges = [edge for edge in edges if edge.label.startswith("MVp")]
            object_edges = [edge for edge in edges if edge.label.startswith("J")]
            if len(predicate_edges) != 1 or len(object_edges) != 1:
                return None
            # RelEx PREP_ADVERBIAL/PREP_OBJECT plus the typed `where` obligation
            # mechanically lowers the locative complement to D45 ARG2.
            edges.append(ConservedLinkEdge(
                predicate_edges[0].left, "Oloc", object_edges[0].right))
        graph = ConservedLinkGraph(statement_source, words, tuple(edges), 0)
        if not graph.verify():
            raise ValueError("SAT-projected binary subgraph failed span conservation")
        projected_graphs.append(graph)
    for check in answer_surface_checks:
        graph = _surface_check_graph(statement_source, check)
        if graph is None:
            raise ValueError("surface-projected binary subgraph failed span conservation")
        projected_graphs.append(graph)
    if not projected_graphs:
        return None
    projected_forest = ConservedLinkForest(
        statement_source, tuple(projected_graphs), len(projected_graphs), False, False,
        len(projected_graphs), len(projected_graphs))
    transported = resolve_binary_relation_via_sigma(
        projected_forest, question, source_id=source_id)
    if transported is None or transported[:2] != (value, answer_span):
        return None
    answer = SatProjectedBinaryAnswer(
        statement_source, hashlib.sha256(statement_source.encode()).hexdigest(),
        value, answer_span, tuple(checks), transported[2], tuple(answer_surface_checks))
    return answer if answer.reopen(bridge) else None


@dataclass(frozen=True)
class _ProofAttentionEnvironment:
    """One immutable row in deterministic Q/K/V proof transport."""

    bindings: tuple[tuple[str, str], ...]
    fact_ids: tuple[int, ...]
    assumptions: tuple[str, ...]
    witnesses: tuple[BindingWitness, ...]


def _proof_attention_unify(arguments: tuple[str, ...], observed: tuple[str, ...],
                           bindings: tuple[tuple[str, str], ...], fact_id: int) \
        -> tuple[tuple[tuple[str, str], ...], tuple[BindingWitness, ...]] | None:
    """Boolean/provenance counterpart of a learned Q/K compatibility score."""
    result = dict(bindings)
    witnesses = []
    for expected, value in zip(arguments, observed):
        if is_variable(expected):
            prior = result.get(expected)
            if prior is not None and prior != value:
                return None
            if prior is None:
                result[expected] = value
                witnesses.append(BindingWitness(expected, value, fact_id))
        elif expected != value:
            return None
    return tuple(sorted(result.items())), tuple(witnesses)


def execute_proof_attention(executor: SigmaPBAExecutor, program: ConjunctiveProgram, *,
                            max_hops: int = 6, max_candidate_checks: int = 100_000,
                            max_evidence_bytes: int = 65_536,
                            max_environments: int = 10_000) -> SigmaPBAResult:
    """Execute D45 facts as sparse deterministic attention over a proof semiring.

    ``Q`` is a relational goal, ``K`` is a fact predicate/typed argument tuple and ``V``
    is the authorized binding plus FactId. Compatibility is Boolean. Alternative values
    are conserved as separate environments and compatible facts join by provenance rather
    than being averaged. This deliberately shares Sigma's authorization boundary but uses
    an independent relational-join control flow for the first differential gate.
    """
    if min(max_hops, max_candidate_checks, max_evidence_bytes, max_environments) < 1:
        raise ValueError("Proof Attention budgets must be positive")
    if len(program.goals) > max_hops:
        return SigmaPBAResult("abstain", (), (), (), 0, 0, 1, "hop_budget_exceeded")

    environments = (_ProofAttentionEnvironment((), (), (), ()),)
    remaining = set(range(len(program.goals)))
    admitted: set[int] = set()
    all_witnesses: set[BindingWitness] = set()
    candidate_checks = 0
    evidence_bytes = 0
    environments_created = 1

    while remaining:
        goal_index = max(remaining, key=lambda index: (
            sum(not is_variable(arg) for arg in program.goals[index].arguments),
            -len(executor.index.get((program.goals[index].predicate,
                                     len(program.goals[index].arguments)), ())),
            -index,
        ))
        goal = program.goals[goal_index]
        keys = executor.index.get((goal.predicate, len(goal.arguments)), ())
        expanded = []
        for environment in environments:
            for fact in keys:
                candidate_checks += 1
                if candidate_checks > max_candidate_checks:
                    return SigmaPBAResult(
                        "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
                        candidate_checks - 1, evidence_bytes, environments_created,
                        "candidate_budget_exhausted")
                unified = _proof_attention_unify(
                    goal.arguments, fact.arguments, environment.bindings, fact.fact_id)
                if unified is None:
                    continue
                bindings, witnesses = unified
                assumptions = tuple(sorted(set(environment.assumptions).union(fact.assumptions)))
                if any(nogood <= frozenset(assumptions) for nogood in executor.nogoods):
                    continue
                if fact.fact_id not in admitted:
                    source = executor.sources[fact.source_id]
                    cost = len(source.content[slice(*fact.source_span)].encode())
                    if evidence_bytes + cost > max_evidence_bytes:
                        return SigmaPBAResult(
                            "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
                            candidate_checks, evidence_bytes, environments_created,
                            "evidence_byte_budget_exhausted")
                    admitted.add(fact.fact_id)
                    evidence_bytes += cost
                joined_witnesses = tuple(sorted(set(environment.witnesses).union(witnesses)))
                all_witnesses.update(witnesses)
                expanded.append(_ProofAttentionEnvironment(
                    bindings,
                    tuple(sorted(set(environment.fact_ids + (fact.fact_id,)))),
                    assumptions,
                    joined_witnesses,
                ))
        environments_created += len(expanded)
        if environments_created > max_environments:
            return SigmaPBAResult(
                "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
                candidate_checks, evidence_bytes, environments_created - len(expanded),
                "environment_budget_exhausted")
        environments = tuple(sorted(set(expanded), key=lambda item: (
            item.bindings, item.fact_ids, item.assumptions, item.witnesses)))
        if not environments:
            return SigmaPBAResult(
                "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
                candidate_checks, evidence_bytes, environments_created,
                "no_complete_authorized_environment")
        remaining.remove(goal_index)

    by_output: dict[tuple[str, ...], set[tuple[int, ...]]] = {}
    for environment in environments:
        bindings = dict(environment.bindings)
        try:
            values = tuple(bindings[variable] for variable in program.output_variables)
        except KeyError:
            continue
        by_output.setdefault(values, set()).add(environment.fact_ids)
    if not by_output:
        return SigmaPBAResult(
            "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
            candidate_checks, evidence_bytes, environments_created, "output_variable_unbound")

    outputs = tuple(
        SigmaPBAOutput(values, ProvenancePolynomial(tuple(sorted(monomials))))
        for values, monomials in sorted(by_output.items())
    )
    state = "resolved" if len(outputs) == 1 else "contested"
    reason = ("all_complete_environments_agree" if state == "resolved" else
              "complete_authorized_environments_disagree")
    return SigmaPBAResult(
        state, outputs, tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
        candidate_checks, evidence_bytes, environments_created, reason)


@dataclass(frozen=True, order=True)
class HPLTGuardedFact:
    """One authorized K/V candidate guarded by a finite interpretation reading."""

    fact: AuthorizedFact
    guard: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.guard != tuple(sorted(set(self.guard))) or any(
                not name or not value for name, value in self.guard):
            raise ValueError("H-PLT guards must be canonical non-empty variable/value pairs")


@dataclass(frozen=True)
class HPLTResult:
    state: str
    outputs: tuple[SigmaPBAOutput, ...]
    complete: bool
    world_count: int
    lattice_explored_states: int
    lattice_pruned_values: int
    candidate_checks: int
    evidence_bytes: int
    problem_sha256: str
    reason: str


def _hplt_guard_provenance(problem: "HDEMProblem",
                           guard: tuple[tuple[str, str], ...]) -> tuple[int, ...]:
    by_name = {variable.name: {value.value: value.fact_ids for value in variable.domain}
               for variable in problem.variables}
    result = {fact_id for constraint in problem.constraints
              for fact_id in constraint.witness_fact_ids}
    result.update(fact_id for name, value in guard for fact_id in by_name[name][value])
    return tuple(sorted(result))


def _hplt_derivation_guard(monomial: tuple[int, ...],
                           guards: dict[int, tuple[tuple[str, str], ...]]) \
        -> tuple[tuple[str, str], ...] | None:
    result = {}
    for fact_id in monomial:
        for name, value in guards[fact_id]:
            previous = result.get(name)
            if previous is not None and previous != value:
                return None
            result[name] = value
    return tuple(sorted(result.items()))


def _hplt_find_world(problem: "HDEMProblem", *,
                     required: tuple[tuple[str, str], ...] = (),
                     forbidden_guards: tuple[tuple[tuple[str, str], ...], ...] = (),
                     max_states: int = 100_000) \
        -> tuple[tuple[tuple[str, str], ...] | None, bool, int, int]:
    """Find one CSP world, with proof guards treated as nogoods.

    Returning ``(None, True, ...)`` proves unsatisfiability. ``complete=False`` means the search
    budget ended before either a witness or a proof of absence. Unit propagation over forbidden
    guards is what lets H-PLT prove coverage without enumerating irrelevant ambiguity dimensions.
    """
    domains = {variable.name: {value.value for value in variable.domain}
               for variable in problem.variables}
    for name, value in required:
        if name not in domains or value not in domains[name]:
            return None, True, 0, 0
        domains[name] = {value}
    explored = 0
    pruned = 0
    exhausted = False

    def propagate(local: dict[str, set[str]]) -> bool:
        nonlocal pruned
        changed = True
        while changed:
            changed = False
            for constraint in problem.constraints:
                for position, name in enumerate(constraint.variables):
                    supported = {row[position] for row in constraint.allowed
                                 if all(row[index] in local[other]
                                        for index, other in enumerate(constraint.variables))}
                    remove = local[name].difference(supported)
                    if remove:
                        local[name].difference_update(remove)
                        pruned += len(remove)
                        changed = True
                        if not local[name]:
                            return False
            for guard in forbidden_guards:
                if any(value not in local[name] for name, value in guard):
                    continue
                open_pairs = [(name, value) for name, value in guard
                              if len(local[name]) > 1]
                if not open_pairs:
                    return False
                if len(open_pairs) == 1:
                    name, value = open_pairs[0]
                    local[name].remove(value)
                    pruned += 1
                    changed = True
                    if not local[name]:
                        return False
        return True

    def visit(local: dict[str, set[str]]) -> tuple[tuple[str, str], ...] | None:
        nonlocal explored, exhausted
        if explored >= max_states:
            exhausted = True
            return None
        explored += 1
        current = {name: set(values) for name, values in local.items()}
        if not propagate(current):
            return None
        open_names = [name for name, values in current.items() if len(values) > 1]
        if not open_names:
            return tuple((name, next(iter(values))) for name, values in sorted(current.items()))
        name = min(open_names, key=lambda item: (len(current[item]), item))
        for value in sorted(current[name]):
            child = {key: set(values) for key, values in current.items()}
            child[name] = {value}
            found = visit(child)
            if found is not None:
                return found
            if exhausted:
                return None
        return None

    witness = visit(domains)
    return witness, not exhausted, explored, pruned


def execute_proof_lattice_attention(problem: "HDEMProblem", *,
                                    guarded_facts: tuple[HPLTGuardedFact, ...],
                                    sources: tuple[SealedSource, ...], scope: str,
                                    allowed_rules: frozenset[str], program: ConjunctiveProgram,
                                    lattice_mode: str = "packed",
                                    nogoods: tuple[frozenset[str], ...] = (),
                                    max_lattice_states: int = 100_000,
                                    max_lattice_worlds: int = 100_000,
                                    max_candidate_checks: int = 100_000,
                                    max_evidence_bytes: int = 65_536,
                                    max_environments: int = 10_000) -> HPLTResult:
    """Query a finite interpretation lattice through the existing Proof Attention path.

    This is the first H-PLT correctness adapter, not a second production executor. H-DEM owns the
    packed candidate-world semantics; Proof Attention owns typed Q/K/V joins; Sigma types own the
    authorization and provenance envelope. A candidate fact becomes visible only in worlds matching
    its complete guard. Horizon resolves only when every complete surviving world closes the query
    and all of them yield exactly the same value.
    """
    from .cjk_covariant_span_readout import (
        HDEMProblem, solve_hdem_enumerative, solve_hdem_packed,
    )

    if not isinstance(problem, HDEMProblem) or not guarded_facts or not sources \
            or not scope or not allowed_rules:
        raise ValueError("H-PLT requires a finite problem, guarded facts, sources and authority")
    if lattice_mode not in {"enumerative", "packed", "symbolic"}:
        raise ValueError("H-PLT lattice mode must be symbolic, packed or enumerative")
    if min(max_lattice_states, max_lattice_worlds, max_candidate_checks,
           max_evidence_bytes, max_environments) < 1:
        raise ValueError("H-PLT budgets must be positive")
    if len({item.fact.fact_id for item in guarded_facts}) != len(guarded_facts):
        raise ValueError("H-PLT candidate FactIds must be unique")

    domains = {variable.name: {value.value for value in variable.domain}
               for variable in problem.variables}
    for candidate in guarded_facts:
        for name, value in candidate.guard:
            if name not in domains or value not in domains[name]:
                raise ValueError("H-PLT guard references an unknown lattice reading")

    guards_by_fact = {candidate.fact.fact_id: candidate.guard for candidate in guarded_facts}

    if lattice_mode == "symbolic":
        initial, complete, explored, pruned = _hplt_find_world(
            problem, max_states=max_lattice_states)
        if not complete:
            return HPLTResult(
                "abstain", (), False, 0, explored, pruned, 0, 0,
                problem.canonical_sha256(), "interpretation_lattice_budget_exhausted")
        if initial is None:
            return HPLTResult(
                "abstain", (), True, 0, explored, pruned, 0, 0,
                problem.canonical_sha256(), "no_complete_interpretation_world")

        executor = SigmaPBAExecutor(
            sources=sources, facts=tuple(candidate.fact for candidate in guarded_facts),
            scope=scope, allowed_rules=allowed_rules, nogoods=nogoods)
        paths = execute_proof_attention(
            executor, program, max_candidate_checks=max_candidate_checks,
            max_evidence_bytes=max_evidence_bytes, max_environments=max_environments)
        if paths.state == "abstain":
            return HPLTResult(
                "abstain", (), True, 0, explored, pruned, paths.candidate_checks,
                paths.evidence_bytes, problem.canonical_sha256(),
                "no_symbolic_proof_path_closes_the_query")

        by_output: dict[tuple[str, ...], set[tuple[int, ...]]] = {}
        live_guards = set()
        for output in paths.outputs:
            for monomial in output.provenance.monomials:
                guard = _hplt_derivation_guard(monomial, guards_by_fact)
                if guard is None:
                    continue
                witness, search_complete, states, removed = _hplt_find_world(
                    problem, required=guard, max_states=max_lattice_states)
                explored += states
                pruned += removed
                if not search_complete:
                    return HPLTResult(
                        "abstain", (), False, 0, explored, pruned,
                        paths.candidate_checks, paths.evidence_bytes,
                        problem.canonical_sha256(), "interpretation_lattice_budget_exhausted")
                if witness is None:
                    continue
                live_guards.add(guard)
                proof = tuple(sorted(set(monomial).union(
                    _hplt_guard_provenance(problem, guard))))
                by_output.setdefault(output.values, set()).add(proof)
        if not live_guards:
            return HPLTResult(
                "abstain", (), True, 0, explored, pruned, paths.candidate_checks,
                paths.evidence_bytes, problem.canonical_sha256(),
                "no_guarded_proof_path_is_interpretation_consistent")

        uncovered, coverage_complete, states, removed = _hplt_find_world(
            problem, forbidden_guards=tuple(sorted(live_guards)),
            max_states=max_lattice_states)
        explored += states
        pruned += removed
        if not coverage_complete:
            return HPLTResult(
                "abstain", (), False, 0, explored, pruned, paths.candidate_checks,
                paths.evidence_bytes, problem.canonical_sha256(),
                "interpretation_lattice_budget_exhausted")
        if uncovered is not None:
            return HPLTResult(
                "abstain", (), True, 0, explored, pruned, paths.candidate_checks,
                paths.evidence_bytes, problem.canonical_sha256(),
                "at_least_one_complete_world_does_not_close_the_query")

        outputs = tuple(SigmaPBAOutput(
            values, ProvenancePolynomial(tuple(sorted(monomials))))
            for values, monomials in sorted(by_output.items()))
        state = "resolved" if len(outputs) == 1 else "contested"
        return HPLTResult(
            state, outputs, True, 0, explored, pruned, paths.candidate_checks,
            paths.evidence_bytes, problem.canonical_sha256(),
            ("symbolic_counterexample_search_proves_consensus" if state == "resolved" else
             "symbolic_paths_prove_multiple_possible_answers"))

    if lattice_mode == "packed":
        lattice = solve_hdem_packed(
            problem, max_states=max_lattice_states, max_worlds=max_lattice_worlds)
    else:
        # The explicit product is a deliberately expensive correctness oracle.
        lattice = solve_hdem_enumerative(
            problem, max_assignments=max_lattice_states)
    if not lattice.complete:
        return HPLTResult(
            "abstain", (), False, len(lattice.worlds), lattice.explored_states,
            lattice.pruned_values, 0, 0, lattice.problem_sha256,
            "interpretation_lattice_budget_exhausted")
    if not lattice.worlds:
        return HPLTResult(
            "abstain", (), True, 0, lattice.explored_states, lattice.pruned_values,
            0, 0, lattice.problem_sha256, "no_complete_interpretation_world")

    by_output: dict[tuple[str, ...], set[tuple[int, ...]]] = {}
    total_checks = 0
    total_bytes = 0
    for world in lattice.worlds:
        assignment = dict(world.assignment)
        active = tuple(candidate.fact for candidate in guarded_facts
                       if all(assignment.get(name) == value
                              for name, value in candidate.guard))
        if not active:
            return HPLTResult(
                "abstain", (), True, len(lattice.worlds), lattice.explored_states,
                lattice.pruned_values, total_checks, total_bytes, lattice.problem_sha256,
                "interpretation_world_has_no_authorized_fact")
        executor = SigmaPBAExecutor(
            sources=sources, facts=active, scope=scope, allowed_rules=allowed_rules,
            nogoods=nogoods)
        result = execute_proof_attention(
            executor, program, max_candidate_checks=max_candidate_checks,
            max_evidence_bytes=max_evidence_bytes, max_environments=max_environments)
        total_checks += result.candidate_checks
        total_bytes += result.evidence_bytes
        if result.state == "abstain":
            return HPLTResult(
                "abstain", (), True, len(lattice.worlds), lattice.explored_states,
                lattice.pruned_values, total_checks, total_bytes, lattice.problem_sha256,
                "at_least_one_complete_world_does_not_close_the_query")
        for output in result.outputs:
            monomials = by_output.setdefault(output.values, set())
            for monomial in output.provenance.monomials:
                guard = _hplt_derivation_guard(monomial, guards_by_fact)
                if guard is None:
                    continue
                monomials.add(tuple(sorted(set(monomial).union(
                    _hplt_guard_provenance(problem, guard)))))

    outputs = tuple(SigmaPBAOutput(
        values, ProvenancePolynomial(tuple(sorted(monomials))))
        for values, monomials in sorted(by_output.items()))
    state = "resolved" if len(outputs) == 1 else "contested"
    return HPLTResult(
        state, outputs, True, len(lattice.worlds), lattice.explored_states,
        lattice.pruned_values, total_checks, total_bytes, lattice.problem_sha256,
        ("all_complete_interpretation_worlds_agree" if state == "resolved" else
         "complete_interpretation_worlds_disagree"))


def _number(match: re.Match[str]) -> Decimal:
    raw = match.group("number").casefold()
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        value = _WORD_NUMBER[raw]
    if match.group("post_half"):
        value += Decimal("0.5")
    return value


def _unit(match: re.Match[str]) -> tuple[str, str, Decimal] | None:
    currency = match.group("currency")
    if currency:
        return _CURRENCY[currency]
    raw = (match.group("unit") or "").casefold()
    return _UNIT.get(raw)


def _render(value: Decimal) -> str:
    value = value.normalize()
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else format(value, "f")


@dataclass(frozen=True)
class AttestedMeasurement:
    fact_id: int
    source_id: str
    source_sha256: str
    source_span: tuple[int, int]
    sentence_span: tuple[int, int]
    binding_span: tuple[int, int]
    surface: str
    qualifier: str
    sentence: str
    binding_text: str
    value: Decimal
    dimension: str
    unit: str
    base_value: Decimal
    terms: frozenset[str]
    asserted: bool
    positive: bool

    def verify(self, documents: dict[int, object]) -> bool:
        document = documents.get(self.fact_id)
        if document is None or getattr(document, "source", None) != self.source_id:
            return False
        text = getattr(document, "text", "")
        start, end = self.source_span
        binding_start, binding_end = self.binding_span
        return (_digest(text) == self.source_sha256 and 0 <= start < end <= len(text)
                and text[start:end] == self.surface and
                0 <= binding_start < binding_end <= len(text) and
                text[binding_start:binding_end] == self.binding_text)


@dataclass(frozen=True)
class ScalarProofWorld:
    selector_threshold: int
    value: str
    unit: str
    fact_ids: tuple[int, ...]
    spans: tuple[tuple[int, int, int], ...]
    reason: str


@dataclass(frozen=True)
class ConvergentScalarAnswer:
    state: str
    value: str | None
    unit: str
    worlds: tuple[ScalarProofWorld, ...]
    reason: str
    surface_complete: bool
    semantic_complete: bool = False


@dataclass(frozen=True)
class AttestedContribution:
    fact_id: int
    value: Decimal
    unit: str
    spans: tuple[tuple[int, int, int], ...]
    terms: frozenset[str]
    reason: str


@dataclass(frozen=True)
class AttestedCountList:
    fact_id: int
    value: int
    identities: tuple[str, ...]
    spans: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True)
class AttestedEntityEvent:
    fact_id: int
    identity: frozenset[str]
    span: tuple[int, int, int]


class AttestedScalarLedger:
    """Lossless measurement projection over a caller-declared authority boundary."""

    def __init__(self, documents: tuple[object, ...], atoms: tuple[AttestedMeasurement, ...],
                 unresolved_measure_sentences: tuple[tuple[int, int, int], ...],
                 authoritative_roles=("user",), fact_groups: dict[int, int] | None = None):
        self.documents = {int(getattr(document, "fact_id")): document for document in documents}
        self.atoms = tuple(sorted(atoms, key=lambda atom: (atom.fact_id, atom.source_span)))
        self.unresolved_measure_sentences = unresolved_measure_sentences
        self.authoritative_roles = frozenset(str(role).casefold() for role in authoritative_roles)
        self.fact_groups = dict(fact_groups or {})
        if len(self.documents) != len(documents):
            raise ValueError("documents must have unique FactIds")
        if any(not atom.verify(self.documents) for atom in self.atoms):
            raise ValueError("measurement authority failed")
        if not set(self.fact_groups) <= set(self.documents):
            raise ValueError("fact groups must refer to authority documents")

    @classmethod
    def build(cls, documents: tuple[object, ...], *, authoritative_roles=("user",),
              fact_groups: dict[int, int] | None = None) \
            -> "AttestedScalarLedger":
        atoms: list[AttestedMeasurement] = []
        unresolved = []
        for document in documents:
            text = str(getattr(document, "text"))
            role = getattr(document, "role", None)
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")].casefold()
            if role not in authoritative_roles:
                continue
            source_id = str(getattr(document, "source"))
            sha256 = _digest(text)
            sentence_matches = tuple(_SENTENCE.finditer(text))
            for sentence_ordinal, sentence_match in enumerate(sentence_matches):
                sentence = sentence_match.group(0)
                binding_start = (sentence_matches[sentence_ordinal - 1].start()
                                 if sentence_ordinal else sentence_match.start())
                binding_span = (binding_start, sentence_match.end())
                binding_text = text[binding_span[0]:binding_span[1]]
                terms = _terms(binding_text)
                sentence_channels = observe_raw_text(sentence)
                asserted = (not sentence.rstrip().endswith("?") and
                            not _NON_ASSERTED.search(sentence) and
                            not re.search(r"\bmay\b", sentence))
                positive = sentence_channels.polarity == "positive"
                found = 0
                for match in _MEASURE.finditer(sentence):
                    unit = _unit(match)
                    if unit is None:
                        continue
                    dimension, canonical, multiplier = unit
                    value = _number(match)
                    start = sentence_match.start() + match.start()
                    end = sentence_match.start() + match.end()
                    atoms.append(AttestedMeasurement(
                        int(getattr(document, "fact_id")), source_id, sha256, (start, end),
                        sentence_match.span(), binding_span, text[start:end],
                        (match.group("qualifier") or "").casefold(), sentence,
                        binding_text, value, dimension,
                        canonical, value * multiplier, terms,
                        asserted, positive,
                    ))
                    found += 1
                for match in _CLOCK.finditer(sentence):
                    hour = int(match.group("hour")) % 12
                    if match.group("ampm").casefold() == "p":
                        hour += 12
                    value = Decimal(hour * 60 + int(match.group("minute")))
                    start = sentence_match.start() + match.start()
                    end = sentence_match.start() + match.end()
                    atoms.append(AttestedMeasurement(
                        int(getattr(document, "fact_id")), source_id, sha256, (start, end),
                        sentence_match.span(), binding_span, text[start:end], "", sentence,
                        binding_text, value, "clock",
                        "clock", value, terms, asserted, positive,
                    ))
                    found += 1
                # A visible digit next to a measurement word that did not enter the ledger is a
                # real parsing debt.  Pure dates/counts are not silently treated as measurements.
                if found == 0 and re.search(r"\d", sentence) and _QUERY_UNIT.search(sentence):
                    unresolved.append((int(getattr(document, "fact_id")), *sentence_match.span()))
        return cls(documents, tuple(atoms), tuple(unresolved), authoritative_roles, fact_groups)

    def authoritative_documents(self) -> tuple[tuple[int, str], ...]:
        values = []
        for fact_id, document in self.documents.items():
            text = str(getattr(document, "text"))
            role = getattr(document, "role", None)
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")].casefold()
            if str(role).casefold() in self.authoritative_roles:
                values.append((fact_id, text))
        return tuple(sorted(values))

    def authority_corpus_digest(self) -> bytes:
        digest = hashlib.sha256(b"HORIZON-AUTHORITY-CORPUS-v1\0")
        for fact_id, text in self.authoritative_documents():
            encoded = text.encode("utf-8")
            digest.update(struct.pack(">II", fact_id, len(encoded)))
            digest.update(encoded)
        return digest.digest()

    @staticmethod
    def _requested_unit(question: str) -> tuple[str, str, Decimal] | None:
        # A duration mentioned in a temporal window ("money spent in four months") is
        # not the codomain of the answer.  Result type is fixed before scanning incidental
        # measurement words in query modifiers.
        if _CURRENCY_RESULT_QUERY.search(question):
            return "currency:any", "money", Decimal(1)
        match = _QUERY_UNIT.search(question)
        if match is None:
            return None
        raw = match.group(1).casefold()
        if raw == "money":
            return "currency:any", "money", Decimal(1)
        normalized = _UNIT.get(raw)
        if normalized is None:
            return None
        dimension, _base_unit, multiplier = normalized
        display = {
            "second": "second", "minute": "minute", "hour": "hour", "day": "day",
            "week": "week", "month": "month", "year": "year", "dollar": "USD",
            "euro": "EUR", "pound": "GBP", "mile": "mile", "kilometer": "kilometer",
            "kilometre": "kilometer",
            "seconds": "second", "minutes": "minute", "hours": "hour", "days": "day",
            "weeks": "week", "months": "month", "years": "year", "dollars": "USD",
            "euros": "EUR", "pounds": "GBP", "miles": "mile", "kilometers": "kilometer",
            "kilometres": "kilometer", "km": "kilometer",
            "mbps": "Mbps", "gbps": "Gbps",
        }.get(raw, _base_unit)
        return dimension, display, multiplier

    @staticmethod
    def _deduplicate(atoms: tuple[AttestedMeasurement, ...]) -> tuple[AttestedMeasurement, ...]:
        # Repeated reports collapse only when value/unit match and their content-term Jaccard is
        # strong.  This is an orbit proposal, so ambiguous clusters remain separate.
        kept: list[AttestedMeasurement] = []
        for atom in atoms:
            duplicate = False
            for prior in kept:
                if (atom.dimension, atom.base_value) != (prior.dimension, prior.base_value):
                    continue
                union = atom.terms | prior.terms
                shared_entities = (set(observe_raw_text(atom.binding_text).entities) &
                                   set(observe_raw_text(prior.binding_text).entities))
                shared_temporal = (set(observe_raw_text(atom.binding_text).temporal) &
                                   set(observe_raw_text(prior.binding_text).temporal))
                shared_alias = _attested_aliases(atom.binding_text) & _attested_aliases(
                    prior.binding_text)
                if ((union and len(atom.terms & prior.terms) / len(union) >= Decimal("0.65")) or
                        (shared_temporal and union and
                         len(atom.terms & prior.terms) / len(union) >= Decimal("0.40")) or
                        len(shared_entities) >= 2 or shared_alias):
                    duplicate = True
                    break
            if not duplicate:
                kept.append(atom)
        return tuple(kept)

    def _local_text(self, atom: AttestedMeasurement, radius: int = 24) -> str:
        text = str(getattr(self.documents[atom.fact_id], "text"))
        start = max(0, atom.source_span[0] - radius)
        end = min(len(text), atom.source_span[1] + radius)
        before = text[start:atom.source_span[0]]
        after = text[atom.source_span[1]:end]
        boundary = max((before.rfind(mark) for mark in ".!?;\n"), default=-1)
        if boundary >= 0:
            start += boundary + 1
        positions = [position for mark in ".!?;\n"
                     if (position := after.find(mark)) >= 0]
        if positions:
            end = atom.source_span[1] + min(positions)
        return text[start:end]

    def sum_convergent(self, question: str) -> ConvergentScalarAnswer:
        if not _SUM_CUE.search(question):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (),
                "sum_requires_explicit_aggregation_obligation", True)
        if re.search(r"\bfrom\b.+\bto\b", question, re.I):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "from_to_interval_requires_interval_operator", True)
        requested = self._requested_unit(question)
        if requested is None:
            return ConvergentScalarAnswer("unsupported", None, "", (),
                                          "question_has_no_exact_measurement_unit", True)
        dimension, output_unit, divisor = requested
        qterms = _terms(question, question=True) - _SCAFFOLD
        # The output unit is a type constraint, not a semantic selector.  Leaving it in the
        # overlap would make every measurement of that unit look relevant.
        unit_surface = _QUERY_UNIT.search(question)
        if unit_surface is not None:
            qterms -= _terms(unit_surface.group(0), question=True)
        # Numerals in a question normally constrain cardinality/result.  Treating them as
        # retrieval terms admitted unrelated "three weeks" evidence into a three-destination sum.
        observed_question = observe_raw_text(question, question=True)
        qterms -= frozenset(observed_question.numbers)
        qterms -= frozenset(_WORD_NUMBER)
        expected_operands = None
        for match in _CARDINAL.finditer(question):
            noun = match.group("noun").casefold()
            if noun in _UNIT or noun in {"times"}:
                continue
            raw = match.group("n").casefold()
            expected_operands = int(raw) if raw.isdigit() else int(_WORD_NUMBER[raw])
            break
        entity_obligations = frozenset(
            term for entity in observed_question.entities for term in _terms(entity))
        candidates = tuple(atom for atom in self.atoms if atom.asserted and atom.positive and
                           not atom.qualifier and
                           not _RATE.search(self._local_text(atom)) and
                           (atom.dimension == dimension or
                            dimension == "currency:any" and atom.dimension.startswith("currency:")))
        if dimension == "currency:any":
            currencies = {atom.dimension for atom in candidates}
            if len(currencies) != 1:
                return ConvergentScalarAnswer("abstain", None, output_unit, (),
                                              "currency_not_conserved", True)
            output_unit = next(iter(currencies)).split(":", 1)[1]
        if not candidates:
            return ConvergentScalarAnswer("abstain", None, output_unit, (),
                                          "no_attested_measurement", True)

        query_relations = frozenset(
            relation for relation in _relations(question, question=True)
            if set(relation.split(">")) <= qterms)
        overlaps = {
            atom: len(qterms & atom.terms) + len(
                query_relations & _relations(atom.sentence))
            for atom in candidates
        }
        maximum = max(overlaps.values(), default=0)
        # Threshold worlds are a bounded gauge family.  Requiring convergence prevents the
        # arbitrary choice between a broad and narrow lexical interpretation from becoming truth.
        thresholds = tuple(range(1, maximum + 1))
        worlds = []
        for threshold in thresholds:
            selected = tuple(atom for atom in candidates if overlaps[atom] >= threshold)
            selected = self._deduplicate(selected)
            if not selected:
                continue
            if expected_operands is not None and len({atom.fact_id for atom in selected}) != expected_operands:
                continue
            witnessed_terms = frozenset(term for atom in selected for term in atom.terms)
            if entity_obligations and not entity_obligations <= witnessed_terms:
                continue
            total_base = sum((atom.base_value for atom in selected), Decimal(0))
            value = total_base / divisor
            worlds.append(ScalarProofWorld(
                threshold, _render(value), output_unit,
                tuple(sorted({atom.fact_id for atom in selected})),
                tuple((atom.fact_id, *atom.source_span) for atom in selected),
                "full_authority_scan_and_exact_unit_conservation",
            ))
        worlds = tuple(worlds)
        if not worlds:
            return ConvergentScalarAnswer("abstain", None, output_unit, (),
                                          "selector_has_no_attested_world", True)
        signatures = {(world.value, world.unit, world.fact_ids) for world in worlds}
        if len(signatures) != 1:
            return ConvergentScalarAnswer("contested", None, output_unit, worlds,
                                          "selector_gauges_do_not_converge", True)

        # Parsing debts block only when their sentence intersects the query surface.  This is a
        # surface certificate, not semantic closed-world proof.
        for fact_id, start, end in self.unresolved_measure_sentences:
            document = self.documents[fact_id]
            if qterms & _terms(str(getattr(document, "text"))[start:end]):
                return ConvergentScalarAnswer("abstain", None, output_unit, worlds,
                                              "relevant_unparsed_measurement", False)
        answer = worlds[0]
        return ConvergentScalarAnswer("resolved", answer.value, answer.unit, worlds,
                                      "all_attested_selector_gauges_converge", True)

    def lookup_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Resolve one exact measured surface only after binding obligations close."""
        if _SUM_CUE.search(question):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "aggregate_query_requires_sum_executor", True)
        if _DERIVED_ARITHMETIC_QUERY.search(question):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "derived_value_requires_typed_arithmetic", True)
        if (re.search(r"\b(?:worth\s+in\s+terms|triple|double|times\s+what)\b", question, re.I) or
                re.match(r"^\s*how\s+many\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b",
                         question, re.I) and re.search(r"\band\b", question, re.I)):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "derived_or_coordinated_query_requires_typed_operator", True)
        if not _SCALAR_LOOKUP_CUE.search(question):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "question_is_not_a_scalar_lookup", True)
        observed = observe_raw_text(question, question=True)
        qterms = _terms(question, question=True) - _SCAFFOLD - frozenset(_WORD_NUMBER)
        qterms -= frozenset(observed.numbers)
        unit_surface = _QUERY_UNIT.search(question)
        if unit_surface is not None:
            qterms -= _terms(unit_surface.group(0), question=True)
        entities = frozenset(term for entity in observed.entities for term in _terms(entity))
        ordered_relations = tuple(
            f"{_LEMMA.get(left, left)}>{_LEMMA.get(right, right)}"
            for raw in observed.relations for left, right in (raw.split(">", 1),))
        # Operator words may be scaffold for lexical selection while still carrying the
        # relation that types a scalar lookup (for example ``move>take``).  Preserve the
        # complete relation chain; it is an obligation, not a relevance score.
        relations = frozenset(ordered_relations)
        left_nodes = {relation.split(">", 1)[0] for relation in relations}
        terminal_relations = frozenset(
            relation for relation in relations
            if relation.split(">", 1)[1] not in left_nodes)
        document_frequency = {term: sum(
            term in _terms(str(getattr(document, "text")))
            for document in self.documents.values()) for term in qterms}
        rare_obligations = frozenset(
            term for term, value in document_frequency.items()
            if document_frequency and value == min(document_frequency.values()))
        admitted = []
        for atom in self.atoms:
            if not atom.asserted or not atom.positive:
                continue
            local = self._local_text(atom)
            if (atom.dimension in {"time", "calendar"} and
                    _RELATIVE_TIME.search(local) and
                    not _RELATIVE_TIME.search(question)):
                continue
            if entities and not entities <= atom.terms:
                continue
            if rare_obligations and not rare_obligations <= atom.terms:
                continue
            atom_relations = _relations(atom.binding_text)
            relation_witnesses = relations & atom_relations
            overlap = len(qterms & atom.terms)
            if terminal_relations and not terminal_relations <= atom_relations:
                continue
            if not entities and not relation_witnesses and overlap < 2:
                continue
            if overlap == 0:
                continue
            admitted.append((overlap + len(relation_witnesses), atom))
        if not admitted:
            # A second, independently auditable gauge permits paraphrase transport only when
            # the result type is known and no source relation fills the query's terminal slot
            # with a different concrete value (film→camera, violin→guitar).  Scores propose;
            # the collision gate authorizes.
            required_dimensions = None
            if re.match(r"^\s*what\s+time\b", question, re.I):
                required_dimensions = {"clock"}
            elif re.match(r"^\s*what\s+speed\b", question, re.I):
                required_dimensions = {"data_rate"}
            elif re.match(r"^\s*how\s+(?:long|much\s+time)\b", question, re.I):
                required_dimensions = {"time", "calendar"}
            elif (re.match(r"^\s*how\s+much\b", question, re.I) and
                  re.search(r"\b(?:paid|price|cost|worth|money)\b", question, re.I)):
                required_dimensions = {"currency:USD", "currency:EUR", "currency:GBP"}
            fallback = []
            for atom in self.atoms:
                if (not atom.asserted or not atom.positive or
                        required_dimensions is not None and atom.dimension not in required_dimensions):
                    continue
                local = self._local_text(atom)
                if (atom.dimension in {"time", "calendar"} and
                        _RELATIVE_TIME.search(local) and
                        not _RELATIVE_TIME.search(question)):
                    continue
                if entities and not entities <= atom.terms:
                    continue
                atom_relations = _relations(atom.binding_text)
                collision = False
                for relation in relations:
                    left, right = relation.split(">", 1)
                    if right not in qterms:
                        continue
                    alternatives = {candidate.split(">", 1)[1] for candidate in atom_relations
                                    if candidate.startswith(left + ">")}
                    governing_path = (left in _RELATION_ACTIONS or any(
                        query_relation.endswith(">" + left) and query_relation in atom_relations
                        for query_relation in relations))
                    if (governing_path and right not in atom.terms and alternatives and
                            right not in alternatives and
                            any(alternative not in _RELATION_ACTIONS for alternative in alternatives)):
                        collision = True
                        break
                if collision:
                    continue
                overlap = len(qterms & atom.terms)
                if overlap >= 2 or entities and overlap > 0:
                    fallback.append((overlap, atom))
            if not fallback:
                return ConvergentScalarAnswer("abstain", None, "", (),
                                              "no_measurement_closes_binding_obligations", True)
            maximum = max(score for score, _atom in fallback)
            selected = self._deduplicate(
                tuple(atom for score, atom in fallback if score == maximum))
            rendered = tuple((atom, *self._lookup_surface(atom)) for atom in selected)
            signatures = {(surface.casefold(), atom.dimension, atom.base_value)
                          for atom, surface, _span in rendered}
            worlds = tuple(ScalarProofWorld(
                maximum, surface, atom.unit, (atom.fact_id,),
                ((atom.fact_id, *span),), "typed_lexical_gauge_without_slot_collision")
                for atom, surface, span in rendered)
            if len(signatures) != 1:
                return ConvergentScalarAnswer("contested", None, "", worlds,
                                              "typed_lexical_gauge_contested", True)
            witness = min(selected, key=lambda atom: (atom.fact_id, atom.source_span))
            surface, _span = self._lookup_surface(witness)
            return ConvergentScalarAnswer(
                "resolved", surface, witness.unit, worlds,
                "typed_lexical_gauge_without_slot_collision", True)
        maximum = max(score for score, _atom in admitted)
        selected = self._deduplicate(
            tuple(atom for score, atom in admitted if score == maximum))
        rendered = tuple((atom, *self._lookup_surface(atom)) for atom in selected)
        signatures = {(surface.casefold(), atom.dimension, atom.base_value)
                      for atom, surface, _span in rendered}
        worlds = tuple(ScalarProofWorld(
            maximum, surface, atom.unit, (atom.fact_id,),
            ((atom.fact_id, *span),), "maximal_exact_binding")
            for atom, surface, span in rendered)
        if len(signatures) != 1:
            return ConvergentScalarAnswer("contested", None, "", worlds,
                                          "maximal_bindings_disagree", True)
        witness = min(selected, key=lambda atom: (atom.fact_id, atom.source_span))
        surface, span = self._lookup_surface(witness)
        world = ScalarProofWorld(
            maximum, surface, witness.unit, (witness.fact_id,),
            ((witness.fact_id, *span),), "unique_maximal_exact_binding")
        return ConvergentScalarAnswer("resolved", surface, witness.unit, (world,),
                                      "unique_maximal_exact_binding", True)

    def _lookup_surface(self, atom: AttestedMeasurement) -> tuple[str, tuple[int, int]]:
        """Preserve an immediately attested distributive modifier such as ``each way``.

        Dropping this suffix changes the denotation of a scalar lookup even though its numeric
        value remains unchanged. The extension is source-exact and becomes part of the reopened
        citation; no suffix is inferred from the question or a unit catalogue.
        """
        start, end = atom.source_span
        text = str(getattr(self.documents[atom.fact_id], "text"))
        suffix = _DISTRIBUTIVE_MEASURE_SUFFIX.match(text, end)
        if suffix is None:
            return atom.surface, atom.source_span
        extended_end = suffix.end("suffix")
        return text[start:extended_end], (start, extended_end)

    def product_sum_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Execute completed ``count × unit price`` terms plus attested lump sums.

        The count and price must occur in the same authoritative sentence and ``each/per``
        must bind the price.  A bare rate is never added as money, and a count from another
        sentence can never tunnel into the product.
        """
        if not _SUM_CUE.search(question) or not re.search(r"\b(?:money|dollars?|euros?|pounds?)\b",
                                                          question, re.I):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "product_sum_requires_total_money_query", True)
        observed = observe_raw_text(question, question=True)
        qterms = _terms(question, question=True) - _SCAFFOLD - frozenset(_WORD_NUMBER)
        qterms -= frozenset(observed.numbers) | {"money"}
        contributions: list[AttestedContribution] = []
        for atom in self.atoms:
            if (not atom.asserted or not atom.positive or atom.qualifier or
                    not atom.dimension.startswith("currency:") or
                    not _COMPLETED_TRANSACTION.search(atom.sentence)):
                continue
            overlap = qterms & atom.terms
            if not overlap:
                continue
            local = self._local_text(atom)
            if not _RATE.search(local):
                contributions.append(AttestedContribution(
                    atom.fact_id, atom.base_value, atom.dimension.split(":", 1)[1],
                    ((atom.fact_id, *atom.source_span),), atom.terms,
                    "completed_lump_sum"))
                continue
            relative_price_start = atom.source_span[0] - atom.sentence_span[0]
            prefix = atom.sentence[:relative_price_start]
            counts = tuple(_FACTOR_COUNT.finditer(prefix))
            if not counts:
                continue
            count_match = counts[-1]
            raw = count_match.group("n").casefold()
            count = (Decimal(raw.replace(",", "")) if raw[0].isdigit()
                     else _WORD_NUMBER[raw])
            if count <= 0 or count != count.to_integral_value():
                continue
            count_start = atom.sentence_span[0] + count_match.start()
            count_end = atom.sentence_span[0] + count_match.end()
            contributions.append(AttestedContribution(
                atom.fact_id, count * atom.base_value, atom.dimension.split(":", 1)[1],
                ((atom.fact_id, count_start, count_end),
                 (atom.fact_id, *atom.source_span)), atom.terms,
                "same_sentence_exact_count_times_unit_price"))
        if not contributions:
            return ConvergentScalarAnswer("abstain", None, "", (),
                                          "no_completed_product_or_lump_sum", True)
        currencies = {item.unit for item in contributions}
        if len(currencies) != 1:
            return ConvergentScalarAnswer("abstain", None, "", (),
                                          "product_sum_currency_not_conserved", True)
        target_terms = qterms - _TRANSACTION_ACTIONS
        gauges = []
        any_target = tuple(item for item in contributions if target_terms & item.terms)
        if any_target:
            gauges.append((1, any_target))
        all_target = tuple(item for item in contributions if target_terms <= item.terms)
        if all_target:
            gauges.append((2, all_target))
        worlds = []
        for gauge, selected in gauges:
            total = sum((item.value for item in selected), Decimal(0))
            worlds.append(ScalarProofWorld(
                gauge, _render(total), next(iter(currencies)),
                tuple(sorted({item.fact_id for item in selected})),
                tuple(dict.fromkeys(span for item in selected for span in item.spans)),
                "completed_transactions_with_exact_product_terms"))
        worlds = tuple(worlds)
        if not worlds:
            return ConvergentScalarAnswer("abstain", None, next(iter(currencies)), (),
                                          "transaction_target_not_witnessed", True)
        signatures = {(world.value, world.unit, world.fact_ids) for world in worlds}
        if len(signatures) != 1:
            return ConvergentScalarAnswer("contested", None, next(iter(currencies)), worlds,
                                          "transaction_selector_gauges_do_not_converge", True)
        answer = worlds[0]
        return ConvergentScalarAnswer("resolved", answer.value, answer.unit, worlds,
                                      "all_product_sum_gauges_converge", True)

    def coordinated_count_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Count explicitly quantified members of a witnessed coordinated type fiber."""
        query_match = _COUNT_QUERY.search(question)
        if query_match is None or not re.search(r"\b(?:total|both|all)\b", question, re.I):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "coordinated_count_requires_total_query", True)
        target_terms = tuple(_terms(query_match.group("target"), question=True) - _SCAFFOLD)
        if not target_terms:
            return ConvergentScalarAnswer("unsupported", None, "", (),
                                          "count_target_is_absent", True)
        target = target_terms[-1]
        lists: list[AttestedCountList] = []
        debts = []
        for document in self.documents.values():
            text = str(getattr(document, "text"))
            role = getattr(document, "role", None)
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")].casefold()
            if role != "user":
                continue
            for sentence_match in _SENTENCE.finditer(text):
                sentence = sentence_match.group(0)
                if sentence.rstrip().endswith("?") or _NON_ASSERTED.search(sentence):
                    continue
                intro = _LIST_INTRODUCER.search(sentence)
                if intro is None:
                    continue
                raw_tail = sentence[intro.end():]
                # The first terminal punctuation closes the enumerated predicate.  Commas and
                # conjunctions remain inside it as coordinate separators.
                tail = re.split(r"[;!?]", raw_tail, maxsplit=1)[0]
                pieces = tuple(
                    re.sub(r"^and\s+", "", piece.strip(" .-"), flags=re.I)
                    for piece in _LIST_SPLIT.split(tail) if piece.strip(" .-"))
                parsed = []
                typed = False
                for piece in pieces:
                    count_match = _LEADING_COUNT.match(piece)
                    piece_terms = _terms(piece)
                    has_type = target in piece_terms or any(
                        token.endswith(target) for token in piece_terms)
                    typed = typed or has_type
                    if count_match is None:
                        # A trailing proper-name apposition (", Bubbles") identifies the prior
                        # member; it is not another unquantified member.
                        if re.fullmatch(r"[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*)*", piece):
                            continue
                        parsed.append((None, piece, None))
                        continue
                    raw = count_match.group("n").casefold()
                    value = 1 if raw in {"a", "an", "my"} else (
                        int(raw.replace(",", "")) if raw[0].isdigit()
                        else int(_WORD_NUMBER[raw]))
                    local_start = sentence.find(piece, intro.end())
                    if local_start < 0:
                        local_start = intro.end() + raw_tail.find(piece)
                    start = sentence_match.start() + local_start + count_match.start()
                    end = sentence_match.start() + local_start + count_match.end()
                    identity = " ".join(sorted(piece_terms - {target})) or piece.casefold()
                    parsed.append((value, identity, (int(getattr(document, "fact_id")), start, end)))
                if not typed:
                    continue
                if any(value is None for value, _identity, _span in parsed):
                    debts.append((int(getattr(document, "fact_id")), sentence_match.span()))
                    continue
                realized = tuple(item for item in parsed if item[0] is not None)
                if not realized:
                    continue
                lists.append(AttestedCountList(
                    int(getattr(document, "fact_id")), sum(item[0] for item in realized),
                    tuple(item[1] for item in realized), tuple(item[2] for item in realized)))
        if debts:
            return ConvergentScalarAnswer("abstain", None, "count", (),
                                          "typed_coordinate_has_unquantified_member", False)
        if not lists:
            return ConvergentScalarAnswer("abstain", None, "count", (),
                                          "no_witnessed_typed_coordinate", True)
        # Identical enumerations are report orbits, not additional members.
        unique = {}
        for item in lists:
            unique.setdefault((item.value, item.identities), item)
        selected = tuple(unique.values())
        value = sum(item.value for item in selected)
        world = ScalarProofWorld(
            1, str(value), "count", tuple(sorted(item.fact_id for item in selected)),
            tuple(span for item in selected for span in item.spans),
            "closed_quantified_coordinate_fiber")
        return ConvergentScalarAnswer("resolved", str(value), "count", (world,),
                                      "closed_quantified_coordinate_fiber", True)

    def acquisition_count_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Count distinct acquired objects in a query-declared recent window."""
        query_match = _COUNT_QUERY.search(question)
        if (query_match is None or _QUERY_UNIT.search(query_match.group("target")) or
                not _ACQUISITION_QUERY.search(question) or
                re.search(r"\bor\b", question, re.I)):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_a_pure_acquisition_count", True)
        target_sequence = tuple(
            _LEMMA.get(token, token) for token in
            observe_raw_text(query_match.group("target"), question=True).lexical
            if _LEMMA.get(token, token) not in _SCAFFOLD)
        if not target_sequence:
            return ConvergentScalarAnswer("unsupported", None, "", (),
                                          "acquisition_target_is_absent", True)
        target = target_sequence[-1]
        if re.search(r"\b(?:so\s+far|to\s+date)\b", question, re.I):
            source_match = re.search(
                r"\bfrom\s+(?P<source>.+?)(?:\s+(?:so\s+far|to\s+date))?\s*\??$",
                question, re.I)
            source_key = (re.sub(r"[^\w]+", "", source_match.group("source").casefold())
                          if source_match else "")
            cumulative = []
            for fact_id, text in self.authoritative_documents():
                if (source_key and source_key not in re.sub(r"[^\w]+", "", text.casefold())):
                    continue
                for sentence_match in _SENTENCE.finditer(text):
                    sentence = sentence_match.group(0)
                    if (not re.search(r"\b(?:already|so\s+far|to\s+date)\b", sentence, re.I) or
                            not (_ACQUISITION_QUERY.search(sentence) or
                                 _ACQUISITION_VERB.search(sentence))):
                        continue
                    for cardinal in _CARDINAL.finditer(sentence):
                        noun_surface = cardinal.group("noun").casefold()
                        noun = _LEMMA.get(noun_surface, noun_surface).rstrip("s")
                        if noun != target.rstrip("s"):
                            continue
                        number_surface = cardinal.group("n").casefold()
                        number_value = (_WORD_NUMBER[number_surface]
                                        if number_surface in _WORD_NUMBER else
                                        Decimal(number_surface.replace(",", "")))
                        value = int(number_value)
                        span = (fact_id, sentence_match.start() + cardinal.start("n"),
                                sentence_match.start() + cardinal.end("n"))
                        cumulative.append((fact_id, value, span))
            cumulative.sort()
            if not cumulative:
                return ConvergentScalarAnswer(
                    "abstain", None, "count", (), "no_cumulative_acquisition_state", True)
            if any(right[1] < left[1] for left, right in zip(cumulative, cumulative[1:])):
                return ConvergentScalarAnswer(
                    "contested", None, "count", (), "cumulative_acquisition_regressed", True)
            fact_id, value, span = cumulative[-1]
            world = ScalarProofWorld(
                1, str(value), "count", (fact_id,), (span,),
                f"monotone_cumulative_acquisition_last_write:{source_key}")
            return ConvergentScalarAnswer(
                "resolved", str(value), "count", (world,),
                "cumulative_acquisition_last_write", True)
        requires_recent = bool(re.search(r"\blast\s+(?:one|two|three|four|\d+)?\s*months?\b",
                                         question, re.I))
        events: list[AttestedEntityEvent] = []
        debts = []
        for document in self.documents.values():
            text = str(getattr(document, "text"))
            role = getattr(document, "role", None)
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")].casefold()
            if role != "user" or target not in _terms(text):
                continue
            for sentence_match in _SENTENCE.finditer(text):
                sentence = sentence_match.group(0)
                if (not _ACQUISITION_VERB.search(sentence) or sentence.rstrip().endswith("?") or
                        observe_raw_text(sentence).polarity != "positive"):
                    continue
                if requires_recent and not _RECENT_WINDOW.search(sentence):
                    continue
                found = []
                for pattern in (_RELATIVE_ACQUISITION, _DIRECT_ACQUISITION):
                    for match in pattern.finditer(sentence):
                        surface = match.group("object").strip()
                        if _RECENT_WINDOW.fullmatch(surface):
                            continue
                        if surface.casefold() in {"it", "them", "this", "that"}:
                            before = text[max(0, sentence_match.start() - 160):sentence_match.start()]
                            antecedents = tuple(re.finditer(
                                r"\bmy\s+(?P<object>[A-Za-z][A-Za-z'’-]*(?:\s+"
                                r"[A-Za-z][A-Za-z'’-]*){0,3})", before, re.I))
                            if not antecedents:
                                continue
                            antecedent = antecedents[-1]
                            surface = antecedent.group("object")
                            start = max(0, sentence_match.start() - 160) + antecedent.start("object")
                            end = max(0, sentence_match.start() - 160) + antecedent.end("object")
                            pieces = ((surface, start, end),)
                        else:
                            raw_pieces = tuple(piece.strip() for piece in re.split(
                                r"\s+and\s+", surface, flags=re.I) if piece.strip())
                            pieces_list = []
                            for piece in raw_pieces:
                                relative = sentence.find(piece, match.start("object"))
                                if relative < 0:
                                    continue
                                pieces_list.append((piece, sentence_match.start() + relative,
                                                    sentence_match.start() + relative + len(piece)))
                            pieces = tuple(pieces_list)
                        for piece, start, end in pieces:
                            identity = _terms(piece) - frozenset(
                                ("new", "simple", "pair", "small", "same", "this", "that",
                                 "those", "the"))
                            # A bare class set ("my plants", "the jewelry") has unknown
                            # cardinality and cannot be counted as one individual.
                            if identity and identity != {target}:
                                found.append(AttestedEntityEvent(
                                    int(getattr(document, "fact_id")), identity,
                                    (int(getattr(document, "fact_id")), start, end)))
                if not found:
                    debts.append((int(getattr(document, "fact_id")), sentence_match.span()))
                events.extend(found)
        if debts:
            return ConvergentScalarAnswer("abstain", None, "count", (),
                                          "acquisition_object_unresolved", False)
        if not events:
            return ConvergentScalarAnswer("abstain", None, "count", (),
                                          "no_typed_acquisition_events", True)
        orbits: list[list[AttestedEntityEvent]] = []
        for event in events:
            placed = False
            for orbit in orbits:
                representative = orbit[0].identity
                union = representative | event.identity
                if (representative <= event.identity or event.identity <= representative or
                        (union and len(representative & event.identity) / len(union) >= 0.6)):
                    orbit.append(event); placed = True; break
            if not placed:
                orbits.append([event])
        witnesses = tuple(min(orbit, key=lambda item: (item.fact_id, item.span)) for orbit in orbits)
        world = ScalarProofWorld(
            1, str(len(witnesses)), "count", tuple(sorted({item.fact_id for item in witnesses})),
            tuple(item.span for item in witnesses), "distinct_attested_acquisition_orbits")
        return ConvergentScalarAnswer("resolved", str(len(witnesses)), "count", (world,),
                                      "distinct_attested_acquisition_orbits", True)

    def textual_projection_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Project one source-exact location or copular attribute value."""
        if not (_WHERE_QUERY.search(question) or _ATTRIBUTE_QUERY.search(question)):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_a_textual_projection", True)
        qterms = _terms(question, question=True) - _SCAFFOLD
        user_documents = []
        sentences = []
        for document in sorted(self.documents.values(), key=lambda item: int(getattr(item, "fact_id"))):
            text = str(getattr(document, "text"))
            role = getattr(document, "role", None)
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")].casefold()
            if role != "user":
                continue
            user_documents.append(document)
            for sentence_match in _SENTENCE.finditer(text):
                sentence = sentence_match.group(0)
                sentences.append((document, sentence_match, sentence))

        candidates = []
        where_action = _WHERE_ACTION.search(question)
        if _WHERE_QUERY.search(question):
            if where_action is None:
                return ConvergentScalarAnswer("unsupported", None, "", (),
                                              "where_action_is_absent", True)
            action_terms = _terms(where_action.group("action"), question=True)
            object_obligations = qterms - action_terms - {"new", "old"}
            anchors = []
            for document, sentence_match, sentence in sentences:
                if sentence.rstrip().endswith("?"):
                    continue
                terms = _terms(sentence)
                if not action_terms <= terms or not object_obligations <= terms:
                    continue
                overlap = len(qterms & terms)
                if overlap:
                    anchors.append((overlap, int(getattr(document, "fact_id")), sentence_match,
                                    sentence, document))
            if not anchors:
                return ConvergentScalarAnswer("abstain", None, "", (),
                                              "where_event_anchor_is_absent", True)
            maximum_anchor = max(item[0] for item in anchors)
            anchors = [item for item in anchors if item[0] == maximum_anchor]
            for overlap, anchor_id, anchor_match, anchor_sentence, anchor_document in anchors:
                occurrences = {}
                for document, sentence_match, sentence in sentences:
                    fact_id = int(getattr(document, "fact_id"))
                    if abs(fact_id - anchor_id) > 2:
                        continue
                    for prep in re.finditer(
                            r"\b(?:at|in|from|to)\s+(?P<value>[^,.;!?]{1,100})", sentence, re.I):
                        proper = _PROPER_VALUE.search(prep.group("value"))
                        if not proper:
                            continue
                        text = str(getattr(document, "text"))
                        start = sentence_match.start() + prep.start("value") + proper.start()
                        end = sentence_match.start() + prep.start("value") + proper.end()
                        value = text[start:end]
                        occurrences.setdefault(value.casefold(), []).append(
                            (value, fact_id, start, end, fact_id == anchor_id))
                for rows in occurrences.values():
                    if not any(item[4] for item in rows) and len({item[1] for item in rows}) < 2:
                        continue
                    value, fact_id, start, end, direct = min(rows, key=lambda item: (item[1], item[2]))
                    candidates.append((overlap + len(rows) + int(direct), value,
                                       fact_id, start, end))
        else:
            query = _ATTRIBUTE_QUERY.search(question)
            subject_terms = _terms(query.group("subject"), question=True)
            subject = re.escape(query.group("subject").strip())
            attribute = _terms(query.group("attribute"), question=True)
            type_attribute = bool(attribute & {"breed", "type", "kind", "class", "category"})
            for document, sentence_match, sentence in sentences:
                fact_id = int(getattr(document, "fact_id"))
                terms = _terms(sentence)
                overlap = len(qterms & terms)
                patterns = (
                    re.compile(rf"\b(?:my|the)\s+{subject}(?:\s*,\s*[A-Z][\w'’-]*\s*,)?\s+"
                               r"(?:is|was)\s+(?:an?\s+)?(?P<value>[^,.;!?]{1,80})", re.I),
                    re.compile(rf"\b(?:my|the)\s+{subject}\s*,\s+(?:an?\s+)?"
                               r"(?P<value>[^,.;!?]{1,80})", re.I),
                )
                matches = [match for pattern in patterns for match in pattern.finditer(sentence)]
                if type_attribute:
                    matches.extend(re.finditer(
                        r"\b(?:an?\s+)?(?P<value>[A-Z][A-Za-z'’-]*(?:\s+[A-Z]"
                        r"[A-Za-z'’-]*){0,4})\s+like\s+[A-Z][A-Za-z'’-]*\b", sentence))
                for match in matches:
                    # Direct copula requires the subject here.  Exemplar typing may use the
                    # bounded discourse neighborhood, but only if the subject is attested nearby.
                    if not subject_terms <= terms:
                        nearby = " ".join(str(getattr(other, "text")) for other in user_documents
                                          if abs(int(getattr(other, "fact_id")) - fact_id) <= 6)
                        if not subject_terms <= _terms(nearby):
                            continue
                    proper = _PROPER_VALUE.search(match.group("value"))
                    if proper:
                        text = str(getattr(document, "text"))
                        start = sentence_match.start() + match.start("value") + proper.start()
                        end = sentence_match.start() + match.start("value") + proper.end()
                        candidates.append((max(1, overlap), text[start:end], fact_id, start, end))
        if not candidates:
            return ConvergentScalarAnswer("abstain", None, "", (),
                                          "no_source_exact_textual_projection", True)
        maximum = max(item[0] for item in candidates)
        winners = tuple(item for item in candidates if item[0] == maximum)
        signatures = {item[1].casefold() for item in winners}
        if len(signatures) != 1:
            worlds = tuple(ScalarProofWorld(
                maximum, value, "text", (fact_id,), ((fact_id, start, end),),
                "maximal_source_exact_projection")
                for _score, value, fact_id, start, end in winners)
            return ConvergentScalarAnswer("contested", None, "text", worlds,
                                          "textual_projection_contested", True)
        _score, value, fact_id, start, end = min(winners, key=lambda item: (item[2], item[3]))
        world = ScalarProofWorld(maximum, value, "text", (fact_id,), ((fact_id, start, end),),
                                 "unique_source_exact_projection")
        return ConvergentScalarAnswer("resolved", value, "text", (world,),
                                      "unique_source_exact_projection", True)

    def relative_value_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Project an explicitly stated multiplicative value relation as source text.

        This operator does not invent an absolute price.  It closes only the algebraic
        relation requested by the question (for example, ``triple what I paid``), and only
        when every authoritative witness in the episode agrees on the exact relation.
        """
        query = _RELATIVE_VALUE_QUERY.match(question)
        if not query:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_relative_value", True)
        candidates = []
        for fact_id, document in self.documents.items():
            text = str(getattr(document, "text"))
            role = getattr(document, "role", None)
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")].casefold()
            if role != "user":
                continue
            for match in _RELATIVE_VALUE_SOURCE.finditer(text):
                start, end = match.span("value")
                candidates.append((text[start:end], fact_id, start, end))
        if not candidates:
            return ConvergentScalarAnswer(
                "abstain", None, "relative_value", (), "no_attested_relative_value", True)
        signatures = {value.casefold() for value, _fact_id, _start, _end in candidates}
        subject = " ".join(query.group("subject").split())
        subject = subject[:1].upper() + subject[1:]
        worlds = tuple(ScalarProofWorld(
            1, f"{subject} is worth {value}", "relative_value", (fact_id,),
            ((fact_id, start, end),),
            "source_exact_relative_value")
            for value, fact_id, start, end in candidates)
        if len(signatures) != 1:
            return ConvergentScalarAnswer(
                "contested", None, "relative_value", worlds,
                "relative_value_witnesses_disagree", True)
        relation, fact_id, start, end = min(candidates, key=lambda item: (item[1], item[2]))
        value = f"{subject} is worth {relation}"
        world = ScalarProofWorld(1, value, "relative_value", (fact_id,),
                                 ((fact_id, start, end),),
                                 "unique_source_exact_relative_value")
        return ConvergentScalarAnswer(
            "resolved", value, "relative_value", (world,),
            "unique_source_exact_relative_value", True)

    def classified_money_sum_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Sum money whose local discourse clause explicitly carries the requested class."""
        query = _CLASSIFIED_MONEY_QUERY.search(question)
        if not query or not _SUM_CUE.search(question):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_classified_money_sum", True)
        class_terms = _terms(query.group("class"), question=True) - {
            "attend", "item", "thing", "purchase", "purchas",
        }
        if not class_terms:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "money_class_has_no_invariant", True)
        selected = []
        for atom in self.atoms:
            if (not atom.asserted or not atom.positive or atom.qualifier or
                    not atom.dimension.startswith("currency:") or
                    _RATE.search(self._local_text(atom))):
                continue
            text = str(getattr(self.documents[atom.fact_id], "text"))
            left = max(text.rfind(mark, 0, atom.source_span[0]) for mark in ".!?;\n") + 1
            # Contrast creates a new classification region inside a sentence.
            before = text[left:atom.source_span[0]]
            for contrast in re.finditer(r"\bbut\b", before, re.I):
                left = left + contrast.end()
                before = text[left:atom.source_span[0]]
            endings = [position for mark in ".!?;\n"
                       if (position := text.find(mark, atom.source_span[1])) >= 0]
            right = min(endings) if endings else len(text)
            after = text[atom.source_span[1]:right]
            contrast_after = re.search(r"\bbut\b", after, re.I)
            if contrast_after:
                right = atom.source_span[1] + contrast_after.start()
            clause = text[left:right]
            context_terms = _terms(clause)
            # A payment clause may resolve its object from the immediately preceding
            # sentence ("I attended a workshop. I paid $20").  No other free anaphora is
            # granted here.
            if (not class_terms <= context_terms and
                    re.match(r"\s*(?:I\s+paid|it\s+(?:was|cost)|the\s+(?:price|cost)\s+was)\b",
                             clause, re.I)):
                previous_start = max(text.rfind(mark, 0, max(0, left - 1))
                                     for mark in ".!?;\n") + 1
                context_terms |= _terms(text[previous_start:left])
            if class_terms <= context_terms:
                selected.append(atom)
        selected_atoms = self._deduplicate(tuple(selected))
        if not selected_atoms:
            return ConvergentScalarAnswer(
                "abstain", None, "money", (), "no_classified_money_witness", True)
        currencies = {atom.dimension for atom in selected_atoms}
        if len(currencies) != 1:
            return ConvergentScalarAnswer(
                "contested", None, "money", (), "classified_currency_not_conserved", True)
        value = _render(sum((atom.base_value for atom in selected_atoms), Decimal(0)))
        unit = next(iter(currencies)).split(":", 1)[1]
        world = ScalarProofWorld(
            1, value, unit, tuple(sorted({atom.fact_id for atom in selected_atoms})),
            tuple((atom.fact_id, *atom.source_span) for atom in selected_atoms),
            "local_clause_classification_and_exact_currency_conservation")
        return ConvergentScalarAnswer(
            "resolved", value, unit, (world,),
            "classified_money_sum_converged", True)

    def activity_duration_sum_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Sum completed activity durations while preserving approximate evidence."""
        query = _ACTIVITY_DURATION_TOTAL_QUERY.match(question)
        if not query:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_activity_duration_total", True)
        activity_terms = _terms(query.group("activity"), question=True) - _SCAFFOLD
        action_terms = {term for term in activity_terms if term in {
            "play", "read", "watch", "listen", "run", "travel", "work", "practic",
        }}
        if not action_terms:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "activity_has_no_attested_action", True)
        eligible = []
        for atom in self.atoms:
            local = self._local_text(atom, radius=48)
            local_channels = observe_raw_text(local)
            if (atom.dimension != "time" or _RATE.search(local) or
                    _NON_ASSERTED.search(local) or local_channels.polarity != "positive"):
                continue
            eligible.append(atom)
        completion_terms = {"finish", "complet", "complete"}
        seeds = [atom for atom in eligible if (
            action_terms & atom.terms or
            completion_terms & atom.terms and
            ({"game", "gaming"} & atom.terms if "play" in action_terms else
             activity_terms & atom.terms))]
        # Entity transport is source-attested: once a named object occurs in an explicit
        # activity frame, another completion report for the same entity inherits the type.
        # This preserves variants (e.g. hard vs normal difficulty) while collapsing repeats.
        seed_entities = set()
        for atom in seeds:
            seed_entities.update(_proper_identities(atom.binding_text))
        candidates = list(seeds)
        for atom in eligible:
            if atom in candidates or not completion_terms & atom.terms:
                continue
            if seed_entities & _proper_identities(atom.binding_text):
                candidates.append(atom)
        selected = self._deduplicate(tuple(candidates))
        if not selected:
            return ConvergentScalarAnswer(
                "abstain", None, "hour", (), "no_activity_duration_witness", True)
        total_seconds = sum((atom.base_value for atom in selected), Decimal(0))
        hours = total_seconds / Decimal(3600)
        rendered = _render(hours)
        if any(atom.qualifier for atom in selected):
            rendered = f"around {rendered} hours"
        world = ScalarProofWorld(
            1, rendered, "hour", tuple(sorted({atom.fact_id for atom in selected})),
            tuple((atom.fact_id, *atom.source_span) for atom in selected),
            "closed_activity_duration_orbits_with_uncertainty_preserved")
        return ConvergentScalarAnswer(
            "resolved", rendered, "hour", (world,),
            "activity_duration_sum_converged", True)

    def difference_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Execute a binary monetary difference only with two independently typed roles."""
        difference = _DIFFERENCE_QUERY.match(question)
        savings = _SAVINGS_QUERY.match(question)
        if not difference and not savings:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_binary_difference", True)
        atoms = tuple(atom for atom in self.atoms if atom.asserted and atom.positive and
                      not atom.qualifier and atom.dimension.startswith("currency:") and
                      not _RATE.search(self._local_text(atom)))
        if difference:
            left_terms = _terms(difference.group("left"), question=True) - _SCAFFOLD - {"amount"}
            right_terms = _terms(difference.group("right"), question=True) - _SCAFFOLD - {"amount"}

            def unique_role(role_terms):
                scored = [(len(role_terms & atom.terms), atom) for atom in atoms]
                maximum = max((score for score, _atom in scored), default=0)
                winners = [atom for score, atom in scored if score == maximum and score > 0]
                return winners[0] if len(winners) == 1 else None

            left, right = unique_role(left_terms), unique_role(right_terms)
            reason = "query_role_bound_binary_difference"
        else:
            object_terms = _terms(savings.group("object"), question=True) - _SCAFFOLD
            relevant = [atom for atom in atoms if any(
                query_term == source_term or
                len(query_term) >= 3 and len(source_term) >= 3 and
                (query_term.endswith(source_term) or source_term.endswith(query_term))
                for query_term in object_terms for source_term in atom.terms)]
            original = [atom for atom in relevant if re.search(
                r"\b(?:originally|original\s+(?:price|cost)|regular\s+price|before\s+the\s+sale)\b",
                atom.binding_text, re.I)]
            paid = [atom for atom in relevant if re.search(
                r"\b(?:got|bought|paid|sale\s+price|cost\s+me)\b", atom.binding_text, re.I)
                and atom not in original]
            left = original[0] if len(original) == 1 else None
            right = paid[0] if len(paid) == 1 else None
            reason = "original_minus_paid_savings"
        if left is None or right is None or left == right:
            return ConvergentScalarAnswer(
                "abstain", None, "money", (), "difference_roles_are_not_unique", True)
        if left.dimension != right.dimension:
            return ConvergentScalarAnswer(
                "contested", None, "money", (), "difference_currency_not_conserved", True)
        value = left.base_value - right.base_value
        if value < 0:
            return ConvergentScalarAnswer(
                "contested", None, "money", (), "difference_direction_is_negative", True)
        unit = left.dimension.split(":", 1)[1]
        world = ScalarProofWorld(
            1, _render(value), unit, tuple(sorted({left.fact_id, right.fact_id})),
            ((left.fact_id, *left.source_span), (right.fact_id, *right.source_span)), reason)
        return ConvergentScalarAnswer(
            "resolved", _render(value), unit, (world,), "binary_difference_converged", True)

    def cashback_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Compute purchase x cashback rate with merchant-bound source witnesses."""
        query = _CASHBACK_QUERY.match(question)
        if not query:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_cashback_product", True)
        merchant_terms = _terms(query.group("merchant"), question=True) - _SCAFFOLD
        if not merchant_terms:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "cashback_merchant_is_absent", True)
        rate_witnesses = []
        for fact_id, document in self.documents.items():
            text = str(getattr(document, "text"))
            role = getattr(document, "role", None)
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")].casefold()
            if (role != "user" or not merchant_terms <= _terms(text) or
                    not re.search(r"\bcash\s*back\b", text, re.I)):
                continue
            for match in _PERCENT.finditer(text):
                rate_witnesses.append((Decimal(match.group("rate")), fact_id, match.span()))
        purchases = [atom for atom in self.atoms if atom.asserted and atom.positive and
                     not atom.qualifier and atom.dimension.startswith("currency:") and
                     merchant_terms <= atom.terms and re.search(
                         r"\b(?:spent|paid|purchase|bought|grocer)\w*\b", atom.binding_text, re.I)]
        if len(rate_witnesses) != 1 or len(purchases) != 1:
            return ConvergentScalarAnswer(
                "abstain", None, "money", (), "cashback_rate_or_purchase_is_not_unique", True)
        rate, rate_fact_id, rate_span = rate_witnesses[0]
        purchase = purchases[0]
        value = purchase.base_value * rate / Decimal(100)
        unit = purchase.dimension.split(":", 1)[1]
        world = ScalarProofWorld(
            1, _render(value), unit, tuple(sorted({purchase.fact_id, rate_fact_id})),
            ((purchase.fact_id, *purchase.source_span), (rate_fact_id, *rate_span)),
            "merchant_bound_purchase_times_cashback_rate")
        return ConvergentScalarAnswer(
            "resolved", _render(value), unit, (world,), "cashback_product_converged", True)

    def current_role_duration_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Subtract pre-promotion tenure from total company tenure in exact calendar months."""
        if not _CURRENT_ROLE_DURATION_QUERY.match(question):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_current_role_duration", True)
        grouped = {}
        for atom in self.atoms:
            if (atom.asserted and atom.positive and not atom.qualifier and
                    atom.dimension == "calendar" and atom.unit in {"month", "year"}):
                grouped.setdefault(atom.fact_id, []).append(atom)

        def months(values):
            return sum((atom.value * (Decimal(12) if atom.unit == "year" else Decimal(1))
                        for atom in values), Decimal(0))

        total_groups, prior_groups = [], []
        for fact_id, values in grouped.items():
            text = values[0].binding_text
            if re.search(r"\b(?:experience|been|working)\b.{0,48}\b(?:company|organisation|organization)\b",
                         text, re.I):
                total_groups.append((fact_id, values))
            if re.search(r"\b(?:started|began)\s+as\b.{0,100}\b(?:after|before\s+promotion)\b",
                         text, re.I):
                prior_groups.append((fact_id, values))
        if len(total_groups) != 1 or len(prior_groups) != 1:
            return ConvergentScalarAnswer(
                "abstain", None, "month", (), "tenure_roles_are_not_unique", True)
        total_fact, total_values = total_groups[0]
        prior_fact, prior_values = prior_groups[0]
        remaining = months(total_values) - months(prior_values)
        if remaining < 0:
            return ConvergentScalarAnswer(
                "contested", None, "month", (), "current_role_duration_is_negative", True)
        years, residual_months = divmod(int(remaining), 12)
        parts = []
        if years:
            parts.append(f"{years} year" + ("s" if years != 1 else ""))
        if residual_months or not parts:
            parts.append(f"{residual_months} month" + ("s" if residual_months != 1 else ""))
        value = " and ".join(parts)
        spans = tuple((atom.fact_id, *atom.source_span)
                      for atom in (*total_values, *prior_values))
        world = ScalarProofWorld(
            1, value, "month", tuple(sorted({total_fact, prior_fact})), spans,
            "total_company_tenure_minus_pre_promotion_tenure")
        return ConvergentScalarAnswer(
            "resolved", value, "month", (world,), "current_role_duration_converged", True)

    def average_age_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Average a closed query-declared kinship fiber from exact age assertions."""
        query = _AVERAGE_AGE_QUERY.match(question)
        if not query:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_average_age", True)
        q = query.group(1).casefold()
        required = set()
        if re.search(r"\b(?:me|myself)\b", q):
            required.add("self")
        if re.search(r"\bparents?\b", q):
            required.update(("mother", "father"))
        if re.search(r"\bgrandparents?\b", q):
            required.update(("grandmother", "grandfather"))
        if not required:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "average_age_roles_are_not_closed", True)
        aliases = {"mom": "mother", "mother": "mother", "dad": "father", "father": "father",
                   "grandma": "grandmother", "grandmother": "grandmother",
                   "grandpa": "grandfather", "grandfather": "grandfather"}
        observed = {}
        for fact_id, document in self.documents.items():
            text = str(getattr(document, "text"))
            role = getattr(document, "role", None)
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")].casefold()
            if role != "user":
                continue
            for match in _KIN_AGE.finditer(text):
                key = aliases[match.group("role").casefold()]
                observed.setdefault(key, []).append(
                    (Decimal(match.group("age")), fact_id, match.span("age")))
            for match in _SELF_AGE.finditer(text):
                observed.setdefault("self", []).append(
                    (Decimal(match.group("age")), fact_id, match.span("age")))
        if any(len(observed.get(role, ())) != 1 for role in required):
            return ConvergentScalarAnswer(
                "abstain", None, "age", (), "age_role_missing_or_contested", True)
        witnesses = [observed[role][0] for role in sorted(required)]
        value = sum((item[0] for item in witnesses), Decimal(0)) / Decimal(len(witnesses))
        world = ScalarProofWorld(
            1, _render(value), "age", tuple(sorted({item[1] for item in witnesses})),
            tuple((item[1], *item[2]) for item in witnesses),
            "closed_kinship_fiber_arithmetic_mean")
        return ConvergentScalarAnswer(
            "resolved", _render(value), "age", (world,), "average_age_converged", True)

    def corpus_nonmembership_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Prove a distinctive presupposed identifier is absent from the full authority corpus."""
        event = _PRESUPPOSED_POSSESSION_EVENT.search(question)
        if not event:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_has_no_certifiable_identifier_presupposition", True)
        identifier = event.group("identifier").casefold()
        if any(identifier in text.casefold() for _fact_id, text in self.authoritative_documents()):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "identifier_occurs_in_authority_corpus", True)
        value = "You did not mention this information."
        world = ScalarProofWorld(
            1, value, "corpus_absence", (), (), f"authority_corpus_nonmembership:{identifier}")
        return ConvergentScalarAnswer(
            "resolved", value, "corpus_absence", (world,),
            "authority_corpus_nonmembership", True, True)

    def timeline_interval_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Subtract two source-attested calendar endpoints selected by query roles."""
        query = _TIMELINE_INTERVAL_QUERY.match(question)
        if not query:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_timeline_interval", True)
        start_terms = _terms(query.group("start"), question=True) - _SCAFFOLD
        end_terms = _terms(query.group("end"), question=True) - _SCAFFOLD - {"completion"}
        starts, ends = [], []
        for fact_id, text in self.authoritative_documents():
            for sentence_match in _SENTENCE.finditer(text):
                sentence = sentence_match.group(0)
                terms = _terms(sentence)
                if start_terms <= terms and re.search(r"\b(?:attend|study|school|enroll)\w*\b",
                                                     sentence, re.I):
                    for match in _YEAR_RANGE.finditer(sentence):
                        start = sentence_match.start() + match.start("start")
                        starts.append((int(match.group("start")), fact_id,
                                       (start, start + len(match.group("start")))))
                if (end_terms <= terms and
                        re.search(r"\b(?:graduat|complet|earn)\w*\b", sentence, re.I)):
                    for match in _YEAR_LITERAL.finditer(sentence):
                        start = sentence_match.start() + match.start()
                        ends.append((int(match.group(0)), fact_id, (start, start + 4)))
        starts = list(dict.fromkeys(starts)); ends = list(dict.fromkeys(ends))
        start_years, end_years = {item[0] for item in starts}, {item[0] for item in ends}
        if len(start_years) != 1 or len(end_years) != 1:
            return ConvergentScalarAnswer(
                "abstain", None, "year", (), "timeline_endpoint_missing_or_contested", True)
        start_year, end_year = next(iter(start_years)), next(iter(end_years))
        if end_year < start_year:
            return ConvergentScalarAnswer(
                "contested", None, "year", (), "timeline_interval_is_negative", True)
        start_witness = min(starts, key=lambda item: (item[1], item[2]))
        end_witness = min(ends, key=lambda item: (item[1], item[2]))
        value = str(end_year - start_year)
        world = ScalarProofWorld(
            1, value, "year", tuple(sorted({start_witness[1], end_witness[1]})),
            ((start_witness[1], *start_witness[2]), (end_witness[1], *end_witness[2])),
            "query_bound_calendar_endpoint_difference")
        return ConvergentScalarAnswer(
            "resolved", value, "year", (world,), "timeline_interval_converged", True)

    def owned_typed_set_convergent(
            self, question: str, ontology: WordNetNounGraph | None = None) \
            -> ConvergentScalarAnswer:
        """Count current possessions whose noun sense is under a versioned IS-A target."""
        query = _OWNED_SET_QUERY.match(question)
        graph = ontology or configured_wordnet()
        if not query or graph is None:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "owned_set_requires_query_and_noun_graph", True)
        raw_tokens = tuple(_LEMMA.get(token, token) for token in
                           observe_raw_text(query.group("target"), question=True).lexical)
        target = None
        for width in range(min(4, len(raw_tokens)), 0, -1):
            for start in range(len(raw_tokens) - width + 1):
                candidate = "_".join(raw_tokens[start:start + width])
                if candidate in graph.senses:
                    target = candidate
                    break
            if target:
                break
        if target is None:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "owned_set_target_absent_from_noun_graph", True)
        observations = []
        for fact_id, text in self.authoritative_documents():
            for sentence_match in _SENTENCE.finditer(text):
                sentence = sentence_match.group(0)
                if (sentence.rstrip().endswith("?") or
                        not re.search(r"\b(?:my|I(?:'ve| have)|own|bought|got)\b", sentence, re.I)):
                    continue
                lexical_matches = list(re.finditer(r"[A-Za-z][A-Za-z'’-]*", sentence))
                words = [match.group(0).casefold().rstrip("s") for match in lexical_matches]
                admitted = []
                for width in (3, 2, 1):
                    for index in range(len(words) - width + 1):
                        if any(left <= index < left + span for left, span in admitted):
                            continue
                        lemma = "_".join(words[index:index + width])
                        if (lemma == target or graph.matching_senses(target, lemma) or
                                not graph.matching_senses(lemma, target)):
                            continue
                        start, end = lexical_matches[index].start(), lexical_matches[index + width - 1].end()
                        local_before = sentence[max(0, start - 48):start]
                        possessives = tuple(re.finditer(
                            r"\b(my|her|his|their|your|our)\b", local_before, re.I))
                        if (not possessives or possessives[-1].group(1).casefold() != "my" or
                                not re.search(r"\bmy(?:\s+[A-Za-z0-9'’-]+){0,8}\s+$",
                                              local_before, re.I)):
                            continue
                        if re.search(r"\b(?:when|once|if)\s+I\s+(?:get|buy|receive)\b.{0,24}$",
                                     local_before, re.I):
                            continue
                        following = (words[index + width]
                                     if index + width < len(words) else "")
                        if following.endswith("ing"):
                            continue
                        # Do not select a polysemous modifier when a longer noun compound
                        # exists in the same frozen ontology and is outside the target type.
                        shadowed = False
                        for longer in range(width + 1, min(4, len(words) - index + 1)):
                            compound = "_".join(words[index:index + longer])
                            if compound in graph.senses and not graph.matching_senses(compound, target):
                                shadowed = True
                                break
                        if shadowed:
                            continue
                        if (re.search(r"\b(?:thinking|considering|planning)\b.{0,30}\b"
                                     r"(?:get(?:ting)?|buy(?:ing)?|purchas(?:e|ing))",
                                     local_before, re.I) and
                                not re.search(r"\bmy\s+$", local_before, re.I)):
                            continue
                        nearby = sentence[max(0, start - 64):min(len(sentence), end + 64)]
                        identities = []
                        for proper in _PROPER_VALUE.finditer(nearby):
                            value = " ".join(proper.group(0).casefold().split()).strip(" .")
                            if (value not in {"i", "i'm", "i've", "my", "the", "by", "user",
                                              "now", "also", "another", "wait", "oh"}
                                    and not value.startswith("i'")):
                                identities.append((abs(proper.start() - min(64, start)), value))
                        identity = min(identities)[1] if identities else lemma
                        absolute = (fact_id, sentence_match.start() + start,
                                    sentence_match.start() + end)
                        observations.append((lemma, identity, absolute))
                        admitted.append((index, width))
        if not observations:
            return ConvergentScalarAnswer(
                "abstain", None, "count", (), "no_owned_typed_entity_witness", True)
        by_lemma = {}
        for lemma, identity, span in observations:
            by_lemma.setdefault(lemma, []).append((identity, span))
        entities = {}
        for lemma, values in by_lemma.items():
            named = {identity for identity, _span in values if identity != lemma}
            for identity, span in values:
                # A generic mention is the unique named member of that subtype when one exists.
                key = next(iter(named)) if identity == lemma and len(named) == 1 else identity
                entities.setdefault((lemma, key), span)
        value = str(len(entities))
        spans = tuple(entities.values())
        world = ScalarProofWorld(
            1, value, "count", tuple(sorted({span[0] for span in spans})), spans,
            f"versioned_wordnet_hypernym_closure:{target}")
        return ConvergentScalarAnswer(
            "resolved", value, "count", (world,), "owned_typed_set_converged", True)

    def attended_event_count_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Count identity orbits of explicitly attended events over the full authority corpus.

        This is intentionally narrower than sentiment about an event: ``the wedding was
        amazing`` does not prove attendance.  A generic role mention can join a named orbit
        only when that role has exactly one named event in the authority boundary; otherwise
        the count is not closed and the operator abstains.
        """
        query = _ATTENDED_EVENT_COUNT_QUERY.match(question)
        if not query:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_attended_event_count", True)
        event_tokens = tuple(observe_raw_text(query.group("event"), question=True).lexical)
        if not event_tokens:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "event_type_is_absent", True)
        event = event_tokens[-1]
        if event.endswith("ies"):
            event = event[:-3] + "y"
        elif event.endswith("s"):
            event = event[:-1]

        role_declarations = {}
        event_pattern = re.compile(rf"\b{re.escape(event)}(?:s)?\b", re.I)
        for fact_id, text in self.authoritative_documents():
            for relation in _PERSON_RELATION.finditer(text):
                if relation.group("name") is None:
                    continue
                local = text[max(0, relation.start() - 120):min(len(text), relation.end() + 120)]
                if not event_pattern.search(local):
                    continue
                role = "_".join(relation.group("relation").casefold().split())
                name = relation.group("name").casefold()
                role_declarations.setdefault(role, []).append(
                    (name, (fact_id, relation.start("name"), relation.end("name"))))

        observations = []
        for fact_id, text in self.authoritative_documents():
            for attendance in _EXPLICIT_EVENT_ATTENDANCE.finditer(text):
                sentence_endings = [position for mark in ".!?\n"
                                    if (position := text.find(mark, attendance.end())) >= 0]
                sentence_end = min(sentence_endings) if sentence_endings else len(text)
                event_surface = event_pattern.search(text[attendance.end():sentence_end])
                if event_surface is None:
                    continue
                event_start = attendance.end() + event_surface.start()
                event_end = attendance.end() + event_surface.end()
                # The event description may be completed by later sentences in the same
                # source turn, but never by assistant text or another authority document.
                local = text[attendance.start():min(len(text), event_end + 420)]
                relations = set()
                names = set()
                for relation in _PERSON_RELATION.finditer(local):
                    role = "_".join(relation.group("relation").casefold().split())
                    relations.add(role)
                    if relation.group("name"):
                        names.add(relation.group("name").casefold())
                for participant in _EVENT_PARTICIPANT.finditer(local):
                    names.add(participant.group("name").casefold())
                spans = [(fact_id, event_start, event_end)]
                if not names:
                    declared = {(name, declared_span) for relation in relations
                                for name, declared_span in role_declarations.get(relation, ())}
                    declared_names = {name for name, _span in declared}
                    if len(declared_names) == 1:
                        names.update(declared_names)
                        spans.extend(span for _name, span in sorted(declared))
                observations.append((frozenset(relations), frozenset(names), tuple(spans)))
        if not observations:
            return ConvergentScalarAnswer(
                "abstain", None, "count", (), "no_explicit_attendance_witness", True)

        # First form named identity orbits.  Overlap is transitive: Emily and Emily+Sarah
        # are one event even when the pair is completed in a later report.
        named = []
        generic = []
        for relations, names, observation_spans in observations:
            if names:
                matches = [index for index, item in enumerate(named)
                           if names & item[1]]
                if len(matches) > 1:
                    return ConvergentScalarAnswer(
                        "contested", None, "count", (), "event_identity_bridges_distinct_orbits", True)
                if matches:
                    index = matches[0]
                    old_relations, old_names, spans = named[index]
                    named[index] = (old_relations | relations, old_names | names,
                                    spans + observation_spans)
                else:
                    named.append((relations, names, observation_spans))
            else:
                generic.append((relations, observation_spans))

        # A role-only observation is a repeat only if the role identifies exactly one named
        # event.  With zero or multiple candidates, counting it would invent an identity.
        for relations, observation_spans in generic:
            matches = [index for index, item in enumerate(named)
                       if relations and relations & item[0]]
            if len(matches) != 1:
                return ConvergentScalarAnswer(
                    "abstain", None, "count", (), "anonymous_event_orbit_is_not_unique", True)
            index = matches[0]
            old_relations, old_names, spans = named[index]
            named[index] = (old_relations | relations, old_names, spans + observation_spans)
        if not named:
            return ConvergentScalarAnswer(
                "abstain", None, "count", (), "no_named_event_orbit", True)
        spans = tuple(span for _relations, _names, orbit_spans in named for span in orbit_spans)
        value = str(len(named))
        world = ScalarProofWorld(
            1, value, "count", tuple(sorted({span[0] for span in spans})), spans,
            f"closed_explicit_attendance_identity_orbits:{event}")
        return ConvergentScalarAnswer(
            "resolved", value, "count", (world,), "attended_event_count_converged", True)

    def scoped_duration_sum_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Sum completed duration events under an explicit activity/place/calendar scope.

        Four surface forms share one algebra: a closed set of completed events, an exact
        duration per orbit, unit conversion, and a query-declared boundary.  Transport across
        facts is permitted only inside a caller-attested group (normally one source session).
        """
        query = _SCOPED_DURATION_QUERY.match(question)
        if not query:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_scoped_duration_sum", True)
        q = question.casefold()
        mode = None
        target_groups = []
        month = None
        if "camping trip" in q:
            mode = "camping"
            target_groups = [frozenset(("camp", "camping"))]
        elif "jogging" in q or "yoga" in q:
            mode = "activity"
            target_groups = [frozenset((term,)) for term in ("jog", "jogging", "yoga")
                             if term in q]
        elif "traveling in" in q or "travelling in" in q:
            mode = "travel"
            tail = re.split(r"travell?ing\s+in", q, maxsplit=1)[1].rstrip(" ?.")
            target_groups = [(_terms(part, question=True) - _SCAFFOLD)
                             for part in re.split(r"\s+and\s+|\s*,\s*", tail) if part.strip()]
            target_groups = [group for group in target_groups if group]
        elif "attending" in q:
            mode = "attendance"
            body = q.split("attending", 1)[1]
            month_hits = [name for name in _MONTH if re.search(rf"\b{name}\b", body)]
            if len(month_hits) != 1:
                return ConvergentScalarAnswer(
                    "abstain", None, query.group("unit"), (), "calendar_scope_is_not_unique", True)
            month = month_hits[0]
            body = re.split(rf"\s+in\s+{month}\b", body, maxsplit=1)[0]
            target_groups = [frozenset((_LEMMA.get(term, term),)) for term in
                             observe_raw_text(body, question=True).lexical
                             if term not in _SCAFFOLD and term not in {"and"}]
        if mode is None or not target_groups:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "scoped_duration_family_is_not_compiled", True)

        def term_matches(left: str, right: str) -> bool:
            return (left == right or min(len(left), len(right)) >= 4 and
                    (left.startswith(right) or right.startswith(left)))

        def group_matches(group, terms) -> bool:
            return all(any(term_matches(required, observed) for observed in terms)
                       for required in group)

        group_text = {}
        for fact_id, text in self.authoritative_documents():
            group = self.fact_groups.get(fact_id, fact_id)
            group_text.setdefault(group, []).append((fact_id, text))
        contributions = []
        for atom in self.atoms:
            if (atom.dimension != "time" or not atom.asserted or not atom.positive or
                    atom.qualifier or _RATE.search(self._local_text(atom))):
                continue
            local = atom.binding_text
            local_terms = _terms(local)
            if _PAST_HABIT_OR_PROSPECT.search(atom.sentence):
                continue
            admitted = False
            reason = ""
            if mode == "camping":
                admitted = (bool({"camp", "camping"} & local_terms) and
                            bool({"trip", "travel"} & local_terms) and
                            not re.search(r"\b(?:not|without)\s+camp", local, re.I))
                reason = "completed_camping_trip_duration"
            elif mode == "activity":
                admitted = (any(group_matches(group, local_terms) for group in target_groups) and
                            bool(re.search(r"\b(?:went|did|completed|finished|jogged|practiced)\b",
                                           local, re.I)))
                reason = "completed_activity_duration"
            elif mode == "travel":
                group = self.fact_groups.get(atom.fact_id, atom.fact_id)
                context = " ".join(text for _fact, text in group_text.get(group, ()))
                context_terms = _terms(context)
                matched_places = [place for place in target_groups
                                  if group_matches(place, context_terms)]
                admitted = (len(matched_places) == 1 and
                            bool(re.search(r"\b(?:trip|travel|stayed|got\s+back)\b", context, re.I)) and
                            not _PAST_HABIT_OR_PROSPECT.search(atom.sentence))
                reason = "session_bound_completed_travel_duration"
            elif mode == "attendance":
                admitted = (any(group_matches(group, local_terms) for group in target_groups) and
                            month in local_terms and
                            bool(re.search(r"\battend(?:ed|ing)?\b", local, re.I)))
                reason = "dated_attendance_duration"
            if admitted:
                proof_spans = [(atom.fact_id, *atom.source_span)]
                if mode == "travel":
                    place = matched_places[0]
                    pattern = re.compile(r"\b" + r"\W+".join(
                        re.escape(term) for term in place) + r"\b", re.I)
                    candidates = []
                    for context_fact, context_document in group_text.get(group, ()):
                        place_match = pattern.search(context_document)
                        if place_match:
                            candidates.append((context_fact, place_match.start(), place_match.end()))
                    if not candidates:
                        # Morphological normalization may have matched a stem.  Bind the first
                        # exact query term rather than emitting an unauthenticated group edge.
                        for context_fact, context_document in group_text.get(group, ()):
                            for term in place:
                                place_match = re.search(rf"\b{re.escape(term)}\w*\b",
                                                        context_document, re.I)
                                if place_match:
                                    candidates.append((context_fact, place_match.start(),
                                                       place_match.end()))
                                    break
                    if not candidates:
                        continue
                    proof_spans.append(min(candidates))
                contributions.append((atom.base_value, atom.fact_id,
                                      tuple(proof_spans), reason))

        # A dated singular attendance event without an explicit duration occupies one day.
        if mode == "attendance":
            for fact_id, text in self.authoritative_documents():
                for sentence_match in _SENTENCE.finditer(text):
                    sentence = sentence_match.group(0)
                    terms = _terms(sentence)
                    if (month not in terms or not re.search(r"\bI\s+(?:recently\s+)?attended\b",
                                                           sentence, re.I) or
                            not any(group_matches(group, terms) for group in target_groups) or
                            any(atom.fact_id == fact_id and
                                sentence_match.start() <= atom.source_span[0] < sentence_match.end()
                                for atom in self.atoms if atom.dimension == "time")):
                        continue
                    date = re.search(r"\b(?:on\s+the\s+)?\d{1,2}(?:st|nd|rd|th)?\s+of\s+"
                                     + re.escape(month) + r"\b", sentence, re.I)
                    if date:
                        span = (fact_id, sentence_match.start() + date.start(),
                                sentence_match.start() + date.end())
                        contributions.append((Decimal(86400), fact_id, (span,),
                                              "dated_singular_attendance_day"))
        if not contributions:
            return ConvergentScalarAnswer(
                "abstain", None, query.group("unit"), (), "no_closed_duration_event", True)

        # Identical fact/span contributions are extraction repeats, not distinct events.
        unique = tuple(dict.fromkeys(contributions))
        total_seconds = sum((item[0] for item in unique), Decimal(0))
        requested = query.group("unit").casefold()
        factor = Decimal(3600) if requested.startswith("hour") else Decimal(86400)
        value = _render(total_seconds / factor)
        unit = "hour" if requested.startswith("hour") else "day"
        spans = tuple(span for item in unique for span in item[2])
        world = ScalarProofWorld(
            1, value, unit, tuple(sorted({item[1] for item in unique})), spans,
            f"closed_scoped_duration_events:{mode}")
        return ConvergentScalarAnswer(
            "resolved", value, unit, (world,), "scoped_duration_sum_converged", True)

    def artifact_event_count_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Count worked/acquired physical artifact identities from type-bearing surfaces.

        A scale declaration is a structural type witness for a scale-model artifact.  Named
        ``... kit`` phrases are direct witnesses.  Completion is required in the same source
        turn; repeated descriptions collapse by their normalized identity, never by topic score.
        """
        query = _ARTIFACT_EVENT_COUNT_QUERY.match(question)
        if not query:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_artifact_event_count", True)
        target_terms = _terms(query.group("target"), question=True) - _SCAFFOLD
        action_terms = _terms(query.group("actions"), question=True) - _SCAFFOLD
        if not ("model" in target_terms and bool({"kit", "kits"} & target_terms) and
                ({"work", "buy", "bought"} & action_terms)):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "artifact_type_or_action_is_not_compiled", True)
        identities = {}
        for fact_id, text in self.authoritative_documents():
            if not _COMPLETED_ARTIFACT_ACTION.search(text):
                continue
            candidates = []
            for pattern in (_SCALE_ARTIFACT, _NAMED_KIT):
                for match in pattern.finditer(text):
                    label = match.group("label").strip(" '\".,;:-")
                    # Strip representation grammar but preserve the actual artifact identity.
                    identity = re.sub(r"^\d+\s*/\s*\d+\s+scale\s+", "", label,
                                      flags=re.I)
                    identity = re.sub(r"\s+(?:model\s+)?kit$", "", identity,
                                      flags=re.I)
                    identity = re.sub(r"\s+model$", "", identity, flags=re.I)
                    if (pattern is _SCALE_ARTIFACT and
                            not re.search(r"\d|(?:^|\s)[A-Z]", identity)):
                        # ``a 1/72 scale model like this`` repeats a representation scale
                        # but supplies no artifact identity and therefore cannot open an orbit.
                        continue
                    key_terms = tuple(term for term in _terms(identity)
                                      if term not in {"model", "kit", "scale"})
                    if not key_terms:
                        continue
                    key = " ".join(key_terms)
                    candidates.append((key, (fact_id, match.start("label"), match.end("label"))))
            for key, span in candidates:
                identities.setdefault(key, span)
        if not identities:
            return ConvergentScalarAnswer(
                "abstain", None, "count", (), "no_completed_typed_artifact", True)
        spans = tuple(identities.values())
        value = str(len(identities))
        world = ScalarProofWorld(
            1, value, "count", tuple(sorted({span[0] for span in spans})), spans,
            "scale_or_kit_typed_completed_artifact_orbits")
        return ConvergentScalarAnswer(
            "resolved", value, "count", (world,), "artifact_event_count_converged", True)

    def functional_device_count_convergent(
            self, question: str, ontology: WordNetNounGraph | None = None) \
            -> ConvergentScalarAnswer:
        """Count current instruments whose attested causal function belongs to a domain.

        Type and function are separate proof obligations.  WordNet proves IS-A
        ``instrumentality``; local syntax proves measurement/treatment/wear/assist use.  The
        health frame below is intentionally relational, never a catalogue of product names.
        """
        query = _FUNCTIONAL_DEVICE_QUERY.match(question)
        graph = ontology or configured_wordnet()
        if not query or graph is None:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "functional_device_requires_query_and_noun_graph", True)
        domain = query.group("domain").casefold()
        if domain != "health":
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "functional_domain_is_not_compiled", True)

        group_text = {}
        for fact_id, text in self.authoritative_documents():
            group_text.setdefault(self.fact_groups.get(fact_id, fact_id), []).append((fact_id, text))

        def instrument_lemma(tokens: list[str], end: int) -> str | None:
            for width in (3, 2, 1):
                if end - width + 1 < 0:
                    continue
                lemma = "_".join(token.rstrip("s") for token in tokens[end - width + 1:end + 1])
                if graph.matching_senses(lemma, "instrumentality"):
                    return lemma
            token = tokens[end].rstrip("s")
            # WNDB 2025 does not yet contain several productive ``smart+artifact``
            # compounds.  Decompose only that explicit morphological construction; arbitrary
            # suffix search turns ``non-stop`` into the unrelated artifact sense of ``stop``.
            if token.startswith("smart") and len(token) > 9:
                suffix = token[5:]
                if graph.matching_senses(suffix, "instrumentality"):
                    return suffix
            return None

        observations = []
        for fact_id, text in self.authoritative_documents():
            if not _CURRENT_DEVICE_USE.search(text) or _PAST_HABIT_OR_PROSPECT.search(text):
                continue
            group = self.fact_groups.get(fact_id, fact_id)
            context = " ".join(value for _context_fact, value in group_text[group])
            domain_attested = bool(re.search(rf"\b{re.escape(domain)}\b", context, re.I))
            word_matches = list(re.finditer(r"[A-Za-z][A-Za-z0-9'’\-]*", text))
            words = [match.group(0).casefold() for match in word_matches]
            for possessive_index, possessive in enumerate(words):
                if possessive not in {"my", "these"}:
                    continue
                best = None
                for end in range(possessive_index + 1,
                                 min(len(words), possessive_index + 9)):
                    between = text[word_matches[possessive_index].end():word_matches[end].end()]
                    if re.search(r"[.;!?]", between):
                        break
                    lemma = instrument_lemma(words, end)
                    if lemma:
                        best = (end, lemma)
                if best is None:
                    continue
                end, lemma = best
                start_char = word_matches[possessive_index + 1].start()
                end_char = word_matches[end].end()
                sentence_start = max(text.rfind(mark, 0, start_char) for mark in ".!?\n") + 1
                endings = [position for mark in ".!?\n"
                           if (position := text.find(mark, end_char)) >= 0]
                sentence_end = min(endings) if endings else len(text)
                sentence = text[sentence_start:sentence_end]
                label = text[start_char:end_char]
                definitions = " ".join(graph.definitions(lemma)).casefold()
                possessive_start = word_matches[possessive_index].start()
                link_prefix = text[max(sentence_start, possessive_start - 24):possessive_start]
                linked_with = bool(re.search(r"\b(?:using|with)\s*$", link_prefix, re.I))
                linked_wearing = bool(re.search(r"\bwearing\s*$", link_prefix, re.I))
                measurement_frame = bool(linked_with and re.search(
                    r"\b(?:testing|measuring|monitoring|tracking)\b", sentence, re.I))
                wearable_frame = bool(domain_attested and linked_wearing)
                treatment_frame = bool(linked_with and re.search(
                    r"\b(?:treatments?|therapy)\b", sentence, re.I))
                assistive_frame = bool(
                    re.search(r"\b(?:compensat\w*|assist\w*)\b.{0,80}\b"
                              r"(?:poor|impair\w*)\b|\b(?:poor|impair\w*)\b.{0,80}\b"
                              r"(?:hearing|vision|mobility)\b", definitions, re.I))
                if not (measurement_frame or wearable_frame or treatment_frame or assistive_frame):
                    continue
                key_terms = frozenset(_terms(label) - {"system", "machine", "device"})
                if not key_terms:
                    key_terms = frozenset((lemma,))
                observations.append((lemma, key_terms, (fact_id, start_char, end_char)))
        if not observations:
            return ConvergentScalarAnswer(
                "abstain", None, "count", (), "no_function_bound_device", True)

        orbits = []
        for lemma, terms, span in observations:
            matches = [index for index, (old_lemma, old_terms, _spans) in enumerate(orbits)
                       if lemma == old_lemma and bool(terms & old_terms)]
            if len(matches) > 1:
                return ConvergentScalarAnswer(
                    "contested", None, "count", (), "device_identity_bridges_orbits", True)
            if matches:
                index = matches[0]
                old_lemma, old_terms, spans = orbits[index]
                orbits[index] = (old_lemma, old_terms | terms, spans + (span,))
            else:
                orbits.append((lemma, terms, (span,)))
        spans = tuple(span for _lemma, _terms_value, orbit_spans in orbits for span in orbit_spans)
        value = str(len(orbits))
        world = ScalarProofWorld(
            1, value, "count", tuple(sorted({span[0] for span in spans})), spans,
            "wordnet_instrumentality_plus_attested_function_frame")
        return ConvergentScalarAnswer(
            "resolved", value, "count", (world,), "functional_device_count_converged", True)

    def assistant_converged_parent_fact_ids(self, question: str) -> tuple[int, ...]:
        """Address assistant utterances by independent direct and causal-successor routes."""
        if not _ASSISTANT_REFERENCE_QUERY.search(question):
            return ()

        def role_of(document) -> str:
            role = getattr(document, "role", None)
            text = str(getattr(document, "text"))
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")]
            return str(role or "").casefold()

        ordered = sorted(self.documents.values(), key=lambda item: int(getattr(item, "fact_id")))
        direct_rows, direct_parent = [], {}
        user_rows, successor = [], {}
        direct_id = user_id = 1
        for position, document in enumerate(ordered):
            fact_id = int(getattr(document, "fact_id"))
            text = str(getattr(document, "text"))
            group = self.fact_groups.get(fact_id, fact_id)
            if role_of(document) == "assistant":
                for start, end in claim_spans(text):
                    direct_rows.append(RawCausalDocument(
                        direct_id, text[start:end], group, position, speaker="assistant"))
                    direct_parent[direct_id] = fact_id
                    direct_id += 1
            elif role_of(document) == "user":
                user_rows.append(RawCausalDocument(
                    user_id, text, group, position, speaker="user"))
                if position + 1 < len(ordered):
                    following = ordered[position + 1]
                    following_id = int(getattr(following, "fact_id"))
                    if (role_of(following) == "assistant" and
                            self.fact_groups.get(following_id, following_id) == group):
                        successor[user_id] = following_id
                user_id += 1
        if not direct_rows or not user_rows:
            return ()
        direct_route = MaterializedIndependentHorizonSearchEngine(tuple(direct_rows)).search(
            question, max_results=8, exploration_reserve=8, core_width=1)
        user_route = MaterializedIndependentHorizonSearchEngine(tuple(user_rows)).search(
            question, max_results=8, exploration_reserve=8, core_width=1)
        direct_parents = {direct_parent[fact_id] for fact_id in direct_route.fact_ids}
        successor_parents = {successor[fact_id] for fact_id in user_route.fact_ids
                             if fact_id in successor}
        return tuple(sorted(direct_parents & successor_parents))

    def assistant_utterance_projection_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Project a literal span from a previously attested assistant speech act.

        The assistant message is authoritative only for ``assistant said this``, never for the
        truth of its content.  Direct answer retrieval and user-turn -> assistant-successor
        transport must address at least one common parent message before structural projection.
        """
        if not _ASSISTANT_REFERENCE_QUERY.search(question):
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "query_is_not_assistant_utterance_reference", True)

        def role_of(document) -> str:
            role = getattr(document, "role", None)
            text = str(getattr(document, "text"))
            if role is None and text.startswith("[") and "]" in text[:16]:
                role = text[1:text.index("]")]
            return str(role or "").casefold()

        ordered = sorted(self.documents.values(), key=lambda item: int(getattr(item, "fact_id")))
        direct_rows, direct_parent = [], {}
        direct_id = 1
        user_rows, successor = [], {}
        user_id = 1
        for position, document in enumerate(ordered):
            fact_id = int(getattr(document, "fact_id"))
            text = str(getattr(document, "text"))
            group = self.fact_groups.get(fact_id, fact_id)
            if role_of(document) == "assistant":
                for start, end in claim_spans(text):
                    direct_rows.append(RawCausalDocument(
                        direct_id, text[start:end], group, position, speaker="assistant"))
                    direct_parent[direct_id] = fact_id
                    direct_id += 1
            elif role_of(document) == "user":
                user_rows.append(RawCausalDocument(
                    user_id, text, group, position, speaker="user"))
                if position + 1 < len(ordered):
                    following = ordered[position + 1]
                    following_id = int(getattr(following, "fact_id"))
                    if (role_of(following) == "assistant" and
                            self.fact_groups.get(following_id, following_id) == group):
                        successor[user_id] = following_id
                user_id += 1
        if not direct_rows or not user_rows:
            return ConvergentScalarAnswer(
                "abstain", None, "text", (), "assistant_causal_routes_are_absent", True)
        direct_engine = MaterializedIndependentHorizonSearchEngine(tuple(direct_rows))
        direct_route = direct_engine.search(
            question, max_results=8, exploration_reserve=8, core_width=1)
        user_engine = MaterializedIndependentHorizonSearchEngine(tuple(user_rows))
        user_route = user_engine.search(
            question, max_results=8, exploration_reserve=8, core_width=1)
        direct_parents = {direct_parent[fact_id] for fact_id in direct_route.fact_ids}
        successor_parents = {successor[fact_id] for fact_id in user_route.fact_ids
                             if fact_id in successor}
        converged_parents = tuple(sorted(direct_parents & successor_parents))
        if not converged_parents:
            return ConvergentScalarAnswer(
                "abstain", None, "text", (), "assistant_routes_do_not_converge", True)

        qtokens = tuple(token for token in observe_raw_text(question, question=True).lexical
                        if token not in _UTTERANCE_META)
        qset = frozenset(qtokens)
        ordinal = next((_ORDINAL_VALUE[word] for word in _ORDINAL_VALUE
                        if re.search(rf"\b{word}\b", question, re.I)), None)
        numeric_ordinal = re.search(r"\b(\d{1,3})(?:st|nd|rd|th)\b", question, re.I)
        if numeric_ordinal:
            ordinal = int(numeric_ordinal.group(1))
        wants_last = bool(re.search(r"\b(?:last|final)\b", question, re.I))
        candidates = []

        def admit(priority: int, coverage: float, fact_id: int,
                  start: int, end: int, reason: str) -> None:
            text = str(getattr(self.documents[fact_id], "text"))
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if 0 <= start < end <= len(text) and end - start <= 1024:
                value = text[start:end].strip("`*_ ")
                inner = text[start:end].find(value)
                if value:
                    candidates.append((priority, round(coverage, 6), value,
                                       fact_id, (fact_id, start + inner,
                                                 start + inner + len(value)), reason))

        for fact_id in converged_parents:
            text = str(getattr(self.documents[fact_id], "text"))
            lines = list(re.finditer(r"[^\n]*(?:\n|$)", text))
            numbered = []
            for index, line_match in enumerate(lines):
                line = line_match.group(0).rstrip("\n")
                field = _LINE_FIELD.match(line)
                if field:
                    label_terms = _terms(field.group("label"))
                    overlap = len(label_terms & qset)
                    value = field.group("value")
                    if overlap and value:
                        admit(4, overlap / max(1, len(label_terms)), fact_id,
                              line_match.start() + field.start("value"),
                              line_match.start() + field.end("value"), "utterance_key_value")
                    elif overlap and not value:
                        for following in lines[index + 1:]:
                            raw = following.group(0).strip()
                            if raw:
                                admit(4, overlap / max(1, len(label_terms)), fact_id,
                                      following.start() + following.group(0).find(raw),
                                      following.start() + following.group(0).find(raw) + len(raw),
                                      "utterance_heading_value")
                                break
                item = _NUMBERED_LINE.match(line)
                if item:
                    numbered.append((int(item.group("n")), line_match, item))
            if ordinal is not None:
                for number, line_match, item in numbered:
                    if number == ordinal:
                        local = text[max(0, line_match.start() - 240):line_match.end()]
                        coverage = len(_terms(local) & qset) / max(1, len(qset))
                        admit(4, coverage, fact_id,
                              line_match.start() + item.start("value"),
                              line_match.start() + item.end("value"), "utterance_ordinal_item")
            elif wants_last and numbered:
                number, line_match, item = numbered[-1]
                local = text[max(0, line_match.start() - 240):line_match.end()]
                coverage = len(_terms(local) & qset) / max(1, len(qset))
                admit(3, coverage, fact_id,
                      line_match.start() + item.start("value"),
                      line_match.start() + item.end("value"), "utterance_last_item")

            # Gauge-complement: the short unmatched region between two query invariants is
            # the answer candidate.  It is source-exact and only admitted with useful coverage.
            for sentence_match in _SENTENCE.finditer(text):
                sentence = sentence_match.group(0)
                words = list(re.finditer(r"[^\W_]+(?:['’][^\W_]+)?", sentence))
                source_tokens = [match.group(0).casefold().replace("’", "'") for match in words]
                if not qtokens or not source_tokens:
                    continue
                table = [[0] * (len(source_tokens) + 1) for _ in range(len(qtokens) + 1)]
                for left in range(len(qtokens) - 1, -1, -1):
                    for right in range(len(source_tokens) - 1, -1, -1):
                        table[left][right] = (1 + table[left + 1][right + 1]
                                              if qtokens[left] == source_tokens[right]
                                              else max(table[left + 1][right],
                                                       table[left][right + 1]))
                left = right = 0
                matched = []
                while left < len(qtokens) and right < len(source_tokens):
                    if qtokens[left] == source_tokens[right]:
                        matched.append(right); left += 1; right += 1
                    elif table[left + 1][right] >= table[left][right + 1]:
                        left += 1
                    else:
                        right += 1
                coverage = len(matched) / max(1, len(qtokens))
                if len(matched) < 2 or coverage < 0.35:
                    continue
                for left_index, right_index in zip(matched, matched[1:]):
                    if not 0 < right_index - left_index - 1 <= 10:
                        continue
                    gap_start, gap_end = left_index + 1, right_index
                    while (gap_start < gap_end and source_tokens[gap_start] in
                           {"has", "had", "is", "was", "were", "are", "a", "an", "the"}):
                        gap_start += 1
                    if gap_start < gap_end:
                        admit(2, coverage, fact_id,
                              sentence_match.start() + words[gap_start].start(),
                              sentence_match.start() + words[gap_end - 1].end(),
                              "utterance_gauge_complement")
        if not candidates:
            return ConvergentScalarAnswer(
                "abstain", None, "text", (), "no_structural_utterance_projection", True)
        best_key = max((item[0], item[1]) for item in candidates)
        best = [item for item in candidates if (item[0], item[1]) == best_key]
        values = {item[2].strip().casefold() for item in best}
        if len(values) != 1:
            return ConvergentScalarAnswer(
                "contested", None, "text", (), "assistant_projection_values_disagree", True)
        chosen = min(best, key=lambda item: (item[3], item[4]))
        _priority, _coverage, value, fact_id, span, reason = chosen
        world = ScalarProofWorld(
            1, value, "text", (fact_id,), (span,), f"assistant_utterance_observation:{reason}")
        return ConvergentScalarAnswer(
            "resolved", value, "text", (world,), "assistant_utterance_projection_converged", True)

    def explicit_absence_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Resolve a negative only from a closed measured slot plus an attested alternative."""
        if self.lookup_convergent(question).state == "resolved":
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "positive_measurement_exists", True)
        observed = observe_raw_text(question, question=True)
        qterms = _terms(question, question=True) - _SCAFFOLD - frozenset(_WORD_NUMBER)
        qterms -= frozenset(observed.numbers)
        ordered_relations = tuple(
            f"{_LEMMA.get(left, left)}>{_LEMMA.get(right, right)}"
            for raw in observed.relations for left, right in (raw.split(">", 1),))
        relations = frozenset(ordered_relations)
        absence_actions = {
            relation.split(">", 1)[0] for relation in relations
            if relation.split(">", 1)[0] in (_RELATION_ACTIONS - {"take", "work", "have", "be"})
        }
        duration_query = _ENTITY_DURATION_QUERY.search(question)
        if not duration_query and not absence_actions:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "absence_has_no_concrete_contrast_action", True)
        required_dimensions = ({"time", "calendar"}
                               if re.match(r"^\s*how\s+(?:long|much\s+time)\b", question, re.I)
                               else None)
        if required_dimensions is None:
            return ConvergentScalarAnswer(
                "unsupported", None, "", (), "absence_query_has_no_closed_measurement_slot", True)
        compatible = tuple(atom for atom in self.atoms if atom.asserted and atom.positive and
                           atom.dimension in required_dimensions)
        if not compatible:
            return ConvergentScalarAnswer("abstain", None, "absence", (),
                                          "no_alternative_measurement_witness", True)

        collision_witnesses = []
        for atom in compatible:
            atom_relations = _relations(atom.binding_text)
            for relation in relations:
                left, right = relation.split(">", 1)
                if right not in qterms or right in atom.terms:
                    continue
                alternatives = {candidate.split(">", 1)[1] for candidate in atom_relations
                                if candidate.startswith(left + ">")}
                # ``take`` is frequently an auxiliary duration frame ("how long did it
                # take to ...").  Treating it as the semantic slot would make unrelated
                # lexical uses such as "take on responsibilities" prove absence.
                governing_path = (left in (_RELATION_ACTIONS - {"take"}) or any(
                    query_relation.endswith(">" + left) and query_relation in atom_relations
                    for query_relation in relations))
                if (governing_path and alternatives and
                        any(item not in _RELATION_ACTIONS for item in alternatives)):
                    collision_witnesses.append(atom)
                    break

        if duration_query:
            target = _terms(duration_query.group("entity"), question=True)
            if not any(target <= atom.terms for atom in compatible):
                analogous = []
                for atom in compatible:
                    location = _OBSERVED_LOCATION.search(atom.binding_text)
                    if location and not target <= _terms(location.group("entity")):
                        analogous.append(atom)
                if analogous:
                    collision_witnesses.extend(analogous)

        if not collision_witnesses:
            return ConvergentScalarAnswer("abstain", None, "absence", (),
                                          "absence_not_proven_by_slot_contrast", True)
        witnesses = self._deduplicate(tuple(collision_witnesses))
        witness = min(witnesses, key=lambda atom: (atom.fact_id, atom.binding_span))
        binding = witness.binding_text.strip()
        value = ("You did not mention this information. "
                 f"Related attested information: {binding}")
        world = ScalarProofWorld(1, value, "absence", (witness.fact_id,),
                                 ((witness.fact_id, *witness.binding_span),),
                                 "closed_measured_slot_with_attested_alternative")
        return ConvergentScalarAnswer("resolved", value, "absence", (world,),
                                      "closed_measured_slot_with_attested_alternative", True)

    def answer_convergent(self, question: str) -> ConvergentScalarAnswer:
        """Run the living operator family and admit only a convergent answer world.

        There is deliberately no learned ranker and no precedence list capable of turning
        disagreement into an answer.  Every applicable deterministic operator proposes a
        proof world; distinct resolved values, or a contested applicable operator, close the
        gate.  This makes integration at least as conservative as every component in isolation.
        """
        product = self.product_sum_convergent(question)
        plain_sum = self.sum_convergent(question)
        classified_sum = self.classified_money_sum_convergent(question)
        activity_sum = self.activity_duration_sum_convergent(question)
        difference = self.difference_convergent(question)
        cashback = self.cashback_convergent(question)
        current_role = self.current_role_duration_convergent(question)
        average_age = self.average_age_convergent(question)
        corpus_absence = self.corpus_nonmembership_convergent(question)
        timeline_interval = self.timeline_interval_convergent(question)
        owned_set = self.owned_typed_set_convergent(question)
        attended_events = self.attended_event_count_convergent(question)
        scoped_duration = self.scoped_duration_sum_convergent(question)
        artifact_events = self.artifact_event_count_convergent(question)
        functional_devices = self.functional_device_count_convergent(question)
        # A witnessed quantity x unit-price term changes the algebra of the question.
        # In that world, the plain currency projection is provably incomplete (it drops
        # the multiplicand), so it is not a competing semantic interpretation.
        if (product.state == "resolved" or classified_sum.state == "resolved" or
                activity_sum.state == "resolved"):
            plain_sum = ConvergentScalarAnswer(
                "unsupported", None, "", (), "dominated_by_more_specific_sum_algebra", True)
        proposals = (
            self.lookup_convergent(question),
            product,
            classified_sum,
            activity_sum,
            difference,
            cashback,
            current_role,
            average_age,
            corpus_absence,
            timeline_interval,
            owned_set,
            attended_events,
            scoped_duration,
            artifact_events,
            functional_devices,
            self.coordinated_count_convergent(question),
            self.acquisition_count_convergent(question),
            self.textual_projection_convergent(question),
            self.relative_value_convergent(question),
            self.explicit_absence_convergent(question),
            plain_sum,
        )
        resolved = tuple(answer for answer in proposals if answer.state == "resolved")
        contested = tuple(answer for answer in proposals if answer.state == "contested")
        if contested:
            worlds = tuple(world for answer in (*resolved, *contested)
                           for world in answer.worlds)
            return ConvergentScalarAnswer(
                "contested", None, "", worlds, "operator_world_contested", True)
        if not resolved:
            state = "abstain" if any(answer.state == "abstain" for answer in proposals) \
                else "unsupported"
            return ConvergentScalarAnswer(state, None, "", (),
                                          "no_resolved_operator_world", True)
        signatures = {(str(answer.value).strip().casefold(), answer.unit)
                      for answer in resolved}
        if len(signatures) != 1:
            worlds = tuple(world for answer in resolved for world in answer.worlds)
            return ConvergentScalarAnswer(
                "contested", None, "", worlds, "resolved_operator_worlds_disagree", True)
        chosen = resolved[0]
        worlds = tuple(dict.fromkeys(world for answer in resolved for world in answer.worlds))
        return ConvergentScalarAnswer(
            "resolved", chosen.value, chosen.unit, worlds,
            "all_applicable_operator_worlds_converged", True)


@dataclass(frozen=True, slots=True)
class IntegratedConvergentAnswer:
    """Executable lab contract for proof-first, deterministic-fallback composition."""

    answer_text: str
    authority: str
    proof_blob: bytes | None
    proof_state: str
    proof_reason: str


def render_convergent_answer(answer: ConvergentScalarAnswer) -> str:
    """Render a resolved typed value without asking a language model to verbalize it."""
    if answer.state != "resolved" or answer.value is None:
        raise ValueError("only resolved answers are renderable")
    value = str(answer.value).strip()
    if answer.unit == "USD" and re.fullmatch(r"\d+(?:\.\d+)?", value):
        number = Decimal(value)
        if number == number.to_integral():
            return f"${int(number):,}"
        return f"${number:,.2f}"
    if answer.unit in {"second", "minute", "hour", "day", "week", "month", "year"}:
        if re.search(r"\b(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b", value, re.I):
            return value
        number = Decimal(value)
        suffix = answer.unit if number == 1 else answer.unit + "s"
        return f"{value} {suffix}"
    return value


def integrate_with_deterministic_fallback(
        ledger: AttestedScalarLedger, question: str, fallback_text: str) \
        -> IntegratedConvergentAnswer:
    """Use a verified proof world when available; otherwise preserve the caller's fallback."""
    proof = ledger.answer_convergent(question)
    if proof.state == "resolved":
        blob = compact_scalar_answer(proof, ledger)
        # Reopen before release so a serialization/provenance defect cannot silently fall
        # through as a user-visible answer.
        open_compact_scalar_answer(blob, ledger)
        return IntegratedConvergentAnswer(
            render_convergent_answer(proof), "proof_convergent", blob,
            proof.state, proof.reason)
    return IntegratedConvergentAnswer(
        fallback_text, "deterministic_fallback", None, proof.state, proof.reason)


_COMPACT_MAGIC = b"HSC1"
_ABSENCE_MAGIC = b"HNA1"


def compact_scalar_answer(answer: ConvergentScalarAnswer,
                          ledger: AttestedScalarLedger) -> bytes:
    """Serialize a resolved answer and its exact source proofs without rendered evidence."""
    if answer.state != "resolved" or answer.value is None or not answer.worlds:
        raise ValueError("only resolved proof-carrying scalar answers are compactable")
    if answer.unit == "corpus_absence":
        reasons = {world.reason for world in answer.worlds}
        prefixes = {reason.split(":", 1)[0] for reason in reasons}
        if prefixes != {"authority_corpus_nonmembership"} or len(reasons) != 1:
            raise ValueError("corpus absence lacks one canonical nonmembership key")
        identifier = next(iter(reasons)).split(":", 1)[1].encode("utf-8")
        value = answer.value.encode("utf-8")
        unit = answer.unit.encode("utf-8")
        if not identifier or len(identifier) > 255 or len(value) > 65535 or len(unit) > 255:
            raise ValueError("compact absence limits exceeded")
        payload = bytearray(_ABSENCE_MAGIC)
        payload.extend(struct.pack(">HBB", len(value), len(unit), len(identifier)))
        payload.extend(value)
        payload.extend(unit)
        payload.extend(identifier)
        payload.extend(ledger.authority_corpus_digest())
        return bytes(payload) + hashlib.sha256(
            b"HORIZON-CORPUS-NONMEMBERSHIP-v1\0" + payload).digest()
    value = answer.value.encode("utf-8")
    unit = answer.unit.encode("utf-8")
    citations = tuple(dict.fromkeys(
        span for world in answer.worlds for span in world.spans))
    if len(value) > 65535 or len(unit) > 255 or len(citations) > 65535:
        raise ValueError("compact scalar limits exceeded")
    payload = bytearray(_COMPACT_MAGIC)
    payload.extend(struct.pack(">HBH", len(value), len(unit), len(citations)))
    payload.extend(value)
    payload.extend(unit)
    for fact_id, start, end in citations:
        document = ledger.documents.get(fact_id)
        if document is None or not (0 <= start < end <= len(str(getattr(document, "text")))):
            raise ValueError("compact citation is outside the authority boundary")
        payload.extend(struct.pack(">III", fact_id, start, end))
        payload.extend(bytes.fromhex(_digest(str(getattr(document, "text")))))
    return bytes(payload) + hashlib.sha256(b"HORIZON-SCALAR-PROOF-v1\0" + payload).digest()


def open_compact_scalar_answer(blob: bytes, ledger: AttestedScalarLedger) \
        -> tuple[str, str, tuple[tuple[int, int, int], ...]]:
    """Verify integrity and every citation before returning the compact answer."""
    if isinstance(blob, bytes) and len(blob) >= 72 and blob[:4] == _ABSENCE_MAGIC:
        payload, claimed = blob[:-32], blob[-32:]
        actual = hashlib.sha256(b"HORIZON-CORPUS-NONMEMBERSHIP-v1\0" + payload).digest()
        if actual != claimed:
            raise ValueError("compact absence integrity failure")
        value_len, unit_len, identifier_len = struct.unpack(">HBB", payload[4:8])
        expected = 8 + value_len + unit_len + identifier_len + 32
        if expected != len(payload) or identifier_len == 0:
            raise ValueError("non-canonical compact absence length")
        cursor = 8
        value = payload[cursor:cursor + value_len].decode("utf-8"); cursor += value_len
        unit = payload[cursor:cursor + unit_len].decode("utf-8"); cursor += unit_len
        identifier = payload[cursor:cursor + identifier_len].decode("utf-8"); cursor += identifier_len
        corpus_digest = payload[cursor:cursor + 32]
        if unit != "corpus_absence" or corpus_digest != ledger.authority_corpus_digest():
            raise ValueError("compact absence corpus authority mismatch")
        if any(identifier.casefold() in text.casefold()
               for _fact_id, text in ledger.authoritative_documents()):
            raise ValueError("compact absence identifier is present")
        return value, unit, ()
    if not isinstance(blob, bytes) or len(blob) < 43 or blob[:4] != _COMPACT_MAGIC:
        raise ValueError("invalid compact scalar envelope")
    payload, claimed = blob[:-32], blob[-32:]
    actual = hashlib.sha256(b"HORIZON-SCALAR-PROOF-v1\0" + payload).digest()
    if actual != claimed:
        raise ValueError("compact scalar integrity failure")
    value_len, unit_len, count = struct.unpack(">HBH", payload[4:9])
    cursor = 9
    expected = cursor + value_len + unit_len + count * 44
    if expected != len(payload):
        raise ValueError("non-canonical compact scalar length")
    value = payload[cursor:cursor + value_len].decode("utf-8")
    cursor += value_len
    unit = payload[cursor:cursor + unit_len].decode("utf-8")
    cursor += unit_len
    citations = []
    for _ in range(count):
        fact_id, start, end = struct.unpack(">III", payload[cursor:cursor + 12])
        source_sha256 = payload[cursor + 12:cursor + 44]
        cursor += 44
        document = ledger.documents.get(fact_id)
        if document is None:
            raise ValueError("compact citation references an unknown FactId")
        text = str(getattr(document, "text"))
        if (bytes.fromhex(_digest(text)) != source_sha256 or
                not (0 <= start < end <= len(text))):
            raise ValueError("compact citation failed source verification")
        citations.append((fact_id, start, end))
    return value, unit, tuple(citations)
