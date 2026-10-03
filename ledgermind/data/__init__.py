"""Synthetic merchant generation and input loading.

gen.py writes transactions.csv, sales.csv and truth.json per merchant. load.py parses
and validates the first two. truth.json is read only by tests/ and eval/ (Article III).
"""
