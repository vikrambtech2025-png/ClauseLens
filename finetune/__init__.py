"""Fine-tuning assets for ClauseLens.

This package is deliberately separate from ``core/``: nothing here is imported
by the running application. The trained model is served locally and reached
through ``core/ft_analyzer.py``, not through this package.
"""
