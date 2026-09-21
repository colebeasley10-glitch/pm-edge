export type Summary = {
  record: string;
  wins: number;
  losses: number;
  net_pl: number;
  roi: number | null;
  risked: number;
  settled: number;
  unfilled: number;
  open: number;
};

export type Signal = {
  signal_id: string;
  ticker: string;
  market_title?: string | null;
  sport_title?: string | null;
  market_type?: string | null;
  side: "YES" | "NO";
  lane?: string | null;
  recommended_limit?: number | null;
  fair_probability?: number | null;
  net_edge?: number | null;
  books?: number | null;
  alerted_at?: string | null;
};

export type OpenBet = {
  ticker: string;
  market_title?: string | null;
  sport_title?: string | null;
  side?: string | null;
  lane?: string | null;
  status?: string | null;
  filled_shares?: number | null;
  average_fill_price?: number | null;
  entry_cost?: number | null;
  current_bid?: number | null;
  current_value?: number | null;
  unrealized_pl?: number | null;
  kalshi_status?: string | null;
};

export type HistoryBet = OpenBet & {
  trade_date?: string | null;
  net_pl?: number | null;
  roi?: number | null;
  outcome?: string | null;
};

export type PerformanceRow = {
  sport_title?: string;
  lane?: string;
  market_type?: string;
  bets: number;
  record: string;
  net_pl: number;
  roi: number | null;
  risked: number;
};

export type DashboardResponse = {
  generated_at: string;
  actual: Summary;
  model: Partial<Summary>;
  new_bets: Signal[];
  open_count: number;
  system: {
    last_model_activity?: string | null;
    odds_credits_today?: number | null;
    drawdown_active: boolean;
  };
};

export type AnalyticsResponse = {
  actual: Summary;
  model: Partial<Summary>;
  by_sport: PerformanceRow[];
  by_lane: PerformanceRow[];
  by_market_type: PerformanceRow[];
  funnel: {
    qualified: number;
    alerts: number;
    placed: number;
    filled: number;
  };
};

export type SystemResponse = {
  last_model_activity?: string | null;
  odds_credits_today?: number | null;
  odds_budget_date?: string | null;
  drawdown?: Record<string, unknown>;
  tracking?: {
    signals?: number;
    candidate_decisions?: number;
    clv_rows?: number;
    fill_rows?: number;
    signal_outcomes?: number;
  };
};

export type BetListResponse<T = Record<string, unknown>> = { items: T[] };
