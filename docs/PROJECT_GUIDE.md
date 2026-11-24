# ResQNet — Project Guide

How the whole system works: architecture, authentication, the five disaster models, data, API, frontend, tests, and what is real versus mocked. Written from the code as it stands on branch `feature/ml-predict-endpoint`.

## 1. What ResQNet is

A disaster-response web app with three kinds of users (citizen, NGO, government) and a machine-learning risk engine. A user signs in, lands on a role-specific dashboard, and the AI-prediction pages return a risk score for a disaster type, severity and location using models trained on real datasets.

| Layer | Stack | Port |
|---|---|---|
| Frontend | Vite + React + TypeScript, Tailwind, shadcn/ui, React Router, TanStack Query, Leaflet map | 8080 |
| Backend | Django + Django REST Framework + simplejwt | 8001 |
| Database | SQLite (`backend/db.sqlite3`); PostgreSQL when `DB_ENGINE=postgres` | — |
| ML | CatBoost classifiers, scikit-learn calibration, joblib artifacts | in-process |

The Vite dev server proxies every `/api/*` request to `http://localhost:8001` (`vite.config.ts`), so the browser only talks to `localhost:8080`.

## 2. Running it locally

```bash
# backend (from backend/)
export DJANGO_DEBUG=1
.venv/bin/python manage.py migrate        # first time and after pulling; creates token_blacklist tables
.venv/bin/python manage.py runserver 8001

# frontend (from the repo root)
npm run dev                               # http://localhost:8080
```

Environment variables (`backend/config/settings.py`):

| Variable | Meaning |
|---|---|
| `DJANGO_DEBUG=1` | Dev mode. Without it the server refuses to start unless a secret key is set |
| `DJANGO_SECRET_KEY` | Required when `DEBUG` is off. Also used as the JWT signing key |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts; `*` in debug |
| `DB_ENGINE=postgres` + `DB_NAME/USER/PASSWORD/HOST/PORT` | Switch from SQLite to PostgreSQL |
| `CORS_ALLOWED_ORIGINS` | Comma-separated origins; all origins allowed only in debug when this is empty |
| `VITE_API_BASE` (frontend) | Prefix for API calls; empty means same origin via the proxy |
| `VITE_MAP_TILE_URL` (frontend) | Custom map tile URL. When set, the Leaflet attribution text is blank |

## 3. Repository layout

```
ResQnet-main/
├── src/                        frontend
│   ├── App.tsx                 routes + ProtectedRoute + LoginRedirect
│   ├── lib/authContext.tsx     session, tokens, preferences, authFetch
│   ├── lib/disasterDefaults.ts API_BASE and default coordinates per disaster type
│   ├── lib/mockData.ts         sample incidents/NGOs/camps/activity (dashboards use these)
│   ├── pages/                  Login, PublicDashboard, NGODashboard, NgoSimulationPage,
│   │                           GovernmentDashboard, GovPredictionPage, NotFound
│   └── components/             PredictionForm, NgoSimulationForm, RiskResultCard,
│                               NgoResultPanel, dashboard/*, ui/*
└── backend/
    ├── config/                 settings.py, urls.py
    ├── users/                  User model, preferences, auth views, serializers, permissions
    ├── incidents/              Incident model and the create endpoint
    ├── ngos/                   placeholder app
    ├── ml_engine/              train.py, model_loader.py, inference.py, views.py (predict API)
    ├── models/<type>/          trained artifacts (git-ignored)
    └── data/                   training datasets (git-ignored)
```

## 4. Users, roles and authentication

### 4.1 Data model (`backend/users/models.py`)
- **`User`** extends Django's `AbstractUser` (`AUTH_USER_MODEL = 'users.User'`). Extra fields: `role` (`citizen`, `ngo`, `government`) and `reliability_score` (default 100).
- **`UserPreferences`** is one-to-one with `User`: `theme` (system/light/dark), `language`, `default_disaster_type`, `notify_email`, `notify_push`, `alert_radius_km` (1–500), `home_latitude`/`home_longitude` (must be set together, finite, in range), `updated_at`.

### 4.2 Endpoints (all under `/api/auth/`)

| Method + path | Purpose | Notes |
|---|---|---|
| `POST register/` | Create an account | **Citizen only.** Needs `email`, `password`, `password_confirm`, `role: "citizen"`; `first_name` optional; `username` defaults to the lower-cased email. Password strength is checked by Django validators. Duplicate username/email (case-insensitive) → 400 |
| `POST login/` | Get tokens | Accepts `email` (or `username`) + `password`, optional `role`. If `role` is sent and does not match the account, the answer is the same 401 as a wrong password. Returns `access`, `refresh`, `role`, `username`, `user_id` |
| `POST logout/` | Blacklist the refresh token | Works without an access token; garbage input → 400; success → 205 |
| `POST refresh/` | Rotate tokens | Old refresh token is blacklisted after use |
| `GET/PATCH me/` | Current user | Only `first_name` is editable; `role`, `email`, `username` are read-only |
| `GET/PATCH me/preferences/` | The user's own preferences | Created on first access; a user can only ever reach their own row |

### 4.3 Security behaviour
- **Self-signup is limited to citizens.** `RegisterSerializer.validate` rejects `ngo`, `government` and anything else with 400. NGO and government accounts are issued by an administrator: Django admin, `createsuperuser`, or `User.objects.create_user(..., role='ngo')` in `manage.py shell`.
- **No user enumeration on login.** Unknown email, wrong password and role mismatch all return the same message ("Invalid username or password…"). For unknown emails a password hash is still computed so timing does not leak existence.
- **Tokens:** access lifetime 60 min, refresh lifetime 1 day, refresh rotation with blacklist (`rest_framework_simplejwt.token_blacklist`), HS256, signing key = `SECRET_KEY`. Role is embedded in the token as a claim.
- **Throttling:** login 10/min, register 10/min, refresh 60/min, anonymous 60/min (DRF `ScopedRateThrottle` / `AnonRateThrottle`). Counters live in the local-memory cache, so they are per-process.
- **Default permission** for every DRF view is `IsAuthenticated`; public views opt out explicitly. Role guards are `IsCitizen`, `IsNGO`, `IsGovernment` in `users/permissions.py`.

### 4.4 Frontend session (`src/lib/authContext.tsx`)
- The **access token lives only in memory** (`accessRef`). The **refresh token is in `localStorage`** under `resqnet.refresh`.
- On page load the app refreshes the access token, then loads `/me/` and `/me/preferences/`. While that happens `loading` is true and routes render nothing.
- `authFetch(path, init)` adds the bearer token, and on a 401 refreshes once (single-flight, so parallel calls share one refresh) and retries once. If the retry is still 401 the session is cleared.
- An `epoch` counter is bumped on every login/logout. Any response that arrives after the epoch changed is discarded, which stops a slow request from re-creating a session that was just signed out.
- Logout clears local state first, then calls `/logout/` to blacklist the refresh token (errors ignored).
- The saved theme is applied to `<html>` (`dark` class); `system` follows the OS setting.

### 4.5 Routes (`src/App.tsx`)

| Path | Who | Page |
|---|---|---|
| `/` | everyone | Login/signup; a signed-in user is redirected to their dashboard |
| `/citizen` | citizen | `PublicDashboard` |
| `/ngo` | ngo | `NGODashboard` |
| `/ngo/simulation` | ngo | `NgoSimulationPage` (risk simulation) |
| `/command` | government | `GovernmentDashboard` |
| `/ai-prediction` | government | `GovPredictionPage` (risk prediction) |
| `*` | everyone | `NotFound` |

`ProtectedRoute` sends unauthenticated users, or users with the wrong role, back to `/`. There is no `/login` route; the login page is `/`.

### 4.6 Login page (`src/pages/Login.tsx`)
Role cards (citizen / NGO / government) plus email and password. In **sign-in** mode all three roles are selectable and the chosen role must match the account. In **sign-up** mode the role is forced to citizen and the NGO and Government cards are disabled (tooltip: "Issued by an administrator"). Errors from the API are shown inline.

## 5. The prediction system

### 5.1 What a prediction means
Each model answers one question: **"Given a disaster of this type, at this location and this severity, how likely is it to be a high-impact event?"** The output `risk_score` is a calibrated probability × 100. It is not a forecast that a disaster will occur; the models rate the seriousness of an event given its type, place and severity. The definition of "high-impact" differs per type (section 5.3).

### 5.2 Request and response

`POST /api/ml/predict/` (no login required; anonymous throttle 60/min)

```json
{ "disaster_type": "fire", "severity": 4, "latitude": -12.5, "longitude": 28.5, "people_affected": 0 }
```

| Field | Rule |
|---|---|
| `disaster_type` | one of `flood`, `earthquake`, `tsunami`, `drought`, `fire` (case-insensitive) |
| `severity` | number, 1–5 |
| `latitude` / `longitude` | numbers within [-90, 90] / [-180, 180] |
| `people_affected` | optional, 0 to 1e10; only the earthquake model uses it |

All values must be finite (NaN/Infinity are rejected). Any violation returns 400 with `{"error": "..."}`.

Response:
```json
{ "disaster_type": "fire", "risk_score": 63.2, "confidence": 63.2, "model_version": "catboost_d6-20261123-1015" }
```
- `risk_score` — calibrated probability of the high-impact class, ×100.
- `confidence` — `max(p, 1-p) × 100`, i.e. how far the model is from a coin flip. It is **not** the model's accuracy.
- `model_version` — best-model name plus the model file's modification time (UTC), read from `meta.json`.
- If the model files are missing the endpoint returns **503** and does not invent a number (`strict=True`).

Each call is logged with type, inputs, risk and model version.

### 5.3 The five models

All are CatBoost classifiers wrapped in probability calibration. Metrics below are from each model's `meta.json`.

| Type | Features | Label (what "high impact" means) | Rows / positives | Held-out ROC-AUC | Spatial-CV ROC-AUC | Best model, calibration |
|---|---|---|---|---|---|---|
| Flood | severity, lat, lon | GDACS alert level Orange or Red (rows with severity > 0) | 2,848 / 410 | 0.826 | 0.827 | CatBoost depth 3, isotonic |
| Earthquake | severity, people_affected, lat, lon | EM-DAT total deaths ≥ 10 | 457 / 143 | 0.893 | 0.785 | CatBoost depth 3, isotonic |
| Tsunami | severity, lat, lon | NOAA deaths order ≥ 2 or damage order ≥ 3; events from 1900 | 500 / 129 | 0.752 | 0.698 | CatBoost depth 3, isotonic |
| Drought | severity, lat, lon | GDACS alert level Orange or Red | 261 / 43 | 0.746 | 0.720 | CatBoost depth 3, sigmoid |
| Wildfire | severity, lat, lon | 0.5° grid cell with fire detected on ≥ 10 distinct days (a persistence proxy) | 26,736 / 11,920 | 0.913 | 0.882 | CatBoost depth 6, isotonic |

**How to read the numbers**
- **ROC-AUC** is 0.5 for guessing and 1.0 for perfect ranking. **Held-out** uses a random 20% test split. **Spatial-CV** holds out whole 15°×15° map blocks, so it measures how well the model works in regions it has not seen; it is the more honest number and is lower for every type.
- **Drought is weak**: only 9 positive cases in the held-out set, so its score is noisy. **Earthquake** and **tsunami** are moderate and drop noticeably under spatial CV.
- **The wildfire label is a proxy, not a measured outcome.** NASA FIRMS records detections, not damage. A cell that burns on ≥ 10 distinct days is treated as a persistent/serious fire zone. The result says "this place and intensity look like persistent-fire conditions", not "people will be harmed".
- With only three features (severity, latitude, longitude) the models mostly learn geography and severity bands. They do not use weather, terrain, population density or time.

### 5.4 Training data

| Type | Source | Files in `backend/data/` |
|---|---|---|
| Flood | GDACS event feed (GeoJSON) | `flood/gdacs_FL.json` |
| Drought | GDACS event feed | `drought/gdacs_DR.json` |
| Earthquake | EM-DAT public export (Excel); magnitude, location, deaths, affected | `public_emdat_custom_request_*.xlsx` |
| Tsunami | NOAA tsunami event catalogue | `tsunami/noaa_tsunami_events.json` |
| Wildfire | NASA FIRMS active-fire CSVs (MODIS and VIIRS, 2024-11-01 to 2025-01-13), filtered to nominal/high confidence and FRP > 0 | `wildfire/fire_nrt_*.csv` |

Other files in `data/` (`fire/gdacs_WF.json`, `forest+fires/`, `modis_2023_India.csv`, `spei03.nc`) are not used by the current training code.

**Severity (1–5).** The app's slider is 1–5, so each source's physical measure is converted to quintile bins: flood and drought use GDACS severity values, earthquake uses magnitude, tsunami uses NOAA's `tsIntensity` bands, wildfire uses the cell's peak daily fire radiative power. The bin edges are saved in `meta.json` for flood, drought, earthquake and fire.

**Wildfire gridding.** Detections are snapped to 0.5° cells; per cell the code counts distinct active days and the peak daily total FRP. Positive = active days ≥ 10.

### 5.5 Training procedure (`backend/ml_engine/train.py`)
Run from `backend/`: `python -m ml_engine.train fire flood` (no arguments trains all five).
1. Load and label the data; skip a type with fewer than 20 positives.
2. Split 80/20, stratified, seed 42.
3. Cross-validate (5-fold) every candidate on the training part: CatBoost depth 3/4/6 with class-balanced weights, plus LightGBM, XGBoost and TabPFN when they import. In this environment LightGBM and XGBoost fail to load (missing native libraries) and TabPFN is absent, so only the three CatBoost variants compete.
4. Pick the best by cross-validated ROC-AUC.
5. Fit it with probability calibration (isotonic when there are ≥ 100 positives, otherwise sigmoid; 3-fold) and score it on the held-out 20%.
6. Run spatial-block cross-validation (15° blocks, grouped 5-fold).
7. Refit on **all** rows and save `model.joblib`, `features.pkl` and `meta.json` into `backend/models/<type>/`.

The tests assert minimum scores for the trained models, including `test_fire_model_scores`.

### 5.6 Serving a prediction (`model_loader.py`, `inference.py`)
1. `load_model(type)` finds `backend/models/<type>/`, loads the first `.joblib`/`.pkl` that is not the scaler or feature list, optionally loads `scaler.pkl` and `features.pkl`, builds `model_version`, and caches everything in memory. A server restart is needed after retraining.
2. `run_disaster_model` builds a one-row table in the model's feature order. In strict mode (the predict endpoint) a missing feature raises an error.
3. It calls `predict_proba`; the positive-class probability × 100 is the `risk_score`.
4. **Fallback:** outside strict mode, if no model is available or inference fails, a rule-based score is used: `severity × 10 + people_affected × 0.1`, clamped to 0–100, with `confidence 0` and `source: "rule_based"`. The incident endpoint can use this fallback; the predict endpoint never does.

## 6. Incidents and NGO assignment

`POST /api/incidents/create/` (citizen only; NGO and government get 403)

- Body: `disaster_type`, `severity`, `people_affected`, `latitude`, `longitude`, `description`.
- The incident is saved with the logged-in citizen as `reported_by`.
- The ML engine scores it, then `priority_score = risk_score × 0.6 + severity × 5 + people_affected × 2`, plus 15 if the description contains "urgent", "trapped" or "critical", clamped to 0–100.
- The incident is auto-assigned to the NGO user with the fewest assigned incidents.
- Response includes `priority_score`, `risk_score`, `risk_source` (`model` or `rule_based`), `model_version` and the assigned NGO's username.

This runs in non-strict mode, so a disaster type with no trained model (for example a type outside the five) falls back to the rule-based score instead of failing.

## 7. Frontend pages: what is real and what is sample data

**Real, backed by the API**
- Sign-in, sign-up, sign-out, session restore, saved preferences and theme.
- `GovPredictionPage` (`/ai-prediction`, `PredictionForm`) and `NgoSimulationPage` (`/ngo/simulation`, `NgoSimulationForm`). Both POST to `/api/ml/predict/`, offer Flood, Earthquake, Tsunami, Drought and Wildfire, fill sensible default coordinates per type (`disasterDefaults.ts`), and show the score on `RiskResultCard` / `NgoResultPanel`.

**Sample data only**
- `PublicDashboard`, `NGODashboard` and `GovernmentDashboard`, with `IncidentTable`, `IncidentMap`, `ActivityFeed`, counters and notifications, read from `src/lib/mockData.ts`. The "7 active incidents / 4 NGOs online / 99.97% uptime" figures on the login page are static text.
- The frontend does **not yet call** `/api/incidents/create/`, so reporting an incident in the UI does not reach the database. The endpoint works and is tested through the API.
- `mockData.ts` uses disaster types (Cyclone, Landslide) that have no trained model.

## 8. Tests and checks

| Check | Command | Current result |
|---|---|---|
| Backend tests | `cd backend && DJANGO_DEBUG=1 .venv/bin/python manage.py test` | 28 tests pass |
| Type check | `npx tsc --noEmit -p tsconfig.app.json` | clean |
| Production build | `npx vite build` | succeeds |

Backend coverage: registration (including citizen-only enforcement), login by username/email, wrong password, role mismatch, inactive user, role permissions on incident creation, `/me/` editing limits, preferences defaults/validation/isolation, logout blacklisting, refresh rotation, inference fallback and classification paths, the predict endpoint's validation, and the trained-model score floors.

## 9. Known limitations and operational notes

**Model limits**
- Inputs are only type, severity and location, so risk is not weather- or population-aware.
- Drought is based on 261 events and is the least reliable. Earthquake and tsunami lose accuracy in unseen regions.
- Wildfire labels are a persistence proxy over about 10 weeks of detections.
- Models are loaded once and cached; restart the server after retraining.

**Before exposing this publicly**
- Set `DJANGO_SECRET_KEY` and use a separate JWT signing key if possible (it currently equals `SECRET_KEY`).
- Set `NUM_PROXIES` correctly behind a proxy; otherwise a spoofed `X-Forwarded-For` can bypass throttles.
- Throttle counters use per-process memory; use a shared cache such as Redis with multiple workers.
- Schedule `manage.py flushexpiredtokens` to prune the token blacklist.
- Run `manage.py migrate` before serving (blacklist tables).
- Turn Leaflet attribution back on if `VITE_MAP_TILE_URL` is set; most tile providers require it.
- There is no database unique constraint on email (it is checked in code only), and a username containing "@" can mimic another person's email.
- Registration reveals whether an email or username already exists.
- The refresh token sits in `localStorage`, so an XSS bug would expose it.
- Existing accounts created under the earlier `public` role are not migrated to `citizen` automatically.

**Never commit:** `backend/data/`, `backend/catboost_info/`, `backend/models/`, `.DS_Store`, `backend.log`, `frontend.log`.
