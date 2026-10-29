import { fmt, scoreClass } from "../format.js";

// Barres horizontales 0-100 ; avec `before`, la valeur d'origine est dessinée en fond pour voir le gain.
export default function ScoreBars({ items }) {
  return (
    <ul className="score-bars">
      {items.map((it) => (
        <li key={it.name}>
          <span className="sb-name" title={it.name}>{it.name}</span>
          <span className="sb-track">
            {it.before != null && <span className="sb-before" style={{ width: `${it.before}%` }} />}
            <span className={"sb-fill " + scoreClass(it.value)} style={{ width: `${it.value ?? 0}%` }} />
          </span>
          <span className="sb-value">
            {it.before != null && it.before !== it.value && <span className="muted">{fmt(it.before)} → </span>}
            <b className={scoreClass(it.value)}>{fmt(it.value)}</b>
          </span>
        </li>
      ))}
    </ul>
  );
}
