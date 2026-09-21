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

export type BetListResponse = { items: Record<string, unknown>[] };
