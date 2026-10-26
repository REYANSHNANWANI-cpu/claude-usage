"""Local network proxy and traffic interceptor for Claude Web & API telemetry."""
import http.server
import json
import logging
import select
import socket
import socketserver
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("claude_sentinel.network_proxy")

DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 8899


def estimate_tokens(text: str) -> int:
    """Accurate BPE heuristic token estimator (~3.8 characters per token)."""
    if not text:
        return 0
    # Average across English, code, and punctuation
    return max(1, int(len(text) / 3.8))


class ProxyRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP/HTTPS proxy handler supporting direct forwarding, token inspection, and quota blocking."""

    # Reference injected by server
    quota_manager = None
    token_monitor = None

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stdout logging."""
        pass

    def do_CONNECT(self) -> None:
        """Handle HTTPS CONNECT tunneling."""
        host, port = self.path.split(":")
        port = int(port)

        # Check if traffic targets Anthropic and quota is breached
        is_anthropic = "anthropic.com" in host.lower() or "claude.ai" in host.lower()
        if is_anthropic and self.quota_manager and self.quota_manager.is_network_blocked():
            logger.warning("Proxy blocking HTTPS CONNECT to %s due to quota breach", host)
            self.send_error(429, "Quota Limit Exceeded by Claude Sentinel")
            return

        try:
            remote_sock = socket.create_connection((host, port), timeout=10)
            self.send_response(200, "Connection Established")
            self.end_headers()
        except Exception as e:
            self.send_error(502, f"Bad Gateway: {str(e)}")
            return

        # Tunnel raw TCP between client and target
        conns = [self.connection, remote_sock]
        self.connection.setblocking(False)
        remote_sock.setblocking(False)

        count = 0
        max_idle = 150  # 15 seconds
        while count < max_idle:
            rlist, _, wlist = select.select(conns, [], conns, 0.1)
            if not rlist:
                count += 1
                continue
            count = 0

            for s in rlist:
                other = remote_sock if s is self.connection else self.connection
                try:
                    data = s.recv(8192)
                    if not data:
                        return
                    other.sendall(data)
                except (socket.error, ConnectionResetError):
                    return

    def do_GET(self) -> None:
        self._handle_http_forwarding("GET")

    def do_POST(self) -> None:
        self._handle_http_forwarding("POST")

    def do_OPTIONS(self) -> None:
        self._handle_http_forwarding("OPTIONS")

    def _handle_http_forwarding(self, method: str) -> None:
        """Forward plain HTTP requests and inspect payloads."""
        url = self.path
        is_anthropic = "anthropic.com" in url or "claude.ai" in url

        # Check quota block
        if is_anthropic and self.quota_manager and self.quota_manager.is_network_blocked():
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            err_body = json.dumps({
                "error": {
                    "type": "quota_exceeded",
                    "message": "Allotted usage quota exceeded. Blocked by Claude Sentinel.",
                }
            }).encode("utf-8")
            self.wfile.write(err_body)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # Inspect and count tokens from payload if directed to Anthropic API
        if is_anthropic and body and self.token_monitor:
            try:
                body_json = json.loads(body.decode("utf-8", errors="ignore"))
                model = body_json.get("model", "claude-3-5-sonnet")
                prompt_text = ""
                for msg in body_json.get("messages", []):
                    c = msg.get("content", "")
                    if isinstance(c, str):
                        prompt_text += c + " "
                    elif isinstance(c, list):
                        for item in c:
                            if isinstance(item, dict) and "text" in item:
                                prompt_text += item["text"] + " "

                in_tokens = estimate_tokens(prompt_text)
                if in_tokens > 0:
                    self.token_monitor.ingest_token_metric(
                        source_env="claude_chat",
                        model_name=model,
                        input_tokens=in_tokens,
                        output_tokens=0,
                        total_tokens=in_tokens,
                        session_id="web-proxy-session",
                    )
            except Exception as e:
                logger.debug("Proxy failed parsing HTTP body: %s", e)

        # Forward request to origin
        try:
            req = urllib.request.Request(url, data=body, method=method)
            for header, value in self.headers.items():
                if header.lower() not in ("host", "content-length"):
                    req.add_header(header, value)

            with urllib.request.urlopen(req, timeout=15) as resp:
                self.send_response(resp.status)
                for h, v in resp.getheaders():
                    self.send_header(h, v)
                self.end_headers()
                resp_data = resp.read()
                self.wfile.write(resp_data)

                # Capture output tokens if response is JSON
                if is_anthropic and resp_data and self.token_monitor:
                    try:
                        resp_json = json.loads(resp_data.decode("utf-8", errors="ignore"))
                        usage = resp_json.get("usage", {})
                        out_tokens = usage.get("output_tokens") or estimate_tokens(str(resp_data))
                        if out_tokens:
                            self.token_monitor.ingest_token_metric(
                                source_env="claude_chat",
                                model_name=resp_json.get("model", "claude-3-5-sonnet"),
                                input_tokens=usage.get("input_tokens", 0),
                                output_tokens=out_tokens,
                                session_id="web-proxy-session",
                            )
                    except Exception:
                        pass

        except Exception as e:
            self.send_error(502, f"Proxy forward error: {str(e)}")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class NetworkProxyDaemon(threading.Thread):
    """Background daemon launching the localized proxy server."""

    def __init__(
        self,
        quota_manager: Any,
        token_monitor: Any,
        host: str = DEFAULT_PROXY_HOST,
        port: int = DEFAULT_PROXY_PORT,
    ):
        super().__init__(name="NetworkProxyDaemonThread", daemon=True)
        self.host = host
        self.port = port
        self.quota_manager = quota_manager
        self.token_monitor = token_monitor
        self.server: Optional[ThreadedTCPServer] = None
        self._running = False

    def run(self) -> None:
        """Start the proxy server listener."""
        ProxyRequestHandler.quota_manager = self.quota_manager
        ProxyRequestHandler.token_monitor = self.token_monitor

        try:
            self.server = ThreadedTCPServer((self.host, self.port), ProxyRequestHandler)
            self._running = True
            logger.info("Local network proxy running on http://%s:%s", self.host, self.port)
            self.server.serve_forever()
        except Exception as e:
            logger.error("Failed starting local network proxy on %s:%s: %s", self.host, self.port, e)

    def stop(self) -> None:
        """Shutdown proxy server."""
        self._running = False
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
