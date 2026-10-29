from __future__ import annotations

import os
import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs

import jwt
import msal

_FLOWS: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()
_JWK: jwt.PyJWKClient | None = None


def allowed_principals() -> set[str]:
    return set(filter(None, os.environ.get("APP_ALLOWED_PRINCIPALS", "").split(",")))


def cookie_value(headers, name: str) -> str | None:
    cookies = SimpleCookie()
    try:
        cookies.load(headers.get("Cookie", ""))
    except CookieError as error:
        raise PermissionError("Cookie no válida.") from error
    return cookies[name].value if name in cookies else None


def config() -> tuple[str, str, str, str]:
    return (
        os.environ["AUTH_TENANT_ID"], os.environ["AUTH_CLIENT_ID"],
        os.environ["AUTH_CLIENT_SECRET"], os.environ["AUTH_SESSION_SECRET"],
    )


def validate_access_token(token: str) -> dict:
    global _JWK
    tenant = os.environ["AUTH_TENANT_ID"]
    app_id = os.environ["AUTH_CLIENT_ID"]
    if _JWK is None:
        _JWK = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
            cache_keys=True, lifespan=300, timeout=15,
        )
    key = _JWK.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token, key.key, algorithms=["RS256"], audience=app_id,
        issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
        options={"require": ["exp", "iat", "iss", "aud", "oid"]},
    )
    if claims["oid"] not in allowed_principals():
        raise PermissionError("Identidad no autorizada.")
    if claims.get("azp") == app_id and claims["oid"] != os.getenv("AUTH_USER_ID"):
        if "Manufacturing.Invoke" not in claims.get("roles", []):
            raise PermissionError("Falta el rol de acceso a esta API.")
    return claims


def authenticated(headers) -> bool:
    bearer = headers.get("Authorization", "")
    if bearer.startswith("Bearer "):
        validate_access_token(bearer[7:])
        return True
    token = cookie_value(headers, "manufacturing_session")
    if not token:
        return False
    claims = jwt.decode(
        token, os.environ["AUTH_SESSION_SECRET"], algorithms=["HS256"],
        audience="manufacturing-app", issuer="manufacturing-app",
        options={"require": ["exp", "iat", "oid"]},
    )
    if claims["oid"] not in allowed_principals():
        raise PermissionError("Sesión no autorizada.")
    return True


def client():
    tenant, app_id, secret, _ = config()
    return msal.ConfidentialClientApplication(
        app_id, client_credential=secret, authority=f"https://login.microsoftonline.com/{tenant}",
    )


def begin_login() -> tuple[str, str]:
    flow_id = secrets.token_urlsafe(32)
    redirect_uri = f"https://{os.environ['APP_PUBLIC_HOST']}/auth/callback"
    flow = client().initiate_auth_code_flow(scopes=[], redirect_uri=redirect_uri)
    if "auth_uri" not in flow:
        raise RuntimeError("Entra no ha generado la petición de inicio de sesión.")
    now = time.time()
    with _LOCK:
        for key, (created, _) in list(_FLOWS.items()):
            if now - created > 600:
                del _FLOWS[key]
        if len(_FLOWS) >= 100:
            raise RuntimeError("Demasiados inicios de sesión pendientes.")
        _FLOWS[flow_id] = (now, flow)
    return flow_id, flow["auth_uri"]


def complete_login(headers, query: str) -> str:
    flow_id = cookie_value(headers, "manufacturing_login")
    if not flow_id:
        raise PermissionError("Falta la cookie de estado de login.")
    with _LOCK:
        saved = _FLOWS.pop(flow_id, None)
    if not saved or time.time() - saved[0] > 600:
        raise PermissionError("El inicio de sesión ha caducado.")
    response = {key: values[0] for key, values in parse_qs(query).items()}
    result = client().acquire_token_by_auth_code_flow(saved[1], response)
    if "error" in result:
        raise PermissionError("Entra no ha completado el inicio de sesión.")
    claims = result.get("id_token_claims") or {}
    if claims.get("oid") not in allowed_principals():
        raise PermissionError("La cuenta no tiene acceso a esta demo.")
    tenant, app_id, _, session_secret = config()
    if claims.get("tid") != tenant or claims.get("aud") != app_id:
        raise PermissionError("Tenant o aplicación de login incorrectos.")
    now = int(time.time())
    return jwt.encode(
        {"oid": claims["oid"], "iat": now, "exp": now + 3600, "aud": "manufacturing-app", "iss": "manufacturing-app"},
        session_secret, algorithm="HS256",
    )
