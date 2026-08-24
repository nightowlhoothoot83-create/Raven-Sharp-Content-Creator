import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")

    def test_api_uses_content_creator_product_name(self):
        self.assertIn('FastAPI(title="Raven Sharp Content Creator API")', self.backend)
        self.assertIn('"service": "Raven Sharp Content Creator API"', self.backend)
        self.assertNotIn("Raven Sharp Video Creator API", self.backend)

    def test_password_reset_uses_content_creator_product_name(self):
        self.assertIn("Reset your Raven Sharp Content Creator password", self.backend)
        self.assertNotIn("Reset your Raven Sharp Video Creator password", self.backend)


if __name__ == "__main__":
    unittest.main()
