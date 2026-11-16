import { useState } from "react";
import { NewsItem, usePoll } from "../api";
import { Card, Empty } from "../components/ui";
import { NewsList } from "./Stock";

const KINDS = [["", "Alle"], ["news", "Nachrichten"], ["filing", "Pflichtmeldungen (SEC)"], ["adhoc", "Ad-hoc"], ["rating", "Analysten"], ["social", "Social Media"]];

export default function News() {
  const [kind, setKind] = useState("");
  const { data } = usePoll<NewsItem[]>(`/api/news?limit=200${kind ? `&kind=${kind}` : ""}`, 30000);
  return (
    <div className="page">
      <Card title="Nachrichten, Meldungen und Social Media" right={
        <div className="seg">{KINDS.map(([k, l]) => <button key={k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>{l}</button>)}</div>}>
        {!data ? <Empty>Lade …</Empty> : data.length ? <NewsList items={data} showSymbol /> : <Empty>Noch keine Meldungen.</Empty>}
        <p className="tiny muted">Stimmung: FinBERT bzw. VADER mit Finanzlexikon, −1 (negativ) bis +1 (positiv). Ereignistyp per Regeln erkannt.
          Für Features zählt der Empfangszeitpunkt, nicht der Veröffentlichungszeitstempel.</p>
      </Card>
    </div>
  );
}
