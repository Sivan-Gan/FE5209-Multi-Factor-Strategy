
"""Unified runner for the FE5209 Adaptive Strategy.

Run from repository root:

python -m src.adaptive_strategy.run \
    --config configs/adaptive_strategy.json

Optionally override the data root:

python -m src.adaptive_strategy.run \
    --data-root /path/to/data
"""

import argparse
import json
from pathlib import Path
from src.adaptive_strategy.recent_extension import (
    run_recent_extension,
)

from src.adaptive_strategy.pipeline import (
    PipelineConfig,
    run_pipeline,
)
from src.adaptive_strategy.model_selection import (
    TrainerConfig,
    run_trainer,
)
from src.adaptive_strategy.fixed_top3_benchmark import (
    FixedBenchmarkConfig,
    run_fixed_benchmark,
)
from src.adaptive_strategy.benchmark_validation import (
    run_adaptive_validation,
    compare_with_fixed,
)
from src.adaptive_strategy.benchmark_reporting import (
    run_benchmark_reporting,
)
from src.adaptive_strategy.reporting import run_reporting


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_path(value, base):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def check_dependencies(data_root):
    required = {
        "factor_scores":
            "handoff/factor_research_archive/data/"
            "all_factor_scores_weekly.parquet",
        "factor_availability":
            "handoff/factor_research_archive/data/"
            "all_factor_availability_weekly.parquet",
        "frozen_metadata":
            "handoff/final_factor/data/"
            "factor_scores_weekly.parquet",
        "factor_returns":
            "data/adaptive_strategy/common_factor_returns.csv",
        "security_returns":
            "handoff/us_downstream/data/"
            "security_weekly_execution_returns.parquet",
        "risk_free":
            "handoff/us_downstream/data/"
            "risk_free_weekly.parquet",
        "market":
            "handoff/us_downstream/data/"
            "benchmark_weekly_execution_returns.parquet",
        "fixed4":
            "data/adaptive_strategy/fixed4_weekly_paths.csv",
    }

    missing = [
        (name, data_root / relative)
        for name, relative in required.items()
        if not (data_root / relative).is_file()
    ]

    if missing:
        message = "\n".join(
            f"{name}: {path}"
            for name, path in missing
        )
        raise FileNotFoundError(
            "Missing required data:\n" + message
        )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--config",
        default="configs/adaptive_strategy.json",
    )
    parser.add_argument("--data-root")
    parser.add_argument("--output-root")
    parser.add_argument(
        "--skip-historical",
        action="store_true",
    )

    args = parser.parse_args()

    config_path = resolve_path(
        args.config, PROJECT_ROOT
    )

    with config_path.open(encoding="utf-8") as f:
        config = json.load(f)

    # All configured paths are relative to the
    # repository root, not the terminal's cwd.
    data_root = resolve_path(
        args.data_root or config["data_root"],
        PROJECT_ROOT,
    )

    output_root = resolve_path(
        args.output_root or config["output_root"],
        PROJECT_ROOT,
    )

    print("\n=== Adaptive Strategy Runner ===")
    print("Project:", PROJECT_ROOT)
    print("Data root:", data_root)
    print("Output root:", output_root)

    check_dependencies(data_root)

    historical_dir = output_root / "historical_reproduction"
    model_dir = output_root / "model_selection"
    fixed_dir = output_root / "fixed_top3_benchmark"
    validation_dir = output_root / "benchmark_validation"

    historical_paths = historical_dir / "weekly_paths.csv"

    if not args.skip_historical:
        print("\n[1/6] Historical Pipeline")

        reference = data_root / (
            "data/adaptive_strategy/"
            "historical_reference_weekly_paths.csv"
        )

        run_pipeline(
            data_root=data_root,
            output_dir=historical_dir,
            config=PipelineConfig(
                **config["historical"]
            ),
            reference_path=(
                reference if reference.is_file() else None
            ),
        )
    elif not historical_paths.is_file():
        raise FileNotFoundError(
            "Historical output missing. "
            "Cannot use --skip-historical."
        )

    rf_file = data_root / (
        "handoff/us_downstream/data/"
        "risk_free_weekly.parquet"
    )

    print("\n[2/6] Model Selection")

    run_trainer(
        paths_file=historical_paths,
        rf_file=rf_file,
        output_dir=model_dir,
        config=TrainerConfig(
            **config["model_selection"]
        ),
    )

    print("\n[3/6] Fixed Top-3 Benchmark")

    fixed_path, _, _ = run_fixed_benchmark(
        data_root=data_root,
        output_dir=fixed_dir,
        config=FixedBenchmarkConfig(
            **config["controlled_benchmark"]
        ),
    )

    print("\n[4/6] Controlled Benchmark Validation")

    adaptive_path = run_adaptive_validation(
        data_root=data_root,
        annual_file=historical_dir /
            "annual_factor_decisions.csv",
        weekly_weights_file=historical_dir /
            "weekly_factor_weights.csv",
        output_dir=validation_dir,
    )

    comparison = compare_with_fixed(
        adaptive_path,
        fixed_dir / "weekly_paths.csv",
    )

    comparison.to_csv(
        validation_dir / "fair_comparison.csv",
        index=False,
    )

    print("\n[5/6] Controlled Benchmark Reporting")

    run_benchmark_reporting(
        adaptive_file=validation_dir /
            "adaptive_weekly_paths.csv",
        fixed_file=fixed_dir / "weekly_paths.csv",
        rf_file=rf_file,
        output_dir=output_root / "benchmark_reporting",
    )

    print("\n[6/6] Historical Reporting")

    run_reporting(
        pipeline_file=historical_paths,
        frozen_file=model_dir / "frozen_model.json",
        fixed_file=data_root / (
            "data/adaptive_strategy/"
            "fixed4_weekly_paths.csv"
        ),
        market_file=data_root / (
            "handoff/us_downstream/data/"
            "benchmark_weekly_execution_returns.parquet"
        ),
        rf_file=rf_file,
        output_dir=output_root / "reporting",
    )

    if config.get("recent_extension", {}).get(
        "enabled", False
    ):
        print("\n[7/7] 2026 Recent Extension")

        recent_data = (
            PROJECT_ROOT
            / "data/adaptive_strategy/recent_2026"
        )

        run_recent_extension(
            strategy_file=recent_data
                / "strategy_weekly_paths.csv",
            benchmark_file=recent_data
                / "benchmark_weekly_returns.csv",
            reference_file=recent_data
                / "performance_reference.csv",
            output_dir=output_root
                / "recent_extension",
        )
    print("\n=== PIPELINE COMPLETED ===")
    print("Outputs:", output_root)


if __name__ == "__main__":
    main()
