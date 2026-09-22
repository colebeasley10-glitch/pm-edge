import { Ionicons } from "@expo/vector-icons";
import { StatusBar } from "expo-status-bar";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { api } from "./src/api";
import { colors, radius } from "./src/theme";
import type {
  AnalyticsResponse,
  DashboardResponse,
  HistoryBet,
  OpenBet,
  ReviewResponse,
  Signal,
  SubmitResponse,
  SystemResponse,
} from "./src/types";

type Tab = "Dashboard" | "Bets" | "Live" | "Analytics" | "More";
type MoreView = "Menu" | "History" | "Pending" | "Account" | "System" | "Settings";
type BetFilter = "Available" | "Filled" | "Unfilled";

const fmtMoney = (x?: number | null, signed = false) => {
  if (x == null || Number.isNaN(x)) return "—";
  const sign = signed ? (x > 0 ? "+" : x < 0 ? "-" : "") : x < 0 ? "-" : "";
  return `${sign}$${Math.abs(x).toFixed(2)}`;
};
const fmtPct = (x?: number | null) =>
  x == null || Number.isNaN(x) ? "—" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
const fmtCents = (x?: number | null) =>
  x == null || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(0)}¢`;
const laneLabel = (value?: string | null) => {
  const lane = (value ?? "").toLowerCase();
  if (lane === "price_fish") return "FISH";
  if (lane === "primary") return "PRIMARY";
  if (lane === "value") return "VALUE";
  return lane.replaceAll("_", " ").toUpperCase() || "PM EDGE";
};
const toneForLane = (value?: string | null) => {
  const lane = (value ?? "").toLowerCase();
  if (lane === "price_fish") return "orange" as const;
  if (lane === "primary") return "green" as const;
  return "blue" as const;
};

function Chip({
  label,
  tone = "default",
}: {
  label: string;
  tone?: "default" | "green" | "orange" | "blue" | "red";
}) {
  const background =
    tone === "green"
      ? "#103D31"
      : tone === "orange"
        ? "#422817"
        : tone === "blue"
          ? "#12335A"
          : tone === "red"
            ? "#421C26"
            : colors.panel2;
  const color =
    tone === "green"
      ? colors.green
      : tone === "orange"
        ? colors.orange
        : tone === "blue"
          ? colors.blue2
          : tone === "red"
            ? colors.red
            : colors.text;
  return (
    <View style={[styles.chip, { backgroundColor: background }]}>
      <Text style={[styles.chipText, { color }]}>{label}</Text>
    </View>
  );
}

function Header({
  title,
  subtitle,
  back,
}: {
  title: string;
  subtitle?: string;
  back?: () => void;
}) {
  return (
    <View style={styles.header}>
      <View style={styles.brandRow}>
        {back ? (
          <Pressable onPress={back} style={styles.iconButton}>
            <Ionicons name="chevron-back" size={22} color={colors.text} />
          </Pressable>
        ) : (
          <Ionicons name="stats-chart" size={20} color={colors.blue} />
        )}
        <Text style={styles.brand}>PM Edge</Text>
      </View>
      <Text style={styles.screenTitle}>{title}</Text>
      {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
    </View>
  );
}

function Metric({
  label,
  value,
  positive,
  compact = false,
  onPress,
}: {
  label: string;
  value: string;
  positive?: boolean;
  compact?: boolean;
  onPress?: () => void;
}) {
  return (
    <Pressable disabled={!onPress} onPress={onPress} style={[styles.metric, compact && styles.metricCompact]}>
      <Text
        style={[
          compact ? styles.metricValueCompact : styles.metricValue,
          positive ? { color: colors.green } : null,
        ]}
      >
        {value}
      </Text>
      <View style={styles.metricLabelRow}><Text style={styles.metricLabel}>{label}</Text>{onPress ? <Ionicons name="chevron-forward" size={14} color={colors.muted} /> : null}</View>
    </Pressable>
  );
}

function PillTabs<T extends string>({
  values,
  selected,
  onSelect,
}: {
  values: T[];
  selected: T;
  onSelect: (value: T) => void;
}) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.pillTabs}>
      {values.map((value) => {
        const active = value === selected;
        return (
          <Pressable
            key={value}
            onPress={() => onSelect(value)}
            style={[styles.pillTab, active && styles.pillTabActive]}
          >
            <Text style={[styles.pillTabText, active && styles.pillTabTextActive]}>{value}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

function BetCard({
  signal,
  onReview,
}: {
  signal: Signal;
  onReview: (signal: Signal) => void;
}) {
  return (
    <View style={styles.card}>
      <View style={styles.rowBetween}>
        <View style={styles.rowGap}>
          <Chip label={laneLabel(signal.lane)} tone={toneForLane(signal.lane)} />
          <Chip label={(signal.sport_title ?? "SPORT").toUpperCase()} />
        </View>
        <Text style={styles.muted}>{fmtCents(signal.recommended_limit)}</Text>
      </View>
      <Text style={styles.betTitle}>{signal.market_title ?? signal.ticker}</Text>
      <Text style={styles.betSide}>BUY {signal.side}</Text>
      <View style={styles.statRow}>
        <View>
          <Text style={styles.greenValue}>{fmtPct(signal.net_edge)}</Text>
          <Text style={styles.mutedSmall}>Edge</Text>
        </View>
        <View>
          <Text style={styles.statValue}>{fmtPct(signal.fair_probability)}</Text>
          <Text style={styles.mutedSmall}>Fair</Text>
        </View>
        <View>
          <Text style={styles.statValue}>{signal.books ?? "—"}</Text>
          <Text style={styles.mutedSmall}>Books</Text>
        </View>
        <View>
          <Text style={styles.statValue}>{fmtCents(signal.recommended_limit)}</Text>
          <Text style={styles.mutedSmall}>Max</Text>
        </View>
      </View>
      <View style={styles.buttonRow}>
        <Pressable style={styles.secondaryButton} onPress={() => onReview(signal)}>
          <Text style={styles.secondaryButtonText}>View Details</Text>
        </Pressable>
        <Pressable style={styles.primaryButton} onPress={() => onReview(signal)}>
          <Text style={styles.primaryButtonText}>Place Bet</Text>
        </Pressable>
      </View>
    </View>
  );
}

function ReviewModal({
  signal,
  visible,
  onClose,
  onSubmitted,
}: {
  signal: Signal | null;
  visible: boolean;
  onClose: () => void;
  onSubmitted: () => void;
}) {
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<SubmitResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [passcode, setPasscode] = useState("");

  const load = useCallback(async () => {
    if (!signal) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setReview(await api.review(signal.signal_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not review this bet");
    } finally {
      setLoading(false);
    }
  }, [signal]);

  useEffect(() => {
    if (visible && signal) void load();
  }, [load, signal, visible]);

  const submit = useCallback(async () => {
    if (!signal || !review?.can_submit || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await api.submit(signal.signal_id);
      setResult(response);
      onSubmitted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Order was not submitted");
      void load();
    } finally {
      setSubmitting(false);
    }
  }, [load, onSubmitted, review?.can_submit, signal, submitting]);

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.modalRoot}>
        <View style={styles.modalTopBar}>
          <Pressable onPress={onClose} style={styles.iconButton}>
            <Ionicons name="close" size={24} color={colors.text} />
          </Pressable>
          <Text style={styles.modalTitle}>Review Bet</Text>
          <Pressable onPress={load} style={styles.iconButton}>
            <Ionicons name="refresh" size={21} color={colors.blue2} />
          </Pressable>
        </View>

        {loading ? (
          <View style={styles.center}>
            <ActivityIndicator size="large" color={colors.blue} />
            <Text style={styles.muted}>Checking live price and risk guards…</Text>
          </View>
        ) : error && !review ? (
          <View style={styles.center}>
            <Ionicons name="warning-outline" size={44} color={colors.orange} />
            <Text style={styles.screenTitle}>Couldn’t review this bet</Text>
            <Text style={[styles.subtitle, { textAlign: "center" }]}>{error}</Text>
            <Pressable style={styles.primaryButtonStandalone} onPress={load}>
              <Text style={styles.primaryButtonText}>Try Again</Text>
            </Pressable>
          </View>
        ) : review ? (
          <ScrollView contentContainerStyle={styles.modalContent}>
            <View style={styles.rowGap}>
              <Chip label={laneLabel(review.lane)} tone={toneForLane(review.lane)} />
              <Chip label={(review.sport_title || "SPORT").toUpperCase()} />
            </View>
            <Text style={[styles.betTitle, { fontSize: 23 }]}>{review.market_title}</Text>
            <Text style={styles.betSide}>BUY {review.side}</Text>

            <View style={styles.metricsGrid}>
              <Metric label="Shares" value={String(review.shares)} />
              <Metric label="Max Price" value={fmtCents(review.limit_price)} />
              <Metric label="Live Ask" value={fmtCents(review.live_ask)} positive={review.price_ok} />
              <Metric label="Max Cost" value={fmtMoney(review.max_loss)} />
              <Metric label="Fair Chance" value={fmtPct(review.fair_probability)} />
              <Metric label="Estimated Edge" value={fmtPct(review.net_edge)} positive />
            </View>

            <View style={[styles.notice, review.price_ok ? styles.noticeBlue : styles.noticeRed]}>
              <Ionicons
                name={review.price_ok ? "information-circle-outline" : "alert-circle-outline"}
                size={21}
                color={review.price_ok ? colors.blue2 : colors.red}
              />
              <Text style={styles.noticeText}>{review.price_message}</Text>
            </View>

            {review.guard?.blocked ? (
              <View style={[styles.notice, styles.noticeRed]}>
                <Ionicons name="shield-outline" size={21} color={colors.red} />
                <Text style={styles.noticeText}>
                  Position guard: {(review.guard.reason ?? "blocked").replaceAll("_", " ")}
                </Text>
              </View>
            ) : (
              <View style={[styles.notice, styles.noticeGreen]}>
                <Ionicons name="shield-checkmark-outline" size={21} color={colors.green} />
                <Text style={styles.noticeText}>Portfolio checks passed.</Text>
              </View>
            )}

            <View style={styles.card}>
              <Text style={styles.sectionTitle}>Order Summary</Text>
              <View style={styles.rowBetween}><Text style={styles.muted}>Execution</Text><Text style={styles.statValue}>{review.execution === "MAKE" ? "Limit / maker" : "Buy now / taker"}</Text></View>
              <View style={styles.divider} />
              <View style={styles.rowBetween}><Text style={styles.muted}>Books</Text><Text style={styles.statValue}>{review.books ?? "—"}</Text></View>
              <View style={styles.divider} />
              <View style={styles.rowBetween}><Text style={styles.muted}>Gross profit if correct</Text><Text style={styles.greenValue}>{fmtMoney(review.gross_profit_if_correct)}</Text></View>
            </View>

            {result?.submitted ? (
              <View style={[styles.notice, styles.noticeGreen]}>
                <Ionicons name="checkmark-circle" size={24} color={colors.green} />
                <Text style={styles.noticeText}>Order sent to Kalshi. PM Edge will track it automatically.</Text>
              </View>
            ) : result?.already_exists ? (
              <View style={[styles.notice, styles.noticeBlue]}>
                <Ionicons name="checkmark-circle-outline" size={24} color={colors.blue2} />
                <Text style={styles.noticeText}>This bet was already submitted. No duplicate was sent.</Text>
              </View>
            ) : null}

            {error ? (
              <View style={[styles.notice, styles.noticeRed]}>
                <Ionicons name="warning-outline" size={21} color={colors.red} />
                <Text style={styles.noticeText}>{error}</Text>
              </View>
            ) : null}

            {!result ? (
              <>
                <Text style={styles.submitWarning}>
                  Pressing Submit sends a real Kalshi order. PM Edge checks the price and all risk guards again before sending it.
                </Text>
                <Pressable
                  style={[styles.submitButton, !review.can_submit && styles.disabledButton]}
                  disabled={!review.can_submit || submitting}
                  onPress={() => {
                    Alert.alert(
                      "Submit real Kalshi order?",
                      `BUY ${review.side} · ${review.shares} shares · max ${fmtCents(review.limit_price)} · max cost ${fmtMoney(review.max_loss)}`,
                      [
                        { text: "Cancel", style: "cancel" },
                        { text: "Submit", onPress: () => void submit() },
                      ],
                    );
                  }}
                >
                  {submitting ? (
                    <ActivityIndicator color={colors.white} />
                  ) : (
                    <Text style={styles.submitButtonText}>
                      SUBMIT TO KALSHI — {review.shares} @ {fmtCents(review.limit_price)}
                    </Text>
                  )}
                </Pressable>
              </>
            ) : (
              <Pressable style={styles.primaryButtonStandalone} onPress={onClose}>
                <Text style={styles.primaryButtonText}>Done</Text>
              </Pressable>
            )}
          </ScrollView>
        ) : null}
      </SafeAreaView>
    </Modal>
  );
}

function LiveCard({ bet }: { bet: OpenBet }) {
  const pnl = Number(bet.unrealized_pl ?? 0);
  return (
    <View style={styles.card}>
      <View style={styles.rowBetween}>
        <View style={styles.rowGap}>
          <Chip label={(bet.sport_title ?? "SPORT").toUpperCase()} />
          <Chip label={(bet.kalshi_status ?? bet.status ?? "OPEN").toUpperCase()} tone="red" />
        </View>
        <Text style={pnl >= 0 ? styles.greenValue : styles.redValue}>{fmtMoney(pnl, true)}</Text>
      </View>
      <Text style={styles.betTitle}>{bet.market_title ?? bet.ticker}</Text>
      <Text style={styles.betSide}>
        {bet.side ?? "—"} · {Number(bet.filled_shares ?? 0).toFixed(0)} @ {fmtCents(bet.average_fill_price)}
      </Text>
      <View style={styles.statRow}>
        <View>
          <Text style={styles.statValue}>{fmtMoney(bet.entry_cost)}</Text>
          <Text style={styles.mutedSmall}>Risked</Text>
        </View>
        <View>
          <Text style={styles.statValue}>{fmtCents(bet.current_bid)}</Text>
          <Text style={styles.mutedSmall}>Current</Text>
        </View>
        <View>
          <Text style={styles.statValue}>{fmtMoney(bet.current_value)}</Text>
          <Text style={styles.mutedSmall}>Value</Text>
        </View>
        <View>
          <Text style={pnl >= 0 ? styles.greenValue : styles.redValue}>
            {bet.entry_cost ? fmtPct(pnl / Number(bet.entry_cost)) : "—"}
          </Text>
          <Text style={styles.mutedSmall}>Return</Text>
        </View>
      </View>
    </View>
  );
}

function HistoryRow({ bet }: { bet: HistoryBet }) {
  const pnl = Number(bet.net_pl ?? 0);
  const won = pnl > 0;
  const lost = pnl < 0;
  return (
    <View style={styles.historyRow}>
      <View style={styles.historyMain}>
        <View style={styles.rowGap}>
          <Chip label={(bet.sport_title ?? "SPORT").toUpperCase()} />
          <Chip label={won ? "W" : lost ? "L" : "—"} tone={won ? "green" : lost ? "red" : "default"} />
        </View>
        <Text style={styles.historyTitle}>{bet.market_title ?? bet.ticker}</Text>
        <Text style={styles.mutedSmall}>
          {bet.side ?? "—"} · {Number(bet.filled_shares ?? 0).toFixed(0)} @ {fmtCents(bet.average_fill_price)}
        </Text>
      </View>
      <View style={styles.historyPnl}>
        <Text style={won ? styles.greenValue : lost ? styles.redValue : styles.statValue}>
          {fmtMoney(pnl, true)}
        </Text>
        <Text style={styles.mutedSmall}>{bet.trade_date ?? ""}</Text>
      </View>
    </View>
  );
}

function DashboardScreen({
  data,
  refresh,
  refreshing,
  onReview,
  navigate,
}: {
  data: DashboardResponse;
  refresh: () => void;
  refreshing: boolean;
  onReview: (signal: Signal) => void;
  navigate: (target: "Analytics" | "Live" | "Bets" | "History" | "Pending" | "Account") => void;
}) {
  return (
    <ScrollView
      contentContainerStyle={styles.screen}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.blue} />}
    >
      <Header title="Dashboard" subtitle="Today · PM Edge command center" />
      <Pressable
        style={[
          styles.heroProfit,
          data.actual.net_pl >= 0 ? styles.heroProfitPositive : styles.heroProfitNegative,
        ]}
        onPress={() => navigate("Analytics")}
      >
        <View>
          <Text style={[styles.heroProfitValue, { color: data.actual.net_pl >= 0 ? colors.green : colors.red }]}>{fmtMoney(data.actual.net_pl, true)}</Text>
          <Text style={styles.heroProfitLabel}>Total tracked P/L</Text>
        </View>
        <View style={styles.heroRoi}>
          <Text style={[styles.heroRoiValue, { color: data.actual.net_pl >= 0 ? colors.green : colors.red }]}>
            {fmtPct(data.actual.roi)}
          </Text>
          <Text style={styles.mutedSmall}>ROI</Text>
        </View>
      </Pressable>
      <View style={styles.metricsGrid}>
        <Metric label="Kalshi Balance" value={fmtMoney(data.account.balance)} onPress={() => navigate("Account")} />
        <Metric label="Record" value={data.actual.record} onPress={() => navigate("History")} />
        <Metric label="Amount Risked" value={fmtMoney(data.actual.risked)} onPress={() => navigate("History")} />
        <Metric label="Open Bets" value={String(data.open_count)} onPress={() => navigate("Live")} />
        <Metric label="Unfilled Orders" value={String(data.actual.unfilled)} onPress={() => navigate("Pending")} />
      </View>

      <Pressable style={styles.sectionHeader} onPress={() => navigate("Bets")}>
        <Text style={styles.sectionTitle}>New PM Edge Bets</Text>
        <Text style={styles.linkText}>{data.new_bets.length} waiting  ›</Text>
      </Pressable>
      {data.new_bets.length ? (
        data.new_bets.slice(0, 4).map((bet) => <BetCard key={bet.signal_id} signal={bet} onReview={onReview} />)
      ) : (
        <View style={styles.emptyCard}>
          <Ionicons name="checkmark-circle-outline" size={26} color={colors.green} />
          <Text style={styles.muted}>No fresh alerts waiting right now.</Text>
        </View>
      )}

      <Text style={styles.sectionTitle}>System</Text>
      <View style={styles.card}>
        <View style={styles.rowBetween}>
          <Text style={styles.systemLabel}>Model status</Text>
          <Text style={styles.greenValue}>Running</Text>
        </View>
        <View style={styles.divider} />
        <View style={styles.rowBetween}>
          <Text style={styles.systemLabel}>Odds credits today</Text>
          <Text style={styles.statValue}>{data.system.odds_credits_today ?? "—"}</Text>
        </View>
        <View style={styles.divider} />
        <View style={styles.rowBetween}>
          <Text style={styles.systemLabel}>Drawdown breaker</Text>
          <Text style={data.system.drawdown_active ? styles.redValue : styles.greenValue}>
            {data.system.drawdown_active ? "ACTIVE" : "Clear"}
          </Text>
        </View>
      </View>
    </ScrollView>
  );
}

function BetsScreen({
  items,
  positions,
  unfilled,
  refresh,
  refreshing,
  onReview,
}: {
  items: Signal[];
  positions: OpenBet[];
  unfilled: HistoryBet[];
  refresh: () => void;
  refreshing: boolean;
  onReview: (signal: Signal) => void;
}) {
  const [filter, setFilter] = useState<BetFilter>("Available");
  const data = filter === "Available" ? items : filter === "Filled" ? positions : unfilled;
  return (
    <FlatList
      style={{ flex: 1 }}
      contentContainerStyle={styles.screen}
      data={data}
      keyExtractor={(item: any, i) => item.signal_id ?? `${item.ticker}-${item.side ?? ""}-${i}`}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.blue} />}
      ListHeaderComponent={
        <View style={{ gap: 12 }}>
          <Header title="Bet Center" subtitle="Available, filled, and unfilled PM Edge bets" />
          <PillTabs values={["Available", "Filled", "Unfilled"]} selected={filter} onSelect={setFilter} />
        </View>
      }
      renderItem={({ item }: any) =>
        filter === "Available" ? <BetCard signal={item as Signal} onReview={onReview} /> : <LiveCard bet={item as OpenBet} />
      }
      ListEmptyComponent={<View style={styles.emptyCard}><Text style={styles.muted}>No {filter.toLowerCase()} bets right now.</Text></View>}
    />
  );
}

function LiveScreen({
  items,
  refresh,
  refreshing,
}: {
  items: OpenBet[];
  refresh: () => void;
  refreshing: boolean;
}) {
  return (
    <FlatList
      style={{ flex: 1 }}
      contentContainerStyle={styles.screen}
      data={items}
      keyExtractor={(item, i) => `${item.ticker}-${item.side ?? ""}-${i}`}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.blue} />}
      ListHeaderComponent={<Header title="Live Bets" subtitle="Track open positions and current value" />}
      renderItem={({ item }) => <LiveCard bet={item} />}
      ListEmptyComponent={<View style={styles.emptyCard}><Text style={styles.muted}>No open PM Edge positions right now.</Text></View>}
    />
  );
}

function BarRow({ label, value, max }: { label: string; value: number; max: number }) {
  const width = max <= 0 ? 0 : Math.max(5, Math.min(100, (Math.abs(value) / max) * 100));
  return (
    <View style={{ gap: 6 }}>
      <View style={styles.rowBetween}>
        <Text style={styles.systemLabel}>{label}</Text>
        <Text style={value >= 0 ? styles.greenValue : styles.redValue}>{fmtMoney(value, true)}</Text>
      </View>
      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${width}%`, backgroundColor: value >= 0 ? colors.green : colors.red }]} />
      </View>
    </View>
  );
}

function AnalyticsScreen({ data, history }: { data: AnalyticsResponse; history: HistoryBet[] }) {
  const [mode, setMode] = useState<"Sport" | "Category" | "Type">("Sport");
  const [period, setPeriod] = useState<"Day" | "Week" | "Month" | "Year" | "All">("Month");
  const now = new Date();
  const cutoff = new Date(now);
  if (period === "Day") cutoff.setDate(now.getDate() - 1);
  if (period === "Week") cutoff.setDate(now.getDate() - 7);
  if (period === "Month") cutoff.setDate(now.getDate() - 30);
  if (period === "Year") cutoff.setDate(now.getDate() - 365);
  const periodBets = history.filter((bet) => {
    if (period === "All") return true;
    if (!bet.trade_date) return false;
    const d = new Date(`${bet.trade_date}T12:00:00`);
    return !Number.isNaN(d.getTime()) && d >= cutoff && d <= now;
  });
  const periodProfit = periodBets.reduce((sum, bet) => sum + Number(bet.net_pl ?? 0), 0);
  const periodRisked = periodBets.reduce((sum, bet) => sum + Number(bet.entry_cost ?? 0), 0);
  const periodWins = periodBets.filter((bet) => Number(bet.net_pl ?? 0) > 0).length;
  const periodLosses = periodBets.filter((bet) => Number(bet.net_pl ?? 0) < 0).length;
  const periodRoi = periodRisked > 0 ? (periodProfit / periodRisked) * 100 : null;
  const rows = mode === "Sport" ? data.by_sport : mode === "Category" ? data.by_lane : data.by_market_type;
  const max = Math.max(1, ...rows.map((r) => Math.abs(Number(r.net_pl ?? 0))));
  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Header title="Analytics" subtitle="Track P/L by time period" />
      <PillTabs values={["Day", "Week", "Month", "Year", "All"]} selected={period} onSelect={(value) => setPeriod(value as typeof period)} />
      <View style={styles.metricsGrid}>
        <Metric label={`${period} Profit`} value={fmtMoney(periodProfit, true)} positive={periodProfit >= 0} />
        <Metric label="ROI" value={fmtPct(periodRoi)} positive={(periodRoi ?? 0) >= 0} />
        <Metric label="Record" value={`${periodWins}-${periodLosses}`} />
        <Metric label="Amount Risked" value={fmtMoney(periodRisked)} />
      </View>
      <View style={styles.card}>
        <View style={styles.rowBetween}>
          <Text style={styles.sectionTitle}>{period} Bets</Text>
          <Text style={styles.mutedSmall}>{periodBets.length} bets</Text>
        </View>
        {periodBets.length
          ? periodBets.slice(0, 20).map((bet, index) => <HistoryRow key={`${bet.ticker}-${bet.trade_date}-${index}`} bet={bet} />)
          : <Text style={styles.muted}>No settled bets in this period.</Text>}
      </View>
      <PillTabs values={["Sport", "Category", "Type"]} selected={mode} onSelect={setMode} />
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Performance by {mode}</Text>
        {rows.length ? rows.map((row, index) => {
          const label =
            mode === "Sport"
              ? row.sport_title ?? "Unknown"
              : mode === "Category"
                ? laneLabel(row.lane)
                : (row.market_type ?? "Unknown").replaceAll("_", " ").toUpperCase();
          return (
            <View key={`${label}-${index}`} style={{ gap: 10 }}>
              <BarRow label={label} value={Number(row.net_pl ?? 0)} max={max} />
              <View style={styles.miniStats}>
                <Text style={styles.mutedSmall}>{row.record}</Text>
                <Text style={styles.mutedSmall}>{fmtPct(row.roi)} ROI</Text>
                <Text style={styles.mutedSmall}>{row.bets} bets</Text>
              </View>
              {index !== rows.length - 1 ? <View style={styles.divider} /> : null}
            </View>
          );
        }) : <Text style={styles.muted}>Not enough settled data yet.</Text>}
      </View>
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Model Funnel</Text>
        <View style={styles.statRow}>
          <View><Text style={styles.statValue}>{data.funnel.qualified}</Text><Text style={styles.mutedSmall}>Qualified</Text></View>
          <View><Text style={styles.statValue}>{data.funnel.alerts}</Text><Text style={styles.mutedSmall}>Alerts</Text></View>
          <View><Text style={styles.statValue}>{data.funnel.placed}</Text><Text style={styles.mutedSmall}>Placed</Text></View>
          <View><Text style={styles.statValue}>{data.funnel.filled}</Text><Text style={styles.mutedSmall}>Filled</Text></View>
        </View>
      </View>
    </ScrollView>
  );
}

function HistoryScreen({ items, back }: { items: HistoryBet[]; back: () => void }) {
  return (
    <FlatList
      style={{ flex: 1 }}
      contentContainerStyle={styles.screen}
      data={items}
      keyExtractor={(item, i) => `${item.ticker}-${item.trade_date ?? ""}-${i}`}
      ListHeaderComponent={<Header title="History" subtitle="All bets and results" back={back} />}
      renderItem={({ item }) => <HistoryRow bet={item} />}
      ListEmptyComponent={<View style={styles.emptyCard}><Text style={styles.muted}>No tracked history yet.</Text></View>}
    />
  );
}


function PendingScreen({ items, back }: { items: HistoryBet[]; back: () => void }) {
  return (
    <FlatList
      style={{ flex: 1 }}
      contentContainerStyle={styles.screen}
      data={items}
      keyExtractor={(item, i) => `${item.ticker}-pending-${i}`}
      ListHeaderComponent={<Header title="Unfilled Orders" subtitle="Orders waiting for a fill" back={back} />}
      renderItem={({ item }) => <HistoryRow bet={item} />}
      ListEmptyComponent={<View style={styles.emptyCard}><Text style={styles.muted}>No unfilled orders right now.</Text></View>}
    />
  );
}

function AccountScreen({ data, back }: { data: DashboardResponse; back: () => void }) {
  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Header title="Kalshi Account" subtitle="Connection and tracked balance" back={back} />
      <View style={styles.statusBanner}>
        <Ionicons name={data.account.connected ? "checkmark-circle" : "alert-circle"} size={20} color={data.account.connected ? colors.green : colors.orange} />
        <Text style={styles.statusBannerText}>{data.account.connected ? "Read connection active" : "Account read connection unavailable"}</Text>
      </View>
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Balance</Text>
        <Text style={styles.heroProfitValue}>{fmtMoney(data.account.balance)}</Text>
        <Text style={styles.muted}>PM Edge reads this value from the connected account. Order placement uses a separate protected write connection.</Text>
      </View>
    </ScrollView>
  );
}

function SystemScreen({ data, back }: { data: SystemResponse; back: () => void }) {
  const drawdownActive = Boolean(data.drawdown?.active);
  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Header title="System Status" subtitle="Live health and model information" back={back} />
      <View style={styles.statusBanner}>
        <Ionicons name="checkmark-circle" size={20} color={colors.green} />
        <Text style={styles.statusBannerText}>PM Edge Mobile API Connected</Text>
      </View>
      <View style={styles.metricsGrid}>
        <Metric label="Model" value="Running" positive />
        <Metric label="Odds Credits" value={String(data.odds_credits_today ?? "—")} />
        <Metric label="Drawdown" value={drawdownActive ? "ACTIVE" : "Clear"} positive={!drawdownActive} />
        <Metric label="Signals" value={String(data.tracking?.signals ?? "—")} />
      </View>
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Tracking Coverage</Text>
        <View style={styles.rowBetween}><Text style={styles.systemLabel}>Candidate decisions</Text><Text style={styles.statValue}>{data.tracking?.candidate_decisions ?? "—"}</Text></View>
        <View style={styles.divider} />
        <View style={styles.rowBetween}><Text style={styles.systemLabel}>CLV rows</Text><Text style={styles.statValue}>{data.tracking?.clv_rows ?? "—"}</Text></View>
        <View style={styles.divider} />
        <View style={styles.rowBetween}><Text style={styles.systemLabel}>Fill audit rows</Text><Text style={styles.statValue}>{data.tracking?.fill_rows ?? "—"}</Text></View>
        <View style={styles.divider} />
        <View style={styles.rowBetween}><Text style={styles.systemLabel}>Signal outcomes</Text><Text style={styles.statValue}>{data.tracking?.signal_outcomes ?? "—"}</Text></View>
      </View>
    </ScrollView>
  );
}

function SettingsScreen({ back }: { back: () => void }) {
  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Header title="Settings" subtitle="App preferences and safety limits" back={back} />
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Risk Controls</Text>
        <View style={styles.rowBetween}><Text style={styles.systemLabel}>Max per order</Text><Text style={styles.statValue}>$10.00</Text></View>
        <View style={styles.divider} />
        <View style={styles.rowBetween}><Text style={styles.systemLabel}>Total open-risk cap</Text><Text style={styles.statValue}>$40.00</Text></View>
        <View style={styles.divider} />
        <View style={styles.rowBetween}><Text style={styles.systemLabel}>Max open per sport</Text><Text style={styles.statValue}>3</Text></View>
        <View style={styles.divider} />
        <View style={styles.rowBetween}><Text style={styles.systemLabel}>Per-sport dollar cap</Text><Text style={styles.greenValue}>Disabled</Text></View>
      </View>
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Execution</Text>
        <Text style={styles.muted}>Real-money orders still require your explicit Review & Submit confirmation. Kalshi write credentials never live inside the phone app.</Text>
      </View>
    </ScrollView>
  );
}

function MoreMenu({ select }: { select: (view: MoreView) => void }) {
  const items: Array<[MoreView, string, keyof typeof Ionicons.glyphMap]> = [
    ["History", "Bet History", "time-outline"],
    ["System", "System Status", "pulse-outline"],
    ["Settings", "Settings", "settings-outline"],
  ];
  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Header title="More" subtitle="History, system health, and settings" />
      {items.map(([view, label, icon]) => (
        <Pressable key={view} style={styles.menuRow} onPress={() => select(view)}>
          <View style={styles.menuIcon}><Ionicons name={icon} size={20} color={colors.blue2} /></View>
          <Text style={styles.menuLabel}>{label}</Text>
          <Ionicons name="chevron-forward" size={20} color={colors.muted} />
        </Pressable>
      ))}
    </ScrollView>
  );
}

function BottomNav({ tab, setTab }: { tab: Tab; setTab: (tab: Tab) => void }) {
  const tabs: Array<[Tab, keyof typeof Ionicons.glyphMap]> = [
    ["Dashboard", "home-outline"],
    ["Bets", "receipt-outline"],
    ["Live", "pulse-outline"],
    ["Analytics", "bar-chart-outline"],
    ["More", "menu-outline"],
  ];
  return (
    <View style={styles.nav}>
      {tabs.map(([name, icon]) => {
        const active = tab === name;
        return (
          <Pressable key={name} style={styles.navItem} onPress={() => setTab(name)}>
            <Ionicons name={icon} size={22} color={active ? colors.blue : colors.muted} />
            <Text style={[styles.navText, active && { color: colors.blue }]}>{name}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("Dashboard");
  const [moreView, setMoreView] = useState<MoreView>("Menu");
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [bets, setBets] = useState<Signal[]>([]);
  const [openBets, setOpenBets] = useState<OpenBet[]>([]);
  const [positions, setPositions] = useState<OpenBet[]>([]);
  const [history, setHistory] = useState<HistoryBet[]>([]);
  const [unfilled, setUnfilled] = useState<HistoryBet[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsResponse | null>(null);
  const [system, setSystem] = useState<SystemResponse | null>(null);
  const [selected, setSelected] = useState<Signal | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [passcode, setPasscode] = useState("");

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const [d, b, o, p, h, u, a, s] = await Promise.all([
        api.dashboard(),
        api.newBets(),
        api.openBets(),
        api.positions(),
        api.history(),
        api.unfilled(),
        api.analytics(),
        api.system(),
      ]);
      setDashboard(d);
      setBets(b.items);
      setOpenBets(o.items);
      setPositions(p.items);
      setHistory(h.items);
      setUnfilled(u.items);
      setAnalytics(a);
      setSystem(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load PM Edge");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const content = useMemo(() => {
    if (!dashboard && refreshing) {
      return <View style={styles.center}><ActivityIndicator color={colors.blue} size="large" /></View>;
    }
    if (!dashboard) {
      return (
        <View style={styles.center}>
          <Ionicons name="cloud-offline-outline" size={48} color={colors.muted} />
          <Text style={styles.screenTitle}>PM Edge is not connected yet</Text>
          <Text style={[styles.subtitle, { textAlign: "center" }]}>
            {error ?? "Connect the mobile API to continue."}
          </Text>
          {error?.includes("sign-in required") ? (
            <>
              <TextInput
                value={passcode}
                onChangeText={setPasscode}
                placeholder="Access code"
                placeholderTextColor={colors.muted}
                secureTextEntry
                autoCapitalize="none"
                style={{ width: "78%", maxWidth: 360, backgroundColor: colors.panel2, color: colors.text, borderRadius: 12, paddingHorizontal: 16, paddingVertical: 14, marginTop: 18 }}
              />
              <Pressable style={styles.primaryButtonStandalone} onPress={async () => {
                try {
                  const r = await fetch("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passcode }) });
                  if (!r.ok) throw new Error("Incorrect access code");
                  setPasscode("");
                  await refresh();
                } catch (e) { setError(e instanceof Error ? e.message : "Sign-in failed"); }
              }}>
                <Text style={styles.primaryButtonText}>Connect PM Edge</Text>
              </Pressable>
            </>
          ) : (
            <Pressable style={styles.primaryButtonStandalone} onPress={refresh}>
              <Text style={styles.primaryButtonText}>Try Again</Text>
            </Pressable>
          )}
        </View>
      );
    }

    if (tab === "Dashboard") return <DashboardScreen data={dashboard} refresh={refresh} refreshing={refreshing} onReview={setSelected} navigate={(target) => {
      if (target === "Analytics" || target === "Live" || target === "Bets") { setTab(target); setMoreView("Menu"); return; }
      setTab("More"); setMoreView(target);
    }} />;
    if (tab === "Bets") return <BetsScreen items={bets} positions={positions} unfilled={unfilled} refresh={refresh} refreshing={refreshing} onReview={setSelected} />;
    if (tab === "Live") return <LiveScreen items={openBets} refresh={refresh} refreshing={refreshing} />;
    if (tab === "Analytics" && analytics) return <AnalyticsScreen data={analytics} history={history} />;

    if (moreView === "History") return <HistoryScreen items={history} back={() => setMoreView("Menu")} />;
    if (moreView === "Pending") return <PendingScreen items={unfilled} back={() => setMoreView("Menu")} />;
    if (moreView === "Account") return <AccountScreen data={dashboard} back={() => setMoreView("Menu")} />;
    if (moreView === "System" && system) return <SystemScreen data={system} back={() => setMoreView("Menu")} />;
    if (moreView === "Settings") return <SettingsScreen back={() => setMoreView("Menu")} />;
    return <MoreMenu select={setMoreView} />;
  }, [analytics, bets, dashboard, error, history, moreView, openBets, positions, passcode, refresh, refreshing, system, tab, unfilled]);

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="light" />
      <View style={styles.body}>{content}</View>
      <BottomNav
        tab={tab}
        setTab={(next) => {
          setTab(next);
          if (next !== "More") setMoreView("Menu");
        }}
      />
      <ReviewModal
        signal={selected}
        visible={selected != null}
        onClose={() => setSelected(null)}
        onSubmitted={() => void refresh()}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  body: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 28, gap: 16 },
  screen: { padding: 16, paddingBottom: 30, gap: 12 },
  header: { gap: 4, marginBottom: 8 },
  brandRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  brand: { color: colors.text, fontWeight: "800", fontSize: 16 },
  screenTitle: { color: colors.text, fontSize: 29, fontWeight: "800" },
  subtitle: { color: colors.muted, fontSize: 14 },
  iconButton: { width: 30, height: 30, alignItems: "center", justifyContent: "center", marginLeft: -6 },
  heroProfit: { borderWidth: 1, borderRadius: radius.lg, padding: 18, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  heroProfitPositive: { backgroundColor: "#0C2F2A", borderColor: "#17614C" },
  heroProfitNegative: { backgroundColor: "#321419", borderColor: "#7A2935" },
  heroProfitValue: { color: colors.green, fontSize: 30, fontWeight: "900" },
  heroProfitLabel: { color: colors.muted, marginTop: 3, fontSize: 12 },
  heroRoi: { alignItems: "flex-end" },
  heroRoiValue: { color: colors.green, fontSize: 20, fontWeight: "800" },
  metricsGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  metric: { backgroundColor: colors.panel, borderColor: colors.border, borderWidth: 1, borderRadius: radius.md, padding: 14, minWidth: "47%", flexGrow: 1 },
  metricCompact: { minWidth: 0 },
  metricValue: { color: colors.text, fontSize: 22, fontWeight: "800" },
  metricValueCompact: { color: colors.text, fontSize: 18, fontWeight: "800" },
  metricLabel: { color: colors.muted, fontSize: 12, marginTop: 4 },
  metricLabelRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 6 },
  sectionTitle: { color: colors.text, fontSize: 18, fontWeight: "800" },
  linkText: { color: colors.blue2, fontWeight: "700" },
  card: { backgroundColor: colors.panel, borderColor: colors.border, borderWidth: 1, borderRadius: radius.lg, padding: 14, gap: 12 },
  emptyCard: { backgroundColor: colors.panel, borderColor: colors.border, borderWidth: 1, borderRadius: radius.lg, padding: 22, alignItems: "center", gap: 8 },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 },
  rowGap: { flexDirection: "row", gap: 6, flexWrap: "wrap" },
  chip: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: 8 },
  chipText: { fontSize: 11, fontWeight: "800" },
  betTitle: { color: colors.text, fontSize: 17, fontWeight: "800" },
  betSide: { color: colors.muted, fontWeight: "700" },
  statRow: { flexDirection: "row", justifyContent: "space-between" },
  statValue: { color: colors.text, fontSize: 17, fontWeight: "800" },
  greenValue: { color: colors.green, fontSize: 17, fontWeight: "800" },
  redValue: { color: colors.red, fontSize: 17, fontWeight: "800" },
  muted: { color: colors.muted, fontSize: 14 },
  mutedSmall: { color: colors.muted, fontSize: 11, marginTop: 2 },
  buttonRow: { flexDirection: "row", gap: 8 },
  primaryButton: { backgroundColor: colors.blue, padding: 12, borderRadius: radius.sm, flex: 1, alignItems: "center" },
  primaryButtonStandalone: { backgroundColor: colors.blue, paddingHorizontal: 24, paddingVertical: 13, borderRadius: radius.sm },
  primaryButtonText: { color: colors.white, fontWeight: "800" },
  secondaryButton: { backgroundColor: colors.panel2, borderColor: colors.border, borderWidth: 1, padding: 12, borderRadius: radius.sm, flex: 1, alignItems: "center" },
  secondaryButtonText: { color: colors.text, fontWeight: "700" },
  systemLabel: { color: colors.text, fontWeight: "700" },
  divider: { height: 1, backgroundColor: colors.border },
  nav: { flexDirection: "row", backgroundColor: "#091521", borderTopColor: colors.border, borderTopWidth: 1, paddingTop: 8, paddingBottom: 10 },
  navItem: { flex: 1, alignItems: "center", gap: 3 },
  navText: { color: colors.muted, fontSize: 10, fontWeight: "700" },
  pillTabs: { gap: 8, paddingVertical: 2 },
  pillTab: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, backgroundColor: colors.panel2, borderWidth: 1, borderColor: colors.border },
  pillTabActive: { backgroundColor: colors.blue, borderColor: colors.blue },
  pillTabText: { color: colors.muted, fontWeight: "800", fontSize: 12 },
  pillTabTextActive: { color: colors.white },
  barTrack: { height: 8, backgroundColor: colors.panel2, borderRadius: 999, overflow: "hidden" },
  barFill: { height: 8, borderRadius: 999 },
  miniStats: { flexDirection: "row", justifyContent: "space-between" },
  historyRow: { flexDirection: "row", justifyContent: "space-between", backgroundColor: colors.panel, borderBottomColor: colors.border, borderBottomWidth: 1, paddingVertical: 14, paddingHorizontal: 4 },
  historyMain: { flex: 1, gap: 6, paddingRight: 10 },
  historyTitle: { color: colors.text, fontWeight: "800", fontSize: 15 },
  historyPnl: { alignItems: "flex-end", justifyContent: "center", gap: 4 },
  statusBanner: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: "#0C2F2A", borderColor: "#17614C", borderWidth: 1, borderRadius: radius.md, padding: 12 },
  statusBannerText: { color: colors.green, fontWeight: "800" },
  menuRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.panel, borderColor: colors.border, borderWidth: 1, borderRadius: radius.md, padding: 15 },
  menuIcon: { width: 36, height: 36, borderRadius: 10, backgroundColor: colors.panel2, alignItems: "center", justifyContent: "center" },
  menuLabel: { flex: 1, color: colors.text, fontSize: 16, fontWeight: "800" },
  modalRoot: { flex: 1, backgroundColor: colors.bg },
  modalTopBar: { height: 54, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 12, borderBottomColor: colors.border, borderBottomWidth: 1 },
  modalTitle: { color: colors.text, fontWeight: "900", fontSize: 17 },
  modalContent: { padding: 16, paddingBottom: 36, gap: 14 },
  notice: { borderRadius: radius.md, padding: 13, flexDirection: "row", gap: 10, alignItems: "flex-start", borderWidth: 1 },
  noticeBlue: { backgroundColor: "#102B48", borderColor: "#1C4C78" },
  noticeGreen: { backgroundColor: "#0D3127", borderColor: "#176047" },
  noticeRed: { backgroundColor: "#381A22", borderColor: "#71313C" },
  noticeText: { color: colors.text, flex: 1, lineHeight: 19 },
  submitWarning: { color: colors.muted, fontSize: 12, lineHeight: 18, textAlign: "center" },
  submitButton: { backgroundColor: colors.blue, borderRadius: radius.md, padding: 15, alignItems: "center" },
  disabledButton: { opacity: 0.42 },
  submitButtonText: { color: colors.white, fontWeight: "900", fontSize: 13 },
});
