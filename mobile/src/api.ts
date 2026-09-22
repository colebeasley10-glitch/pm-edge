import type {
  AnalyticsResponse,
  BetListResponse,
  DashboardResponse,
  HistoryBet,
  OpenBet,
  ReviewResponse,
  Signal,
  SubmitResponse,
  SystemResponse,
} from "./types";

const API_BASE = process.env.EXPO_PUBLIC_PM_EDGE_API_BASE_URL?.replace(/\/$/, "") ?? "";

function headers() {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {

  const response = await fetch(`${API_BASE}/api${path}`, {
    ...init,
    headers: { ...headers(), ...(init?.headers ?? {}) },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message ?? `PM Edge API error ${response.status}`;
    throw new Error(message);
  }
  return payload as T;
}

export const api = {
  dashboard: () => request<DashboardResponse>("/v1/dashboard"),
  newBets: () => request<BetListResponse<Signal>>("/v1/bets?status=new"),
  openBets: () => request<BetListResponse<OpenBet>>("/v1/live"),
  positions: () => request<BetListResponse<OpenBet>>("/v1/positions"),
  history: () => request<BetListResponse<HistoryBet>>("/v1/bets?status=history"),
  unfilled: () => request<BetListResponse<HistoryBet>>("/v1/bets?status=unfilled"),
  analytics: () => request<AnalyticsResponse>("/v1/analytics"),
  system: () => request<SystemResponse>("/v1/system"),
  review: (signalId: string) =>
    request<ReviewResponse>(`/v1/bets/${encodeURIComponent(signalId)}/review`),
  submit: (signalId: string) =>
    request<SubmitResponse>(`/v1/bets/${encodeURIComponent(signalId)}/submit`, {
      method: "POST",
    }),
};
