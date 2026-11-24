from django.test import TestCase
from unittest.mock import patch
import numpy as np

from ml_engine.inference import run_disaster_model


class InferenceTests(TestCase):
    def test_rule_based_fallback(self):
        # use a disaster type for which no model exists
        result = run_disaster_model("nonexistent", {"severity": 3, "people_affected": 20, "latitude": 0, "longitude": 0})
        self.assertIn("risk_score", result)
        self.assertIn("confidence", result)
        # confidence should be zero for fallback
        self.assertEqual(result["confidence"], 0.0)
        self.assertGreaterEqual(result["risk_score"], 0.0)
        self.assertLessEqual(result["risk_score"], 100.0)

    @patch("ml_engine.inference.load_model")
    def test_inference_classification(self, mock_loader):
        class DummyClf:
            def predict_proba(self, X):
                return np.array([[0.2, 0.8]])
        mock_loader.return_value = {"model": DummyClf()}
        result = run_disaster_model("any", {"severity": 1, "people_affected": 1, "latitude": 0, "longitude": 0})
        self.assertAlmostEqual(result["risk_score"], 80.0)
        self.assertAlmostEqual(result["confidence"], 0.8)

    @patch("ml_engine.inference.load_model")
    def test_inference_regression(self, mock_loader):
        class DummyReg:
            def predict(self, X):
                return np.array([42])
        mock_loader.return_value = {"model": DummyReg()}
        result = run_disaster_model("any", {"severity": 1, "people_affected": 1, "latitude": 0, "longitude": 0})
        self.assertAlmostEqual(result["risk_score"], 42.0)
        self.assertEqual(result["confidence"], 1.0)

    def test_strict_mode_raises_when_model_missing(self):
        with self.assertRaises(ValueError):
            run_disaster_model("nonexistent", {"severity": 3, "latitude": 0, "longitude": 0}, strict=True)


class PredictEndpointTests(TestCase):
    URL = "/api/ml/predict/"

    def post(self, **body):
        from rest_framework.test import APIClient

        return APIClient().post(self.URL, body, format="json")

    def test_non_finite_and_negative_people_affected_rejected(self):
        base = {"disaster_type": "flood", "severity": 3, "latitude": 10, "longitude": 10}
        for bad in ("nan", "inf", -5):
            resp = self.post(**base, people_affected=bad)
            self.assertEqual(resp.status_code, 400)

    def test_public_valid_request_returns_scores(self):
        resp = self.post(disaster_type="flood", severity=3, people_affected=100, latitude=28.6, longitude=77.2)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(0 <= resp.data["risk_score"] <= 100)
        self.assertTrue(50 <= resp.data["confidence"] <= 100)

    def test_rejects_unknown_type_and_bad_ranges(self):
        self.assertEqual(self.post(disaster_type="volcano", severity=3, latitude=0, longitude=0).status_code, 400)
        self.assertEqual(self.post(disaster_type="flood", severity=9, latitude=0, longitude=0).status_code, 400)
        self.assertEqual(self.post(disaster_type="flood", severity=3, latitude=95, longitude=0).status_code, 400)

    def test_missing_model_returns_503(self):
        with patch("ml_engine.views.run_disaster_model", side_effect=ValueError("no model")):
            self.assertEqual(self.post(disaster_type="fire", severity=3, latitude=0, longitude=0).status_code, 503)

    def test_fire_model_scores(self):
        resp = self.post(disaster_type="fire", severity=4, latitude=-12.5, longitude=28.5)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(0 <= resp.data["risk_score"] <= 100)
