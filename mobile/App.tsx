import { Ionicons } from "@expo/vector-icons";
import { StatusBar } from "expo-status-bar";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { api } from "./src/api";
import { colors, radius } from "./src/theme";
import type { DashboardResponse, Signal } from "./src/types";

type Tab = "Dashboard" | "Bets" | "Live" | "Analytics" | "More";

const money = (x?: number | null) =>
  x == null || Number.isNaN(x) ? "—" : `${x >= 0 ? "+" : "-"}$${Math.abs(x).toFixed(2)}`;
const pct = (x?: number | null) =>
  x == null || Number.isNaN(x) ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
const cents = (x?: number | null) =>
  x == null || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(0)}¢`;

function Chip({ label, tone = "default" }: { label: string; tone?: "default" | "green" | "orange" | "blue" }) {
  const background =
    tone === "green" ? "#103D31" : tone === "orange" ? "#422817" : tone === "blue" ? "#12335A" : colors.panel2;
  const color =
    tone === "green" ? colors.green : tone === "orange" ? colors.orange : tone === "blue" ? colors.blue2 : colors.text;
  return (
    <View style={[styles.chip, { backgroundColor: background }]}>
      <Text style={[styles.chipText, { color }]}>{label}</Text>
    </View>
  );
}

function Metric({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <View style={styles.metric}>
      <Text style={[styles.metricValue, positive ? { color: colors.green } : null]}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function BetCard({ signal }: { signal: Signal }) {
  const lane = (signal.lane ?? "primary").replace("_", " ");
  const tone = lane === "price fish" ? "orange" : lane === "primary" ? "green" : "blue";
  return (
    <View style={styles.card}>
      <View style={styles.rowBetween}>
        <View style={styles.rowGap}>
          <Chip label={lane.toUpperCase()} tone={tone as "green" | "orange" | "blue"} />
          <Chip label={(signal.sport_title ?? "SPORT").toUpperCase()} />
        </View>
        <Text style={styles.muted}>{cents(signal.recommended_limit)}</Text>
      </View>
      <Text style={styles.betTitle}>{signal.market_title ?? signal.ticker}</Text>
      <Text style={styles.betSide}>{signal.side}</Text>
      <View style={styles.statRow}>
        <View>
          <Text style={styles.greenValue}>{pct(signal.net_edge)}</Text>
          <Text style={styles.mutedSmall}>Edge</Text>
        </View>
        <View>
          <Text style={styles.statValue}>{pct(signal.fair_probability)}</Text>
          <Text style={styles.mutedSmall}>Fair</Text>
        </View>
        <View>
          <Text style={styles.statValue}>{signal.books ?? "—"}</Text>
          <Text style={styles.mutedSmall}>Books</Text>
        </View>
        <View>
          <Text style={styles.statValue}>{cents(signal.recommended_limit)}</Text>
          <Text style={styles.mutedSmall}>Max</Text>
        </View>
      </View>
      <View style={styles.buttonRow}>
        <Pressable style={styles.secondaryButton}>
          <Text style={styles.secondaryButtonText}>View Details</Text>
        </Pressable>
        <Pressable
          style={styles.primaryButton}
          onPress={() => api.openApproval(signal.signal_id).catch(() => undefined)}
        >
          <Text style={styles.primaryButtonText}>Place Bet</Text>
        </Pressable>
      </View>
    </View>
  );
}

function Header({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <View style={styles.header}>
      <View style={styles.brandRow}>
        <Ionicons name="stats-chart" size={20} color={colors.blue} />
        <Text style={styles.brand}>PM Edge</Text>
      </View>
      <Text style={styles.screenTitle}>{title}</Text>
      <Text style={styles.subtitle}>{subtitle}</Text>
    </View>
  );
}

function Dashboard({ data, refresh, refreshing }: { data: DashboardResponse; refresh: () => void; refreshing: boolean }) {
  return (
    <ScrollView
      contentContainerStyle={styles.screen}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.blue} />}
    >
      <Header title="Dashboard" subtitle="Your PM Edge command center" />
      <View style={styles.metricsGrid}>
        <Metric label="Profit / Loss" value={money(data.actual.net_pl)} positive={(data.actual.net_pl ?? 0) >= 0} />
        <Metric label="ROI" value={pct(data.actual.roi)} positive={(data.actual.roi ?? 0) >= 0} />
        <Metric label="Record" value={data.actual.record} />
        <Metric label="Amount Risked" value={`$${data.actual.risked.toFixed(2)}`} />
        <Metric label="Open Bets" value={String(data.open_count)} />
        <Metric label="Unfilled" value={String(data.actual.unfilled)} />
      </View>
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>New PM Edge Bets</Text>
        <Text style={styles.linkText}>View all</Text>
      </View>
      {data.new_bets.length ? data.new_bets.slice(0, 4).map((b) => <BetCard signal={b} key={b.signal_id} />) : (
        <View style={styles.emptyCard}><Text style={styles.muted}>No fresh alerts waiting right now.</Text></View>
      )}
      <Text style={styles.sectionTitle}>System</Text>
      <View style={styles.card}>
        <View style={styles.rowBetween}>
          <Text style={styles.systemLabel}>Model</Text>
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

function BetsScreen({ items, refresh, refreshing }: { items: Signal[]; refresh: () => void; refreshing: boolean }) {
  return (
    <FlatList
      style={{ flex: 1 }}
      contentContainerStyle={styles.screen}
      data={items}
      keyExtractor={(item) => item.signal_id}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.blue} />}
      ListHeaderComponent={<Header title="Bet Center" subtitle="All model bets, ranked by edge" />}
      renderItem={({ item }) => <BetCard signal={item} />}
      ListEmptyComponent={<View style={styles.emptyCard}><Text style={styles.muted}>No fresh PM Edge bets.</Text></View>}
    />
  );
}

function Placeholder({ title, subtitle, children }: React.PropsWithChildren<{ title: string; subtitle: string }>) {
  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Header title={title} subtitle={subtitle} />
      {children}
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
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [bets, setBets] = useState<Signal[]>([]);
  const [open, setOpen] = useState<Record<string, unknown>[]>([]);
  const [analytics, setAnalytics] = useState<Record<string, unknown> | null>(null);
  const [system, setSystem] = useState<Record<string, unknown> | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const [d, b, o, a, s] = await Promise.all([
        api.dashboard(),
        api.bets("new"),
        api.bets("open"),
        api.analytics(),
        api.system(),
      ]);
      setDashboard(d);
      setBets(b.items as Signal[]);
      setOpen(o.items);
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
          <Text style={[styles.subtitle, { textAlign: "center" }]}>{error ?? "Configure the mobile API to continue."}</Text>
          <Pressable style={styles.primaryButtonStandalone} onPress={refresh}><Text style={styles.primaryButtonText}>Try Again</Text></Pressable>
        </View>
      );
    }
    if (tab === "Dashboard") return <Dashboard data={dashboard} refresh={refresh} refreshing={refreshing} />;
    if (tab === "Bets") return <BetsScreen items={bets} refresh={refresh} refreshing={refreshing} />;
    if (tab === "Live") {
      return (
        <Placeholder title="Live Bets" subtitle="Track open positions and current exposure">
          <Metric label="Open positions" value={String(open.length)} />
          <View style={styles.emptyCard}><Text style={styles.muted}>Live market cards populate from your PM Edge API.</Text></View>
        </Placeholder>
      );
    }
    if (tab === "Analytics") {
      const actual = analytics?.actual as { record?: string; net_pl?: number; roi?: number } | undefined;
      return (
        <Placeholder title="Analytics" subtitle="See what is actually making money">
          <View style={styles.metricsGrid}>
            <Metric label="Record" value={actual?.record ?? dashboard.actual.record} />
            <Metric label="Profit" value={money(actual?.net_pl ?? dashboard.actual.net_pl)} positive />
            <Metric label="ROI" value={pct(actual?.roi ?? dashboard.actual.roi)} positive />
            <Metric label="Signals Settled" value={String(dashboard.model.settled ?? 0)} />
          </View>
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Breakdowns</Text>
            <Text style={styles.muted}>By sport · Primary / Value / Price Fish · market type · full signal set</Text>
          </View>
        </Placeholder>
      );
    }
    return (
      <Placeholder title="System Status" subtitle="Live health and model information">
        <View style={styles.card}>
          <View style={styles.rowBetween}><Text style={styles.systemLabel}>Mobile API</Text><Text style={styles.greenValue}>Connected</Text></View>
          <View style={styles.divider} />
          <View style={styles.rowBetween}><Text style={styles.systemLabel}>Model</Text><Text style={styles.greenValue}>Running</Text></View>
          <View style={styles.divider} />
          <View style={styles.rowBetween}><Text style={styles.systemLabel}>Tracking</Text><Text style={styles.statValue}>{String((system?.tracking as { signals?: number } | undefined)?.signals ?? "—")} signals</Text></View>
        </View>
      </Placeholder>
    );
  }, [analytics, bets, dashboard, error, open.length, refresh, refreshing, system, tab]);

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="light" />
      <View style={styles.body}>{content}</View>
      <BottomNav tab={tab} setTab={setTab} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  body: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 28, gap: 16 },
  screen: { padding: 16, paddingBottom: 28, gap: 12 },
  header: { gap: 4, marginBottom: 8 },
  brandRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  brand: { color: colors.text, fontWeight: "800", fontSize: 16 },
  screenTitle: { color: colors.text, fontSize: 29, fontWeight: "800" },
  subtitle: { color: colors.muted, fontSize: 14 },
  metricsGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  metric: { backgroundColor: colors.panel, borderColor: colors.border, borderWidth: 1, borderRadius: radius.md, padding: 14, minWidth: "47%", flexGrow: 1 },
  metricValue: { color: colors.text, fontSize: 22, fontWeight: "800" },
  metricLabel: { color: colors.muted, fontSize: 12, marginTop: 4 },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 6 },
  sectionTitle: { color: colors.text, fontSize: 18, fontWeight: "800" },
  linkText: { color: colors.blue2, fontWeight: "700" },
  card: { backgroundColor: colors.panel, borderColor: colors.border, borderWidth: 1, borderRadius: radius.lg, padding: 14, gap: 12 },
  emptyCard: { backgroundColor: colors.panel, borderColor: colors.border, borderWidth: 1, borderRadius: radius.lg, padding: 22 },
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
});
