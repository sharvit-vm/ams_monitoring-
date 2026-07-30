import unittest
from datetime import date, datetime
from decimal import Decimal

from dashboard_state import _json_safe_value, record_execution


class DashboardStateSerializationTests(unittest.TestCase):
    def test_json_safe_value_converts_decimal_and_dates(self):
        payload = {
            "amount": Decimal("12.34"),
            "created_on": datetime(2024, 1, 2, 3, 4, 5),
            "day": date(2024, 1, 2),
            "nested": [{"value": Decimal("1.5")}],
        }

        safe = _json_safe_value(payload)

        self.assertEqual(safe["amount"], 12.34)
        self.assertEqual(safe["created_on"], "2024-01-02T03:04:05")
        self.assertEqual(safe["day"], "2024-01-02")
        self.assertEqual(safe["nested"][0]["value"], 1.5)

    def test_record_execution_stores_json_safe_response(self):
        execution = record_execution({"value": Decimal("3.14")})
        self.assertEqual(execution["response"]["value"], 3.14)


if __name__ == "__main__":
    unittest.main()
