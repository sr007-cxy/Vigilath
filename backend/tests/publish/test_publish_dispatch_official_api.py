from types import SimpleNamespace
import unittest
from unittest.mock import patch

from geo.api import publish_dispatch as dispatch


class PublishDispatchOfficialApiTests(unittest.TestCase):
    def test_cookie_account_does_not_decrypt_official_credentials(self):
        acct = SimpleNamespace(auth_type="plugin_cookie", credentials_enc="unused")

        with patch.object(dispatch, "decrypt_required") as decrypt:
            self.assertIsNone(dispatch._official_api_credentials(acct))

        decrypt.assert_not_called()

    def test_official_api_account_decrypts_appid_and_secret(self):
        acct = SimpleNamespace(auth_type="official_api", credentials_enc="enc")

        with patch.object(dispatch, "decrypt_required",
                          return_value='{"appid":" wx123 ","appsecret":" secret "}'):
            result = dispatch._official_api_credentials(acct)

        self.assertEqual(result, {"appid": "wx123", "appsecret": "secret"})


if __name__ == "__main__":
    unittest.main()
