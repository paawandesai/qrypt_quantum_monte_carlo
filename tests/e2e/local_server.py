"""Local, test-only server for the console smoke test.

Serves the real api handler (App) over HTTP with DynamoDB/SQS mocked by moto,
the test-only MockQrypt, and an in-process worker that drains the queue after
each submit or replay. Never deployed. Usage:

  python tests/e2e/local_server.py 8765
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from conftest import TEST_ENV, FakeSecrets, create_resources, drain_queue  # noqa: E402
from fakes.mock_qrypt import MockQrypt  # noqa: E402
from moto import mock_aws  # noqa: E402

KEY = "local-smoke-bearer"


def main(port: int) -> None:
    for k, v in TEST_ENV.items():
        os.environ[k] = v
    os.environ["ENABLE_FAULT_INJECTION"] = "true"
    with mock_aws():
        aws = create_resources()
        from qmc.handlers import worker
        from qmc.handlers.api import App
        from qmc.store import Store

        store = Store()
        worker._STORE = store
        app = App(
            env=dict(os.environ),
            entropy=MockQrypt(request_id=None),
            secrets=FakeSecrets({TEST_ENV["API_KEY_PARAM"]: KEY}),
        )

        class H(BaseHTTPRequestHandler):
            def _do(self, method):
                u = urlsplit(self.path)
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n).decode() if n else None
                event = {
                    "rawPath": u.path,
                    "requestContext": {"http": {"method": method}},
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "queryStringParameters": dict(parse_qsl(u.query)) or None,
                    "body": body,
                    "isBase64Encoded": False,
                }
                resp = app.handle(event, None)
                if method == "POST" and resp["statusCode"] == 202:
                    for _ in range(4):  # retries so a fault-injected shard reaches its last attempt
                        recs = drain_queue(aws["sqs"], aws["queue_url"])
                        for r in recs:
                            r["attributes"]["ApproximateReceiveCount"] = "3"
                        if recs:
                            worker.handler({"Records": recs}, None)
                data = resp["body"].encode()
                self.send_response(resp["statusCode"])
                for k, v in resp["headers"].items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

            def log_message(self, *a):
                pass

        print(json.dumps({"listening": port}), flush=True)
        ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
