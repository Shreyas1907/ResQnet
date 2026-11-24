# ResQNet — AI-Powered Disaster Response Platform

ResQNet is an intelligent disaster-response coordination platform that connects citizens, NGOs, and government emergency management agencies with a real-time machine-learning risk assessment engine.

---

## Architecture Overview

| Component | Stack | Port |
|---|---|---|
| **Frontend** | React 18, Vite, TypeScript, Tailwind CSS, shadcn/ui, Leaflet Map | `8080` |
| **Backend** | Django 5, Django REST Framework, SimpleJWT | `8001` |
| **ML Engine** | CatBoost classifiers, scikit-learn isotonic/sigmoid calibration, Joblib | In-process |
| **Database** | SQLite (development) / PostgreSQL (production supported via `DB_ENGINE`) | — |

The Vite dev server proxies `/api/*` requests directly to Django at `http://localhost:8001`.

---

## Getting Started

### 1. Prerequisites
- **Node.js**: v20+
- **Python**: 3.11+

### 2. Windows Quick Start
You can launch both servers with one click using:
```cmd
run.bat
```

### 3. Manual Setup

#### Backend (Django + ML Engine)
```bash
cd backend
python -m venv .venv

# Windows:
.\.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
export DJANGO_DEBUG=1       # On Windows PowerShell: $env:DJANGO_DEBUG="1"
python manage.py migrate
python manage.py runserver 127.0.0.1:8001
```

#### Frontend (React + Vite)
```bash
npm install
npm run dev
```
Open **[http://localhost:8080](http://localhost:8080)** in your browser.

---

## User Roles & Demo Accounts

All accounts use password: `ResQNet@2026!`

| Role | Email | Capabilities |
|---|---|---|
| **Citizen** | `citizen@resqnet.com` | Report incidents, view community alerts and interactive maps |
| **NGO** | `ngo@resqnet.com` | Triage incidents, mobilize relief resources, run disaster simulations (`/ngo/simulation`) |
| **Government** | `government@resqnet.com` | Command center oversight, live ML disaster risk predictions (`/ai-prediction`) |

---

## ML Risk Prediction Models

ResQNet includes 5 trained CatBoost classifiers with probability calibration:

- **Flood**: Features: severity, lat, lon. Calibrated with Isotonic regression.
- **Earthquake**: Features: severity, people_affected, lat, lon.
- **Tsunami**: Features: severity, lat, lon.
- **Drought**: Features: severity, lat, lon. Sigmoid calibrated.
- **Wildfire**: Features: peak FRP severity, lat, lon.

### API Endpoint
`POST /api/ml/predict/`
```json
{
  "disaster_type": "flood",
  "severity": 4,
  "latitude": 12.9716,
  "longitude": 77.5946
}
```
Response:
```json
{
  "disaster_type": "flood",
  "risk_score": 63.2,
  "confidence": 63.2,
  "model_version": "catboost_d3-20261124-1540"
}
```

---

## Running Tests

To run the backend test suite (28 test cases covering auth, permissions, ML inference, and validation):
```bash
cd backend
python manage.py test
```

To run frontend tests:
```bash
npm run test
```
