"""Opaque-box E2E test package for UTHelper Store Distribution & Auto-Update.

Keep this package marker free of eager imports of M1-M4 implementation modules
so ``pytest --collect-only tests/e2e_store_distribution`` always succeeds cleanly
even when implementation milestones land in parallel.
"""
