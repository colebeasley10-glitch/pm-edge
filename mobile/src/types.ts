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
  commence_time?: string | null;
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
    max_open_positions_per_sport: number | null;
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


export type AutoTraderPosition = {
  ticker: string;
  side: "YES" | "NO" | string;
  contracts: number;
  entry: string;
  entry_fee: string;
  exposure_key?: string | null;
  fair_snapshot_id?: string | null;
  market_title?: string | null;
  market_subtitle?: string | null;
  side_label?: string | null;
  event_ticker?: string | null;
  close_time?: string | null;
};

export type ClosedPaperBet = {
  ticker: string;
  side: "YES" | "NO" | string;
  market_title?: string | null;
  market_subtitle?: string | null;
  side_label?: string | null;
  contracts: number;
  entry_price: string;
  risked: string;
  pnl: string;
  roi: string;
  returned: string;
  outcome: "WIN" | "LOSS" | "PUSH" | string;
  lane?: string | null;
};

export type AutoTraderRun = {
  observed_at?: string | null;
  provider?: string | null;
  fair_refresh?: Record<string, any> | null;
  candidate_count?: number;
  fresh_probability_count?: number;
  matched_candidate_count?: number;
  paper_fills?: number;
  funnel?: Record<string, number>;
  portfolio?: {
    cash?: string;
    equity?: string;
    realized_pnl?: string;
    unrealized_pnl?: string;
    open_positions?: number;
  };
  decisions?: Array<Record<string, any>>;
};

export type AutoTraderStatus = {
  mode: "paper";
  worker_online: boolean;
  scan_interval_seconds: number;
  starting_bankroll: string;
  portfolio: {
    cash: string;
    equity: string;
    realized_pnl: string;
    unrealized_pnl: string;
    open_positions: number;
  };
  positions: AutoTraderPosition[];
  record: string;
  settled_count: number;
  closed_count?: number;
  closed_bets: ClosedPaperBet[];
  blotter: Array<Record<string, any>>;
  last_run?: AutoTraderRun | null;
  recent_runs: AutoTraderRun[];
  recorder?: {
    enabled?: boolean;
    db_path?: string;
    counts?: Record<string, number>;
    storage_bytes?: {
      db?: number;
      wal?: number;
      shm?: number;
      total?: number;
    };
    maker_probe_summary?: {
      pending?: number;
      observed_fills?: number;
      expired?: number;
      resolved?: number;
      fill_rate_resolved?: number | null;
      avg_entry_vs_mid?: number | null;
      avg_side_mid_drift_5m?: number | null;
      avg_side_mid_drift_30m?: number | null;
      marked_5m?: number;
      marked_30m?: number;
    };
    weather_calibration?: {
      n?: number;
      brier?: number | null;
      log_loss?: number | null;
    };
    error?: string;
  };
};

export type AutoTraderScanResponse = {
  accepted: boolean;
  mode: "paper";
};


export type WhalePaperPosition = {
  ticker: string;
  side: "YES" | "NO" | string;
  contracts: number;
  entry: string;
  entry_fee: string;
  current_bid?: string | null;
  market_value?: string | null;
  open_pnl?: string | null;
  exposure_key?: string | null;
  market_title?: string | null;
  market_subtitle?: string | null;
  side_label?: string | null;
  event_ticker?: string | null;
  close_time?: string | null;
  thesis?: Record<string, any> | null;
};

export type WhalePaperRun = {
  observed_at?: string | null;
  public_trades?: number;
  fresh_trades?: number;
  qualified_flows?: number;
  paper_fills?: number;
  maker_orders_resting?: number;
  pending_orders?: number;
  api_error?: string | null;
  rate_limited?: boolean;
  credit_usage?: {
    paid_odds_api_calls?: number;
    kalshi_public_trade_tape_calls?: number;
  };
  portfolio?: {
    cash?: string;
    equity?: string;
    realized_pnl?: string;
    unrealized_pnl?: string;
    open_positions?: number;
  };
  decisions?: Array<Record<string, any>>;
};

export type WhalePaperStatus = {
  mode: "paper";
  strategy: "whale_flow" | "quick_whale" | "quick_whale_v2" | "quick_whale_v3";
  worker_online: boolean;
  scan_interval_seconds: number;
  starting_bankroll: string;
  credit_policy: {
    paid_odds_api_calls: number;
    source: string;
    monthly_paid_credit_target: number;
  };
  portfolio: {
    cash: string;
    equity: string;
    realized_pnl: string;
    unrealized_pnl: string;
    open_positions: number;
  };
  positions: WhalePaperPosition[];
  pending_orders: Array<Record<string, any>>;
  record: string;
  settled_count: number;
  closed_count?: number;
  closed_bets: ClosedPaperBet[];
  blotter: Array<Record<string, any>>;
  last_run?: WhalePaperRun | null;
  recent_runs: WhalePaperRun[];
};

export type WhalePaperScanResponse = {
  accepted: boolean;
  mode: "paper";
  strategy: "whale_flow" | "quick_whale" | "quick_whale_v2" | "quick_whale_v3";
};
