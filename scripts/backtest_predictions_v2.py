"""Compatibility entry point: python -m scripts.backtest_predictions_v2 dataset.json."""

import runpy

if __name__ == "__main__":
    runpy.run_module("app.metrics.prediction_backtest", run_name="__main__")
