"""One run's shared market data: fetched once, reused by every research strategy.

Strategies share the data but never share decisions or ledgers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from research_lab.public_data import Book, PublicDataError


@dataclass
class RunContext:
    now: datetime
    kalshi: object                            # research_lab.public_data.PublicKalshi (or a test fake)
    quotes: list = field(default_factory=list)   # snapshot.FairQuote (main scanner, v1)
    pinnacle_quotes: list = field(default_factory=list)  # FairQuote priced off Pinnacle (v2)
    pinnacle_events: dict = field(default_factory=dict)  # sport_id -> [PinEvent] fetched this round
    ev_games: list = field(default_factory=list)          # ev_data.Game with every venue's offers
    markets: dict = field(default_factory=dict)  # ticker -> fresh normalized market
    flows: dict = field(default_factory=dict)    # (ticker, side) -> flow.Flow
    flow_complete: bool = True
    events: list = field(default_factory=list)   # open events with nested markets (structural)
    max_books: int = 80
    books: dict = field(default_factory=dict)
    book_errors: int = 0
    notes: list = field(default_factory=list)

    def book(self, ticker: str) -> Book | None:
        """Order book for a ticker, fetched at most once per run."""
        if ticker in self.books:
            return self.books[ticker]
        if len([b for b in self.books.values() if b is not None]) >= self.max_books:
            self.books[ticker] = None
            self.notes.append(f"order-book budget reached; {ticker} not checked")
            return None
        try:
            b = self.kalshi.orderbook(ticker)
        except PublicDataError:
            self.book_errors += 1
            b = None
        self.books[ticker] = b
        return b

    def quotes_for(self, params: dict) -> list:
        return self.pinnacle_quotes if params.get("fair_source") == "pinnacle" else self.quotes
