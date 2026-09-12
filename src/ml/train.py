"""Reproducible training for the house-price artifact.

Refits the linear model on synthetic data (fixed seed — same artifact every
run) and writes the versioned JSON schema the backend expects. This makes
the artifact REVIEWABLE instead of magic: change the data generator, bump
the version, commit the diff.

Usage:
    uv run python -m ml.train                    # write house_price_v1.json
    uv run python -m ml.train --version 1.1.0    # bump the artifact version
"""

import argparse
import json
import random
from pathlib import Path

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"

TRUE_WEIGHTS = {"rooms": 15.0, "area_sqm": 3.2, "age_years": -0.8}
TRUE_BIAS = 20.0
NOISE_STD = 2.0
N_SAMPLES = 500
SEED = 42


def _solve_linear_system(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting for the normal equations."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]

    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        pivot_value = a[col][col]
        if abs(pivot_value) < 1e-12:
            raise ValueError("Singular matrix — check the feature generator")
        for row in range(col + 1, n):
            factor = a[row][col] / pivot_value
            for c in range(col, n + 1):
                a[row][c] -= factor * a[col][c]

    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        solution[row] = (a[row][n] - sum(a[row][c] * solution[c] for c in range(row + 1, n))) / a[row][row]
    return solution


def fit() -> tuple[dict[str, float], float, float]:
    """Closed-form least squares on synthetic data. Returns (weights, bias, mae)."""
    rng = random.Random(SEED)
    keys = list(TRUE_WEIGHTS)

    # Normal equations: X^T X w = X^T y  (with intercept column)
    xtx = [[0.0] * (len(keys) + 1) for _ in range(len(keys) + 1)]
    xty = [0.0] * (len(keys) + 1)

    samples = []
    for _ in range(N_SAMPLES):
        features = {
            "rooms": max(1, round(rng.gauss(3, 1))),
            "area_sqm": max(20, round(rng.gauss(80, 25))),
            "age_years": max(0, round(rng.gauss(15, 10))),
        }
        target = TRUE_BIAS + sum(TRUE_WEIGHTS[k] * features[k] for k in keys) + rng.gauss(0, NOISE_STD)
        samples.append((features, target))

        row = [1.0, *[float(features[k]) for k in keys]]
        for i in range(len(row)):
            xty[i] += row[i] * target
            for j in range(len(row)):
                xtx[i][j] += row[i] * row[j]

    solved = _solve_linear_system(xtx, xty)
    bias, weights_values = solved[0], solved[1:]
    weights = dict(zip(keys, weights_values, strict=True))

    mae = sum(abs((bias + sum(weights[k] * f[k] for k in keys)) - t) for f, t in samples) / len(samples)
    return weights, bias, mae


def write_artifact(version: str = "1.0.0", out_dir: Path = ARTIFACTS_DIR) -> Path:
    """Fit and write the artifact. Returns the written path."""
    weights, bias, mae = fit()
    artifact = {
        "name": "house-price",
        "version": version,
        "description": "Linear model fit on synthetic data (seed=42): price ~ rooms + area + age. "
        "Regenerate with: uv run python -m ml.train",
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "bias": round(bias, 4),
        "unit": "kUSD",
        "metrics": {"mae_kUSD": round(mae, 3), "n_samples": N_SAMPLES},
    }

    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"house_price_v{version.split('.')[0]}.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"Wrote {out} (train MAE: {artifact['metrics']['mae_kUSD']} kUSD over {N_SAMPLES} samples)")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.0.0", help="Artifact version to write")
    args = parser.parse_args()
    write_artifact(version=args.version)
