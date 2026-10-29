export default function Busy({ text }) {
  return (
    <div className="busy">
      <span className="spinner" />
      <span>{text}</span>
    </div>
  );
}
