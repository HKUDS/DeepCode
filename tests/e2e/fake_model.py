"""A scripted OpenAI-compatible model server for terminal tests.

DeepCode reaches it through its real provider code, over real HTTP, exactly as
it would reach a hosted model. Only the model's words are scripted: a test
registers a reply for a prompt, and the server streams it back as
``chat.completion.chunk`` events at a pace the test chooses.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Reasoning:
    text: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict


@dataclass(frozen=True)
class Pause:
    seconds: float


@dataclass
class Reply:
    """One model response: its parts, streamed in order.

    Text and reasoning are split into words, so a client sees many small
    deltas the way it does from a real provider. ``delay`` is the pause
    between deltas.
    """

    parts: list
    delay: float = 0.005


@dataclass
class Script:
    """Replies keyed by a phrase in the latest user message.

    A rule's replies are used in order, one per model request made since
    that user message: the first answers the prompt, the next answers the
    first tool result, and so on. The last reply repeats if the agent asks
    again.
    """

    rules: list[tuple[str, list[Reply]]] = field(default_factory=list)
    fallback: Reply = field(default_factory=lambda: Reply([Text("OK.")]))

    def reply_for(self, messages: list[dict]) -> Reply:
        prompt, answered = _latest_user_text(messages)
        # The phrase written last wins: after an interrupted Turn, DeepCode
        # sends the unanswered prompt and the new one as one user message.
        found = [
            (prompt.rfind(phrase), replies)
            for phrase, replies in self.rules
            if phrase in prompt
        ]
        if not found:
            return self.fallback
        _, replies = max(found, key=lambda item: item[0])
        return replies[min(answered, len(replies) - 1)]


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def _latest_user_text(messages: list[dict]) -> tuple[str, int]:
    """The newest user message, and how many model replies followed it."""

    answered = 0
    for message in reversed(messages):
        if message.get("role") == "user":
            return _content_text(message.get("content")), answered
        if message.get("role") == "assistant":
            answered += 1
    return "", answered


class FakeModel:
    """Serve ``/v1/chat/completions`` on 127.0.0.1 from a :class:`Script`."""

    model_id = "fake-model"

    def __init__(self) -> None:
        self.script = Script()
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fake-model", daemon=True
        )

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def start(self) -> "FakeModel":
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def reset(self) -> None:
        with self._lock:
            self.script = Script()
            self.requests.clear()

    def on(self, phrase: str, *replies: Reply) -> None:
        """Answer prompts containing ``phrase`` with ``replies``."""

        with self._lock:
            self.script.rules.append((phrase, list(replies)))

    def _record(self, body: dict) -> Reply:
        with self._lock:
            self.requests.append(body)
            return self.script.reply_for(body.get("messages") or [])

    def _handler(self):
        model = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):  # keep pytest output clean
                pass

            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    self._json({"object": "list", "data": [{"id": model.model_id}]})
                else:
                    self._json({"error": {"message": "not found"}}, status=404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._json({"error": {"message": "not found"}}, status=404)
                    return
                reply = model._record(body)
                completion_id = f"chatcmpl-{next(model._ids)}"
                if body.get("stream"):
                    self._stream(completion_id, reply)
                else:
                    self._json(_completion(completion_id, reply))

            def _json(self, payload, status=200):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _stream(self, completion_id: str, reply: Reply) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    for delta, finish in _deltas(reply):
                        if isinstance(delta, Pause):
                            time.sleep(delta.seconds)
                            continue
                        self._event(_chunk(completion_id, delta, finish))
                        time.sleep(reply.delay)
                    self._event(
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model.model_id,
                            "choices": [],
                            "usage": _usage(reply),
                        }
                    )
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # The client stopped listening: an interrupted Turn.
                    pass

            def _event(self, payload: dict) -> None:
                self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
                self.wfile.flush()

        return Handler


def _words(text: str) -> list[str]:
    """Split text into deltas that keep every character, spaces included."""

    pieces, current = [], ""
    for character in text:
        current += character
        if character in " \n":
            pieces.append(current)
            current = ""
    if current:
        pieces.append(current)
    return pieces


def _deltas(reply: Reply):
    """Yield ``(delta, finish_reason)`` pairs, ending with a finish."""

    calls = [part for part in reply.parts if isinstance(part, ToolCall)]
    first = True
    for part in reply.parts:
        if isinstance(part, Pause):
            yield part, None
        elif isinstance(part, Reasoning):
            for piece in _words(part.text):
                yield {"reasoning_content": piece}, None
        elif isinstance(part, Text):
            for piece in _words(part.text):
                delta = {"content": piece}
                if first:
                    delta["role"] = "assistant"
                    first = False
                yield delta, None
    for index, call in enumerate(calls):
        yield (
            {
                "tool_calls": [
                    {
                        "index": index,
                        "id": f"call_{index}_{call.name}",
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                ]
            },
            None,
        )
    yield {}, "tool_calls" if calls else "stop"


def _chunk(completion_id: str, delta: dict, finish: str | None) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": FakeModel.model_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _usage(reply: Reply) -> dict:
    words = sum(
        len(part.text.split())
        for part in reply.parts
        if isinstance(part, (Text, Reasoning))
    )
    return {
        "prompt_tokens": 1200,
        "completion_tokens": words,
        "total_tokens": 1200 + words,
    }


def _completion(completion_id: str, reply: Reply) -> dict:
    """The same reply, unstreamed (used by summaries and other side calls)."""

    text = "".join(part.text for part in reply.parts if isinstance(part, Text))
    reasoning = "".join(
        part.text for part in reply.parts if isinstance(part, Reasoning)
    )
    message: dict = {"role": "assistant", "content": text}
    if reasoning:
        message["reasoning_content"] = reasoning
    calls = [part for part in reply.parts if isinstance(part, ToolCall)]
    if calls:
        message["tool_calls"] = [
            {
                "id": f"call_{index}_{call.name}",
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for index, call in enumerate(calls)
        ]
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": FakeModel.model_id,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
        "usage": _usage(reply),
    }
