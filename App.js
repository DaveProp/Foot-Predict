import React, { useEffect, useRef, useState } from "react";
import {
  View, Text, FlatList, TouchableOpacity, StyleSheet, SafeAreaView, ScrollView,
} from "react-native";
import { StatusBar } from "expo-status-bar";
import { WS_URL } from "./config";

const pct = (x) => `${Math.round(x * 100)}%`;

function ProbBar({ label, value, color }) {
  return (
    <View style={s.probRow}>
      <Text style={s.probLabel}>{label}</Text>
      <View style={s.barBg}>
        <View style={[s.barFill, { width: pct(value), backgroundColor: color }]} />
      </View>
      <Text style={s.probValue}>{pct(value)}</Text>
    </View>
  );
}

function Probabilities({ p, home, away }) {
  return (
    <View>
      <ProbBar label={home} value={p.home_win} color="#22c55e" />
      <ProbBar label="Nul" value={p.draw} color="#94a3b8" />
      <ProbBar label={away} value={p.away_win} color="#ef4444" />
    </View>
  );
}

function MatchCard({ m, onPress }) {
  return (
    <TouchableOpacity style={s.card} onPress={onPress}>
      <Text style={s.league}>{m.league} · {m.status === "live" ? `${m.minute}'` : "Terminé"}</Text>
      <View style={s.scoreRow}>
        <Text style={s.team}>{m.home}</Text>
        <Text style={s.score}>{m.score[0]} - {m.score[1]}</Text>
        <Text style={[s.team, { textAlign: "right" }]}>{m.away}</Text>
      </View>
      <Probabilities p={m.live} home="Dom." away="Ext." />
    </TouchableOpacity>
  );
}

function MatchDetail({ m, onBack }) {
  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 16 }}>
      <TouchableOpacity onPress={onBack}><Text style={s.back}>← Retour</Text></TouchableOpacity>
      <Text style={s.league}>{m.league} · {m.status === "live" ? `${m.minute}'` : "Terminé"}</Text>
      <View style={s.scoreRow}>
        <Text style={s.team}>{m.home}</Text>
        <Text style={s.score}>{m.score[0]} - {m.score[1]}</Text>
        <Text style={[s.team, { textAlign: "right" }]}>{m.away}</Text>
      </View>

      <Text style={s.section}>Probabilités en direct</Text>
      <Probabilities p={m.live} home={m.home} away={m.away} />
      <ProbBar label="+2,5 buts" value={m.live.over_2_5} color="#38bdf8" />
      <ProbBar label="2 équipes marquent" value={m.live.btts} color="#a78bfa" />
      <Text style={s.muted}>Score final le plus probable : {m.live.most_likely_score}</Text>

      <Text style={s.section}>Avant-match</Text>
      <Probabilities p={m.prematch} home={m.home} away={m.away} />
      <Text style={s.muted}>Buts attendus : {m.prematch.xg_home} - {m.prematch.xg_away}</Text>

      <Text style={s.section}>Événements</Text>
      {m.events.length === 0 && <Text style={s.muted}>Aucun événement pour le moment.</Text>}
      {m.events.map((e, i) => (
        <Text key={i} style={s.event}>{e.minute}' · {e.type} · {e.team}</Text>
      ))}
    </ScrollView>
  );
}

export default function App() {
  const [matches, setMatches] = useState([]);
  const [connected, setConnected] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const wsRef = useRef(null);

  useEffect(() => {
    let stop = false;
    const connect = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === "update") setMatches(data.matches);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!stop) setTimeout(connect, 2000); // reconnexion automatique
      };
    };
    connect();
    return () => { stop = true; wsRef.current && wsRef.current.close(); };
  }, []);

  const selected = matches.find((m) => m.id === selectedId);

  return (
    <SafeAreaView style={s.container}>
      <StatusBar style="light" />
      <View style={s.header}>
        <Text style={s.title}>⚽ Foot Predict</Text>
        <Text style={{ color: connected ? "#22c55e" : "#ef4444" }}>
          {connected ? "● En direct" : "● Hors ligne"}
        </Text>
      </View>
      {selected ? (
        <MatchDetail m={selected} onBack={() => setSelectedId(null)} />
      ) : (
        <FlatList
          data={matches}
          keyExtractor={(m) => String(m.id)}
          contentContainerStyle={{ padding: 16 }}
          renderItem={({ item }) => <MatchCard m={item} onPress={() => setSelectedId(item.id)} />}
          ListEmptyComponent={<Text style={s.muted}>En attente des matchs… (le serveur est-il lancé ?)</Text>}
          ListFooterComponent={
            <Text style={s.disclaimer}>
              Les probabilités sont des estimations statistiques, pas des certitudes. Matchs de démonstration simulés.
            </Text>
          }
        />
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0f172a" },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: 16 },
  title: { color: "#fff", fontSize: 22, fontWeight: "700" },
  card: { backgroundColor: "#1e293b", borderRadius: 14, padding: 14, marginBottom: 12 },
  league: { color: "#94a3b8", fontSize: 12, marginBottom: 8 },
  scoreRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 },
  team: { color: "#fff", fontSize: 16, fontWeight: "600", flex: 1 },
  score: { color: "#fff", fontSize: 24, fontWeight: "800", paddingHorizontal: 10 },
  probRow: { flexDirection: "row", alignItems: "center", marginVertical: 3 },
  probLabel: { color: "#cbd5e1", width: 110, fontSize: 12 },
  barBg: { flex: 1, height: 8, backgroundColor: "#334155", borderRadius: 4, overflow: "hidden" },
  barFill: { height: 8, borderRadius: 4 },
  probValue: { color: "#fff", width: 44, textAlign: "right", fontSize: 12, fontWeight: "600" },
  section: { color: "#fff", fontSize: 16, fontWeight: "700", marginTop: 20, marginBottom: 8 },
  muted: { color: "#94a3b8", marginTop: 8, fontSize: 13 },
  event: { color: "#e2e8f0", marginVertical: 2 },
  back: { color: "#38bdf8", fontSize: 16, marginBottom: 12 },
  disclaimer: { color: "#64748b", fontSize: 11, textAlign: "center", marginTop: 8 },
});
