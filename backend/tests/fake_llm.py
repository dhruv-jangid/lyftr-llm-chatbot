"""A tiny fake OpenAI-compatible LLM server used only by the tests.

LiteLLM talks to it exactly like it would talk to a real provider (real HTTP),
so the tests exercise the real LiteLLM router, fallback and cache code paths
without spending any API credits.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CALLS: list[dict] = []          # every request received: {"api_key", "messages"}
FAILING_KEYS: dict[str, int] = {}  # api_key -> HTTP status to return (real failures)
DELAY_SECONDS = 0.2             # makes live calls clearly slower than cache hits


def count_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence default logging
        pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        api_key = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        CALLS.append({"api_key": api_key, "messages": body["messages"]})
        time.sleep(DELAY_SECONDS)

        if api_key in FAILING_KEYS:
            status = FAILING_KEYS[api_key]
            self._send(status, {"error": {"message": f"fake error {status}", "type": "fake", "code": status}})
            return

        user_turns = [m["content"] for m in body["messages"] if m["role"] == "user"]
        reply = f"echo: {user_turns[-1]} (user turns seen: {len(user_turns)})"
        prompt_text = "".join(m["content"] for m in body["messages"])
        self._send(200, {
            "id": "fake-1",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": count_tokens(prompt_text),
                "completion_tokens": count_tokens(reply),
                "total_tokens": count_tokens(prompt_text) + count_tokens(reply),
            },
        })


def start() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]
