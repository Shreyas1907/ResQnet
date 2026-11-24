"""Train one risk classifier per disaster type and save it where ml_engine.model_loader looks.

Usage (from backend/):  python -m ml_engine.train tsunami flood drought fire
"""
import json
import sys
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold, StratifiedKFold, cross_val_predict, train_test_split

warnings.filterwarnings("ignore")

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
MODELS = BASE / "models"
SEED = 42
TABPFN_MAX_ROWS = 10000


def severity_bins(values: pd.Series, edges=None):
    """Map a physical severity measure onto the app's 1-5 scale using quantile edges."""
    if edges is None:
        edges = np.unique(np.quantile(values, [0.2, 0.4, 0.6, 0.8]))
    return (np.searchsorted(edges, values, side="right") + 1).clip(1, 5), edges


def gdacs_frame(code: str) -> pd.DataFrame:
    folder = {"FL": "flood", "DR": "drought", "WF": "fire"}[code]
    feats = json.load(open(DATA / folder / f"gdacs_{code}.json"))["features"]
    rows = []
    for f in feats:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        rows.append(
            {
                "event": p["eventid"],
                "alert": str(p["alertlevel"]).lower(),
                "raw_severity": float(p["severitydata"]["severity"]),
                "latitude": lat,
                "longitude": lon,
            }
        )
    d = pd.DataFrame(rows).drop_duplicates("event")
    d["y"] = d["alert"].isin(["orange", "red"]).astype(int)
    return d


def load_flood():
    d = gdacs_frame("FL")
    d = d[d["raw_severity"] > 0]
    d["severity"], edges = severity_bins(d["raw_severity"])
    return d, ["severity", "latitude", "longitude"], {"severity_edges": edges.tolist(), "label": "GDACS Orange/Red alert; rows with severity>0"}


def load_drought():
    d = gdacs_frame("DR")
    d["severity"], edges = severity_bins(d["raw_severity"])
    return d, ["severity", "latitude", "longitude"], {"severity_edges": edges.tolist(), "label": "GDACS Orange/Red alert"}


FIRE_CELL_DEG = 0.5
FIRE_PERSISTENT_DAYS = 10


def load_fire():
    frames = []
    for path in sorted((DATA / "wildfire").glob("fire_nrt_*.csv")):
        f = pd.read_csv(path, usecols=["latitude", "longitude", "acq_date", "confidence", "frp"])
        conf = f["confidence"].astype(str).str.lower()
        keep = conf.isin(["n", "h"]) | (pd.to_numeric(conf, errors="coerce") >= 50)
        frames.append(f[keep & (f["frp"] > 0)].drop(columns="confidence"))
    f = pd.concat(frames, ignore_index=True)
    f["clat"] = np.floor(f["latitude"] / FIRE_CELL_DEG) * FIRE_CELL_DEG + FIRE_CELL_DEG / 2
    f["clon"] = np.floor(f["longitude"] / FIRE_CELL_DEG) * FIRE_CELL_DEG + FIRE_CELL_DEG / 2
    daily = f.groupby(["clat", "clon", "acq_date"], as_index=False).agg(frp=("frp", "sum"))
    cells = daily.groupby(["clat", "clon"]).agg(active_days=("acq_date", "nunique"), peak_frp=("frp", "max")).reset_index()
    d = cells.rename(columns={"clat": "latitude", "clon": "longitude"})
    d["severity"], edges = severity_bins(d["peak_frp"])
    d["y"] = (d["active_days"] >= FIRE_PERSISTENT_DAYS).astype(int)
    return d, ["severity", "latitude", "longitude"], {
        "severity_edges": edges.tolist(),
        "label": f"NASA FIRMS (MODIS+VIIRS, 2024-11-01..2025-01-13): 0.5deg cell with fire on >={FIRE_PERSISTENT_DAYS} distinct days; severity from peak daily FRP quantiles",
    }


def load_tsunami():
    d = pd.DataFrame(json.load(open(DATA / "tsunami" / "noaa_tsunami_events.json")))
    d = d[(d["year"] >= 1900) & d["latitude"].notna() & d["longitude"].notna() & d["tsIntensity"].notna()].copy()
    d["severity"] = np.select(
        [d.tsIntensity < 1, d.tsIntensity < 2, d.tsIntensity < 3, d.tsIntensity < 4], [1, 2, 3, 4], 5
    )
    deaths = d["deathsAmountOrderTotal"].fillna(0)
    damage = d["damageAmountOrderTotal"].fillna(0)
    d["y"] = ((deaths >= 2) | (damage >= 3)).astype(int)
    return d, ["severity", "latitude", "longitude"], {
        "label": "NOAA deaths order>=2 or damage order>=3 (missing treated as none); year>=1900; severity from tsIntensity"
    }


def load_earthquake():
    import glob

    d = pd.read_excel(glob.glob(str(DATA / "public_emdat*.xlsx"))[0])
    d = d[(d["Disaster Type"] == "Earthquake") & d["Magnitude"].notna() & d["Latitude"].notna() & d["Longitude"].notna()].copy()
    d["people_affected"] = d["Total Affected"].fillna(0)
    d["severity"], edges = severity_bins(d["Magnitude"])
    d["y"] = (d["Total Deaths"].fillna(0) >= 10).astype(int)
    d = d.rename(columns={"Latitude": "latitude", "Longitude": "longitude"})
    return d, ["severity", "people_affected", "latitude", "longitude"], {
        "severity_edges": edges.tolist(),
        "label": "EM-DAT total deaths >= 10; severity from magnitude quantiles",
    }


LOADERS = {"flood": load_flood, "drought": load_drought, "fire": load_fire, "tsunami": load_tsunami, "earthquake": load_earthquake}


def candidates(n_pos, n_neg):
    spw = max(n_neg / max(n_pos, 1), 1.0)
    c = {
        f"catboost_d{depth}": CatBoostClassifier(iterations=300, depth=depth, learning_rate=0.05, l2_leaf_reg=l2, auto_class_weights="Balanced", verbose=0, random_seed=SEED, thread_count=4)
        for depth, l2 in [(3, 3), (4, 5), (6, 10)]
    }
    try:
        from lightgbm import LGBMClassifier

        c["lightgbm"] = LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=10, class_weight="balanced", random_state=SEED, verbose=-1, n_jobs=4)
    except Exception as exc:
        print(f"  lightgbm unavailable: {str(exc)[:60]}")
    try:
        from xgboost import XGBClassifier

        c["xgboost"] = XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.9, scale_pos_weight=spw, random_state=SEED, n_jobs=4, eval_metric="logloss")
    except Exception as exc:
        print(f"  xgboost unavailable: {str(exc)[:60]}")
    try:
        from tabpfn import TabPFNClassifier

        c["tabpfn"] = TabPFNClassifier(device="cpu", random_state=SEED)
    except Exception as exc:
        print(f"  tabpfn unavailable: {exc}")
    return c


def train_type(name: str):
    t0 = time.time()
    d, features, meta = LOADERS[name]()
    X, y = d[features].astype(float), d["y"].values
    n_pos = int(y.sum())
    print(f"\n== {name}: {len(d)} rows, {n_pos} positive ({n_pos / len(d):.1%}) ==")
    if n_pos < 20:
        print("  too few positives, skipping")
        return None

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, stratify=y, random_state=SEED)
    skf = StratifiedKFold(5, shuffle=True, random_state=SEED)
    scores = {}
    for cname, model in candidates(int(ytr.sum()), int((ytr == 0).sum())).items():
        if cname == "tabpfn" and len(Xtr) > TABPFN_MAX_ROWS:
            print(f"  tabpfn skipped: {len(Xtr)} rows > {TABPFN_MAX_ROWS}")
            continue
        try:
            p = cross_val_predict(model, Xtr, ytr, cv=skf, method="predict_proba")[:, 1]
            scores[cname] = (roc_auc_score(ytr, p), average_precision_score(ytr, p))
            print(f"  {cname:9s} CV ROC-AUC {scores[cname][0]:.3f}  PR-AUC {scores[cname][1]:.3f}")
        except Exception as exc:
            print(f"  {cname} failed: {exc}")
    if not scores:
        return None

    best = max(scores, key=lambda k: scores[k][0])
    make = lambda: candidates(int(ytr.sum()), int((ytr == 0).sum()))[best]
    cal_method = "isotonic" if n_pos >= 100 else "sigmoid"
    final = CalibratedClassifierCV(make(), method=cal_method, cv=3).fit(Xtr, ytr)
    pt = final.predict_proba(Xte)[:, 1]
    test = {
        "roc_auc": float(roc_auc_score(yte, pt)),
        "pr_auc": float(average_precision_score(yte, pt)),
        "prevalence": float(yte.mean()),
        "n_test": int(len(yte)),
        "n_test_pos": int(yte.sum()),
    }
    print(f"  best={best} ({cal_method}); HELD-OUT ROC-AUC {test['roc_auc']:.3f}  PR-AUC {test['pr_auc']:.3f} (prevalence {test['prevalence']:.3f})")

    groups = (np.floor(d["latitude"].values / 15).astype(int) * 100 + np.floor(d["longitude"].values / 15).astype(int))
    spatial = None
    try:
        ps = cross_val_predict(make(), X, y, cv=GroupKFold(5), groups=groups, method="predict_proba")[:, 1]
        spatial = {"roc_auc": float(roc_auc_score(y, ps)), "pr_auc": float(average_precision_score(y, ps)), "block_deg": 15}
        print(f"  SPATIAL-BLOCK CV ROC-AUC {spatial['roc_auc']:.3f}  PR-AUC {spatial['pr_auc']:.3f}")
    except Exception as exc:
        print(f"  spatial cv failed: {exc}")

    deployed = CalibratedClassifierCV(make(), method=cal_method, cv=3).fit(X, y)
    out = MODELS / name
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(deployed, out / "model.joblib")
    joblib.dump(features, out / "features.pkl")
    json.dump(
        {"disaster_type": name, "best_model": best, "calibration": cal_method, "cv": {k: {"roc_auc": v[0], "pr_auc": v[1]} for k, v in scores.items()}, "heldout": test, "spatial_cv": spatial, "n_rows": len(d), "n_positive": n_pos, "features": features, **meta, "seconds": round(time.time() - t0)},
        open(out / "meta.json", "w"),
        indent=2,
    )
    return best, test


if __name__ == "__main__":
    for t in sys.argv[1:] or list(LOADERS):
        train_type(t)
