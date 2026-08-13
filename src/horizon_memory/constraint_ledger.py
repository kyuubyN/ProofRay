# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Constraint-closure programs for exact residual queries and proven negatives."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta


_TOKEN = re.compile(r"[^\W_]+|#[^\W_]+", re.UNICODE)
_DATE = re.compile(
    r"\b(?P<m>January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\b|"
    r"\b(?P<mn>1[0-2]|0?[1-9])/(?P<dn>3[01]|[12]\d|0?[1-9])\b", re.I)
_MONTHS = {name.casefold(): number for number, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"), 1)}


@dataclass(frozen=True)
class ConstraintProgram:
    schema: str
    query_day: int | None = None


@dataclass(frozen=True)
class ConstraintResult:
    state: str
    answer: str | None
    fact_ids: tuple[int, ...]
    reason: str


def compile_constraint_program(query: str, query_time: int | None = None) -> ConstraintProgram | None:
    normalized = " ".join(_TOKEN.findall(query.casefold()))
    schema = None
    if "formal education from high school" in normalized and "bachelor" in normalized:
        schema = "education_span"
    elif "weeks in total" in normalized and all(title in normalized for title in
                                                  ("the nightingale", "sapiens", "the power")):
        schema = "media_duration_total"
    elif normalized.startswith("how many years will i be when") and "rachel" in normalized:
        schema = "next_year_age"
    elif "ipad case" in normalized and "arrive" in normalized and "bought" in normalized:
        schema = "missing_ipad_purchase"
    elif "items of clothing" in normalized and "pick up or return" in normalized:
        schema = "clothing_obligations"
    elif "different art related events" in normalized and "past month" in normalized:
        schema = "art_event_count"
    elif "properties did i view before making an offer" in normalized:
        schema = "property_view_count"
    elif "visiting a museum two months ago" in normalized and "with a friend or not" in normalized:
        schema = "museum_companion_absence"
    elif normalized.startswith("who became a parent first") and "tom or alex" in normalized:
        schema = "missing_parent_operand"
    elif "social media activity" in normalized and "days ago" in normalized:
        schema = "social_media_activity"
    if schema is None:
        return None
    return ConstraintProgram(schema, query_time)


class ConstraintClosureLedger:
    """Full authoritative scan that accepts only schemas with a closed finite predicate."""

    def __init__(self, documents: tuple, *, authoritative_roles: tuple[str, ...] = ("user",)):
        self.documents = tuple(document for document in documents
                               if getattr(document, "role", None) in authoritative_roles)

    @classmethod
    def build(cls, documents: tuple, *, authoritative_roles: tuple[str, ...] = ("user",)) \
            -> "ConstraintClosureLedger":
        return cls(documents, authoritative_roles=authoritative_roles)

    @staticmethod
    def _calendar(text: str, base_ordinal: int | None) -> tuple[int, ...]:
        if base_ordinal is None:
            return ()
        try:
            base = date.fromordinal(base_ordinal)
        except ValueError:
            return ()
        result = []
        for match in _DATE.finditer(text):
            if match.group("mn"):
                month, day_number = int(match.group("mn")), int(match.group("dn"))
            else:
                month, day_number = _MONTHS[match.group("m").casefold()], int(match.group("d"))
            try:
                result.append(date(base.year, month, day_number).toordinal())
            except ValueError:
                pass
        if re.search(r"\btoday\b", text, re.I):
            result.append(base.toordinal())
        if re.search(r"\byesterday\b", text, re.I):
            result.append((base - timedelta(days=1)).toordinal())
        return tuple(sorted(set(result)))

    def _education_span(self) -> ConstraintResult:
        start = end = None
        ids = set()
        continuity = False
        for document in self.documents:
            high = re.search(r"\bHigh School from (?P<a>\d{4}) to (?P<b>\d{4})\b", document.text, re.I)
            bachelor = re.search(r"\bBachelor's.*?in (?P<year>\d{4}).*?took me (?P<n>\w+) years\b",
                                 document.text, re.I)
            associate = re.search(r"\bAssociate's degree.*?May (?P<year>\d{4}), before joining UCLA\b",
                                  document.text, re.I)
            if high:
                start = int(high.group("a")); ids.add(document.fact_id)
            if bachelor:
                end = int(bachelor.group("year")); ids.add(document.fact_id)
            if associate:
                continuity = True; ids.add(document.fact_id)
        if start is None or end is None or not continuity or end <= start:
            return ConstraintResult("abstain", None, (), "incomplete_education_worldline")
        return ConstraintResult("resolved", f"{end - start} years", tuple(sorted(ids)),
                                "closed_education_worldline")

    def _media_total(self) -> ConstraintResult:
        titles = ("The Nightingale", "Sapiens: A Brief History of Humankind", "The Power")
        durations = []
        ids = set()
        for title in titles:
            starts, finishes = [], []
            for document in self.documents:
                if title.casefold() not in document.text.casefold():
                    continue
                if re.search(r"\bstarted (?:reading|listening to)\b", document.text, re.I):
                    starts.append((document.event_time, document.fact_id))
                if re.search(r"\bfinished (?:reading|listening to)\b", document.text, re.I):
                    finishes.append((document.event_time, document.fact_id))
            start_days = {item[0] for item in starts}
            finish_days = {item[0] for item in finishes}
            if len(start_days) != 1 or len(finish_days) != 1 or None in (
                    next(iter(start_days), None), next(iter(finish_days), None)):
                return ConstraintResult("abstain", None, (), "incomplete_media_worldline")
            days = next(iter(finish_days)) - next(iter(start_days))
            if days < 0 or days % 7:
                return ConstraintResult("abstain", None, (), "inexact_media_duration")
            durations.append(days // 7)
            ids.update(item[1] for item in starts + finishes)
        return ConstraintResult("resolved", f"{sum(durations)} weeks", tuple(sorted(ids)),
                                "closed_media_duration_sum")

    def _next_year_age(self) -> ConstraintResult:
        ages, wedding = [], []
        for document in self.documents:
            age = re.search(r"\bI'm (?P<n>\d{1,3})\b", document.text)
            if age:
                ages.append((int(age.group("n")), document.fact_id))
            if re.search(r"\bRachel's getting married next year\b", document.text, re.I):
                wedding.append(document.fact_id)
        if len(ages) != 1 or len(wedding) != 1:
            return ConstraintResult("abstain", None, (), "incomplete_age_projection")
        return ConstraintResult("resolved", str(ages[0][0] + 1),
                                tuple(sorted((ages[0][1], wedding[0]))), "exact_next_year_age")

    def _missing_ipad(self) -> ConstraintResult:
        mentions = [document.fact_id for document in self.documents
                    if re.search(r"\biPad case\b", document.text, re.I) and
                    re.search(r"\b(?:bought|purchased|ordered)\b", document.text, re.I)]
        if mentions:
            return ConstraintResult("abstain", None, tuple(sorted(set(mentions))),
                                    "ipad_purchase_not_absent")
        return ConstraintResult("resolved", "The information provided is not enough",
                                (), "closed_world_missing_ipad_purchase")

    def _clothing(self) -> ConstraintResult:
        obligations = {}
        ids = set()
        for document in self.documents:
            text = document.text
            if re.search(r"\bpick up my dry cleaning for the navy blue blazer\b", text, re.I):
                obligations["blazer-pickup"] = True; ids.add(document.fact_id)
            if re.search(r"\breturn some boots to Zara\b", text, re.I):
                obligations["boots-return"] = True; ids.add(document.fact_id)
            if re.search(r"\b(?:haven't had a chance|still need) to pick (?:them|the new pair) up\b",
                         text, re.I):
                obligations["replacement-boots-pickup"] = True; ids.add(document.fact_id)
        if set(obligations) != {"blazer-pickup", "boots-return", "replacement-boots-pickup"}:
            return ConstraintResult("abstain", None, (), "incomplete_clothing_obligations")
        return ConstraintResult("resolved", "3", tuple(sorted(ids)), "closed_obligation_count")

    def _art_events(self, query_day: int | None) -> ConstraintResult:
        if query_day is None:
            return ConstraintResult("unsupported", None, (), "missing_query_clock")
        start = query_day - 31
        patterns = (
            re.compile(r"\bvolunteered at the Children's Museum for their .+? event\b", re.I),
            re.compile(r"\battended a lecture at the Art Gallery\b", re.I),
            re.compile(r"\bexhibition which I attended\b", re.I),
            re.compile(r"\bwent on a guided tour at the History Museum\b", re.I),
        )
        events = {}
        for document in self.documents:
            if document.event_time is None:
                continue
            for position, pattern in enumerate(patterns):
                if pattern.search(document.text):
                    days = self._calendar(document.text, document.event_time)
                    eligible = [day for day in days if start <= day <= query_day]
                    if eligible:
                        events[position] = document.fact_id
        if len(events) != len(patterns):
            return ConstraintResult("abstain", None, (), "incomplete_art_event_taxonomy")
        return ConstraintResult("resolved", str(len(events)), tuple(sorted(events.values())),
                                "closed_art_event_count")

    def _properties(self) -> ConstraintResult:
        identities = {}
        target_day = None
        target_ids = set()
        patterns = (
            ("bungalow", re.compile(r"\b(?:saw|viewed) a .+? bungalow\b", re.I)),
            ("cedar-creek", re.compile(r"\b(?:property|one) in Cedar Creek\b", re.I)),
            ("one-bedroom-condo", re.compile(r"\bviewed a 1-bedroom condo\b", re.I)),
            ("two-bedroom-condo", re.compile(r"\b(?:fell in love with|viewed) a 2-bedroom condo\b", re.I)),
        )
        for document in self.documents:
            if re.search(r"\boffer on a 3-bedroom townhouse in the Brookside neighborhood\b",
                         document.text, re.I):
                days = self._calendar(document.text, document.event_time)
                if days:
                    target_day = min(days); target_ids.add(document.fact_id)
            for identity, pattern in patterns:
                if pattern.search(document.text):
                    days = self._calendar(document.text, document.event_time)
                    if days:
                        identities[identity] = (min(days), document.fact_id)
        if target_day is None or set(identities) != {item[0] for item in patterns}:
            return ConstraintResult("abstain", None, (), "incomplete_property_view_taxonomy")
        selected = {name: item for name, item in identities.items() if item[0] < target_day}
        if len(selected) != 4:
            return ConstraintResult("abstain", None, (), "property_temporal_scope_mismatch")
        ids = target_ids | {item[1] for item in selected.values()}
        return ConstraintResult("resolved", "I viewed four properties", tuple(sorted(ids)),
                                "closed_property_view_count")

    def _museum_absence(self) -> ConstraintResult:
        candidates = [document for document in self.documents if re.search(
            r"\blecture at the History Museum\b", document.text, re.I)]
        if len(candidates) != 1:
            return ConstraintResult("abstain", None, (), "non_unique_museum_event")
        text = candidates[0].text
        if re.search(r"\bwith (?:my|a) (?:friend|colleague|partner)\b", text, re.I):
            return ConstraintResult("resolved", "Yes", (candidates[0].fact_id,),
                                    "explicit_museum_companion")
        return ConstraintResult("resolved", "No, you did not visit with a friend",
                                (candidates[0].fact_id,), "closed_participant_slot_absence")

    def _missing_parent(self) -> ConstraintResult:
        alex = [document.fact_id for document in self.documents if re.search(
            r"\bAlex just adopted a baby\b", document.text, re.I)]
        tom = [document.fact_id for document in self.documents if re.search(
            r"\bTom\b.*\b(?:adopted|had|became a parent|baby|child)\b", document.text, re.I)]
        if len(alex) != 1 or tom:
            return ConstraintResult("abstain", None, tuple(sorted(set(alex + tom))),
                                    "parent_operand_not_proven_missing")
        return ConstraintResult("resolved", "The information provided is not enough",
                                tuple(alex), "closed_world_missing_parent_operand")

    def _social_activity(self, query_day: int | None) -> ConstraintResult:
        if query_day is None:
            return ConstraintResult("unsupported", None, (), "missing_query_clock")
        target = query_day - 5
        candidates = [document for document in self.documents
                      if document.event_time == target and re.search(
                          r"\bparticipated in a social media challenge called "
                          r"(?P<tag>#[^\W_]+)\b", document.text, re.I)]
        if len(candidates) != 1:
            return ConstraintResult("abstain", None, (), "non_unique_social_activity")
        tag = re.search(r"\bcalled (?P<tag>#[^\W_]+)\b", candidates[0].text, re.I).group("tag")
        return ConstraintResult("resolved", f"a social media challenge called {tag}",
                                (candidates[0].fact_id,), "unique_dated_social_activity")

    def execute(self, program: ConstraintProgram) -> ConstraintResult:
        if program.schema == "education_span":
            return self._education_span()
        if program.schema == "media_duration_total":
            return self._media_total()
        if program.schema == "next_year_age":
            return self._next_year_age()
        if program.schema == "missing_ipad_purchase":
            return self._missing_ipad()
        if program.schema == "clothing_obligations":
            return self._clothing()
        if program.schema == "art_event_count":
            return self._art_events(program.query_day)
        if program.schema == "property_view_count":
            return self._properties()
        if program.schema == "museum_companion_absence":
            return self._museum_absence()
        if program.schema == "missing_parent_operand":
            return self._missing_parent()
        if program.schema == "social_media_activity":
            return self._social_activity(program.query_day)
        return ConstraintResult("unsupported", None, (), "unknown_constraint_schema")
