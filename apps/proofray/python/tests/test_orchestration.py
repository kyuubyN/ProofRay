from proofray_app.memory_service import MemoryReply
from proofray_app.orchestration import ChatOrchestrator
from proofray_app.providers import ProviderEvent
from proofray_app.rewrite_guard import guard_rewrite


class _Memory:
    def __init__(self, reply):
        self.reply = reply
        self.queries = []
        self.remembered = []
        self.calls = []

    def answer_prior(self, conversation_id, question, **kwargs):
        self.calls.append("answer")
        self.queries.append((conversation_id, question))
        return self.reply

    def remember_user_message(self, **kwargs):
        self.calls.append("remember")
        self.remembered.append(kwargs)


class _Provider:
    def __init__(self, first, second=()):
        self.responses = [tuple(first), tuple(second)]
        self.requests = []

    def stream_chat(self, request):
        self.requests.append(request)
        yield from self.responses.pop(0)


class _Providers:
    def __init__(self, provider):
        self.provider = provider

    def stream_chat(self, provider_id, request, *, secret=None):
        assert provider_id == "provider"
        return tuple(self.provider.stream_chat(request))


def test_rewrite_guard_preserves_numbers_names_and_polarity():
    assert guard_rewrite(
        "O projeto Meridian reduziu o custo em 42%.",
        "O projeto Meridian reduziu, em 42%, o custo.",
    ).accepted
    assert not guard_rewrite(
        "O projeto Meridian reduziu o custo em 42%.",
        "O projeto Meridian reduziu o custo em 45%.",
    ).accepted
    assert not guard_rewrite("Alice não viajou.", "Alice viajou.").accepted
    assert not guard_rewrite(
        "Alice viajou para Paraty.",
        "Alice e Bob viajaram para Paraty.",
    ).accepted


def test_rewrite_guard_rejects_changed_non_numeric_memory_detail():
    decision = guard_rewrite(
        "Minha bicicleta é azul cobalto.",
        "Minha bicicleta é azul marinho.",
    )
    assert decision.accepted is False
    assert decision.reason == "protected_details_changed"


def test_tool_call_queries_memory_but_remembers_original_user_turn():
    memory = _Memory(MemoryReply(
        "proved", "A viagem foi para Paraty.", True,
        "A viagem foi para Paraty.", "abcd", "fixture",
        ({"source_id": "chat:1"},),
    ))
    provider = _Provider(
        (ProviderEvent("tool.call", {
            "name": "proofray_recall", "arguments": {"question": "Onde foi a viagem?"}}),
         ProviderEvent("completed", {"text": ""})),
        (ProviderEvent("model.delta", {"text": "A viagem foi para Paraty."}),
         ProviderEvent("completed", {"text": "A viagem foi para Paraty."})),
    )
    events = tuple(ChatOrchestrator(
        memory=memory, providers=_Providers(provider)).respond(
            conversation_id="thread", message_id="m1",
            text="Você lembra aonde eu viajei?", mode="tool", provider_id="provider"))
    assert memory.queries == [("thread", "Onde foi a viagem?")]
    assert memory.calls == ["remember", "answer"]
    assert memory.remembered[0]["text"] == "Você lembra aonde eu viajei?"
    assert [event.event for event in events[:4]] == [
        "memory.started", "routing", "verifying", "proof.closed"]
    assert events[-1].payload["authority"] == "proved"
    assert events[-1].payload["memory_consulted"] is True
    assert len(events[-1].payload["query_digest"]) == 64


def test_failed_rewrite_never_replaces_certified_answer():
    memory = _Memory(MemoryReply(
        "proved", "Meridian reduziu o custo em 42%.", True,
        "Meridian reduziu o custo em 42%.", "abcd", "fixture",
        ({"source_id": "doc:1"},),
    ))
    provider = _Provider((
        ProviderEvent("model.delta", {"text": "Meridian reduziu o custo em 99%."}),
        ProviderEvent("completed", {"text": "Meridian reduziu o custo em 99%."}),
    ))
    events = tuple(ChatOrchestrator(
        memory=memory, providers=_Providers(provider)).respond(
            conversation_id="thread", message_id="m1", text="Qual foi a redução?",
            mode="forceNext", provider_id="provider"))
    assert events[-1].payload["text"] == "Meridian reduziu o custo em 42%."
    assert events[-1].payload["rewrite_displayed"] is False


def test_general_model_response_has_no_memory_marker():
    memory = _Memory(MemoryReply("abstention", "", True))
    provider = _Provider((
        ProviderEvent("model.delta", {"text": "Olá!"}),
        ProviderEvent("completed", {"text": "Olá!"}),
    ))
    events = tuple(ChatOrchestrator(
        memory=memory, providers=_Providers(provider)).respond(
            conversation_id="thread", message_id="m1", text="Olá",
            mode="tool", provider_id="provider"))
    assert memory.queries == []
    assert events[-1].payload["authority"] == "model"
    assert events[-1].payload["memory_consulted"] is False


def test_keyword_mode_without_trigger_still_returns_bounded_general_model_text():
    memory = _Memory(MemoryReply("model", "", False))
    provider = _Provider((
        ProviderEvent("model.delta", {"text": "x" * 30_000}),
        ProviderEvent("completed", {"text": "x" * 30_000}),
    ))
    events = tuple(ChatOrchestrator(
        memory=memory, providers=_Providers(provider)).respond(
            conversation_id="thread", message_id="m1", text="Olá",
            mode="keywords", provider_id="provider"))
    assert events[-1].payload["authority"] == "model"
    assert events[-1].payload["memory_consulted"] is False
    assert len(events[-1].payload["text"].encode("utf-8")) == 24_576
    assert events[-1].payload["text_truncated"] is True
