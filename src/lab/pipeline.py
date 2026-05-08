from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.lab.backtester import run_backtest
from src.lab.datasets import load_ohlcv_csv, split_out_of_sample
from src.lab.genetic import GeneticOptimizer, SearchDimension
from src.lab.models import StrategyCandidate, ValidationReport, ValidationThresholds
from src.lab.registry import StrategyRegistry
from src.lab.strategy_family import default_search_dimensions
from src.lab.validation import compute_metrics, monte_carlo_permutation, parameter_permutations, walk_forward_splits


@dataclass
class ResearchJob:
    market: str
    dataset_path: str
    regime: str = "all"
    seed: int = 7
    population_size: int = 48
    generations: int = 10
    train_bars: int = 400
    test_bars: int = 120
    step_bars: int = 120
    thresholds: ValidationThresholds = field(default_factory=ValidationThresholds)


class ResearchPipeline:
    def __init__(self, registry: StrategyRegistry | None = None):
        self.registry = registry or StrategyRegistry()

    def run_job(self, job: ResearchJob) -> dict:
        dataset = load_ohlcv_csv(job.dataset_path, symbol=job.market)
        in_sample, out_of_sample = split_out_of_sample(dataset, job.thresholds.out_of_sample_pct)
        if len(in_sample) < job.train_bars + job.test_bars + 10:
            raise ValueError("Dataset insuficiente para walk-forward")

        dimensions = [SearchDimension(**dimension) for dimension in default_search_dimensions()]
        optimizer = GeneticOptimizer(dimensions, seed=job.seed)

        def score_fn(parameters: dict) -> float:
            result = run_backtest(in_sample, parameters)
            metrics = result.metrics
            if metrics.trades < max(8, job.thresholds.min_trades // 2):
                return -1_000.0 + metrics.trades
            penalty = metrics.max_drawdown_pct * 0.05
            return metrics.sharpe + (metrics.total_return_pct * 0.01) - penalty

        scored = optimizer.evolve(
            score_fn=score_fn,
            population_size=job.population_size,
            generations=job.generations,
            elite_size=max(4, min(12, job.population_size // 6)),
        )
        best_score, best_parameters = scored[0]

        candidate = StrategyCandidate(
            candidate_id=str(uuid.uuid4()),
            family="scalper",
            market=job.market,
            regime=job.regime,
            parameters=best_parameters,
            seed=job.seed,
        )
        self.registry.add_candidate(candidate)

        walk_forward = self._walk_forward_validate(in_sample, best_parameters, job)
        oos_result = run_backtest(out_of_sample, best_parameters)
        monte_carlo = monte_carlo_permutation(
            [trade.net_return for trade in oos_result.trades],
            iterations=job.thresholds.monte_carlo_iterations,
            seed=job.seed,
        )
        permutation = self._parameter_permutation_validate(out_of_sample, best_parameters, job)

        report = self._build_validation_report(
            candidate=candidate,
            best_score=best_score,
            out_of_sample_metrics=oos_result.metrics.to_dict(),
            walk_forward=walk_forward,
            monte_carlo=monte_carlo,
            permutation=permutation,
            thresholds=job.thresholds,
        )

        promoted_profile = None
        if report.status == "approved":
            profile_id = f"{candidate.family}-{candidate.market}-{candidate.seed}"
            promoted_profile = self.registry.promote_candidate(candidate, report, profile_id).to_dict()

        return {
            "candidate": candidate.to_dict(),
            "report": report.to_dict(),
            "promoted_profile": promoted_profile,
            "job": asdict(job),
        }

    def _walk_forward_validate(self, bars, parameters: dict, job: ResearchJob) -> dict:
        splits = walk_forward_splits(
            length=len(bars),
            train_size=job.train_bars,
            test_size=job.test_bars,
            step_size=job.step_bars,
        )
        fold_metrics = []
        pass_flags = []
        for train_slice, test_slice in splits:
            train_result = run_backtest(bars[train_slice], parameters)
            test_result = run_backtest(bars[test_slice], parameters)
            fold = {
                "train": train_result.metrics.to_dict(),
                "test": test_result.metrics.to_dict(),
            }
            fold_metrics.append(fold)
            pass_flags.append(
                test_result.metrics.sharpe > 0
                and test_result.metrics.max_drawdown_pct <= job.thresholds.max_drawdown_pct
            )
        pass_rate = (sum(pass_flags) / len(pass_flags)) if pass_flags else 0.0
        return {
            "folds": fold_metrics,
            "fold_count": len(fold_metrics),
            "pass_rate": round(pass_rate, 4),
        }

    def _parameter_permutation_validate(self, bars, parameters: dict, job: ResearchJob) -> dict:
        variants = parameter_permutations(parameters, pct=job.thresholds.parameter_perturbation_pct)
        metrics_list = []
        pass_flags = []
        for variant in variants:
            result = run_backtest(bars, variant)
            metrics = result.metrics.to_dict()
            metrics_list.append({"parameters": variant, "metrics": metrics})
            pass_flags.append(
                metrics["sharpe"] > 0
                and metrics["max_drawdown_pct"] <= job.thresholds.max_drawdown_pct
            )
        pass_rate = (sum(pass_flags) / len(pass_flags)) if pass_flags else 0.0
        return {
            "variants_tested": len(variants),
            "pass_rate": round(pass_rate, 4),
            "samples": metrics_list[:6],
        }

    def _build_validation_report(
        self,
        candidate: StrategyCandidate,
        best_score: float,
        out_of_sample_metrics: dict,
        walk_forward: dict,
        monte_carlo: dict,
        permutation: dict,
        thresholds: ValidationThresholds,
    ) -> ValidationReport:
        notes = []
        approved = True

        sharpe = float(out_of_sample_metrics["sharpe"])
        max_dd = float(out_of_sample_metrics["max_drawdown_pct"])
        profit_factor = float(out_of_sample_metrics["profit_factor"])
        trades = int(out_of_sample_metrics["trades"])

        if sharpe < thresholds.min_sharpe:
            approved = False
            notes.append(f"Sharpe OOS {sharpe:.2f} < {thresholds.min_sharpe:.2f}")
        if trades < thresholds.min_trades:
            approved = False
            notes.append(f"Trades OOS {trades} < {thresholds.min_trades}")
        if max_dd > thresholds.max_drawdown_pct:
            approved = False
            notes.append(f"Max DD OOS {max_dd:.2f}% > {thresholds.max_drawdown_pct:.2f}%")
        if profit_factor < thresholds.min_profit_factor:
            approved = False
            notes.append(f"Profit factor {profit_factor:.2f} < {thresholds.min_profit_factor:.2f}")
        if walk_forward["pass_rate"] < thresholds.min_walk_forward_pass_rate:
            approved = False
            notes.append("Walk-forward insuficiente")
        if monte_carlo["pass_rate"] < thresholds.min_monte_carlo_pass_rate:
            approved = False
            notes.append("Monte Carlo insuficiente")
        if permutation["pass_rate"] < thresholds.min_permutation_pass_rate:
            approved = False
            notes.append("Permutation insuficiente")

        return ValidationReport(
            candidate_id=candidate.candidate_id,
            status="approved" if approved else "rejected",
            metrics={
                "best_score": round(float(best_score), 4),
                "out_of_sample": out_of_sample_metrics,
                "thresholds": asdict(thresholds),
            },
            walk_forward=walk_forward,
            monte_carlo=monte_carlo,
            permutation=permutation,
            notes=notes,
        )


def run_research_job(
    dataset_path: str | Path,
    market: str,
    regime: str = "all",
    seed: int = 7,
) -> dict:
    pipeline = ResearchPipeline()
    job = ResearchJob(
        market=market,
        dataset_path=str(dataset_path),
        regime=regime,
        seed=seed,
    )
    return pipeline.run_job(job)
