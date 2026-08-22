# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Promoted PT relation pack: H-PLT/H-DEM/H-DCA coalition for surface role resolution.

This module recognizes a deliberately finite one-hole question grammar ("Quem ou o que X Y?" /
"O que X Y?") and extracts one-token ARG1 (subject)/ARG2 (object) mentions from exact source
spans, via a genuine typed constraint-satisfaction resolution rather than a fixed positional
before/after split:

  - H-FMRL (`finite_morphology_lattice.py`) supplies typed per-token readings.
  - H-DEM/H-DCA (`hdem_hdca_kernel.py`) turn the two real structural facts this problem needs --
    PP-governance and clause-local candidate competition -- into an explicit `HDEMProblem`,
    instead of leaving them as procedural side effects.
  - H-PLT's `execute_proof_lattice_attention` (`proof_lattice_attention.py`) runs the actual
    resolution: every surviving candidate is a guarded `AuthorizedFact`, and Horizon only answers
    when every complete world agrees -- a genuine tie surfaces as `contested`, never a guess.

Locality -- each clause gets its own small CSP, scoped by `clause_bounds`, never a whole-sentence
brute-force scan -- is what lets this handle arbitrarily long sentences without a raw token-count
cutoff. It is a textual relation reader, not a factual writer: interrogative, conditional, modal
and negated clauses retain that force and must not be promoted to asserted memory without a
stronger authority. No model, embedding, network or external parser runs.

Promoted from `lab/surface_role_lattice_bridge.py` after a 196-scenario informal/slang battery
(84.69%->98.98%) and a formal-register development corpus (CINTIL-dev, 428/456 = 93.86% with the
optional PortiLexicon-UD lexicon wired in). Not yet confirmed against a genuinely fresh,
never-touched holdout the way the English pack was (see `english_atomic_relations.py`'s own GUM
holdout) -- that confirmation remains open, tracked separately from this promotion.

An optional real PortiLexicon-UD lexicon recovers a small number of irregular/ambiguous verb forms
the closed-class rules below cannot reach ("compro", "garanto") -- entirely opt-in via the
`lexicon` parameter; omitting it preserves every result computed without it. The compact lexicon
artifact itself is not shipped in this package (it is a derived, rebuildable index over a
third-party MIT-licensed resource, not project code): build it locally with
`horizon_memory.portilexicon_compact.build_compact_portilexicon` from the official POS-split TSVs
at `github.com/LuceleneL/PortiLexicon-UD` (concatenate `ADJ.tsv`, `ADP.tsv`, `ADV.tsv`, `AUX.tsv`,
`CCONJ.tsv`, `DET.tsv`, `INTJ.tsv`, `NOUN.tsv`, `NUM.tsv`, `PRON.tsv`, `SCONJ.tsv`, `VERB.tsv`, each
row tagged with its own POS in a 4th column, sorted by casefolded surface form) then pass the
resulting `.hfm` path to `CompactPortiLexicon`.
"""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import re

from .hdem_hdca_kernel import (
    HDCAResult, HDEMConstraint, HDEMProblem, HDEMValue, HDEMVariable, HDEMResult, solve_hdca,
    solve_hdem_enumerative, solve_hdem_packed,
)
from .finite_morphology_lattice import (
    FiniteMorphologySpec, MorphClass, MorphologyLattice, SuffixRule, compile_finite_morphology,
)
from .portilexicon_compact import CompactPortiLexicon
from .proof_lattice_attention import (
    HPLTGuardedFact, HPLTResult, execute_proof_lattice_attention,
)
from .sigma_pba import AuthorizedFact, ConjunctiveProgram, RelationalGoal, SealedSource
from .surface_atomic_kernel import SurfaceKernelToken
from .english_atomic_relations import BinaryQueryDemand


# --- Base PT closed-class morphology + tokenizer + question grammar -------------------------
# Reused, trimmed subset of `lab/portuguese_atomic_relations.py`'s own base pack: only the
# morphology data, tokenizer and question-compiling regexes survive here -- that file's own
# ~9 hand-rolled, order-dependent boolean-flag candidate-selection cascade (everything from
# `PortugueseAtomicRelationCompiler.read` onward) is exactly what the H-FMRL/H-DEM/H-PLT
# coalition below replaces, and is not reused.

_PT_ABBREVIATION = r"(?:sr|sra|dr|dra|prof)\."
_PT_SOCIAL_ID = r"(?:[#@][A-Za-z0-9_]+)"
_PT_CLITIC_ATOM = r"(?:-(?:a|as|lhe|lhes|me|nos|o|os|se|te))"
_PT_URL = r"(?:https?://\S+)"
_PT_ATOM = rf"(?:{_PT_SOCIAL_ID}|{_PT_CLITIC_ATOM}|{_PT_ABBREVIATION}|[^\W_]+(?:[’'-][^\W_]+)*)"
_PT_TOKEN = re.compile(rf"(?:{_PT_URL}|{_PT_ATOM}|[^\w\s])", re.UNICODE | re.I)
_PT_ENCLITIC = re.compile(r"^(.+)(-(?:a|as|lhe|lhes|me|nos|o|os|se|te))$", re.I)
_PT_SUBJECT_QUERY = re.compile(
    rf"^\s*Quem\s+ou\s+o\s+que\s+(?P<predicate>{_PT_ATOM})\s+(?P<known>{_PT_ATOM})\s*\?\s*$",
    re.I)
_PT_OBJECT_QUERY = re.compile(
    rf"^\s*O\s+que\s+(?P<known>{_PT_ATOM})\s+(?P<predicate>{_PT_ATOM})\s*\?\s*$", re.I)

_PT_ADPOSITIONS = frozenset({
    "a", "ante", "após", "até", "com", "contra", "da", "das", "de", "desde", "do", "dos",
    "durante", "em", "entre", "na", "nas", "no", "nos", "num", "numa", "nuns", "numas",
    "para", "pela", "pelas", "pelo", "pelos", "perante", "por", "sem", "sob", "sobre",
    "trás", "à", "às",
})
_PT_COORDINATORS = frozenset({"e", "mas", "nem", "ou", "porém", "quando", "que", "se"})
_PT_DETERMINERS = frozenset({
    "a", "algum", "alguma", "alguns", "algumas", "aquele", "aquela", "aqueles", "aquelas",
    "as", "cada", "certa", "certas", "certo", "certos", "essa", "essas", "esse", "esses",
    "esta", "estas", "este", "estes", "minha", "minhas", "meu", "meus", "muita", "muitas",
    "muito", "muitos", "nossa", "nossas", "nosso", "nossos", "o", "os", "outra", "outras",
    "outro", "outros", "mesma", "mesmas", "mesmo", "mesmos", "pouca", "poucas", "pouco", "poucos", "quais", "qual", "quanta",
    "quantas", "quanto", "quantos", "seu", "seus", "sua", "suas", "toda", "todas", "todo",
    "todos", "um", "uma", "umas", "uns",
})
_PT_CLITICS = frozenset({
    "a", "as", "lhe", "lhes", "me", "nos", "o", "os", "se", "te",
    "-a", "-as", "-lhe", "-lhes", "-me", "-nos", "-o", "-os", "-se", "-te",
})
_PT_RELATIVES = frozenset({"cuja", "cujas", "cujo", "cujos", "onde", "que", "quem", "qual", "quais"})
_PT_ADVERBS = frozenset({
    "ainda", "agora", "aí", "ali", "aqui", "assim", "bem", "como", "depois", "então",
    "hoje", "já", "jamais", "lá", "mais", "menos", "muito", "não", "nunca", "ontem",
    "quando", "sempre", "só", "também", "tanto", "talvez",
})
_PT_AUXILIARIES = frozenset({
    "deve", "devem", "deverá", "deverão", "deveria", "deveriam", "era", "eram", "és", "está",
    "estão", "estava", "estavam", "estará", "estarão", "estaria", "estariam", "foi", "fomos",
    "foram", "fui", "há", "havia", "haviam", "irá", "irão", "iria", "iriam", "pode", "podem",
    "poderá", "poderão", "poderia", "poderiam", "será", "serão", "seria", "seriam", "sido", "sou",
    "somos", "são", "tem", "têm", "terá", "terão", "teremos", "teria", "teriam", "tinha",
    "tinham", "vai", "vão", "é",
})


def _pt_morphology_spec() -> FiniteMorphologySpec:
    by_form: dict[str, set[MorphClass]] = {}
    for forms, morph_class in (
        (_PT_ADPOSITIONS, MorphClass.ADPOSITION),
        (_PT_COORDINATORS, MorphClass.COORDINATOR),
        (_PT_DETERMINERS, MorphClass.DET),
        (_PT_CLITICS, MorphClass.CLITIC),
        (_PT_RELATIVES, MorphClass.REL),
        (_PT_ADVERBS, MorphClass.ADV),
        (_PT_AUXILIARIES, MorphClass.AUX),
    ):
        for form in forms:
            by_form.setdefault(form, set()).add(morph_class)
    for form in {"um", "uma", "uns", "umas"}:
        by_form[form].add(MorphClass.NOMINAL)
    for form in {"aquele", "aquela", "aqueles", "aquelas", "essa", "essas", "esse", "esses",
                 "esta", "estas", "este", "estes", "aquilo", "isso", "isto"}:
        by_form.setdefault(form, set()).add(MorphClass.NOMINAL)
    contractions = {
        "ao": ("a", "o"), "aos": ("a", "os"), "da": ("de", "a"),
        "das": ("de", "as"), "do": ("de", "o"), "dos": ("de", "os"),
        "na": ("em", "a"), "nas": ("em", "as"), "no": ("em", "o"),
        "nos": ("em", "os"), "num": ("em", "um"), "numa": ("em", "uma"),
        "nuns": ("em", "uns"), "numas": ("em", "umas"), "pela": ("por", "a"),
        "pelas": ("por", "as"), "pelo": ("por", "o"), "pelos": ("por", "os"),
        "daquele": ("de", "aquele"), "daquela": ("de", "aquela"),
        "daqueles": ("de", "aqueles"), "daquelas": ("de", "aquelas"),
        "desse": ("de", "esse"), "dessa": ("de", "essa"),
        "desses": ("de", "esses"), "dessas": ("de", "essas"),
        "deste": ("de", "este"), "desta": ("de", "esta"),
        "destes": ("de", "estes"), "destas": ("de", "estas"),
        "à": ("a", "a"), "às": ("a", "as"),
    }
    return FiniteMorphologySpec(
        language="pt",
        exact=tuple((form, tuple(sorted(classes, key=lambda item: item.value)))
                    for form, classes in sorted(by_form.items())),
        suffixes=(SuffixRule("mente", MorphClass.ADV),),
        contractions=tuple(sorted(contractions.items())),
    )


_PT_MORPHOLOGY = _pt_morphology_spec()


def compile_query(question: str) -> BinaryQueryDemand | None:
    if match := _PT_SUBJECT_QUERY.fullmatch(question):
        return BinaryQueryDemand(
            match.group("predicate").casefold(), "ARG1", "what", "ARG2",
            match.group("known").casefold())
    if match := _PT_OBJECT_QUERY.fullmatch(question):
        return BinaryQueryDemand(
            match.group("predicate").casefold(), "ARG2", "what", "ARG1",
            match.group("known").casefold())
    return None


def _tokens(source: str) -> tuple[SurfaceKernelToken, ...]:
    pieces = []
    for match in _PT_TOKEN.finditer(source):
        if split := _PT_ENCLITIC.fullmatch(match.group()):
            boundary = match.start() + len(split.group(1))
            pieces.append((split.group(1), (match.start(), boundary)))
            pieces.append((split.group(2), (boundary, match.end())))
        else:
            pieces.append((match.group(), match.span()))
    return tuple(SurfaceKernelToken(index, surface, span)
                 for index, (surface, span) in enumerate(pieces))


# --- H-PLT/H-DEM/H-DCA coalition for single-clause surface role resolution -------------------


_CLAUSE_BOUNDARIES = frozenset({".", "?", "!", ";", ":", "-"})
# NOMINAL/CONTENT/UNKNOWN are the H-FMRL classes a genuine argument head can carry. A pure
# function-word class (DET/ADPOSITION/ADV/AUX/COORDINATOR/...) never is one on its own.
_ARGUMENT_CLASSES = frozenset({MorphClass.NOMINAL, MorphClass.CONTENT, MorphClass.UNKNOWN})
_RULE_ID = "surface_role_lattice_bridge_v1"

# `_PT_MORPHOLOGY`'s own closed AUX list is small (a dozen or so high-frequency forms), so most
# ordinary conjugated verbs ("devemos", "pretendem", "aceitar") fall through its "unknown" branch
# and get tagged CONTENT/UNKNOWN -- exactly like a real noun -- becoming spurious argument
# candidates. This supplementary spec adds general, regular Portuguese conjugation endings (not a
# per-verb list) tagged MorphClass.PREDICATE, so a matching token is excluded from
# `_ARGUMENT_CLASSES` outright instead of masquerading as a nominal. Endings were screened one at a
# time against real CINTIL-dev text before being kept: "-am"/"-em" (3rd-plural present) were
# dropped after measuring a net regression -- they collide with common nouns ("ordem", "origem",
# "imagem", "viagem") too often to be worth it. This is a new, separate spec; `_PT_MORPHOLOGY`
# itself (the frozen, already-measured pack) is never modified. Measured net gain on real
# CINTIL-dev text: 79.17% -> 79.61% (361/456 -> 363/456), zero new regressions.
_PT_VERB_SUFFIXES = (
    SuffixRule("aram", MorphClass.PREDICATE), SuffixRule("eram", MorphClass.PREDICATE),
    SuffixRule("iram", MorphClass.PREDICATE), SuffixRule("avam", MorphClass.PREDICATE),
    SuffixRule("emos", MorphClass.PREDICATE), SuffixRule("amos", MorphClass.PREDICATE),
    SuffixRule("ariam", MorphClass.PREDICATE), SuffixRule("essem", MorphClass.PREDICATE),
    SuffixRule("assem", MorphClass.PREDICATE), SuffixRule("arem", MorphClass.PREDICATE),
    SuffixRule("erem", MorphClass.PREDICATE), SuffixRule("irem", MorphClass.PREDICATE),
    SuffixRule("ou", MorphClass.PREDICATE),
    # "eu" was tried and dropped: real nouns collide ("museu", "hebreu", "europeu"), and the
    # valence-slot assignment (unlike the earlier positional-only mechanism) treats every
    # PREDICATE-tagged token as a competing verb, so a false positive here doesn't just mislabel
    # one word -- it steals a slot from the real predicate's own true object (found directly on
    # "O motorista do estadio visitou o jardim do museu.", where a falsely-tagged "museu" absorbed
    # the object query that belonged to "visitou").
    #
    # "iu" (3rd-singular preterite of regular -ir verbs: sumiu/caiu/pediu/decidiu/conseguiu...) was
    # screened the same way and kept: zero non-VERB tokens end in "iu" anywhere in the full
    # CINTIL-dev corpus (unlike "eu"'s real noun collisions). Found on a real Gen-Z chat sentence
    # ("ele comeu 4 pedacos e sumiu?") where an uncoordinated second verb sharing the first verb's
    # own subject, with no PREDICATE tag of its own, wrongly stayed in the object candidate pool
    # and tied with the true object ("pedacos"), forcing an honest but avoidable `contested` state.
    SuffixRule("iu", MorphClass.PREDICATE),
)
# `_PT_DETERMINERS` (the frozen base pack) already treats several quantifiers ("muita"/"muitos",
# "pouca"/"poucos", "toda"/"todos", "cada", ...) as DET-class, so the existing "attributive"
# exclusion rule (a DET followed by another eligible candidate is excluded, favoring the head
# noun) already handles them. This closed-class extension adds the remaining standard PT
# quantifiers/indefinites the base pack does not cover ("nenhum(a)(s)", "ambos"/"ambas",
# "vário(a)(s)", "qualquer"/"quaisquer") plus the intensifier "próprio(a)(s)" ("os seus próprios
# programas" -- an emphatic adjective, not a real argument head, that otherwise stole an object
# slot from the noun it modifies before the noun was even reached). All are genuinely closed PT
# function-word classes, the same kind already present in `_PT_DETERMINERS`/`_PT_AUXILIARIES` --
# not an open-ended, growing dictionary of semantic cases.
_PT_QUANTIFIER_EXACT = tuple(
    (form, (MorphClass.DET,)) for form in sorted({
        "nenhum", "nenhuma", "nenhuns", "nenhumas", "ambos", "ambas",
        "vário", "vária", "vários", "várias", "qualquer", "quaisquer",
        "próprio", "própria", "próprios", "próprias", "cerca",
    }))
# Only "primeiro" family added here, deliberately narrow: most PT ordinals are real, common,
# genuinely ambiguous nouns too ("segundo"/"segundos" = seconds, a duration noun -- the exact word
# already needed as a real headword in "tinha dez segundos de sobra"/"cinquenta e oito segundos";
# "quarto"/"quartos" = room(s); "quinta"/"quintas" = farm/estate or "Thursday"; "sexta" = "Friday")
# -- a blanket "tag every ordinal DET-only" rule would silently break those correct noun readings.
# "primeiro"/"primeira"/"primeiros"/"primeiras" were screened against the full CINTIL-dev corpus
# plus the entire 200-scenario Gen-Z PT battery and found with zero noun-reading collisions.
# "último" family ("last") added the same way after an identical screen (12-27 occurrences each,
# always ADJ; only the unrelated noun "ultimato" shares the stem, not the word itself) -- found on
# "Ele matou o último terrorista..." wrongly resolving to "último" instead of "terrorista".
_PT_ORDINAL_EXACT = tuple(
    (form, (MorphClass.DET,)) for form in
    ("primeiro", "primeira", "primeiros", "primeiras",
     "último", "última", "últimos", "últimas"))
# A small, genuinely closed class of PT restrictive/focus adverbs ("only") that fall through to
# CONTENT/UNKNOWN like an ordinary noun and steal an object slot from the noun they modify --
# found on real Gen-Z text: "comprei apenas uma coxinha" resolved to "apenas" instead of "coxinha".
_PT_RESTRICTIVE_ADVERB_EXACT = tuple(
    (form, (MorphClass.ADV,)) for form in ("apenas", "somente", "unicamente"))
# A small, closed set of high-frequency irregular PT preterite verb forms the regular suffix rules
# above cannot reach (no shared ending pattern safe to generalize) -- each screened individually
# against the full CINTIL-dev corpus and found with zero non-verb readings. "disse" ("said", 99/99
# VERB in CINTIL-dev) was found wrongly left as a spurious competing object candidate on real
# Gen-Z text: "Ele colocou os óculos e disse que ia auditar..." resolved to "óculos e disse"
# tying instead of cleanly reaching "óculos".
_PT_IRREGULAR_PREDICATE_EXACT = tuple((form, (MorphClass.PREDICATE,)) for form in ("disse",))
# Spelled-out PT cardinal numbers ("catorze"/"duzentos"/"mil"...) never trigger the quantity/
# partitive head-shift below, which is keyed on an actual digit character in the token surface --
# a real, recurring gap in natural conversational counting (first named against `domains_pt_v2`'s
# own "cerca de trinta elementos", never fixed there; confirmed a second time on real Gen-Z chat
# text: "Ela postou exatamente quatorze vídeos..." resolved to "quatorze" instead of "vídeos"; "O
# chefe tem duzentos bilhões de vida!" resolved to "duzentos" instead of "vida"). This is a real,
# small, finite, closed PT word class (0-999,999,999-ish cardinals) -- not an open dictionary, the
# same discipline as `_PT_QUANTIFIER_EXACT` above. "um"/"uma" are deliberately excluded: the base
# pack already gives them their own (DET, NOMINAL) indefinite-article reading, used far too often
# as a plain article to risk a second, competing NUMERIC reading here. Given only `MorphClass.
# NUMERIC` (no NOMINAL/CONTENT/UNKNOWN alternative, matching how a digit-numeral like "4" is
# already classed), so these words become ineligible nominal candidates on their own -- exactly
# like a digit -- rather than needing every downstream exclusion rule to special-case them.
_PT_SPELLED_NUMERAL_EXACT = tuple(
    (form, (MorphClass.NUMERIC,)) for form in sorted({
        "dois", "duas", "três", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez",
        "onze", "doze", "treze", "catorze", "quatorze", "quinze",
        "dezesseis", "dezasseis", "dezessete", "dezassete", "dezoito", "dezenove", "dezanove",
        "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa",
        "cem", "cento",
        "duzentos", "duzentas", "trezentos", "trezentas", "quatrocentos", "quatrocentas",
        "quinhentos", "quinhentas", "seiscentos", "seiscentas", "setecentos", "setecentas",
        "oitocentos", "oitocentas", "novecentos", "novecentas",
        "mil", "milhão", "milhao", "milhões", "milhoes", "bilhão", "bilhao", "bilhões", "bilhoes",
    }))
_PT_SPELLED_NUMERAL_WORDS = frozenset(form for form, _ in _PT_SPELLED_NUMERAL_EXACT)
# Closed-class informal chat/SMS abbreviations for existing function words -- real, bounded PT-BR
# orthographic variants (common in social-media text, e.g. DANTEStocks), not an open slang
# dictionary. "q"/"oq" get the same (COORDINATOR, REL) reading as "que"/"o que"; "pq" ("porque",
# a causal subordinator) is tagged ADV so it can never be mistaken for a nominal-eligible
# candidate or a relative pronoun -- mirrors the never-tagged, always-fallback-CONTENT behaviour
# of the full form "porque" today, made explicit for the abbreviation specifically.
# A small, genuinely closed set of standard PT conjunctive/discourse adverbs ("however",
# "meanwhile") missing from the frozen base pack's own `_PT_ADVERBS` list -- "porém" (the same
# discourse-connective function) is already a COORDINATOR there, but "contudo"/"entretanto"/
# "todavia" fell through to CONTENT/UNKNOWN, exactly like a real noun, becoming a spurious
# candidate. Found directly on real CINTIL-dev text: "Contudo , a partir daí , a situação
# inverteu-se ." resolved to "Contudo" instead of the true subject "situação" -- a real fronted-
# adverb false candidate, not the assignment-order bug it first looked like (a broader "nearest
# candidate wins" fix was tried and reverted: it also fixed this one case but caused three new
# regressions on genuine appositive/parenthetical-subject sentences, e.g. "O juiz Hancock..." and
# "Gomes , contudo , não se compromete .", where the FIRST candidate is correctly the subject).
# Recognizing "contudo" as ADV removes it from candidacy outright, which is what actually fixes
# 34489 without touching the assignment logic at all -- and "Gomes , contudo , ..." above is
# itself the clean confirming case: same adverb, now correctly excluded, "Gomes" reached normally.
_PT_DISCOURSE_ADVERB_EXACT = tuple(
    (form, (MorphClass.ADV,)) for form in ("contudo", "entretanto", "todavia"))
# "enquanto" ("while") is a real, closed-class PT subordinating conjunction, screened as 11/11
# SCONJ in CINTIL-dev with zero noun collisions -- found wrongly winning an object slot on
# "Trata-se de benefícios que a Ordem atribuiu enquanto pôde.": with no ADV/SCONJ reading, it fell
# through to CONTENT/UNKNOWN and was mistaken for the target verb's own waiting forward object.
_PT_SUBORDINATOR_EXACT = tuple((form, (MorphClass.ADV,)) for form in ("enquanto",))
# The same closed set of subordinating/discourse connectors used in `_PT_SUBORDINATOR_EXACT`/
# `_PT_DISCOURSE_ADVERB_EXACT` above, reused as a clause-boundary marker for the "que"
# forward-object check in `_assign_valence_slots` (a subordinate clause's own verb must not be
# mistaken for the OUTER target verb's own waiting object).
_CLAUSE_CONNECTOR_BOUNDARY_WORDS = frozenset({"enquanto", "contudo", "entretanto", "todavia"})
_PT_CHAT_ABBREVIATION_EXACT = (
    # "mto"/"mt" are the standard chat-abbreviated forms of "muito" -- the base pack already gives
    # "muito" itself a dual (ADV, DET) reading (intensifier adverb vs. quantifier determiner); the
    # abbreviation inherits the identical reading rather than a new, invented single-purpose class.
    # Without this, "mto"/"mt" fall through to CONTENT/UNKNOWN -- exactly like a real noun -- and
    # can wrongly outrank the true object (found on real Gen-Z chat text: "eu quero mto essa
    # massa!" resolved to "mto" instead of "massa"; "mt" produced the same wrong answer).
    ("mt", (MorphClass.ADV, MorphClass.DET)),
    ("mto", (MorphClass.ADV, MorphClass.DET)),
    ("oq", (MorphClass.COORDINATOR, MorphClass.REL)),
    ("pq", (MorphClass.ADV,)),
    ("q", (MorphClass.COORDINATOR, MorphClass.REL)),
)
_BRIDGE_EXACT = tuple(sorted(
    _PT_MORPHOLOGY.exact + _PT_QUANTIFIER_EXACT + _PT_CHAT_ABBREVIATION_EXACT +
    _PT_SPELLED_NUMERAL_EXACT + _PT_DISCOURSE_ADVERB_EXACT + _PT_ORDINAL_EXACT +
    _PT_RESTRICTIVE_ADVERB_EXACT + _PT_IRREGULAR_PREDICATE_EXACT + _PT_SUBORDINATOR_EXACT))
_BRIDGE_MORPHOLOGY = FiniteMorphologySpec(
    language=_PT_MORPHOLOGY.language, exact=_BRIDGE_EXACT,
    suffixes=_PT_MORPHOLOGY.suffixes + _PT_VERB_SUFFIXES,
    contractions=_PT_MORPHOLOGY.contractions,
)


@dataclass(frozen=True, order=True)
class CandidateSpan:
    token_index: int
    surface: str
    span: tuple[int, int]
    is_clitic: bool
    structurally_excluded: bool
    is_rel: bool = False


_PREDICATE_LIKE_CLASSES = frozenset({MorphClass.PREDICATE, MorphClass.AUX})


@dataclass(frozen=True)
class RoleReadResult:
    state: str  # "resolved" | "contested" | "abstain"
    answer: str | None
    answer_span: tuple[int, int] | None
    reason: str
    explored_states: int
    mechanism: str  # "hdca_agrees" | "hdem_packed_only" | "none"


def _token_classes(morphology: MorphologyLattice) -> dict[int, frozenset[MorphClass]]:
    return {entry.token.index: frozenset(reading.morph_class for reading in entry.readings)
            for entry in morphology.tokens}


# A real, closed family of "de" + demonstrative/personal-pronoun fusions ("dele", "deste",
# "desse"...) that PortiLexicon-UD has no row for at the FUSED surface form -- it only lists them
# under a coincidental, unrelated rare verb conjugation (e.g. "dele" also happens to be an
# imperative/present form of the archaic verb "delir"), so the lexicon's own "PREDICATE and
# nothing else" ambiguity check cannot arbitrate: there is no competing NOMINAL/PRON reading to
# find, even though the fused form is overwhelmingly common as a plain possessive/demonstrative
# in real text. Found directly on "O tatuador limpou a pele DELE com álcool...": "dele" ("his")
# was wrongly promoted to PREDICATE, silently swallowing the true object "pele". Screened
# systematically against the whole fused "de+pronoun/demonstrative" paradigm before excluding --
# only these specific forms collide; "dela"/"delas"/"desta"/"destas"/"disso"/"daquilo"/
# "daquele(s)"/"daquela(s)" have no PortiLexicon entry at all and need no exclusion.
_PT_LEXICON_FUSED_PRONOUN_EXCLUDE = frozenset({
    "dele", "deles", "disto", "deste", "destes", "desse", "dessa", "desses", "dessas",
})
# A second, separate real PortiLexicon gap: most deverbal "-o" nouns ("comando", "trabalho",
# "acordo", "estudo", "abraço"...) are correctly listed with BOTH a verb and a noun reading, so the
# existing "PREDICATE and nothing else" check already refuses to promote them -- confirmed directly
# across 19 common deverbal nouns, only 2 gaps found. "controlo" ("control", found wrongly promoted
# on "BCM quer controlo da Sofinloc" -- swallowing the true object "controlo" itself is fine, but a
# real regression showed up on a DIFFERENT sentence, see the module's own CINTIL-dev history) and
# "registo" ("record"/"registration") are both missing their real, common NOUN entry in the
# upstream resource entirely, exactly like the fused-pronoun family above -- a genuine data gap in
# PortiLexicon-UD itself, not a flaw in the "unambiguous" safety check's own logic.
_PT_LEXICON_DEVERBAL_NOUN_EXCLUDE = frozenset({"controlo", "registo"})


def _augment_classes_with_lexicon(
        classes: dict[int, frozenset[MorphClass]], tokens: tuple[SurfaceKernelToken, ...],
        lexicon: CompactPortiLexicon) -> dict[int, frozenset[MorphClass]]:
    """Real-lexicon PREDICATE recovery for irregular verbs the closed-class suffix rules do not
    cover ("sabe", "disse", "observa", "conclui") -- these otherwise default to CONTENT/UNKNOWN,
    the same bucket as an ordinary noun, letting an earlier matrix-clause subject leak forward
    into their own clause (found directly on "A Casa Branca disse que Agnos recusou os
    convites.": "Casa" wrongly won "recusou"'s subject instead of "Agnos", because "disse" was
    never recognized as a verb at all).

    Deliberately narrow, not a naive union: the real PortiLexicon-UD lexicon preserves genuine
    ambiguity (e.g. "cerca"/"que"/"empresa" all carry several competing analyses, PREDICATE
    included, since Portuguese has real homographs) -- unioning it in wholesale was tried and
    found to regress (see this module's own `CLAUDE.md` section). Promotion only fires when ALL
    of: (a) the token is currently unclassified (still at the bare CONTENT/UNKNOWN fallback,
    meaning no closed-class exact/suffix rule already has an opinion); (b) the lexicon's own
    analysis for that surface form is PREDICATE and nothing else; (c) the token is not
    capitalized -- the lexicon has no entries for proper nouns at all, so a genuine name that
    happens to collide with a rare verb conjugation (found directly on "Paris adorou-a à primeira
    vista.": "Paris" is also the informal 2nd-person-plural of "parir", "to give birth") would
    otherwise be misclassified with no competing NOMINAL analysis to arbitrate against; (d) the
    token is not immediately preceded by a DET-classed token -- a determiner is itself strong,
    already-available evidence that this token is functioning as a noun here (found directly on
    "Uma equipa que enervou...": "equipa" is also the informal 2nd-singular/3rd-singular-present
    of "equipar", "to equip", but the European-Portuguese noun sense the lexicon under-covers is
    what a preceding "Uma" already tells us is intended).
    """
    by_index = {token.index: token for token in tokens}
    augmented = dict(classes)
    for token in tokens:
        current = classes.get(token.index, frozenset())
        if not (current and current <= {MorphClass.CONTENT, MorphClass.UNKNOWN}):
            continue
        preceding = by_index.get(token.index - 1)
        # Capitalization normally blocks promotion (proper-noun signal -- the lexicon has no
        # proper-noun entries at all, so a name colliding with a rare verb conjugation would
        # otherwise be misclassified with nothing to arbitrate against, e.g. "Paris"/parir,
        # "Gomes"/gomar). The one narrow exemption: a clause-initial capitalized token
        # immediately followed by a CLITIC is almost certainly the enclitic-reflexive-passive
        # shape ("Observa-se que...", "Discute-se...", "Conclui-se que...") -- ordinary sentence-
        # initial capitalization on a genuine verb, not a name (a bare proper noun is never
        # followed by its own attached clitic this way).
        following = by_index.get(token.index + 1)
        clause_initial = (preceding is None or
                          preceding.surface.casefold() in _CLAUSE_BOUNDARIES)
        enclitic_reflexive_shape = (
            clause_initial and following is not None and
            MorphClass.CLITIC in classes.get(following.index, frozenset()))
        if token.surface[:1].isupper() and not enclitic_reflexive_shape:
            continue
        if preceding is not None and MorphClass.DET in classes.get(preceding.index, frozenset()):
            continue
        if (token.surface.casefold() in _PT_LEXICON_FUSED_PRONOUN_EXCLUDE or
                token.surface.casefold() in _PT_LEXICON_DEVERBAL_NOUN_EXCLUDE):
            continue
        lexical = lexicon.lookup(token.surface)
        if lexical == frozenset({MorphClass.PREDICATE}):
            augmented[token.index] = lexical
    return augmented


# Only a NOMINAL-standing relative can ever fill a subject/object gap by itself -- "onde"
# ("where") is REL-classed in the base pack but stands for a PLACE/PP, never a nominal argument,
# so it must never claim a role the way "que"/"quem"/"qual" do (found directly on "Ainda não
# sabemos a onde esse populismo nos levará.": "onde" wrongly won the object slot that belongs to
# the clitic "nos"). "q" is the closed-class informal chat abbreviation for "que" (common in
# social-media text, e.g. DANTEStocks), given the exact same eligibility as the full form.
_NOMINAL_REL_FORMS = frozenset({"que", "q", "quem", "qual", "quais", "cuja", "cujas", "cujo", "cujos"})
# PREDICATE/AUX added after wiring in the real PortiLexicon lexicon (`_augment_classes_with_
# lexicon`): once an irregular reporting verb like "disse" is correctly recognized as a verb
# instead of falling through to CONTENT/UNKNOWN, a "que" immediately after it is a plain
# complementizer introducing a clausal complement ("disse que Agnos recusou..."), not a relative
# pronoun with a NOUN antecedent -- it must not claim a role the same way "o livro que..." does
# (found directly on "A Casa Branca disse que Agnos recusou os convites.": "que" wrongly won the
# object of "recusou" instead of "convites", once "disse" started being seen as a verb at all).
_QUE_BLOCKING_CLASSES = frozenset(
    {MorphClass.ADV, MorphClass.ADPOSITION, MorphClass.PREDICATE, MorphClass.AUX})


def _rel_argument_eligible(token: SurfaceKernelToken, by_index: dict[int, SurfaceKernelToken],
                           classes: dict[int, frozenset[MorphClass]]) -> bool:
    """A relative-classed token only competes for a subject/object role when it genuinely stands
    for a missing nominal argument -- never "onde" (see above), and never "que"/"q" when it is
    part of a fixed subordinating expression ("por que", "assim que", "à medida que", the
    informal one-token "pq") rather than referring to a preceding antecedent noun: found directly
    on "Sabe por que eu amo tanto você" (que wrongly beat "você") and "assim que vc tiver um
    tempo" (que wrongly beat "tempo"). Keyed on the immediately preceding token's own class (ADV/
    ADPOSITION), a structural signal, not a per-expression word list.
    """
    surface = token.surface.casefold()
    if surface not in _NOMINAL_REL_FORMS:
        return False
    if surface in ("que", "q"):
        preceding = by_index.get(token.index - 1)
        if preceding is not None and classes.get(preceding.index, frozenset()) & _QUE_BLOCKING_CLASSES:
            return False
        # A stricter "the antecedent must immediately precede 'que'" requirement was tried here
        # (targeting a plain complementizer/subordinator "que" -- "Lembra QUE eu comprei...",
        # "vem aqui em casa QUE a gente monta..." -- wrongly winning an object slot via the
        # `other_between` fronted-object path in `_assign_valence_slots`, meant for genuine
        # relatives like "o livro que o autor escreveu") and reverted: measured directly against
        # real CINTIL-dev text it was net negative (1 improvement, ~6 regressions on sentences
        # where "que" IS the correct answer but its real antecedent sits further back than the
        # immediately-preceding token, e.g. across an intervening adjective/PP). The two target
        # Gen-Z cases remain an open, precisely-diagnosed gap, not yet safely fixable this way.
    return True


def clause_bounds(tokens: tuple[SurfaceKernelToken, ...], predicate_index: int) -> tuple[int, int]:
    """Nearest clause-boundary token indices strictly before/after the predicate (exclusive)."""
    left = max((token.index for token in tokens
                if token.index < predicate_index and
                token.surface.casefold() in _CLAUSE_BOUNDARIES), default=-1)
    right = min((token.index for token in tokens
                 if token.index > predicate_index and
                 token.surface.casefold() in _CLAUSE_BOUNDARIES), default=len(tokens))
    return left, right


def _numeral_span_continues(
        cursor: int, by_index: dict[int, SurfaceKernelToken],
        classes: dict[int, frozenset[MorphClass]]) -> bool:
    """Whether `cursor` is still inside a multi-token compound numeral/quantity-classifier span
    the head-shift cursor walk in `extract_clause_candidates` should skip past, rather than treat
    as the answer itself. Three real PT compounding shapes, all found directly on real text:

    - Two adjacent NUMERIC tokens ("duzentos bilhões", "dezoito mil") -- the second magnitude word
      is itself spelled-numeral-classed, so a plain NUMERIC check already covers it.
    - A bare "e"/"ou" coordinator flanked by NUMERIC on both sides ("cento e vinte", "trinta e
      seis", "dezoito mil e quinhentos") -- without this, the coordinator (COORDINATOR-classed,
      not itself NUMERIC) would stop the walk one token early.
    - An ADPOSITION immediately preceded by a NUMERIC token ("duzentos bilhões DE vida") -- this
      is a numeral-classifier "de" ("200 billion OF life-points"), not a genitive/oblique PP
      opener; without this, the ordinary PP-governance loop would open a fresh PP right here and
      wrongly exclude the true head noun that follows it.
    """
    token_classes = classes.get(cursor, frozenset())
    if MorphClass.NUMERIC in token_classes:
        return True
    token = by_index.get(cursor)
    if token is None:
        return False
    before = by_index.get(cursor - 1)
    before_numeric = (before is not None and
                      MorphClass.NUMERIC in classes.get(before.index, frozenset()))
    if (token.surface.casefold() in ("e", "ou") and
            MorphClass.COORDINATOR in token_classes and before_numeric):
        after = by_index.get(cursor + 1)
        return (after is not None and
                MorphClass.NUMERIC in classes.get(after.index, frozenset()))
    if MorphClass.ADPOSITION in token_classes and before_numeric:
        return True
    return False


def extract_clause_candidates(
        tokens: tuple[SurfaceKernelToken, ...], classes: dict[int, frozenset[MorphClass]],
        predicate_index: int, role: str) -> tuple[CandidateSpan, ...]:
    """Real nominal/clitic/relative candidates in the predicate's own clause, positioned by role.

    Subject candidates: nominal tokens strictly between the clause's left boundary and the
    predicate (bare clitics can never be Portuguese subjects, so they are never subject-eligible
    on their own). Object candidates: nominal/clitic tokens strictly between the predicate and the
    clause's right boundary, plus one immediately-proclitic token directly left of the predicate
    (Portuguese object clitics routinely appear proclitically, e.g. "... lhe contou ...").

    A relative pronoun ("que"/"quem"/"qual", `MorphClass.REL`) is eligible for EITHER role,
    regardless of position: in "[antecedent] que [verb]", UD attaches the pronoun itself, never
    the antecedent, as the relative clause verb's own nsubj/obj -- `build_role_problem` gives it
    absolute precedence over any competing nominal or clitic for exactly this reason. Measured
    directly against real CINTIL-dev text: this raised whole-relation accuracy from 74.78% to
    78.29% (341/456 -> 357/456) over the version that only ever excluded REL tokens outright.

    This is the one simplification this first slice keeps from the old code's own base pass
    (default SVO order); VSO/OVS/topicalized-OSV inversion is out of scope here (see the module
    docstring) and is left to abstain rather than guess.
    """
    if role not in ("subject", "object"):
        raise ValueError("role must be 'subject' or 'object'")
    left, right = clause_bounds(tokens, predicate_index)
    by_index = {token.index: token for token in tokens}
    if role == "subject":
        # Ported from the old procedural compiler's own `left_nominal_candidates`: the nearest
        # relative-classed token before the predicate becomes an additional left boundary, walling
        # off an earlier matrix clause's own subject from leaking into an embedded clause's search
        # (found directly on "Você sabe quais planos que ele tinha?": without this, "Você" -- the
        # OUTER clause's subject for "sabe", an irregular verb this pack's suffix rules do not tag
        # PREDICATE -- wrongly won "tinha"'s subject slot instead of "ele").
        #
        # Walking backward stops at the first REL marker found, but ALSO stops (without shifting
        # the boundary at all) at the first predicate-like token found first -- an intervening
        # predicate means that relative clause already closed before reaching the query predicate,
        # so it must not wall off an EARLIER, unrelated antecedent noun that is genuinely this
        # query's own subject (found directly on "A empresa que Maria fundou faliu.": naively
        # using the nearest REL marker regardless of what lies between it and the query predicate
        # wrongly excluded "empresa" -- the correct subject of "faliu" -- because "que" belongs to
        # a DIFFERENT, already-closed relative clause modifying "empresa", not to "faliu"'s own
        # clause).
        boundary = left
        for token in reversed(tokens):
            if not (left < token.index < predicate_index):
                continue
            token_classes = classes.get(token.index, frozenset())
            if MorphClass.REL in token_classes:
                # Shift to one position BEFORE the marker, not the marker's own index -- the
                # marker itself must stay IN scope (it is still a real subject/object candidate
                # via the existing is_rel machinery), only material strictly before it is walled
                # off. Using the marker's own index here was a real bug: it excluded the marker
                # from its own clause's candidate pool entirely (found on "Uma expressão que se
                # prolonga.", where "que" -- the correct subject -- disappeared as a candidate).
                boundary = token.index - 1
                break
            if token_classes & _PREDICATE_LIKE_CLASSES:
                break
        left = boundary
        scope = [token for token in tokens if left < token.index < predicate_index]
    else:
        scope = [token for token in tokens if predicate_index < token.index < right]
        proclitic = by_index.get(predicate_index - 1)
        if (proclitic is not None and left < proclitic.index and
                MorphClass.CLITIC in classes.get(proclitic.index, frozenset())):
            scope = [proclitic] + scope

    def eligible(token: SurfaceKernelToken) -> bool:
        token_classes = classes.get(token.index, frozenset())
        if token_classes & _ARGUMENT_CLASSES:
            return True
        if MorphClass.REL in token_classes:
            return _rel_argument_eligible(token, by_index, classes)
        return role == "object" and MorphClass.CLITIC in token_classes

    eligible_indices = {token.index for token in scope if eligible(token)}
    # Quantifier/partitive head-shift: "cerca de 850 trabalhadores" -- the true head sits AFTER
    # the numeral, not before "de" ("cerca"). This is the exact opposite structural decision from
    # plain PP-governance for the same "X de Y" surface shape, so it is resolved separately, keyed
    # on an actual digit-bearing numeral rather than any closed word list. The eligible nominal
    # immediately following a numeral (skipping a determiner) becomes the preferred head; every
    # candidate positioned before that numeral in the same scope is excluded outright. Measured
    # directly against real CINTIL-dev text: raised whole-relation accuracy from 78.29% to 81.14%
    # (357/456 -> 370/456).
    # `compile_finite_morphology`'s own NUMERIC check accepts any run of digits/","/"." -- a bare
    # comma or period alone vacuously satisfies "every character is a digit or separator" with no
    # digit present at all. Require an actual digit here so a plain "," never gets mistaken for a
    # quantity numeral (caught by a real false positive: it silently misfired on "O rio, o lago...").
    # A spelled-out cardinal ("catorze", "duzentos") triggers the same shift via its own closed-
    # class NUMERIC reading (`_PT_SPELLED_NUMERAL_EXACT`), never a digit -- checked by word
    # membership, not `isdigit()`, since it has none.
    numeral_indices = [token.index for token in scope
                      if MorphClass.NUMERIC in classes.get(token.index, frozenset()) and
                      (any(character.isdigit() for character in token.surface) or
                       token.surface.casefold() in _PT_SPELLED_NUMERAL_WORDS)]
    quantity_heads = set()
    quantity_excluded = set()
    for numeral_index in numeral_indices:
        # A numeral directly preceded by an ALREADY-eligible candidate ("nota nove vírgula oito",
        # "sedã quatro portas") is post-nominal/appositive to that noun, not a pre-nominal
        # quantifier for a noun still to come -- the numeral-shift's whole premise ("cerca de 200
        # pessoas"/"quatorze vídeos": the true head sits AFTER the numeral) does not apply here,
        # and applying it anyway wrongly wipes out the real, already-correct preceding head as if
        # it were a discardable quantity-phrase prefix. Found directly on "Ele concedeu a nota
        # nove vírgula oito...": true object "nota" excluded, "vírgula" (a real noun meaning
        # "comma", not the answer) wrongly promoted to quantity head instead.
        preceding = by_index.get(numeral_index - 1)
        if preceding is not None and preceding.index in eligible_indices:
            continue
        cursor = numeral_index + 1
        while (by_index.get(cursor) is not None and
              ((MorphClass.DET in classes.get(cursor, frozenset()) and
                cursor not in eligible_indices) or
               _numeral_span_continues(cursor, by_index, classes))):
            cursor += 1
        if cursor in eligible_indices:
            # The nearest preposition opening this same span (if any) decides whether the shift
            # is even licensed at all -- not just how far back to exclude. A quantity/partitive
            # classifier construction ("cerca DE 200 pessoas", "duzentos bilhões DE vida") always
            # uses "de" (plain or fused with a definite article: do/da/dos/das); a genuine
            # oblique-argument preposition of the VERB itself ("expande-se PARA sete membros" --
            # "to seven members," a goal/purpose complement, not a classifier) must NOT bypass
            # ordinary PP-governance the way the classifier construction legitimately does.
            # Confirmed as a real, pre-existing limitation (not introduced by the spelled-numeral
            # extension): the identical digit version, "...para 7 membros.", already produced the
            # same wrong answer before any change this session.
            pp_start = max(
                (token.index for token in scope
                 if token.index < numeral_index and
                 MorphClass.ADPOSITION in classes.get(token.index, frozenset()) and
                 MorphClass.DET not in classes.get(token.index, frozenset())),
                default=None)
            governing = by_index.get(pp_start) if pp_start is not None else None
            if governing is not None and governing.surface.casefold() not in (
                    "de", "do", "da", "dos", "das"):
                continue
            quantity_heads.add(cursor)
            # Exclude only the LOCAL quantity-phrase prefix (from the nearest preposition
            # opening this same PP, e.g. "de" in "cerca de 200"), never the whole scope --
            # otherwise an unrelated, earlier real object in the same scope was wrongly wiped
            # out too (found on "Alqueva já dá trabalho a cerca de 200 pessoas": the direct
            # object "trabalho" was excluded along with "cerca" just because it came before the
            # numeral anywhere in scope, not because it was part of the quantity phrase itself).
            # With no PP at all, the same reasoning still applies across a comma/second-clause
            # boundary: a plain "-1" default reaches all the way back into an EARLIER, unrelated
            # coordinate clause's own real object ("Ele matou o último terrorista, tinha dez
            # segundos de sobra...": "terrorista", the true object of "matou", was wrongly wiped
            # out just because "dez" appears anywhere later in the same undifferentiated scope,
            # across a comma and an AUX-classed "tinha" the query predicate never reaches). The
            # default lower bound is instead the nearest preceding comma or predicate/AUX-like
            # token before the numeral, never crossing into an earlier clause's own material.
            boundary_default = max(
                (token.index for token in scope
                 if token.index < numeral_index and
                 (token.surface == "," or
                  (classes.get(token.index, frozenset()) & _PREDICATE_LIKE_CLASSES))),
                default=-1)
            lower_bound = pp_start if pp_start is not None else boundary_default
            # Never wipe out a token that is ITSELF already an established quantity head from an
            # earlier numeral in the same scope -- a coordinated list of separately-quantified
            # items ("três fumaças e quatro paredes") has one numeral+head pair per item; without
            # this, "paredes"'s own shift silently deleted "fumaças" (a genuine, already-resolved
            # earlier quantity head, not a discardable quantity-phrase prefix) just because no PP
            # or clause boundary happened to separate them.
            quantity_excluded.update(
                token.index for token in scope
                if lower_bound <= token.index < numeral_index and token.index not in quantity_heads)
    # Partitive quantifier-pronoun head-shift: "Nenhuma das empresas" -- the true head sits INSIDE
    # the partitive "de"-PP ("empresas"), not on the quantifier itself ("Nenhuma", already
    # ineligible as DET) or excluded by ordinary PP-governance the way a ordinary genitive PP
    # modifier would be. Keyed on a small, genuinely closed class of PT quantifier-pronouns that
    # take a partitive complement (not plain articles, which do not license this shift the same
    # way -- "o de Maria" is a separate elliptical-possessive construction, out of scope here).
    partitive_quantifiers = frozenset({
        "nenhum", "nenhuma", "nenhuns", "nenhumas", "ambos", "ambas", "algum", "alguma",
        "alguns", "algumas", "vário", "vária", "vários", "várias", "qualquer", "quaisquer"})
    partitive_indices = [
        token.index for token in scope
        if token.surface.casefold() in partitive_quantifiers and
        (following := by_index.get(token.index + 1)) is not None and
        MorphClass.ADPOSITION in classes.get(following.index, frozenset())]
    for partitive_index in partitive_indices:
        cursor = partitive_index + 1
        if MorphClass.ADPOSITION in classes.get(cursor, frozenset()):
            cursor += 1
        while (by_index.get(cursor) is not None and
              ((MorphClass.DET in classes.get(cursor, frozenset()) and
                cursor not in eligible_indices) or
               _numeral_span_continues(cursor, by_index, classes))):
            cursor += 1
        if cursor in eligible_indices:
            quantity_heads.add(cursor)
            quantity_excluded.update(
                token.index for token in scope if token.index < partitive_index)

    candidates = []
    # PP governance is tracked as a real span, not a one-token-back look-behind: everything from
    # an unambiguous preposition (no DET reading -- see the note below) up to the next punctuation
    # mark or coordinator is inside that oblique/genitive PP, including a following determined noun
    # ("com as companhias aéreas") and any adjective after it, not just the token immediately after
    # the preposition. Measured directly against real CINTIL-dev text: this alone raised whole-
    # relation accuracy from 64.69% to 74.78% (295/456 -> 341/456) over the one-token-back version.
    in_pp = False
    # Whether the CURRENTLY open PP started with no determiner right after its own preposition
    # ("com emoção", a bare-noun manner/manner-adjunct PP) as opposed to one whose own head noun
    # IS determined ("com as companhias aéreas", a genitive/accompaniment PP). Only a bare-started
    # PP closes early, the moment a determiner introduces a genuinely NEW noun phrase -- a
    # determined PP stays open through its own whole determined NP, exactly as before.
    pp_started_bare = False
    for token in scope:
        token_classes = classes.get(token.index, frozenset())
        # Membership, not exact-set equality: `compile_finite_morphology`'s NUMERIC check still
        # tags a bare comma/period with an extra (vacuous) NUMERIC reading alongside PUNCT (the
        # digit-requiring guard above only fixed the numeral-index computation, not this
        # classification itself), so `token_classes == {PUNCT}` silently never matched a comma and
        # an open PP span never closed across it (found directly on "Em princípio , Tenet não
        # terá..." -- the comma failed to end the "Em" PP, so "Tenet" stayed wrongly excluded as
        # if still inside it).
        if MorphClass.PUNCT in token_classes or MorphClass.COORDINATOR in token_classes:
            in_pp = False
        # A short, bare-noun manner/adjunct PP has no internal determiner of its own -- a
        # determiner appearing later in the very same still-open span therefore signals a genuinely
        # NEW noun phrase, not a continuation of that PP, and closes it early. Found directly on
        # real Gen-Z chat text: "... gritou com emoção o bordão de agradecimento." -- "com emoção"
        # (bare) stayed wrongly open all the way through the determined "o bordão", excluding the
        # true object entirely.
        if in_pp and pp_started_bare and MorphClass.DET in token_classes:
            in_pp = False
            pp_started_bare = False
        # Predicative "como" ("usar/tratar/considerar/ver ALGUEM como ALGO") introduces a
        # depictive complement, not the clause's own direct object -- the real object is
        # whatever precedes "como" (often a clitic, e.g. "me usa como assistente" -- the object
        # is "me", not "assistente"). Restricted to object-scope only: interrogative "como"
        # ("Como você fez isso?") always sits clause-initial, BEFORE the predicate, so it can
        # never appear inside an object-role scope loop in the first place, and this pack gives
        # "como" only one closed ADV reading either way (never REL/ADPOSITION) so there is no
        # separate homograph to disambiguate here. Found directly on real Gen-Z chat text: "O
        # meu supervisor me usa como assistente pessoal..." wrongly resolved to "assistente"
        # instead of the clitic "me".
        if role == "object" and token.surface.casefold() == "como":
            in_pp = True
            following = by_index.get(token.index + 1)
            pp_started_bare = not (
                following is not None and
                MorphClass.DET in classes.get(following.index, frozenset()))
        # A bare "a"/"o"/"as"/"os" is ambiguously DET/ADPOSITION/CLITIC in this compact lexicon;
        # only an unambiguous preposition (no DET reading at all -- e.g. a fused "da"/"do"/"na"/
        # "no", or a real preposition like "com"/"entre") is trusted to open a genitive/oblique PP
        # span. Otherwise a clause-initial "A vítima..." would wrongly exclude its own subject as
        # if "A" opened one.
        if MorphClass.ADPOSITION in token_classes and MorphClass.DET not in token_classes:
            in_pp = True
            following = by_index.get(token.index + 1)
            pp_started_bare = not (
                following is not None and
                MorphClass.DET in classes.get(following.index, frozenset()))
        if token.index not in eligible_indices:
            continue
        is_clitic = bool(MorphClass.CLITIC in token_classes and
                         not (token_classes & _ARGUMENT_CLASSES))
        is_rel = bool(MorphClass.REL in token_classes and not (token_classes & _ARGUMENT_CLASSES))
        # Skip past any intervening DET-only (ineligible) token(s) to find the real following
        # eligible candidate -- "uma NOVA história" stacks two determiner-like tokens ("uma", then
        # the newly DET-tagged prenominal adjective "nova") before the real head noun. Without
        # this, "uma" only ever looked one token ahead, found "nova" (DET-only, not eligible), and
        # never reached "história" -- so "uma" itself (eligible via its own indefinite-pronoun
        # NOMINAL reading) stayed a candidate instead of being excluded.
        following = by_index.get(token.index + 1)
        while (following is not None and following.index not in eligible_indices and
               MorphClass.DET in classes.get(following.index, frozenset())):
            following = by_index.get(following.index + 1)
        attributive = bool(
            MorphClass.DET in token_classes and following is not None and
            following.index in eligible_indices)
        pp_excluded = in_pp and token.index not in quantity_heads
        excluded = pp_excluded or attributive or token.index in quantity_excluded
        candidates.append(CandidateSpan(
            token.index, token.surface, token.span, is_clitic, excluded, is_rel))
    return tuple(candidates)



def _predicates_in_scope(tokens: tuple[SurfaceKernelToken, ...],
                         classes: dict[int, frozenset[MorphClass]], left: int, right: int,
                         query_predicate_index: int) -> tuple[int, ...]:
    """Every predicate-like token in the clause that independently competes for its own subject/
    object slot, plus the query's own predicate unconditionally (it may not itself carry a
    PREDICATE/AUX reading -- e.g. an irregular verb the suffix rules in `_BRIDGE_MORPHOLOGY` do
    not cover -- but it is trivially known to be one because it is the verb being asked about).

    An AUX-classed token is excluded here unless it is the query's own target: an auxiliary never
    has an independent subject/object different from the lexical verb it supports (periphrastic
    "estar a VERB", "andar a VERB" chains) -- without this, a candidate positioned right before the
    AUX (e.g. "PSD" in "O PSD está a perder qualidades.") locked onto the AUX's own subject slot
    instead of reaching the real query predicate ("perder") one token further along, and abstained.
    This applies even when a suffix rule ALSO happens to tag the same token PREDICATE (e.g.
    "estavam" matches both the closed AUX list and the "-avam" suffix rule) -- the exclusion must
    not require "AUX without PREDICATE," since that let a genuinely auxiliary "estavam" keep
    competing for its own subject slot anyway (found on "Cerca de 1100 chineses estavam ontem a
    aguardar repatriamento.": "chineses" locked onto "estavam" instead of reaching "aguardar").
    Measured directly against real CINTIL-dev text: 85.31% -> 87.28% (389/456 -> 398/456) for the
    original fix, plus this refinement.
    """
    found = {token.index for token in tokens if left < token.index < right and
            (classes.get(token.index, frozenset()) & _PREDICATE_LIKE_CLASSES) and
            not (MorphClass.AUX in classes.get(token.index, frozenset()) and
                 token.index != query_predicate_index)}
    found.add(query_predicate_index)
    return tuple(sorted(found))


def _candidate_pool(tokens: tuple[SurfaceKernelToken, ...],
                    classes: dict[int, frozenset[MorphClass]], predicates: tuple[int, ...]) \
        -> dict[int, CandidateSpan]:
    """Union of the already-validated per-predicate/per-role candidate extraction across every
    real predicate in the clause -- reuses `extract_clause_candidates`'s own PP-governance/
    attributive/quantity-shift exclusion logic exactly, rather than re-deriving eligibility here.

    A predicate token can never be a candidate for another predicate's own argument slot (a plain
    positional scope, computed per-predicate, does not know about a SECOND predicate sitting
    inside its own object-side scope -- e.g. "escreveu" showing up as a spurious object candidate
    for "comprou" in "Isabel comprou o livro que o autor escreveu.").
    """
    pool: dict[int, CandidateSpan] = {}
    for predicate_index in predicates:
        for role in ("subject", "object"):
            for candidate in extract_clause_candidates(tokens, classes, predicate_index, role):
                if not candidate.structurally_excluded and candidate.token_index not in predicates:
                    pool[candidate.token_index] = candidate
    return pool


def _reflexive_passive_se_subject(
        tokens: tuple[SurfaceKernelToken, ...], classes: dict[int, frozenset[MorphClass]],
        predicate_index: int, pool: dict[int, CandidateSpan]) -> CandidateSpan | None:
    """Reflexive-passive/impersonal "se" ("Previa-se o lançamento...", "O que se verificou...") is
    the grammatical subject-role filler in this construction -- a real, general Portuguese
    phenomenon, not a per-verb list -- but a bare clitic is otherwise never subject-eligible
    (Portuguese has no ordinary subject clitics). "se" attaches either proclitically (right before
    its verb) or enclitically with a hyphen (right after -- "Previa-se", already split into its own
    token by the tokenizer's own enclitic rule), so both directions are checked. This is a genuine
    special case, not routed through the normal positional slot assignment: enclitic "-se" sits
    positionally AFTER its own verb, which the ordinary nearest-slot logic would otherwise treat as
    filling an OBJECT, not a subject.
    """
    by_index = {token.index: token for token in tokens}
    for neighbor_index in (predicate_index - 1, predicate_index + 1):
        neighbor = by_index.get(neighbor_index)
        if (neighbor is not None and neighbor.surface.casefold() in ("se", "-se") and
                MorphClass.CLITIC in classes.get(neighbor.index, frozenset())):
            return CandidateSpan(neighbor.index, neighbor.surface, neighbor.span, False, False, False)
    return None


def _coordination_groups(tokens: tuple[SurfaceKernelToken, ...],
                         classes: dict[int, frozenset[MorphClass]],
                         pool: dict[int, CandidateSpan]) -> dict[int, int]:
    """Union-find over non-REL candidates: two are one coordinate group only if a genuine bare
    coordinator ("e"/"ou") anchors the chain somewhere. A comma alone between two candidates is
    NOT sufficient on its own -- measured directly: treating any comma as coordination caused a
    real, repeated aggregate regression (79.61% down to 77.6-75.9% across several variants) because
    a bare comma is far more often an unrelated parenthetical/appositive/clause-internal boundary
    than a real coordinated list. Only once a real CONJ-linked pair exists does an adjacent
    comma-only pair extend that SAME group (the classic "A, B e C" list shape).
    """
    parent = {index: index for index in pool}

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(first: int, second: int) -> None:
        root_first, root_second = find(first), find(second)
        if root_first != root_second:
            parent[root_second] = root_first

    def is_unambiguous_coordinator(token_index: int) -> bool:
        # "que" is ambiguously REL/COORDINATOR in this compact lexicon (it can be a relative
        # pronoun or a plain complementizer/conjunction); only a token that is COORDINATOR-only
        # (no REL reading) is trusted as a genuine "e"/"ou"-style coordination anchor. Otherwise
        # a relative clause boundary gets mistaken for a coordinated list (found directly on
        # "Isabel comprou o livro que o autor escreveu.", where "que" wrongly bridged "livro" and
        # "autor" into one fake coordination group).
        #
        # The base pack's own `_PT_COORDINATORS` lumps "mas"/"nem"/"porém"/"quando"/"se" into the
        # same COORDINATOR class as "e"/"ou" -- correct for other uses, but far too coarse for
        # list-coordination grouping specifically: "mas" ("but") introduces a CONTRASTIVE clause
        # (a separate proposition), not another item of the same argument list. Found directly on
        # "Mandei mensagem, mas ele visualizou...": "mensagem" and "ele" (the SUBJECT of a
        # completely different verb) were wrongly merged into one coordination group across
        # ", mas ", so once "mensagem" claimed Mandei's object slot, "ele" was dragged along with
        # it into the same slot instead of correctly reaching "visualizou"'s own subject. Requiring
        # the literal surface "e"/"ou" (the only two words this file's other coordination logic --
        # the shared-subject fix, the numeral-chain walk -- already treats as real list anchors)
        # keeps every genuine "A, B e C" list intact while excluding every adversative/subordinate
        # use of a COORDINATOR-classed word that never lists a shared argument.
        token_classes = classes.get(token_index, frozenset())
        token = next((t for t in tokens if t.index == token_index), None)
        return (token is not None and token.surface.casefold() in ("e", "ou") and
                MorphClass.COORDINATOR in token_classes and MorphClass.REL not in token_classes)

    def transparent_span(a: int, b: int) -> bool:
        return all(
            token.surface == "," or is_unambiguous_coordinator(token.index) or
            MorphClass.DET in classes.get(token.index, frozenset())
            for token in tokens if a < token.index < b)

    ordered = sorted(index for index in pool if not pool[index].is_rel)
    for first, second in zip(ordered, ordered[1:]):
        has_conj = any(
            is_unambiguous_coordinator(token.index)
            for token in tokens if first < token.index < second)
        if has_conj and transparent_span(first, second):
            union(first, second)
    changed = True
    while changed:
        changed = False
        for first, second in zip(ordered, ordered[1:]):
            if find(first) == find(second) or not transparent_span(first, second):
                continue
            first_group_size = sum(1 for index in ordered if find(index) == find(first))
            second_group_size = sum(1 for index in ordered if find(index) == find(second))
            if first_group_size > 1 or second_group_size > 1:
                union(first, second)
                changed = True
    return {index: find(index) for index in pool}


def _enclosed_by_open_relative(
        candidate_index: int, target_predicate: int, tokens: tuple[SurfaceKernelToken, ...],
        classes: dict[int, frozenset[MorphClass]], predicates: tuple[int, ...]) -> bool:
    """True when `target_predicate` is the immediate verb of a relative clause opened by a REL
    marker strictly between `candidate_index` and `target_predicate` -- making it unreachable as
    an attachment target for a candidate positioned BEFORE that marker (the candidate is outside
    the relative clause; the target predicate is inside it). Distinguished from a predicate that
    merely comes AFTER an already-closed relative clause by checking whether any OTHER predicate
    lies between the marker and the target: if one does, the relative clause already closed
    before reaching `target_predicate`, which is then a legitimate outer-clause target (found
    directly on "A empresa que Maria fundou faliu.": "que" sits between "empresa" and BOTH
    "fundou" and "faliu", but only "fundou" -- the relative clause's own, unenclosed verb -- should
    be unreachable; "faliu" must stay reachable as the true subject target).
    """
    rel_markers = [token.index for token in tokens
                   if candidate_index < token.index < target_predicate and
                   MorphClass.REL in classes.get(token.index, frozenset())]
    if not rel_markers:
        return False
    last_marker = rel_markers[-1]
    intervening_predicates = [predicate for predicate in predicates
                              if last_marker < predicate < target_predicate]
    return not intervening_predicates


def _assign_valence_slots(
        tokens: tuple[SurfaceKernelToken, ...], predicates: tuple[int, ...],
        pool: dict[int, CandidateSpan], group_of: dict[int, int],
        classes: dict[int, frozenset[MorphClass]]) \
        -> dict[int, tuple[tuple[int, str], ...]]:
    """Attach each candidate to a specific (predicate, subject-or-object) slot of the nearest
    unsaturated predicate, instead of a fixed positional before/after split -- this is what lets a
    clause with more than one verb (coordinated verbs, control/raising chains) resolve correctly.
    A relative pronoun may "steal back" a slot a preceding antecedent grabbed first, but only when
    no other real candidate sits between the pronoun and its verb (otherwise the pronoun is the
    fronted OBJECT of that verb, and the intervening candidate is the true subject). A bare clitic
    always loses its slot to a later-arriving real lexical NP. Coordinated candidates (see
    `_coordination_groups`) are assigned as one tied group, so a genuine tie surfaces as CONTESTED
    downstream rather than being arbitrarily resolved to one member.
    """
    by_index = {token.index: token for token in tokens}
    subject_open = {predicate: True for predicate in predicates}
    # A predicate immediately preceded by a bare coordinator ("... e sumiu", "... ou desistiu")
    # shares the preceding predicate's own subject rather than needing a new one of its own -- a
    # real, general PT coordination pattern (not a per-verb list), keyed only on the closed
    # COORDINATOR class. Without this, a tie in the forward/backward distance comparison below
    # (the coordinated verb sitting exactly as far as the true object of the FIRST verb) let the
    # object wrongly get pulled forward into the second verb's own still-open subject slot instead
    # (found on real Gen-Z chat text: "ele comeu 4 pedacos e sumiu?" -- "pedacos", the true object
    # of "comeu", was pulled into "sumiu"'s subject slot, leaving "comeu" with no object at all).
    for predicate in predicates:
        preceding = by_index.get(predicate - 1)
        if (preceding is not None and preceding.surface.casefold() in ("e", "ou") and
                MorphClass.COORDINATOR in classes.get(preceding.index, frozenset())):
            subject_open[predicate] = False
    object_open = {predicate: True for predicate in predicates}
    assignment: dict[int, list[tuple[int, str]]] = {}
    last_predicate = None
    consumed: set[int] = set()

    def unassign(predicate: int, slot: str) -> int | None:
        holder = next((index for index, slots in assignment.items()
                       if (predicate, slot) in slots), None)
        if holder is not None:
            assignment[holder] = [item for item in assignment[holder] if item != (predicate, slot)]
            if not assignment[holder]:
                del assignment[holder]
        return holder

    for token in tokens:
        if token.index in predicates:
            last_predicate = token.index
            continue
        candidate = pool.get(token.index)
        if candidate is None or token.index in consumed:
            continue
        if candidate.is_rel:
            target = next((predicate for predicate in predicates if predicate > token.index), None)
            if target is None:
                continue
            # A real, non-clitic nominal between the pronoun and its verb means the pronoun is the
            # fronted OBJECT, not the subject -- but a bare clitic ("se") sitting there does not
            # count as one (found directly on "Uma expressão que se prolonga.", where "se" between
            # "que" and "prolonga" wrongly blocked "que" from claiming its own subject slot).
            other_between = any(
                index in pool and not pool[index].is_rel and not pool[index].is_clitic and
                token.index < index < target
                for index in pool)
            # "o que" with no real antecedent noun before it (just a bare determiner at/near the
            # clause start) is the fixed free-relative "that which"/"what", not an ordinary
            # relative pronoun referring back to a preceding noun -- it must not out-rank a
            # reflexive-passive "se" the way a genuine relative clause's own subject pronoun does
            # (found on "O que se verificou foi o contrário.": gold subject is "se", not "que").
            preceding = by_index.get(token.index - 1)
            is_free_relative = bool(
                preceding is not None and preceding.surface.casefold() in ("o", "a") and
                by_index.get(preceding.index - 1) is None)
            se_between = any(
                token_ahead.surface.casefold() in ("se", "-se") and
                token.index < token_ahead.index < target
                for token_ahead in tokens)
            if other_between or (is_free_relative and se_between):
                # A genuine relative clause is missing exactly one argument that "que" itself
                # fills ("o livro que o autor escreveu": "autor"=subj, target "escreveu" has no
                # further candidate of its own, so "que" supplies the missing object). A plain
                # complementizer/subordinator "que" ("Lembra QUE eu comprei aquele tênis...",
                # "vem aqui em casa QUE a gente monta um look...") instead introduces an already
                # COMPLETE clause -- the target verb has its OWN real forward object candidate
                # waiting ("tênis"/"look"), so "que" has nothing left to fill and must not
                # compete for it. Checked directly: without this, "que" wrongly claimed the
                # object slot ahead of the real forward candidate in both cases above.
                #
                # This forward search must stop at the NEXT verb-like boundary after `target`,
                # never spilling into a later, separate clause -- a first version scanned the
                # whole pool and broke real relative clauses like "A bola que ele rebateu não foi
                # um strike.": "strike" belongs to the OUTER matrix clause's own copula ("foi"),
                # not to "rebateu" at all, and was wrongly treated as if it were "rebateu"'s own
                # waiting object. `predicates` alone cannot supply this boundary: it is already
                # filtered per-query (an AUX like "foi" is deliberately excluded from `predicates`
                # unless "foi" is itself the query's own target, since an AUX never has an
                # independent argument in a real periphrastic chain) -- but "foi" here is NOT part
                # of a periphrastic chain with "rebateu" at all, it is a separate clause's own
                # copula, so the boundary must be found by scanning `classes` directly for ANY
                # verb-like token, not by trusting the query-specific `predicates` tuple.
                # A closed-class subordinating/discourse connector ("enquanto", "contudo") also
                # opens its own separate clause the same way a verb-like token does -- found on
                # "...que a Ordem atribuiu ENQUANTO pôde.": once "enquanto" itself stopped being a
                # spurious candidate, its own subordinate clause's verb ("pôde", an irregular,
                # unrecognized preterite with no safe general suffix) was still wrongly reachable
                # as if it were "atribuiu"'s own forward object, for the same underlying reason a
                # verb boundary is needed at all.
                next_predicate_bound = next(
                    (token.index for token in tokens
                     if token.index > target and
                     ((classes.get(token.index, frozenset()) & _PREDICATE_LIKE_CLASSES) or
                      token.surface.casefold() in _CLAUSE_CONNECTOR_BOUNDARY_WORDS)),
                    None)
                forward_limit = (
                    next_predicate_bound if next_predicate_bound is not None else float("inf"))
                # A forward candidate governed by an ADPOSITION between it and `target` is an
                # OBLIQUE/dative argument ("ofereceu objectos A várias pessoas" -- "pessoas" is the
                # recipient, a THIRD argument of a ditransitive verb), not the plain direct object
                # "que" itself fills -- found on "Trata-se de objectos que Elvis oferecera a
                # várias pessoas.": "pessoas" wrongly disqualified "que" from being the real direct
                # object. The governing "a" carries an ambiguous (DET, ADPOSITION) reading here
                # (the classic definite-article/dative-preposition overlap), so ordinary PP
                # governance elsewhere in this file does not already exclude "pessoas" as a
                # candidate -- checked directly rather than assumed.
                def _pp_governed_before(candidate_index: int) -> bool:
                    return any(
                        MorphClass.ADPOSITION in classes.get(between.index, frozenset())
                        for between in tokens if target < between.index < candidate_index)

                target_has_own_forward_object = any(
                    index in pool and not pool[index].is_rel and not pool[index].is_clitic and
                    target < index < forward_limit and not _pp_governed_before(index)
                    for index in pool)
                if target_has_own_forward_object and not (is_free_relative and se_between):
                    continue
                # In the free-relative case this leaves "se" itself free (never object-eligible
                # as a bare clitic competing with a stronger nominal, but still assignable
                # elsewhere) so the reflexive-passive-"se" subject fallback can claim it
                # afterwards -- gold "O que se verificou foi o contrário." has subj=se, obj=que,
                # not the other way around; skipping the assignment outright (a bare `continue`)
                # left "que" with no slot at all and let the "se" clitic's own default object-
                # seeking logic wrongly grab verificou's object slot instead.
                if object_open.get(target):
                    assignment.setdefault(token.index, []).append((target, "obj"))
                    object_open[target] = False
                continue
            if unassign(target, "subj") is not None:
                subject_open[target] = True
            if subject_open.get(target):
                assignment.setdefault(token.index, []).append((target, "subj"))
                subject_open[target] = False
            elif object_open.get(target):
                assignment.setdefault(token.index, []).append((target, "obj"))
                object_open[target] = False
            continue
        if candidate.is_clitic:
            if last_predicate is not None and object_open.get(last_predicate):
                assignment.setdefault(token.index, []).append((last_predicate, "obj"))
                object_open[last_predicate] = False
            else:
                target = next((predicate for predicate in predicates
                               if predicate > token.index and object_open.get(predicate)), None)
                if target is not None:
                    assignment.setdefault(token.index, []).append((target, "obj"))
                    object_open[target] = False
            continue
        group = group_of[token.index]
        chain = sorted(index for index in pool
                       if group_of.get(index) == group and not pool[index].is_rel)
        # Attach to whichever open slot is POSITIONALLY NEARER -- the next predicate's subject
        # slot, or the immediately preceding predicate's object slot -- rather than always
        # preferring a forward subject slot regardless of distance. Without this, a candidate
        # sitting right after its own verb (a plain direct object) could instead be pulled all
        # the way forward into a later, unrelated predicate's subject slot whenever that
        # predicate's own true subject has not been reached yet (found directly on "Isabel
        # comprou o livro que o autor escreveu.", where "livro" -- comprou's real object --
        # was wrongly pulled forward into "escreveu"'s subject slot instead of "autor").
        # A relative clause's own (unenclosed) verb is never a valid forward target for a
        # candidate positioned BEFORE the REL marker that opens it -- that candidate is the
        # OUTER clause's antecedent, not an argument of the embedded verb (see
        # `_enclosed_by_open_relative`; found directly on "A empresa que Maria fundou faliu.",
        # where "empresa" was wrongly pulled into "fundou"'s subject slot instead of reaching
        # "faliu", the true outer-clause target, letting "Maria" wrongly win "faliu"'s subject).
        forward_target = next(
            (predicate for predicate in predicates
             if predicate > token.index and subject_open.get(predicate) and
             not _enclosed_by_open_relative(token.index, predicate, tokens, classes, predicates)),
            None)
        backward_target = last_predicate if (
            last_predicate is not None and object_open.get(last_predicate)) else None
        if backward_target is None and last_predicate is not None and object_open.get(
                last_predicate) is False:
            holder = next((index for index, slots in assignment.items()
                          if (last_predicate, "obj") in slots and pool[index].is_clitic), None)
            if holder is not None:
                backward_target = last_predicate
        forward_distance = (forward_target - token.index) if forward_target is not None else None
        backward_distance = (token.index - backward_target) if backward_target is not None else None
        # On an exact tie (found once "limpei"/"troquei" started being recognized as real
        # predicates too, on "Eu desmontei os coolers, limpei a poeira..., troquei a pasta...":
        # "coolers" sits exactly as far from "desmontei" backward as from "limpei" forward),
        # prefer the BACKWARD predicate's still-open object slot -- an immediately-following
        # direct object is the far more common real structure than being the distant subject of
        # the NEXT, comma-separated (not "e"-coordinated) clause's own verb. A strict "<" here
        # (was "<=") does not change any case where forward is genuinely nearer.
        if forward_distance is not None and (
                backward_distance is None or forward_distance < backward_distance):
            target, slot = forward_target, "subj"
        elif backward_target is not None:
            target, slot = backward_target, "obj"
            holder = next((index for index, slots in assignment.items()
                          if (target, "obj") in slots and pool[index].is_clitic), None)
            if holder is not None:
                unassign(target, "obj")
        else:
            target, slot = None, None
        if target is not None:
            for member in chain:
                assignment.setdefault(member, []).append((target, slot))
                consumed.add(member)
            if slot == "subj":
                subject_open[target] = False
            else:
                object_open[target] = False
    return {index: tuple(slots) for index, slots in assignment.items()}


def _fronted_adjunct_excluded(tokens: tuple[SurfaceKernelToken, ...], left: int, right: int,
                              predicates: tuple[int, ...]) -> frozenset[int]:
    """A clause-initial, comma-terminated span ("Contudo,", "Em princípio,", "nesse período,") is
    a fronted adjunct/adverbial, not a candidate for the clause's own subject/object slots --
    whenever every real predicate of the clause occurs strictly after that first comma. This is a
    narrow, position-only rule (only the clause's very FIRST comma, never a later, clause-internal
    one) specifically to avoid excluding a genuine subject that happens to precede a mid-clause
    parenthetical aside instead ("Os bombeiros temiam, sobretudo, uma mudança..." -- "bombeiros"
    must stay a candidate; a mid-clause parenthetical is a materially different, not-yet-attempted
    case, left open on purpose rather than guessed at here). Measured directly against real
    CINTIL-dev text, together with the companion PP-comma-reset fix in `extract_clause_candidates`
    (a comma still carries a vacuous extra NUMERIC reading, so the PP-span reset had to check
    PUNCT *membership*, not exact-set equality, or an open PP never closed across it): 83.99% ->
    85.09% (383/456 -> 388/456).

    Also requires exactly ONE comma between the clause boundary and the predicate: a SECOND comma
    before the predicate ("A vingança , hoje , tem...") signals a bracketed mid-clause aside sitting
    right after the real subject, not a single fronted adjunct -- excluding the material before the
    first comma there would wrongly drop the genuine subject ("vingança") instead of an adjunct.
    """
    commas_before_predicate = sorted(
        token.index for token in tokens if left < token.index < right and token.surface == "," and
        all(token.index < predicate for predicate in predicates))
    if len(commas_before_predicate) != 1:
        return frozenset()
    first_comma = commas_before_predicate[0]
    return frozenset(range(left + 1, first_comma))


def extract_valence_candidates(
        tokens: tuple[SurfaceKernelToken, ...], classes: dict[int, frozenset[MorphClass]],
        predicate_index: int, role: str) -> tuple[CandidateSpan, ...]:
    """H-PLT/H-DEM candidate extraction via verb-valence slot assignment, not a fixed positional
    subject-before/object-after split. Measured directly against real CINTIL-dev text: 79.61% ->
    79.82% (363/456 -> 364/456) over the purely-positional `extract_clause_candidates`, with every
    known correctness case (including a genuine 3-way coordinated-subject tie, which now correctly
    surfaces as `contested` instead of resolving to one arbitrary member) passing clean. Four
    coordination-detection variants were tried and rejected before this one for regressing the
    aggregate despite fixing their own target case -- see the module's `CLAUDE.md`/living-program
    history for the full account; the version kept here is the one where every fix measured as a
    real, non-regressing gain.
    """
    left, right = clause_bounds(tokens, predicate_index)
    predicates = _predicates_in_scope(tokens, classes, left, right, predicate_index)
    pool = _candidate_pool(tokens, classes, predicates)
    group_of = _coordination_groups(tokens, classes, pool)
    fronted = _fronted_adjunct_excluded(tokens, left, right, predicates)
    for index in [index for index in pool if index in fronted]:
        group = group_of.get(index)
        group_size = sum(1 for other in pool if group_of.get(other) == group)
        if group_size <= 1:
            del pool[index]
    assignment = _assign_valence_slots(tokens, predicates, pool, group_of, classes)
    slot = "subj" if role == "subject" else "obj"
    matches = sorted(index for index, slots in assignment.items() if (predicate_index, slot) in slots)
    if not matches and role == "subject":
        fallback = _reflexive_passive_se_subject(tokens, classes, predicate_index, pool)
        if fallback is not None:
            return (fallback,)
    return tuple(pool[index] for index in matches)


def build_role_problem(candidates: tuple[CandidateSpan, ...]) -> HDEMProblem | None:
    """One H-DEM variable per candidate; a joint constraint enforces exactly one selection.

    A structurally-excluded candidate (PP-governed, or an attributive determiner) gets a forced
    singleton domain -- excluded by construction, not by search. Precedence among the rest is a
    strict tier, each forced to a singleton whenever a higher tier has a free competitor: a
    relative pronoun ("que") always wins (UD attaches it, never its antecedent, as the relative
    clause's own argument); absent one, a full lexical NP wins over a same-slot clitic. Returns
    `None` when nothing in the clause could ever be the answer, so the caller abstains directly
    rather than build a vacuous constraint.
    """
    if not candidates:
        return None
    has_free_rel = any(candidate.is_rel and not candidate.structurally_excluded
                       for candidate in candidates)
    has_free_nominal = any(
        not candidate.is_clitic and not candidate.is_rel and not candidate.structurally_excluded
        for candidate in candidates)
    variables = []
    free_names = []
    for candidate in candidates:
        name = f"role_{candidate.token_index}"
        forced_excluded = (
            candidate.structurally_excluded or
            (not candidate.is_rel and has_free_rel) or
            (candidate.is_clitic and not candidate.is_rel and has_free_nominal))
        if forced_excluded:
            domain = (HDEMValue("excluded", (candidate.token_index,)),)
        else:
            domain = (HDEMValue("excluded", (candidate.token_index,)),
                      HDEMValue("selected", (candidate.token_index,)))
            free_names.append(name)
        variables.append(HDEMVariable(name, domain))
    if not free_names:
        return None
    ordered_free = tuple(sorted(free_names))
    domain_by_name = {variable.name: tuple(value.value for value in variable.domain)
                      for variable in variables if variable.name in free_names}
    allowed = tuple(sorted(
        row for row in itertools.product(*(domain_by_name[name] for name in ordered_free))
        if sum(value == "selected" for value in row) == 1))
    constraint = HDEMConstraint(
        "exactly_one_selected", ordered_free, allowed,
        tuple(sorted(candidate.token_index for candidate in candidates)))
    return HDEMProblem(tuple(sorted(variables)), (constraint,), ordered_free)


def _resolve_via_hplt(source: str, candidates: tuple[CandidateSpan, ...], problem: HDEMProblem,
                       *, scope: str) -> HPLTResult:
    sealed_source = SealedSource.seal("clause_source", source)
    free_names = set(problem.answer_variables)
    guarded_facts = []
    for candidate in candidates:
        if f"role_{candidate.token_index}" not in free_names:
            continue
        span_key = f"{candidate.span[0]}:{candidate.span[1]}"
        fact = AuthorizedFact.seal(
            fact_id=candidate.token_index, predicate="role_candidate", arguments=(span_key,),
            scope=scope, source=sealed_source, source_span=candidate.span,
            compiler_rule=_RULE_ID)
        guarded_facts.append(HPLTGuardedFact(
            fact=fact, guard=((f"role_{candidate.token_index}", "selected"),)))
    program = ConjunctiveProgram(
        goals=(RelationalGoal("role_candidate", ("?Answer",)),), output_variables=("?Answer",))
    return execute_proof_lattice_attention(
        problem, guarded_facts=tuple(guarded_facts), sources=(sealed_source,), scope=scope,
        allowed_rules=frozenset({_RULE_ID}), program=program, lattice_mode="packed")


# `_PT_VERB_SUFFIXES` above already screened "-eu"/"-iu"/"-ou" one at a time against real corpus
# text; the 1st-person-singular preterite "-ei" ending ("comprei", "anotei", "encontrei", "tirei")
# is by far the single most common verb-suffix gap in real Gen-Z first-person narration (30 of the
# 200-scenario Gen-Z PT battery use it) but was never added as a `SuffixRule`, because
# `_token_classes` unions every reading a token has -- a `SuffixRule` cannot express "PREDICATE,
# except for these four specific known nouns," it can only ever add PREDICATE unconditionally to
# every matching surface form. A direct screen against the full CINTIL-dev corpus plus the entire
# 200-scenario Gen-Z PT battery found exactly four real, closed collisions: "lei" (law) and "sei"
# (I know/from saber) are both real nouns/verbs but only 3 characters (shorter than "rei"'s own
# 3-character exclusion precedent), so a length > 3 floor already excludes them for free without
# an explicit list; "hóquei" (hockey), "vôlei" (volleyball), "nikkei" and "iapmei" (proper nouns,
# a stock index and an institution acronym) are all 6 characters and must be explicitly excluded.
# Implemented as a small standalone augmentation (mirroring `_augment_classes_with_lexicon`'s own
# "promote only when currently unclassified" gate is not needed here, since PREDICATE is simply
# added alongside any existing readings -- exactly like every other `_PT_VERB_SUFFIXES` entry)
# rather than a `SuffixRule`, so the exclusion list can be expressed directly. Always-on (not
# opt-in): a small, genuinely closed PT verb-morphology class, not a growing per-example
# dictionary, matching the existing `_PT_VERB_SUFFIXES` discipline exactly.
_PT_EI_VERB_SUFFIX_EXCLUDE = frozenset({"hóquei", "hoquei", "vôlei", "volei", "nikkei", "iapmei"})

# PT gerunds ("-ando"/"-endo"/"-indo") are a real, highly reliable closed morphological class --
# unlike English "-ing" (routinely also a noun: "building", "meeting"), Portuguese gerunds
# essentially never double as ordinary nouns. Found spuriously winning an object/subject slot on
# real Gen-Z text: a secondary gerund clause ("...pedindo respeito...", "...elogiando meu
# estilo...", "...mandando mensagens...") sat in the same scope as the query predicate's own
# object/subject and, with no PREDICATE-like tag of its own, competed as if it were a plain noun.
# Screened directly against the full CINTIL-dev corpus plus the entire 200-scenario Gen-Z PT
# battery (checked from length 4 up, not just the eventual floor): "quando" (SCONJ, "when" -- by
# far the most frequent single collision, 44 occurrences) and a handful of real nouns/proper names
# sharing the same ending ("comando", "diferendo", "dividendo", "referendo", "fernando", "arlindo",
# "orlando") are the only collisions found; "sendo" (AUX, a real gerund of "ser") is correctly
# included. A length >= 5 floor (excludes only the very short "indo" on its own, never screened)
# plus this small, closed exclusion list keeps every real collision out without narrowing the
# suffix itself.
_PT_GERUND_SUFFIX_EXCLUDE = frozenset({
    "quando", "comando", "diferendo", "dividendo", "referendo",
    "fernando", "arlindo", "orlando",
})

# 1st/3rd-person-singular imperfect ("-ava"/"-ia" for -ar/-er/-ir verbs: "usava", "regava",
# "tinha"... "tinha" is already a closed AUX form, so only "-ava" is added here) is the second
# most common coordinated-second-verb gap after "-ei", found the same way: "Eu usava aquele
# borrifador e regava generosamente..." wiped out the true object "borrifador" because "regava"
# (the coordinated second verb) was never recognized, letting the query's own numeral-shift
# wipeout reach all the way back across it. Screened directly against the full CINTIL-dev corpus
# plus the entire 200-scenario Gen-Z PT battery: "bratislava" (a city name) and "nava" (a name/
# brand fragment) are the only two real collisions found; a length >= 5 floor (at least one real
# stem character before "-ava") already excludes the 4-character "lava" (the noun) for free, and
# "trava"/"brava"/"escrava" (real nouns/adjectives, not observed in either corpus but real PT
# words) are excluded defensively by the same explicit-list discipline used for "-ei".
_PT_AVA_VERB_SUFFIX_EXCLUDE = frozenset({
    "bratislava", "nava", "trava", "brava", "escrava",
})


def _augment_classes_with_ei_verb_suffix(
        classes: dict[int, frozenset[MorphClass]],
        tokens: tuple[SurfaceKernelToken, ...]) -> dict[int, frozenset[MorphClass]]:
    updated = dict(classes)
    for token in tokens:
        surface = token.surface.casefold()
        if (len(surface) > 3 and surface.endswith("ei")
                and surface not in _PT_EI_VERB_SUFFIX_EXCLUDE):
            updated[token.index] = updated.get(token.index, frozenset()) | {MorphClass.PREDICATE}
    return updated


_GERUND_SUFFIXES = ("ando", "endo", "indo")


def _augment_classes_with_gerund_suffix(
        classes: dict[int, frozenset[MorphClass]],
        tokens: tuple[SurfaceKernelToken, ...]) -> dict[int, frozenset[MorphClass]]:
    updated = dict(classes)
    for token in tokens:
        surface = token.surface.casefold()
        if (len(surface) >= 5 and surface.endswith(_GERUND_SUFFIXES)
                and surface not in _PT_GERUND_SUFFIX_EXCLUDE):
            updated[token.index] = updated.get(token.index, frozenset()) | {MorphClass.PREDICATE}
    return updated


def _augment_classes_with_ava_verb_suffix(
        classes: dict[int, frozenset[MorphClass]],
        tokens: tuple[SurfaceKernelToken, ...]) -> dict[int, frozenset[MorphClass]]:
    updated = dict(classes)
    for token in tokens:
        surface = token.surface.casefold()
        if (len(surface) >= 5 and surface.endswith("ava")
                and surface not in _PT_AVA_VERB_SUFFIX_EXCLUDE):
            updated[token.index] = updated.get(token.index, frozenset()) | {MorphClass.PREDICATE}
    return updated


_INFINITIVE_SUFFIXES = ("ar", "er", "ir")


def _augment_classes_with_licensed_infinitive_coordination(
        classes: dict[int, frozenset[MorphClass]],
        tokens: tuple[SurfaceKernelToken, ...]) -> dict[int, frozenset[MorphClass]]:
    """A bare infinitive coordinated with an already-licensed one ("para apagar o número E
    SEGUIR minha vida", "Vou pedir um lanche E ASSISTIR a um filme") is a real second predicate,
    not a plain noun -- found wrongly left as a spurious competing object candidate on both real
    Gen-Z sentences above. Blanket "-ar"/"-er"/"-ir" suffix recognition is genuinely unsafe on its
    own: Portuguese nominalizes bare infinitives as ordinary nouns constantly ("o jantar", "o
    andar"), and a direct corpus screen found a real false positive on the identical coordination
    shape -- "que mexer em celular e cozinhar de madrugada não combina": "cozinhar" here is a
    nominalized-infinitive SUBJECT of "combina", not a second predicate, and "celular" (a plain
    noun, "cellphone") happens to share the same "-ar" ending by pure coincidence.

    The safe, narrower signal: only tag the SECOND coordinated infinitive when the FIRST one is
    itself independently LICENSED as a real infinitive by its own immediately-preceding context --
    "para" (a purpose-clause marker) or an AUX/PREDICATE-classed token (an "ir + infinitive"
    periphrastic chain, e.g. "Vou pedir..."). Confirmed directly: "mexer" (the unlicensed case)
    is preceded only by "que" (REL/COORDINATOR) -- neither license applies, so the rule correctly
    does not fire there. Requires the candidate to sit DIRECTLY after a bare "e"/"ou" (no
    intervening token), matching both real target cases exactly.
    """
    updated = dict(classes)
    by_index = {token.index: token for token in tokens}

    def is_licensed_infinitive(token: SurfaceKernelToken) -> bool:
        if not (len(token.surface) > 3 and token.surface.casefold().endswith(_INFINITIVE_SUFFIXES)):
            return False
        preceding = by_index.get(token.index - 1)
        if preceding is None:
            return False
        if preceding.surface.casefold() == "para":
            return True
        return bool(classes.get(preceding.index, frozenset()) & _PREDICATE_LIKE_CLASSES)

    for token in tokens:
        surface = token.surface.casefold()
        if not (len(surface) > 3 and surface.endswith(_INFINITIVE_SUFFIXES)):
            continue
        preceding = by_index.get(token.index - 1)
        if preceding is None or preceding.surface.casefold() not in ("e", "ou"):
            continue
        if not (MorphClass.COORDINATOR in classes.get(preceding.index, frozenset()) and
                MorphClass.REL not in classes.get(preceding.index, frozenset())):
            continue
        has_licensed_earlier_infinitive = any(
            other.index < preceding.index and is_licensed_infinitive(other) for other in tokens)
        if has_licensed_earlier_infinitive:
            updated[token.index] = updated.get(token.index, frozenset()) | {MorphClass.PREDICATE}
    return updated


_PT_PRENOMINAL_ADJECTIVE_LOWER_FORMS = frozenset({
    "grande", "grandes", "novo", "nova", "novos", "novas",
})


def _augment_classes_with_prenominal_adjective(
        classes: dict[int, frozenset[MorphClass]],
        tokens: tuple[SurfaceKernelToken, ...]) -> dict[int, frozenset[MorphClass]]:
    """A small, closed set of common PT adjectives that can precede their noun ("uma NOVA
    história", "uma GRANDE multidão") fell through to CONTENT/UNKNOWN like an ordinary noun and
    tied with the real head noun. Unlike "primeiro"/"último" (screened as essentially always ADJ),
    "grande"/"novo"/"nova" carry a real, non-trivial PROPN collision in CINTIL-dev (2-24% of
    occurrences -- "Nova" as a given name, "Grande" as part of a place name) that a casefold-based
    exact entry cannot safely exclude, since the base pack's own morphology lookup is
    case-insensitive by design. Fixed via the ORIGINAL, non-casefolded surface instead: a genuine
    name is essentially always capitalized in running PT text, while a plain mid-clause attributive
    adjective is lowercase -- checking `token.surface` directly (not `.casefold()`) keeps every
    real capitalized name (PROPN) reading untouched and only ever excludes the lowercase-only
    adjective use, at the cost of not helping a genuinely lowercase-adjacent sentence-initial case
    (an acceptable, non-regressing gap, not a false positive).
    """
    updated = dict(classes)
    for token in tokens:
        if token.surface in _PT_PRENOMINAL_ADJECTIVE_LOWER_FORMS:
            updated[token.index] = frozenset({MorphClass.DET})
    return updated


def _correct_classes_for_single_uppercase_keybind(
        classes: dict[int, frozenset[MorphClass]],
        tokens: tuple[SurfaceKernelToken, ...]) -> dict[int, frozenset[MorphClass]]:
    """A bare uppercase "Q" ("apertei o Q") is gaming-slang for a keybind, a plain nominal referent
    -- never the informal lowercase-only chat abbreviation "q" for "que"
    (`_PT_CHAT_ABBREVIATION_EXACT`), which morphology's own casefold-based exact lookup otherwise
    applies regardless of case. Found on real Gen-Z text: "apertei o Q" abstained because "Q"
    inherited (COORDINATOR, REL) and outranked itself as the object. Deliberately narrow: keyed on
    the exact original (non-casefolded) surface "Q" only, not every single uppercase letter -- a
    bare "A" is the extremely common capitalized definite article ("A vítima...") and must keep
    its real DET reading; this fix must never touch any letter but the one actual collision found.
    """
    updated = dict(classes)
    for token in tokens:
        if token.surface == "Q":
            updated[token.index] = frozenset({MorphClass.CONTENT, MorphClass.UNKNOWN})
    return updated


def resolve_surface_role(source: str, predicate_index: int, role: str, *,
                         scope: str = "lab.surface_role_lattice_bridge",
                         lexicon: CompactPortiLexicon | None = None) -> RoleReadResult:
    """Resolve the subject or object of one clause via the H-DEM/H-DCA/H-PLT coalition.

    `lexicon`, if given, is a real PortiLexicon-UD lookup used only to recover PREDICATE for
    irregular verbs the closed-class suffix rules miss (see `_augment_classes_with_lexicon`) --
    entirely opt-in; omitting it preserves every prior digest computed without it.
    """
    tokens = _tokens(source)
    if predicate_index not in {token.index for token in tokens}:
        raise ValueError("predicate_index out of range")
    morphology = compile_finite_morphology(tokens, _BRIDGE_MORPHOLOGY)
    classes = _token_classes(morphology)
    classes = _augment_classes_with_ei_verb_suffix(classes, tokens)
    classes = _augment_classes_with_gerund_suffix(classes, tokens)
    classes = _augment_classes_with_ava_verb_suffix(classes, tokens)
    classes = _augment_classes_with_licensed_infinitive_coordination(classes, tokens)
    classes = _augment_classes_with_prenominal_adjective(classes, tokens)
    classes = _correct_classes_for_single_uppercase_keybind(classes, tokens)
    if lexicon is not None:
        classes = _augment_classes_with_lexicon(classes, tokens, lexicon)
    candidates = extract_valence_candidates(tokens, classes, predicate_index, role)
    problem = build_role_problem(candidates)
    if problem is None:
        return RoleReadResult("abstain", None, None, "no_eligible_candidate_in_clause", 0, "none")
    hdca = solve_hdca(problem)
    plt = _resolve_via_hplt(source, candidates, problem, scope=scope)
    mechanism = "hdca_agrees" if hdca.state != "abstain" else "hdem_packed_only"
    if plt.state != "resolved":
        return RoleReadResult(plt.state, None, None, plt.reason, plt.lattice_explored_states,
                              mechanism)
    span_key = plt.outputs[0].values[0]
    start_text, end_text = span_key.split(":")
    answer_span = (int(start_text), int(end_text))
    return RoleReadResult("resolved", source[answer_span[0]:answer_span[1]], answer_span,
                          plt.reason, plt.lattice_explored_states, mechanism)


def read(source: str, question: str, *,
         scope: str = "lab.surface_role_lattice_bridge",
         lexicon: CompactPortiLexicon | None = None) -> RoleReadResult:
    """Compile a natural-language PT question and resolve it via the coalition.

    Reuses `compile_query` verbatim (the same "Quem ou o que X
    Y?" / "O que X Y?" regex templates the old compiler itself uses) so this can be dropped into
    the same evaluation harness question set -- it is not a second, competing question grammar.
    A question the templates cannot parse, or a predicate that does not occur exactly once in the
    source, abstains with a distinct, honest reason rather than guessing which occurrence to use.
    """
    demand = compile_query(question)
    if demand is None:
        return RoleReadResult("unsupported", None, None, "query_outside_pt_dev_grammar", 0, "none")
    role = "subject" if demand.answer_role == "ARG1" else "object"
    tokens = _tokens(source)
    predicate_matches = tuple(
        token for token in tokens if token.surface.casefold() == demand.predicate)
    if len(predicate_matches) != 1:
        reason = ("no_predicate_occurrence_in_source" if not predicate_matches else
                  "ambiguous_predicate_occurrence")
        return RoleReadResult("abstain", None, None, reason, 0, "none")
    return resolve_surface_role(
        source, predicate_matches[0].index, role, scope=scope, lexicon=lexicon)


def hdca_agrees_with_hdem(problem: HDEMProblem) -> tuple[bool, HDCAResult, HDEMResult]:
    """Differential check: H-DCA must never resolve a value H-DEM itself would not certify.

    H-DCA is a sound under-approximation, not an independent oracle -- it may abstain where H-DEM
    resolves (a non-forest-shaped clause, too many simultaneously free candidates), but it must
    never resolve a *different* answer. Returns whether that contract held, plus both raw results
    for inspection.
    """
    hdca = solve_hdca(problem)
    hdem = solve_hdem_packed(problem)
    oracle = solve_hdem_enumerative(problem)
    assert (hdem.state, hdem.answer, hdem.problem_sha256) == (
        oracle.state, oracle.answer, oracle.problem_sha256), "H-DEM packed/enumerative disagreed"
    if hdca.state == "resolved":
        agree = hdem.state == "resolved" and hdca.answer == hdem.answer
    else:
        agree = True
    return agree, hdca, hdem


__all__ = [
    "CandidateSpan", "RoleReadResult", "build_role_problem", "clause_bounds",
    "extract_clause_candidates", "extract_valence_candidates", "hdca_agrees_with_hdem", "read",
    "resolve_surface_role",
]
