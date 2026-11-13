"""Fake Google and Apple ID tokens, signed with a key the tests make up.

The `provider_keys` fixture (accounts/tests/conftest.py) points accounts/providers.py at
that key instead of the providers' published key sets, so no test goes to the network.
"""

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

GOOGLE_WEB_CLIENT_ID = "web-client.apps.googleusercontent.com"
GOOGLE_IOS_CLIENT_ID = "ios-client.apps.googleusercontent.com"
APPLE_BUNDLE_ID = "hr.taskbeavo.app"
APPLE_SERVICES_ID = "hr.taskbeavo.web"

ISSUERS = {"google": "https://accounts.google.com", "apple": "https://appleid.apple.com"}
AUDIENCES = {"google": GOOGLE_WEB_CLIENT_ID, "apple": APPLE_BUNDLE_ID}

PROVIDER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def id_token(
    provider: str = "google",
    *,
    nonce: str | None,
    key: rsa.RSAPrivateKey = PROVIDER_KEY,
    **claims: Any,
) -> str:
    """An ID token as `provider` would sign it; `claims` override or (as None) drop claims."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": ISSUERS[provider],
        "aud": AUDIENCES[provider],
        "sub": f"{provider}-subject-1",
        "iat": now,
        "exp": now + 600,
        "nonce": nonce,
        "email": "ana.kovac@mail.example.com",
        "email_verified": True,
    }
    if provider == "google":
        payload |= {"given_name": "Ana", "family_name": "Kovač"}
    else:
        # Apple sends booleans as strings.
        payload["email_verified"] = "true"
    payload.update(claims)
    payload = {name: value for name, value in payload.items() if value is not None}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test-key"})
