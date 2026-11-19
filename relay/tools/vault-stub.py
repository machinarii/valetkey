#!/usr/bin/env python3
"""Spike stub of a HashiCorp Vault KV endpoint, used to record what fnox's
`vault` provider sends. Logs METHOD PATH header-names(sorted); never values.
  default   : KV v2 at */v1/secret/data/<name>, KV v1 at */v1/secret/<name>
  --v1-only : v2 path answers 404
"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

V1_ONLY = "--v1-only" in sys.argv


class H(BaseHTTPRequestHandler):
    def _send(self, code, body=None):
        raw = json.dumps(body if body is not None else {"errors": []}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        names = ",".join(sorted(k.lower() for k in self.headers.keys()))
        print(f"{self.command} {self.path} {names}", flush=True)
        p = self.path.split("?")[0]
        if p.endswith("/v1/sys/health"):
            return self._send(200, {"initialized": True, "sealed": False})
        if "/v1/secret/data/" in p:
            if V1_ONLY:
                return self._send(404)
            name = p.split("/v1/secret/data/", 1)[1]
            return self._send(200, {"data": {"data": {"value": "stub-" + name},
                                              "metadata": {"version": 1}}})
        if "/v1/secret/" in p:
            name = p.split("/v1/secret/", 1)[1]
            return self._send(200, {"data": {"value": "stub-" + name}})
        self._send(404)

    do_POST = do_PUT = do_LIST = do_GET

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", 8099), H).serve_forever()
