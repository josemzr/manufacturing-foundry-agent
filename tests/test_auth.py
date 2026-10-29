import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

import auth


class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "AUTH_TENANT_ID": "demo-tenant", "AUTH_CLIENT_ID": "demo-app",
            "AUTH_USER_ID": "demo-user", "APP_ALLOWED_PRINCIPALS": "demo-user,demo-principal",
            "AUTH_SESSION_SECRET": "test-only-session-signing-key-not-used-outside-tests",
        })
        self.environment.start()
        self.jwk = patch.object(auth, "_JWK", Mock())
        mocked = self.jwk.start()
        mocked.get_signing_key_from_jwt.return_value = SimpleNamespace(key=self.key.public_key())

    def tearDown(self):
        self.jwk.stop()
        self.environment.stop()

    def token(self, **changes):
        now = int(time.time())
        claims = {
            "oid": "demo-user", "aud": "demo-app", "iat": now, "exp": now + 60,
            "iss": "https://login.microsoftonline.com/demo-tenant/v2.0", "azp": "demo-client",
        }
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256")

    def test_signed_token_for_allowed_user(self):
        self.assertEqual(auth.validate_access_token(self.token())["oid"], "demo-user")

    def test_wrong_tenant_audience_expiry_and_identity_are_denied(self):
        for changes in (
            {"iss": "https://other-tenant.example"}, {"aud": "other-app"},
            {"exp": int(time.time()) - 60}, {"oid": "unknown-user"},
        ):
            with self.subTest(changes=changes), self.assertRaises((jwt.PyJWTError, PermissionError)):
                auth.validate_access_token(self.token(**changes))

    def test_daemon_requires_scoped_api_role(self):
        with self.assertRaises(PermissionError):
            auth.validate_access_token(self.token(oid="demo-principal", azp="demo-app"))
        self.assertEqual(
            auth.validate_access_token(self.token(oid="demo-principal", azp="demo-app", roles=["Manufacturing.Invoke"]))["oid"],
            "demo-principal",
        )

    def test_unsigned_or_wrong_signature_is_not_accepted(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = jwt.decode(self.token(), options={"verify_signature": False})
        forged = jwt.encode(claims, other, algorithm="RS256")
        with self.assertRaises(jwt.InvalidSignatureError):
            auth.validate_access_token(forged)

    def test_no_session_or_forged_headers_do_not_authenticate(self):
        self.assertFalse(auth.authenticated({}))
        self.assertFalse(auth.authenticated({
            "X-MS-CLIENT-PRINCIPAL-ID": "demo-user", "X-MS-CLIENT-PRINCIPAL-IDP": "aad",
        }))

    def test_signed_session_and_expired_session(self):
        now = int(time.time())
        claims = {"oid": "demo-user", "iat": now, "exp": now + 60, "aud": "manufacturing-app", "iss": "manufacturing-app"}
        token = jwt.encode(claims, os.environ["AUTH_SESSION_SECRET"], algorithm="HS256")
        self.assertTrue(auth.authenticated({"Cookie": "manufacturing_session=" + token}))
        claims["exp"] = now - 60
        token = jwt.encode(claims, os.environ["AUTH_SESSION_SECRET"], algorithm="HS256")
        with self.assertRaises(jwt.ExpiredSignatureError):
            auth.authenticated({"Cookie": "manufacturing_session=" + token})


if __name__ == "__main__":
    unittest.main()
