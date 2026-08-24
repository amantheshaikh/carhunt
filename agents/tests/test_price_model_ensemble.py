import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from tools.price_model import (
    _to_features,
    resolve_drivetrain,
    _is_cpo,
    _fallback_trim_premium,
    _trim_premium,
    StackedModel,
)

def test_fallback_trim_premium():
    assert _fallback_trim_premium("tucson hybrid", "blue") == 0.0
    assert _fallback_trim_premium("tucson hybrid", "sel") == 2000.0
    assert _fallback_trim_premium("tucson hybrid", "unknown_custom_trim") == 3000.0

    row = {"make": "Hyundai", "model": "Tucson Hybrid", "year": 2025, "trim": "unknown_custom_trim"}
    assert _trim_premium(row) == 3000.0

def test_drivetrain_for_overrides():
    # 2025+ Tucson Hybrid -> always AWD
    row_tucson = {"make": "Hyundai", "model": "Tucson Hybrid", "year": 2025, "trim": "SEL Convenience"}
    assert resolve_drivetrain(row_tucson) == "awd"

    # 2023+ BMW X1 -> always AWD
    row_x1 = {"make": "BMW", "model": "X1", "year": 2024, "trim": "xDrive28i"}
    assert resolve_drivetrain(row_x1) == "awd"

    # 2023+ Lexus NX 350h -> always AWD
    row_nx = {"make": "Lexus", "model": "NX 350h", "year": 2023, "trim": "Luxury"}
    assert resolve_drivetrain(row_nx) == "awd"

    # VW Tiguan SEL R-Line -> always AWD (trim level override)
    row_tiguan = {"make": "Volkswagen", "model": "Tiguan", "year": 2025, "trim": "SEL R-Line"}
    assert resolve_drivetrain(row_tiguan) == "awd"

def test_to_features_transformation():
    row = {
        "make": "Hyundai",
        "model": "Tucson Hybrid",
        "year": 2025,
        "mileage": 10000,
        "trim": "Limited",
        "title_status": "clean",
        "accident_count": 0,
        "insights": {"cpo": True},
    }
    feats = _to_features(row)
    assert len(feats) == 6
    # feats: [year, log_mileage, trim_premium, drivetrain, powertrain, cpo]
    assert feats[0] == 2025
    assert np.isclose(feats[1], np.log1p(10000))  # log-transformed mileage
    assert feats[3] == 1.0   # AWD override for 2025 Tucson Hybrid
    assert feats[4] == 0.8   # Hybrid powertrain
    assert feats[5] == 1.0   # CPO flag

def test_stacked_model_training_and_prediction():
    from tools.price_model import _fit_stacked, _to_features
    # Construct synthetic dataset of 40 vehicles
    np.random.seed(42)
    X_rows = []
    y_rows = []
    for i in range(40):
        year = int(np.random.choice([2023, 2024, 2025]))
        mileage = int(np.random.uniform(5000, 40000))
        trim = str(np.random.choice(["SEL Convenience", "Limited"]))
        price = 40000 + (year - 2023) * 3000 - (mileage / 1000) * 300 + (3000 if trim == "Limited" else 0)
        row = {
            "make": "Hyundai", "model": "Tucson Hybrid",
            "year": year, "mileage": mileage, "price": price,
            "trim": trim, "title_status": "clean", "accident_count": 0,
        }
        X_rows.append(_to_features(row))
        y_rows.append(price)

    model = _fit_stacked("Tucson Hybrid Test", X_rows, y_rows, "tucsonhybrid", pkl_path="", save=False)
    assert model is not None

    # Predict for lower mileage vs higher mileage listing
    low_mi_row = {"make": "Hyundai", "model": "Tucson Hybrid", "year": 2025, "mileage": 5000, "trim": "Limited"}
    high_mi_row = {"make": "Hyundai", "model": "Tucson Hybrid", "year": 2025, "mileage": 35000, "trim": "Limited"}

    pred_low = float(model.predict([_to_features(low_mi_row)])[0])
    pred_high = float(model.predict([_to_features(high_mi_row)])[0])

    # Lower mileage vehicle must have a higher predicted fair price
    assert pred_low > pred_high
