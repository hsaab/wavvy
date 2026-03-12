const CONFIDENCE_STYLES = {
  high: "bg-green-500/20 text-green-400",
  medium: "bg-yellow-500/20 text-yellow-400",
  low: "bg-orange-500/20 text-orange-400",
  not_found: "bg-gray-500/15 text-gray-500",
};

export default function ConfidenceBadge({ confidence, score }) {
  const style = CONFIDENCE_STYLES[confidence] || CONFIDENCE_STYLES.not_found;
  const label =
    confidence === "not_found"
      ? "No match"
      : `${confidence} (${score}%)`;

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded ${style}`}
    >
      {label}
    </span>
  );
}
