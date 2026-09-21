import * as Linking from "expo-linking";
import type {
  AnalyticsResponse,
  BetListResponse,
  DashboardResponse,
  HistoryBet,
  OpenBet,
  Signal,
  SystemResponse,
} from "./types";

const API_BASE = process.env.EXPO_PUBLIC_PM_EDGE_API_BASE_URL?.replace(/\/$/, "") ?? "";
const API_TOKEN = process.env.EXPO_PUBLIC_PM_EDGE_API_TOKEN ?? "";

function headers() {
  return {
    Accept: "application/json",
    Authorization: `Bearer ${API_TOKEN}`,
  };
}

async function request<T>(path: string): Promise<T> {
  if (!API_BASE || !API_TOKEN) {
    throw new Error("PM Edge mobile API is not configured");
  }
  const response = await fetch(`${API_BASE}${path}`, { headers: headers() });
  if (!response.ok) {
    throw new Error(`PM Edge API error ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  dashboard: () => request<DashboardResponse>("/v1/dashboard"),
  newBets: () => request<BetListResponse<Signal>>("/v1/bets?status=new"),
  openBets: () => request<BetListResponse<OpenBet>>("/v1/bets?status=open"),
  history: () => request<BetListResponse<HistoryBet>>("/v1/bets?status=history"),
  analytics: () => request<AnalyticsResponse>("/v1/analytics"),
  system: () => request<SystemResponse>("/v1/system"),
  async openApproval(signalId: string) {
    const payload = await request<{ approval_url: string }>(
      `/v1/bets/${encodeURIComponent(signalId)}/approval`,
    );
    await Linking.openURL(payload.approval_url);
  },
};
