from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass
class SearchDimension:
    name: str
    low: float
    high: float
    integer: bool = False


class GeneticOptimizer:
    def __init__(self, dimensions: list[SearchDimension], seed: int = 7):
        self.dimensions = dimensions
        self.rng = np.random.default_rng(seed)

    def sample_candidate(self) -> dict:
        candidate = {}
        for dim in self.dimensions:
            value = self.rng.uniform(dim.low, dim.high)
            candidate[dim.name] = int(round(value)) if dim.integer else float(value)
        return candidate

    def mutate(self, candidate: dict, mutation_rate: float = 0.2) -> dict:
        mutated = dict(candidate)
        for dim in self.dimensions:
            if self.rng.random() > mutation_rate:
                continue
            value = self.rng.uniform(dim.low, dim.high)
            mutated[dim.name] = int(round(value)) if dim.integer else float(value)
        return mutated

    def crossover(self, left: dict, right: dict) -> dict:
        child = {}
        for dim in self.dimensions:
            child[dim.name] = left[dim.name] if self.rng.random() < 0.5 else right[dim.name]
        return child

    def evolve(
        self,
        score_fn: Callable[[dict], float],
        population_size: int = 64,
        generations: int = 20,
        elite_size: int = 8,
    ) -> list[tuple[float, dict]]:
        population = [self.sample_candidate() for _ in range(population_size)]
        scored: list[tuple[float, dict]] = []

        for _ in range(generations):
            scored = sorted(
                ((float(score_fn(candidate)), candidate) for candidate in population),
                key=lambda item: item[0],
                reverse=True,
            )
            elites = [candidate for _, candidate in scored[:elite_size]]
            next_population = list(elites)

            while len(next_population) < population_size:
                left = elites[int(self.rng.integers(0, len(elites)))]
                right = elites[int(self.rng.integers(0, len(elites)))]
                child = self.crossover(left, right)
                child = self.mutate(child)
                next_population.append(child)
            population = next_population

        return scored

