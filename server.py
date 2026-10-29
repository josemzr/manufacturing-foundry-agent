from __future__ import annotations

import argparse
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from azure.core.exceptions import AzureError
from openai import APIError
import jwt
from auth import authenticated, begin_login, complete_login

from domain import ROOT, bootstrap
from foundry import CONFIG, ENDPOINT, run

LOGGER = logging.getLogger("manufacturing")
SLOTS = threading.BoundedSemaphore(2)
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        if urlparse(self.path).path == "/auth/callback":
            LOGGER.info("Callback de autenticación recibido.")
        else:
            super().log_message(format, *args)

    def redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def json(self, status: int, value: dict) -> None:
        self.send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def local_request(self) -> bool:
        port = self.server.server_port
        host = self.headers.get("Host", "")
        allowed = {f"localhost:{port}", f"127.0.0.1:{port}"}
        public_host = os.getenv("APP_PUBLIC_HOST")
        if public_host:
            allowed.add(public_host)
        origin = self.headers.get("Origin")
        origins = {f"http://{item}" for item in allowed}
        if public_host:
            origins.discard(f"http://{public_host}")
            origins.add(f"https://{public_host}")
        if host not in allowed or (origin and origin not in origins):
            self.json(403, {"error": "Esta aplicación solo acepta peticiones de su mismo origen."})
            return False
        return True

    def authorized(self, api: bool) -> bool:
        if os.getenv("REQUIRE_ENTRA_AUTH") != "true":
            return True
        try:
            if authenticated(self.headers):
                return True
        except (jwt.PyJWTError, PermissionError) as error:
            LOGGER.warning("Autenticación rechazada: %s", type(error).__name__)
            self.json(403, {"error": "La identidad o sesión no tiene acceso a esta demo."})
            return False
        if api:
            self.json(401, {"error": "Se requiere inicio de sesión Microsoft Entra ID."})
        else:
            self.redirect("/login")
        return False

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.json(200, {"status": "ready", "cloud_verified": False, "architecture": "hosted_mcp_iq_search"})
            return
        if not self.local_request():
            return
        if path == "/login" and os.getenv("REQUIRE_ENTRA_AUTH") == "true":
            try:
                flow_id, location = begin_login()
                self.redirect(location, f"manufacturing_login={flow_id}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=600")
            except (RuntimeError, AzureError) as error:
                LOGGER.exception("No se ha podido iniciar sesión")
                self.json(502, {"error": "No se ha podido iniciar sesión con Entra."})
            return
        if path == "/auth/callback" and os.getenv("REQUIRE_ENTRA_AUTH") == "true":
            try:
                session = complete_login(self.headers, urlparse(self.path).query)
                self.redirect("/", f"manufacturing_session={session}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=3600")
            except (PermissionError, ValueError) as error:
                LOGGER.warning("Callback de login rechazado: %s", type(error).__name__)
                self.json(403, {"error": "No se ha completado el inicio de sesión autorizado. Vuelve a /login."})
            return
        if not self.authorized(api=path.startswith("/api/")):
            return
        if path == "/api/bootstrap":
            self.json(200, {
                **bootstrap(),
                "foundry": {
                    "project": CONFIG["project"], "agent": CONFIG["agent"],
                    "endpoint": ENDPOINT, "region": CONFIG["region"], "kind": "hosted",
                    "knowledge_base": CONFIG["knowledge_base"], "search_region": CONFIG["search_region"],
                },
            })
        elif path in FILES:
            name, content_type = FILES[path]
            self.send(200, (ROOT / "web" / name).read_bytes(), content_type)
        else:
            self.json(404, {"error": "Ruta no disponible."})

    def do_POST(self) -> None:
        if not self.local_request():
            return
        if not self.authorized(api=True):
            return
        if self.path != "/api/ask":
            self.json(404, {"error": "Ruta no disponible."})
            return
        if self.headers.get_content_type() != "application/json":
            self.json(415, {"error": "Se requiere application/json."})
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or not 0 < int(length) <= 8192:
            self.json(413, {"error": "El cuerpo debe tener entre 1 y 8.192 bytes."})
            return
        try:
            body = json.loads(self.rfile.read(int(length)))
            if not isinstance(body, dict) or set(body) - {"action", "zone_id", "question"}:
                raise ValueError("Formato de petición no permitido.")
            if not isinstance(body.get("action"), str) or not isinstance(body.get("zone_id"), str):
                raise ValueError("Acción y zona son obligatorias.")
            if not isinstance(body.get("question", ""), str) or len(body.get("question", "")) > 1200:
                raise ValueError("La pregunta debe tener como máximo 1.200 caracteres.")
        except (ValueError, UnicodeDecodeError) as error:
            self.json(400, {"error": str(error)})
            return
        if not SLOTS.acquire(blocking=False):
            self.json(429, {"error": "Hay dos consultas en curso. Espera a que termine una."})
            return
        try:
            result = run(body["action"], body["zone_id"], body.get("question", ""))
            self.json(200, result)
        except ValueError as error:
            self.json(400, {"error": str(error)})
        except (APIError, AzureError, RuntimeError) as error:
            LOGGER.exception("Error al consultar el agente de Foundry")
            self.json(502, {
                "error": "Foundry no ha completado la consulta. No se muestra una respuesta simulada.",
                "detail": f"{type(error).__name__}: revisa la sesión CLI y el proyecto o consulta el terminal.",
            })
        finally:
            SLOTS.release()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "0.0.0.0"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.getenv("REQUIRE_ENTRA_AUTH") == "true":
        required = ("AUTH_TENANT_ID", "AUTH_CLIENT_ID", "AUTH_CLIENT_SECRET", "AUTH_SESSION_SECRET", "APP_ALLOWED_PRINCIPALS", "APP_PUBLIC_HOST")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise RuntimeError(f"Falta configuración de autenticación: {', '.join(missing)}")
    elif args.host != "127.0.0.1":
        raise RuntimeError("Para exponer la aplicación fuera de localhost, configura REQUIRE_ENTRA_AUTH=true.")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    LOGGER.info("Servidor en %s:%s; las consultas se delegan al hosted agent.", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Servidor detenido.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
