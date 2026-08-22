import pytest

from horizon_memory import OpenTextHorizonMemory, RouteDocument, read_pt_atomic_relation, resolve_pt_surface_role


@pytest.mark.parametrize(("source", "question", "expected"), (
    ("Eu comprei um tênis novo ontem.", "O que eu comprei?", "tênis"),
    ("Ele matou o último terrorista.", "O que ele matou?", "terrorista"),
    ("Maria viu o Pedro no parque.", "Quem ou o que viu Pedro?", "Maria"),
    ("A empresa contratou o novo gerente.", "O que empresa contratou?", "gerente"),
))
def test_promoted_pt_pack_returns_only_exact_reopenable_spans(source, question, expected):
    result = read_pt_atomic_relation(source, question)
    assert result.state == "resolved"
    assert result.answer.casefold() == expected.casefold()
    assert source[slice(*result.answer_span)].casefold() == expected.casefold()


def test_promoted_pt_pack_abstains_on_unsupported_question_grammar():
    result = read_pt_atomic_relation("Aurelia admirou Fiora.", "Isso é uma pergunta estranha")
    assert result.state == "unsupported"


def test_promoted_pt_pack_contests_a_genuine_coordinated_tie():
    # A real multi-way coordinated object list has no single correct atomic span -- CINTIL-dev's
    # own gold methodology excludes exactly this shape (any "conj"-dependent operand) from its
    # eligible relation set for the identical structural reason. Honest abstention, not a bug.
    result = read_pt_atomic_relation(
        "Eles baniram Azir, Syndra e Viktor para te deixar sem nenhum mago.",
        "O que eles baniram?")
    assert result.state == "contested"
    assert result.answer is None


def test_resolve_surface_role_direct_predicate_index_entry_point():
    from horizon_memory.portuguese_atomic_relations import _tokens  # noqa: PLC0415
    source = "Eu usava aquele borrifador e regava generosamente três vezes por dia."
    tokens = _tokens(source)
    predicate_index = next(t.index for t in tokens if t.surface == "usava")
    result = resolve_pt_surface_role(source, predicate_index, role="object")
    assert result.state == "resolved"
    assert result.answer == "borrifador"


def test_open_text_memory_exposes_opt_in_attested_pt_reader_without_changing_default_answer():
    memory = OpenTextHorizonMemory(scope_id=8, session_id="pt-pack")
    document = RouteDocument(1, "Eu comprei um tênis novo ontem.", 8, "pt-pack", 1, "source:1")
    assert memory.ingest_documents((document,)).state == "APPLIED"
    result = memory.answer_atomic_relation_pt("O que eu comprei?", fact_id=1)
    assert result.fact_id == 1 and result.source_id == "source:1"
    assert result.relation.answer == "tênis" and result.proof_closed
    with pytest.raises(ValueError, match="known document"):
        memory.answer_atomic_relation_pt("O que eu comprei?", fact_id=999)


def test_stable_top_level_exports_are_the_same_objects_as_the_research_namespace():
    # The PT pack is now reachable from the stable top-level `horizon_memory` namespace (this
    # session's own promotion decision), but the pre-existing `horizon_memory.research` aliases
    # must keep working and must resolve to the identical objects -- no import path is removed.
    from horizon_memory import OpenTextAtomicRelationResultPT, RoleReadResult
    from horizon_memory.research import (
        RoleReadResult as ResearchRoleReadResult,
        read_pt_atomic_relation as research_read_pt_atomic_relation,
        resolve_pt_surface_role as research_resolve_pt_surface_role,
    )
    assert RoleReadResult is ResearchRoleReadResult
    assert read_pt_atomic_relation is research_read_pt_atomic_relation
    assert resolve_pt_surface_role is research_resolve_pt_surface_role
    assert OpenTextAtomicRelationResultPT.__name__ == "OpenTextAtomicRelationResultPT"
