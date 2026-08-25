from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from functools import partial
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from typing import Mapping
from datetime import datetime

from .host_record_store import HostAuthorizedSidecarRecordStore
from .connector_manager import ConnectorManager
from .connectors import ConnectorConfig, ConnectorKind, DocumentMapping
from .connectors.host_database import HostDatabaseConnector
from .connectors.registry import create_connector
from .memory_service import ConversationMemoryService
from .orchestration import ChatOrchestrator
from .provider_manager import ProviderManager
from .providers import ChatTurn, ProviderConfig, ProviderKind
from .protocol import BridgeEvent, BridgeRequest, MAX_FRAME_BYTES, ProtocolError, safe_error


_BOOTSTRAP_SCHEMA = "proofray.app.bootstrap.v1"
_BOOTSTRAP_MAX_BYTES = 16 * 1024
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}")
_APP_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")


class HostCallBroker:
    """Synchronous worker-to-async-host request boundary."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._write_lock: asyncio.Lock | None = None
        self._binding_lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop, writer: asyncio.StreamWriter) -> bool:
        with self._binding_lock:
            if self._writer is not None and self._writer is not writer:
                return False
            self._loop = loop
            self._writer = writer
            self._write_lock = asyncio.Lock()
            return True

    def unbind(self, writer: asyncio.StreamWriter) -> None:
        with self._binding_lock:
            if self._writer is writer:
                self._writer = None
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(OSError("host bridge disconnected"))
                self._pending.clear()

    def call(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        with self._binding_lock:
            loop = self._loop
        if loop is None:
            raise OSError("host bridge is unavailable")
        task = asyncio.run_coroutine_threadsafe(self._call(method, payload), loop)
        try:
            return task.result(timeout=35)
        except TimeoutError as error:
            task.cancel()
            raise OSError("host persistence timed out") from error

    async def _call(self, method: str,
                    payload: dict[str, object]) -> dict[str, object]:
        writer = self._writer
        lock = self._write_lock
        if writer is None or lock is None:
            raise OSError("host bridge is unavailable")
        request_id = "host_" + secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            event = BridgeEvent(request_id, "host.request", {
                "method": method, "payload": payload,
            })
            async with lock:
                writer.write(event.encode())
                await writer.drain()
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request: BridgeRequest) -> bool:
        future = self._pending.get(request.request_id)
        if future is None or future.done():
            return False
        ok = request.payload.get("ok")
        payload = request.payload.get("payload", {})
        if ok is True and isinstance(payload, Mapping):
            future.set_result(dict(payload))
        else:
            future.set_exception(OSError("host rejected persistence request"))
        return True


class ProofRayBridgeServer:
    def __init__(self, token: str, *, memory: ConversationMemoryService | None = None,
                 providers: ProviderManager | None = None,
                 profile_name: str = "User", timezone_name: str = "UTC"):
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("bridge token must be high entropy")
        self._token = token
        self._providers = providers or ProviderManager()
        self._host_calls = HostCallBroker()
        self._connectors = ConnectorManager(factory=lambda config: (
            HostDatabaseConnector(config, self._host_calls.call)
            if config.kind in (ConnectorKind.SQLITE, ConnectorKind.DUCKDB)
            else create_connector(config)))
        if memory is None:
            record_store = HostAuthorizedSidecarRecordStore(
                "personal-memory-v1", self._host_calls.call)
            memory = ConversationMemoryService(
                record_store=record_store, profile_name=profile_name,
                timezone_name=timezone_name)
        self._memory = memory
        self._orchestrator = ChatOrchestrator(
            memory=memory, providers=self._providers)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._request_providers: dict[str, str] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="proofray-core")

    async def close(self) -> None:
        for task in tuple(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._executor.shutdown(wait=True, cancel_futures=True)

    async def handle(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        authenticated = False
        try:
            while not reader.at_eof():
                raw = await reader.readline()
                if not raw:
                    break
                if len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
                    break
                try:
                    request = BridgeRequest.decode(raw[:-1])
                except ProtocolError:
                    break
                if not authenticated:
                    authenticated = await self._authenticate(request, writer)
                    if not authenticated:
                        break
                    continue
                if request.method == "request.cancel":
                    await self._cancel(request, writer)
                    continue
                task = asyncio.create_task(self._dispatch(request, writer))
                self._tasks[request.request_id] = task
                task.add_done_callback(
                    lambda _task, key=request.request_id: self._tasks.pop(key, None))
        finally:
            self._host_calls.unbind(writer)
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    async def _authenticate(self, request: BridgeRequest,
                            writer: asyncio.StreamWriter) -> bool:
        supplied = request.payload.get("token")
        token_valid = request.method == "bridge.authenticate" and isinstance(supplied, str) \
            and secrets.compare_digest(supplied, self._token)
        valid = token_valid and self._host_calls.bind(
            asyncio.get_running_loop(), writer)
        event = BridgeEvent(
            request.request_id,
            "authenticated" if valid else "error",
            {} if valid else {"code": "authentication_failed"},
        )
        writer.write(event.encode())
        await writer.drain()
        return valid

    async def _cancel(self, request: BridgeRequest,
                      writer: asyncio.StreamWriter) -> None:
        target = request.payload.get("target_request_id")
        task = self._tasks.get(target) if isinstance(target, str) else None
        if task is not None:
            provider_id = self._request_providers.get(str(target))
            if provider_id is not None:
                self._providers.cancel(provider_id)
            task.cancel()
        writer.write(BridgeEvent(
            request.request_id, "completed", {"cancelled": task is not None}).encode())
        await writer.drain()

    async def _dispatch(self, request: BridgeRequest,
                        writer: asyncio.StreamWriter) -> None:
        try:
            if request.method == "bridge.health":
                events = (BridgeEvent(request.request_id, "completed", {
                    "status": "ok", "core_network_required": False,
                }),)
            elif request.method == "message.send":
                await self._stream_message(request, writer)
                events = ()
            elif request.method == "memory.purge_source":
                source_id = request.payload.get("source_id")
                if not isinstance(source_id, str):
                    raise ValueError("memory source identity is required")
                result = await asyncio.get_running_loop().run_in_executor(
                    self._executor, self._memory.purge_source, source_id)
                events = (BridgeEvent(request.request_id, "completed", result),)
            elif request.method == "memory.warm":
                result = await asyncio.get_running_loop().run_in_executor(
                    self._executor, self._memory.warm)
                events = (BridgeEvent(request.request_id, "completed", result),)
            elif request.method == "memory.source.get":
                fact_id = request.payload.get("fact_id")
                source_id = request.payload.get("source_id")
                result = await asyncio.get_running_loop().run_in_executor(
                    self._executor,
                    partial(
                        self._memory.get_source,
                        fact_id=fact_id,
                        source_id=source_id,
                    ),
                )
                events = (BridgeEvent(request.request_id, "completed", result),)
            elif request.method == "memory.purge_sources":
                values = request.payload.get("source_ids")
                if (not isinstance(values, list) or not values
                        or any(not isinstance(item, str) for item in values)):
                    raise ValueError("memory source identities are required")
                sources = tuple(sorted(set(values)))
                result = await asyncio.get_running_loop().run_in_executor(
                    self._executor, self._memory.purge_sources, sources)
                events = (BridgeEvent(request.request_id, "completed", result),)
            elif request.method == "memory.purge_source_prefix":
                prefix = request.payload.get("prefix")
                if not isinstance(prefix, str):
                    raise ValueError("memory source prefix is required")
                result = await asyncio.get_running_loop().run_in_executor(
                    self._executor, self._memory.purge_source_prefix, prefix)
                events = (BridgeEvent(request.request_id, "completed", result),)
            elif request.method == "import.local_chunk":
                payload = request.payload
                result = await asyncio.get_running_loop().run_in_executor(
                    self._executor,
                    partial(
                        self._memory.import_local_chunk,
                        file_name=payload["file_name"],
                        file_sha256=payload["file_sha256"],
                        byte_start=payload["byte_start"],
                        byte_end=payload["byte_end"],
                        text=payload["text"],
                    ),
                )
                events = (BridgeEvent(request.request_id, "completed", result),)
            elif request.method == "memory.confirm":
                payload = request.payload
                timestamp = datetime.fromisoformat(
                    str(payload["created_at"]).replace("Z", "+00:00"))
                result = await asyncio.get_running_loop().run_in_executor(
                    self._executor,
                    partial(
                        self._memory.confirm_user_observation,
                        conversation_id=payload["conversation_id"],
                        message_id=payload["message_id"],
                        text=payload["text"],
                        timestamp=timestamp,
                        sequence=payload["sequence"],
                    ),
                )
                events = (BridgeEvent(request.request_id, "completed", result),)
            elif request.method == "profile.update":
                profile_name = request.payload.get("profile_name")
                timezone_name = request.payload.get("timezone_name")
                if not isinstance(profile_name, str) or not isinstance(timezone_name, str):
                    raise ValueError("profile update fields are required")
                await asyncio.get_running_loop().run_in_executor(
                    self._executor,
                    partial(
                        self._memory.update_profile,
                        profile_name=profile_name,
                        timezone_name=timezone_name,
                    ),
                )
                events = (BridgeEvent(request.request_id, "completed", {"updated": True}),)
            elif request.method == "host.response":
                events = (BridgeEvent(request.request_id, "completed", {
                    "accepted": self._host_calls.resolve(request),
                }),)
            elif request.method == "provider.configure":
                self._configure_provider(request.payload)
                events = (BridgeEvent(request.request_id, "completed", {"configured": True}),)
            elif request.method == "provider.remove":
                provider_id = request.payload.get("provider_id")
                if not isinstance(provider_id, str):
                    raise ValueError("provider identity is required")
                self._providers.remove(provider_id)
                events = (BridgeEvent(request.request_id, "completed", {"removed": True}),)
            elif request.method in ("provider.models", "provider.test"):
                events = await self._provider_command(request)
            elif request.method.startswith("connector."):
                events = await self._connector_command(request)
            else:
                events = (safe_error(request.request_id, "unknown_method"),)
            for event in events:
                writer.write(event.encode())
                await writer.drain()
        except asyncio.CancelledError:
            writer.write(safe_error(request.request_id, "cancelled").encode())
            with suppress(ConnectionError):
                await writer.drain()
        except (KeyError, TypeError, ValueError):
            writer.write(safe_error(request.request_id, "invalid_request").encode())
            await writer.drain()
        except Exception:
            writer.write(safe_error(request.request_id, "internal_error").encode())
            await writer.drain()

    def _message_arguments(self, request: BridgeRequest) -> dict[str, object]:
        payload = request.payload
        conversation_id = payload["conversation_id"]
        message_id = payload["message_id"]
        text = payload["text"]
        mode = payload["memory_mode"]
        sequence = payload["sequence"]
        timestamp_value = payload["created_at"]
        if not all(isinstance(item, str) for item in (
                conversation_id, message_id, text, mode, timestamp_value)) or \
                not _APP_IDENTIFIER.fullmatch(conversation_id) or \
                not _APP_IDENTIFIER.fullmatch(message_id) or \
                isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("invalid message fields")
        timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
        provider_id = payload.get("provider_id")
        if provider_id is not None and not isinstance(provider_id, str):
            raise ValueError("provider identity must be text")
        provider_secret = payload.get("provider_secret")
        if (provider_secret is not None and
                (not isinstance(provider_secret, str)
                 or len(provider_secret.encode("utf-8")) > 64 * 1024)):
            raise ValueError("provider secret lease must be text")
        raw_turns = payload.get("turns", [])
        raw_keywords = payload.get("keywords")
        if raw_keywords is not None and (
                not isinstance(raw_keywords, list) or len(raw_keywords) > 64
                or sum(len(item.encode("utf-8")) for item in raw_keywords
                       if isinstance(item, str)) > 4096
                or any(not isinstance(item, str) or not item.strip()
                       or len(item.encode("utf-8")) > 128
                       for item in raw_keywords)):
            raise ValueError("keywords must be non-empty strings")
        keywords = None if raw_keywords is None else frozenset(
            item.casefold().strip() for item in raw_keywords)
        if (not isinstance(raw_turns, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("role"), str)
                or not isinstance(item.get("text"), str) for item in raw_turns)):
            raise ValueError("turns must be an array")
        turns = tuple(ChatTurn(item["role"], item["text"]) for item in raw_turns)
        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "text": text,
            "mode": mode,
            "provider_id": provider_id,
            "turns": turns,
            "sequence": sequence,
            "timestamp": timestamp,
            "provider_secret": provider_secret,
            "keywords": keywords,
        }

    async def _stream_message(self, request: BridgeRequest,
                              writer: asyncio.StreamWriter) -> None:
        arguments = self._message_arguments(request)
        provider_id = arguments.get("provider_id")
        if isinstance(provider_id, str):
            self._request_providers[request.request_id] = provider_id
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()

        def produce() -> None:
            try:
                for event in self._orchestrator.respond(**arguments):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as error:
                loop.call_soon_threadsafe(queue.put_nowait, error)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        worker = loop.run_in_executor(self._executor, produce)
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                if isinstance(item, Exception):
                    raise item
                event = item
                writer.write(BridgeEvent(
                    request.request_id, event.event, event.payload).encode())
                await writer.drain()
            await worker
        finally:
            self._request_providers.pop(request.request_id, None)

    def _configure_provider(self, payload: dict[str, object]) -> None:
        provider_id = payload.get("provider_id")
        kind = payload.get("kind")
        model_id = payload.get("model_id")
        endpoint = payload.get("endpoint")
        if not all(isinstance(item, str) for item in (
                provider_id, kind, model_id, endpoint)):
            raise ValueError("provider configuration fields are required")
        self._providers.configure(ProviderConfig(
            provider_id, ProviderKind(kind), model_id, endpoint,
            custom_model=payload.get("custom_model") is True,
            tool_calling_override=(payload.get("tool_calling_override")
                                   if isinstance(payload.get("tool_calling_override"), bool)
                                   else None),
        ))

    async def _provider_command(self, request: BridgeRequest) -> tuple[BridgeEvent, ...]:
        provider_id = request.payload.get("provider_id")
        if not isinstance(provider_id, str):
            raise ValueError("provider identity is required")
        if request.method == "provider.test":
            secret = request.payload.get("secret")
            if (secret is not None and
                    (not isinstance(secret, str)
                     or len(secret.encode("utf-8")) > 64 * 1024)):
                raise ValueError("provider secret lease must be text")
            await asyncio.get_running_loop().run_in_executor(
                self._executor,
                partial(self._providers.test_connection, provider_id, secret=secret))
            return (BridgeEvent(request.request_id, "completed", {"reachable": True}),)
        secret = request.payload.get("secret")
        if (secret is not None and
                (not isinstance(secret, str)
                 or len(secret.encode("utf-8")) > 64 * 1024)):
            raise ValueError("provider secret lease must be text")
        models = await asyncio.get_running_loop().run_in_executor(
            self._executor, partial(self._providers.list_models, provider_id, secret=secret))
        return (BridgeEvent(request.request_id, "completed", {
            "models": [{
                "model_id": item.model_id, "display_name": item.display_name,
                "supports_tools": item.supports_tools,
                "context_tokens": item.context_tokens,
            } for item in models],
        }),)

    async def _connector_command(self, request: BridgeRequest) -> tuple[BridgeEvent, ...]:
        payload = request.payload
        if request.method == "connector.detect":
            endpoint = payload.get("endpoint")
            if not isinstance(endpoint, str):
                raise ValueError("connector endpoint is required")
            result = self._connectors.detect(endpoint)
        elif request.method == "connector.configure":
            connector_id = payload.get("connector_id")
            kind = payload.get("kind")
            endpoint = payload.get("endpoint")
            options = payload.get("options", {})
            if (not isinstance(connector_id, str) or not isinstance(kind, str)
                    or not isinstance(endpoint, str) or not isinstance(options, Mapping)):
                raise ValueError("connector configuration is invalid")
            self._connectors.configure(ConnectorConfig(
                connector_id, ConnectorKind(kind), endpoint, dict(options)))
            result = {"configured": True}
        elif request.method == "connector.remove":
            connector_id = self._connector_id(payload)
            self._connectors.remove(connector_id)
            result = {"removed": True}
        elif request.method == "connector.mapping.suggest":
            namespace = payload.get("namespace")
            if not isinstance(namespace, Mapping):
                raise ValueError("connector namespace is required")
            result = self._connectors.suggest_mapping(namespace)
        else:
            connector_id = self._connector_id(payload)
            secret = payload.get("secret")
            if (secret is not None and
                    (not isinstance(secret, str)
                     or len(secret.encode("utf-8")) > 64 * 1024)):
                raise ValueError("connector secret lease must be text")
            loop = asyncio.get_running_loop()
            if request.method == "connector.test":
                await loop.run_in_executor(
                    self._executor,
                    partial(self._connectors.test_connection, connector_id, secret=secret))
                result = {"reachable": True}
            elif request.method == "connector.namespaces":
                rows = await loop.run_in_executor(
                    self._executor,
                    partial(self._connectors.discover, connector_id, secret=secret))
                result = {"namespaces": list(rows)}
            elif request.method == "connector.sample":
                namespace = payload.get("namespace")
                limit = payload.get("limit", 50)
                if not isinstance(namespace, str) or not isinstance(limit, int):
                    raise ValueError("connector sample is invalid")
                result = await loop.run_in_executor(
                    self._executor,
                    partial(self._connectors.sample, connector_id, namespace,
                            secret=secret, limit=limit))
            elif request.method in ("connector.preview", "connector.sync"):
                mapping_value = payload.get("mapping")
                if not isinstance(mapping_value, Mapping):
                    raise ValueError("connector mapping is required")
                mapping = DocumentMapping(**dict(mapping_value))
                if request.method == "connector.preview":
                    rows = await loop.run_in_executor(
                        self._executor,
                        partial(self._connectors.preview, connector_id, mapping, secret=secret))
                    result = {"documents": list(rows)}
                else:
                    checkpoint = payload.get("checkpoint")
                    if checkpoint is not None and not isinstance(checkpoint, Mapping):
                        raise ValueError("connector checkpoint is invalid")
                    result = await loop.run_in_executor(
                        self._executor,
                        partial(
                            self._connectors.sync, connector_id, mapping,
                            ingest_batch=self._memory.ingest_mapped_documents,
                            secret=secret,
                            checkpoint=None if checkpoint is None else dict(checkpoint)))
            elif request.method == "connector.managed.create":
                authorized = payload.get("authorize_managed_write") is True
                namespace = await loop.run_in_executor(
                    self._executor,
                    partial(self._connectors.create_managed_namespace,
                            connector_id, secret=secret, authorized=authorized))
                result = {"namespace": namespace}
            else:
                return (safe_error(request.request_id, "unknown_method"),)
        return (BridgeEvent(request.request_id, "completed", result),)

    @staticmethod
    def _connector_id(payload: Mapping[str, object]) -> str:
        connector_id = payload.get("connector_id")
        if not isinstance(connector_id, str):
            raise ValueError("connector identity is required")
        return connector_id


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _load_bootstrap(bootstrap_path: Path) -> tuple[str, str, str]:
    token = os.environ.pop("PROOFRAY_APP_TOKEN", None)
    raw = bootstrap_path.read_bytes()
    if not raw or len(raw) > _BOOTSTRAP_MAX_BYTES:
        raise ValueError("bootstrap file is empty or too large")
    try:
        bootstrap = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("bootstrap file is invalid") from None
    if not isinstance(bootstrap, dict) or bootstrap.get("schema") != _BOOTSTRAP_SCHEMA:
        raise ValueError("bootstrap schema is invalid")
    profile_name = bootstrap.get("profile_name", "User")
    timezone_name = bootstrap.get("timezone", "UTC")
    if (not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token)
            or not isinstance(profile_name, str) or not profile_name.strip()
            or len(profile_name.encode("utf-8")) > 256
            or not isinstance(timezone_name, str) or not timezone_name
            or len(timezone_name.encode("utf-8")) > 128
            or any(ord(character) < 32 for character in profile_name + timezone_name)):
        raise ValueError("bootstrap identity and process token are required")
    return token, profile_name.strip(), timezone_name


async def serve(bootstrap_path: Path) -> None:
    token, profile_name, timezone_name = _load_bootstrap(bootstrap_path)
    with suppress(FileNotFoundError):
        bootstrap_path.unlink()
    service = ProofRayBridgeServer(
        token, profile_name=profile_name, timezone_name=timezone_name)
    server = await asyncio.start_server(
        service.handle, host="127.0.0.1", port=0, limit=MAX_FRAME_BYTES + 1)
    sockets = server.sockets or ()
    if len(sockets) != 1:
        raise RuntimeError("bridge failed to bind exactly one loopback socket")
    port = int(sockets[0].getsockname()[1])
    _atomic_json(bootstrap_path.with_name("runtime.json"), {
        "schema": "proofray.app.runtime.v1", "port": port, "pid": os.getpid(),
    })
    try:
        async with server:
            await server.serve_forever()
    finally:
        await service.close()


def main() -> None:
    path = Path(os.environ.get("PROOFRAY_APP_BOOTSTRAP", "bootstrap.json"))
    asyncio.run(serve(path))


__all__ = ["HostCallBroker", "ProofRayBridgeServer", "_load_bootstrap", "serve", "main"]
