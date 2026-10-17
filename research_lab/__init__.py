"""PM Edge v30 Strategy Research Lab: PAPER TRADING ONLY.

Nothing in this package can place, amend, or cancel a real Kalshi order. It reads
Kalshi's public market data (no keys) and the main scanner's own priced-market
snapshot, and records simulated trades in its own ledgers under research_lab/data/.

tests in test_research_lab_safety.py fail the build if any module here imports or
references live order-submission code.
"""

PAPER_ONLY = True
