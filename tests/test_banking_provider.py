import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.banking import EnableBanking


class ProviderTests(unittest.TestCase):
    def test_jwt_claims_and_signature(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        with patch("app.services.banking.time.time", return_value=2000000000):
            token = EnableBanking("application-id", private).token()
        self.assertEqual(jwt.get_unverified_header(token)["kid"], "application-id")
        claims = jwt.decode(token, key.public_key(), algorithms=["RS256"],
                            audience="api.enablebanking.com", issuer="enablebanking.com",
                            options={"verify_iat": False})
        self.assertEqual(claims["iat"], 2000000000)
        self.assertEqual(claims["exp"] - claims["iat"], 3600)

    def test_pagination_and_repeated_key(self):
        provider = EnableBanking("id", b"key")
        with patch.object(provider, "request", side_effect=[
            {"transactions": [{"transaction_id": "1"}], "continuation_key": "next"},
            {"transactions": [{"transaction_id": "2"}]},
        ]) as request:
            self.assertEqual(len(list(provider.transactions("account", "2026-01-01"))), 2)
            self.assertEqual(request.call_args.kwargs["params"]["continuation_key"], "next")
        with patch.object(provider, "request", return_value={"continuation_key": "same"}):
            with self.assertRaises(ValueError):
                list(provider.transactions("account", "2026-01-01"))


if __name__ == "__main__":
    unittest.main()
