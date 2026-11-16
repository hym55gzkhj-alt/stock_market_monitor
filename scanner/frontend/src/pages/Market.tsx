import { Link } from "react-router-dom";
import { useLiveData } from "../App";
import { fmt, pct, time, usePoll } from "../api";
import { Card, Change, Empty, Kpi, Sparkline } from "../components/ui";

type Market = {
  asof: string; benchmarks: { symbol: string; name: string; price: number; change: number; sparkline: number[] }[];
  breadth: { up: number; down: number; total: number }; avg_change: number; sectors: { sector: string; change: number; n: number }[]; unusual_count: number;
};

export default function MarketPage() {
  const { data } = usePoll<Market>("/api/market", 30000);
  const { rows } = useLiveData();
  if (!data?.benchmarks) return <div className="page"><Empty>Lade Marktdaten …</Empty></div>;
  const maxAbs = Math.max(0.005, ...data.sectors.map((s) => Math.abs(s.change)));
  const movers = [...(rows ?? [])].sort((a, b) => b.day_change - a.day_change);
  const volume = [...(rows ?? [])].filter((r) => r.rvol != null).sort((a, b) => (b.rvol ?? 0) - (a.rvol ?? 0)).slice(0, 10);
  return (
    <div className="page">
      <div className="kpis">
        {data.benchmarks.map((b) => <Kpi key={b.symbol} label={b.name} value={fmt(b.price, 2)} sub={<><Change value={b.change} /> <Sparkline values={b.sparkline} width={80} height={20} /></>} />)}
        <Kpi label="Ø Veränderung Universum" value={<Change value={data.avg_change} />} sub={`Stand ${time(data.asof)}`} />
        <Kpi label="Steigend / fallend" value={`${data.breadth.up} / ${data.breadth.down}`} sub={`von ${data.breadth.total}`} />
      </div>
      <div className="grid-2">
        <Card title="Sektoren heute">
          <ul className="sector-bars">
            {data.sectors.map((s) => (
              <li key={s.sector}>
                <span className="small">{s.sector} <span className="muted">({s.n})</span></span>
                <div className="sbar"><span className={s.change >= 0 ? "pos" : "neg"} style={{ width: `${(Math.abs(s.change) / maxAbs) * 50}%`, [s.change >= 0 ? "left" : "right"]: "50%" }} /></div>
                <span className={`num small ${s.change >= 0 ? "up" : "down"}`}>{pct(s.change, 2)}</span>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Höchstes relatives Volumen">
          <table className="table compact">
            <thead><tr><th>Aktie</th><th className="num">Volumen</th><th className="num">Heute</th></tr></thead>
            <tbody>{volume.map((r) => <tr key={r.symbol}><td><Link to={`/aktie/${r.symbol}`} className="sym">{r.symbol}</Link> <span className="small muted">{r.name}</span></td>
              <td className="num">{fmt(r.rvol, 1)}×</td><td className="num"><Change value={r.day_change} /></td></tr>)}</tbody>
          </table>
        </Card>
      </div>
      <div className="grid-2">
        <Card title="Stärkste Gewinner"><MoverTable rows={movers.slice(0, 8)} /></Card>
        <Card title="Stärkste Verlierer"><MoverTable rows={movers.slice(-8).reverse()} /></Card>
      </div>
    </div>
  );
}

function MoverTable({ rows }: { rows: { symbol: string; name: string; day_change: number; score: number }[] }) {
  return (
    <table className="table compact">
      <thead><tr><th>Aktie</th><th className="num">Heute</th><th className="num">KI-Score</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.symbol}><td><Link to={`/aktie/${r.symbol}`} className="sym">{r.symbol}</Link> <span className="small muted">{r.name}</span></td>
        <td className="num"><Change value={r.day_change} /></td><td className="num">{r.score}</td></tr>)}</tbody>
    </table>
  );
}
