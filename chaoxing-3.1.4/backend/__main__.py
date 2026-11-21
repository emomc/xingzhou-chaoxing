"""Run the authenticated headless service with a production WSGI server."""

import argparse
import http.client
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import sys

from backend.security import StateInUse
from backend.service import Service, Settings, create_app
from api.question_images import MAX_IMAGE_REQUEST_BYTES


def running_backend(settings):
    host = {"localhost": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}.get(
        settings.host.lower(), settings.host
    )
    try:
        if not ipaddress.ip_address(host).is_loopback or not settings.port:
            return None
        token = settings.token
        if not token:
            token = (settings.state_dir / "api-token").read_text(encoding="ascii").strip()
        if not 32 <= len(token) <= 512 or any(not 33 <= ord(char) <= 126 for char in token):
            return None
    except (OSError, ValueError):
        return None
    connection = http.client.HTTPConnection(host, settings.port, timeout=2)
    try:
        # Verify the public identity before sending an operator token to a local port.
        for route, headers in (
            ("/healthz", {}),
            ("/v1/health", {"Authorization": "Bearer " + token}),
        ):
            connection.request("GET", route, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                return None
            raw = response.read(16385)
            if len(raw) > 16384:
                return None
            health = json.loads(raw)
            if not isinstance(health, dict):
                return None
            if route == "/healthz" and health.get("service") != "chaoxing-backend":
                return None
        if health.get("version") != 1 or health.get("status") not in {
            "ok", "starting", "degraded",
        }:
            return None
        address = f"[{host}]" if ":" in host else host
        return f"http://{address}:{settings.port}", health
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("CHAOXING_SERVICE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CHAOXING_SERVICE_PORT", "3100")))
    parser.add_argument("--state-dir", type=Path, default=Path(
        os.getenv("CHAOXING_STATE_DIR", str(Path(__file__).resolve().parents[1] / ".backend-state"))
    ))
    parser.add_argument("--max-workers", type=int, default=int(os.getenv("CHAOXING_MAX_WORKERS", "30")))
    parser.add_argument("--ai-workers", type=int, default=int(os.getenv("CHAOXING_AI_WORKERS", "1")))
    parser.add_argument("--init-only", action="store_true")
    args = parser.parse_args()
    settings = Settings(
        state_dir=args.state_dir, host=args.host, port=args.port,
        max_workers=args.max_workers, ai_workers=args.ai_workers,
        ai_wait_timeout=int(os.getenv("CHAOXING_AI_WAIT_TIMEOUT", "3600")),
        max_runtime=int(os.getenv("CHAOXING_MAX_RUNTIME", "259200")),
        token=os.getenv("CHAOXING_SERVICE_TOKEN") or None,
        ai_endpoint=os.getenv("CHAOXING_AI_ENDPOINT", "http://127.0.0.1:3001/v1"),
        ai_model=os.getenv("CHAOXING_AI_MODEL", "deepseek-search"),
        ai_model_fallbacks=os.getenv("CHAOXING_AI_MODEL_FALLBACKS", "[]"),
        ai_vision_endpoint=os.getenv("CHAOXING_AI_VISION_ENDPOINT", ""),
        ai_vision_model=os.getenv("CHAOXING_AI_VISION_MODEL", "auto"),
        ai_vision_model_fallbacks=os.getenv("CHAOXING_AI_VISION_MODEL_FALLBACKS", "[]"),
        ai_vision_key_env=os.getenv("CHAOXING_AI_VISION_KEY_ENV", "WEBAI_VISION_API_KEY"),
        ai_api_endpoint=os.getenv("CHAOXING_AI_API_ENDPOINT", ""),
        ai_api_model=os.getenv("CHAOXING_AI_API_MODEL", ""),
        ai_api_model_fallbacks=os.getenv("CHAOXING_AI_API_MODEL_FALLBACKS", "[]"),
        ai_api_vision_model=os.getenv("CHAOXING_AI_API_VISION_MODEL", ""),
        ai_api_vision_model_fallbacks=os.getenv("CHAOXING_AI_API_VISION_MODEL_FALLBACKS", "[]"),
        ai_api_key_env=os.getenv("CHAOXING_AI_API_KEY_ENV", "CHAOXING_AI_API_KEY"),
    )
    try:
        service = Service(settings)
    except StateInUse:
        running = running_backend(settings)
        if running:
            url, health = running
            print("Backend is already running; using the existing instance.")
            print(f"Headless API: {url}")
            print(f"Account limit: {health.get('max_workers')}; AI concurrency: {health.get('ai_workers')}")
            print("No duplicate backend was started. Existing settings and jobs were left unchanged.")
            if health["status"] == "ok":
                return 0
            print("The existing backend is not ready; check its logs. It was not restarted.", file=sys.stderr)
        else:
            print("Backend startup skipped: this state directory is already in use.", file=sys.stderr)
            print("A matching backend could not be verified at the requested local address.", file=sys.stderr)
            print("Keep service.lock intact. Check the existing instance, port, and API token.", file=sys.stderr)
        return 2
    handler = RotatingFileHandler(
        settings.state_dir / "service.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    server = None
    try:
        if args.init_only:
            print(f"Initialized private state: {settings.state_dir}")
            print("API token: api-token; encryption key: credentials.key. Keep both private.")
            return 0
        from waitress import create_server
        server = create_server(
            create_app(service), host=settings.host, port=settings.port, threads=8,
            connection_limit=256, channel_timeout=30,
            max_request_body_size=MAX_IMAGE_REQUEST_BYTES,
            clear_untrusted_proxy_headers=True,
        )
        internal_host = "[::1]" if ":" in settings.host else "127.0.0.1"
        service.start(f"http://{internal_host}:{server.effective_port}")
        print(f"Headless API: http://{settings.host}:{server.effective_port}", flush=True)
        print(f"Private state: {settings.state_dir}", flush=True)
        print(f"Account limit: {settings.max_workers}; AI concurrency: {settings.ai_workers}", flush=True)
        print("No accounts or course jobs are started until explicitly created.", flush=True)
        print("Press Ctrl+C to stop this foreground instance.", flush=True)

        def stop(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        try:
            server.run()
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        if server is not None:
            server.close()
        service.close()
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
