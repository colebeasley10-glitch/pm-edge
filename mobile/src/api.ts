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
const API_TOKEN = process.env.EXPO_PUBLIC_PM_EDGE_API_TOKEN ?? "";

function headers() {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    Authorization: `Bearer ${API_TOKEN}`,
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_BASE || !API_TOKEN) {
    throw new Error("PM Edge mobile API is not configured");
  }
  const response = await fetch(`${API_BASE}${path}`, {
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
  history: () => request<BetListResponse<HistoryBet>>("/v1/bets?status=history"),
  analytics: () => request<AnalyticsResponse>("/v1/analytics"),
  system: () => request<SystemResponse>("/v1/system"),
  review: (signalId: string) =>
    request<ReviewResponse>(`/v1/bets/${encodeURIComponent(signalId)}/review`),
  submit: (signalId: string) =>
    request<SubmitResponse>(`/v1/bets/${encodeURIComponent(signalId)}/submit`, {
      method: "POST",
    }),
};
