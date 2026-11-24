from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

import logging
import math

from .inference import run_disaster_model

LOG = logging.getLogger(__name__)
DISASTER_TYPES = {"flood", "earthquake", "tsunami", "drought", "fire"}


class PredictView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        data = request.data
        disaster_type = str(data.get("disaster_type", "")).lower()
        if disaster_type not in DISASTER_TYPES:
            return Response({"error": f"disaster_type must be one of {sorted(DISASTER_TYPES)}"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            features = {
                "severity": float(data["severity"]),
                "people_affected": float(data.get("people_affected", 0)),
                "latitude": float(data["latitude"]),
                "longitude": float(data["longitude"]),
            }
        except (KeyError, TypeError, ValueError):
            return Response({"error": "severity, latitude and longitude are required numbers"}, status=status.HTTP_400_BAD_REQUEST)
        if not all(math.isfinite(v) for v in features.values()):
            return Response({"error": "inputs must be finite numbers"}, status=status.HTTP_400_BAD_REQUEST)
        if not 0 <= features["people_affected"] <= 1e10:
            return Response({"error": "people_affected must be between 0 and 1e10"}, status=status.HTTP_400_BAD_REQUEST)
        if not 1 <= features["severity"] <= 5:
            return Response({"error": "severity must be between 1 and 5"}, status=status.HTTP_400_BAD_REQUEST)
        if not (-90 <= features["latitude"] <= 90 and -180 <= features["longitude"] <= 180):
            return Response({"error": "latitude must be within [-90, 90] and longitude within [-180, 180]"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = run_disaster_model(disaster_type, features, strict=True)
        except Exception:
            return Response({"error": f"No trained model available for '{disaster_type}'"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        probability = result["risk_score"] / 100.0
        LOG.info("predict type=%s input=%s risk=%.1f model=%s", disaster_type, features, result["risk_score"], result.get("model_version"))
        return Response(
            {
                "disaster_type": disaster_type,
                "risk_score": result["risk_score"],
                "confidence": max(probability, 1 - probability) * 100.0,
                "model_version": result.get("model_version"),
            }
        )
