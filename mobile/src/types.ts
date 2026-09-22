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
  execution?: string | null;
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
  client_order_id?: string | null;
  submitted_at?: string | null;
  created_at?: string | null;
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
  account: {
    connected: boolean;
    balance: number | null;
  };
  actual: Summary;
  model: Partial<Summary>;
  new_bets: Signal[];
  recent_activity?: HistoryBet[];
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
  guardrails: {
    max_per_order_dollars: number;
    max_open_risk_dollars: number;
    max_open_positions_per_sport: number;
    per_sport_dollar_cap: number | null;
  };
  tracking: {
    signals: number;
    candidate_decisions: number;
    clv_rows: number;
    fill_rows: number;
    signal_outcomes: number;
  };
};

export type ReviewResponse = {
  signal_id: string;
  ticker: string;
  market_title: string;
  sport_title: string;
  market_type: string;
  lane: string;
  side: "YES" | "NO";
  execution: "MAKE" | "TAKE";
  fair_probability?: number | null;
  net_edge?: number | null;
  books?: number | null;
  limit_price: number;
  live_ask: number;
  shares: number;
  max_loss: number;
  gross_profit_if_correct: number;
  price_ok: boolean;
  price_message: string;
  guard: { blocked?: boolean; reason?: string };
  existing_order?: Record<string, unknown> | null;
  kalshi_ready: boolean;
  can_submit: boolean;
};

export type SubmitResponse = {
  submitted: boolean;
  already_exists: boolean;
  order: Record<string, unknown>;
  review: ReviewResponse;
};

export type BetListResponse<T = Record<string, unknown>> = { items: T[] };

export type ScoreEvent = {
  id?: string | null;
  sport_key?: string | null;
  commence_time?: string | null;
  completed: boolean;
  home_team?: string | null;
  away_team?: string | null;
  scores: { name?: string | null; score?: string | number | null }[];
  last_update?: string | null;
};

export type ScoresResponse = { items: ScoreEvent[]; available: boolean };
