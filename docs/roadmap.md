# Roadmap

These are planned capabilities, not claims about the alpha.

1. **Dataset Spec:** compile natural language into schema, domain coverage, quotas,
   allowed sources, quality gates and budgets; validate requirements before execution.
2. **Verified outputs:** source-grounded QA verification, executable code checks,
   SQL result validation and explicit acceptance reports.
3. **Reliable acquisition:** global time/page/token budgets, stronger network isolation,
   prompt-injection defenses and quality-aware source ranking.
4. **Reproducibility:** one documented execution environment, dependency constraints,
   optional backend tests and replayable small online examples.
5. **Evaluation:** compare against search + crawler + fixed-pipeline baselines under
   equal budgets; report relevance, coverage, duplication, accepted yield and cost.
   Use held-out downstream evaluation before claiming model improvement.
6. **Release readiness:** project-level license and provenance review, documented
   source/pipeline compatibility, and a reproducible release process.

The current product is domain dataset acquisition and curation. Browser-interaction
trajectory generation and environment replay are separate extensions.
