const STATUS_STYLES = {
  new: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  approved: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  carted: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  purchased: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  processing: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  done: "bg-green-500/20 text-green-400 border-green-500/30",
  skipped: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  cart_failed: "bg-red-500/20 text-red-400 border-red-500/30",
  baseline: "bg-slate-500/20 text-slate-400 border-slate-500/30",
};

const STATUS_LABELS = {
  new: "New",
  approved: "Approved",
  carted: "Carted",
  purchased: "Purchased",
  processing: "Processing",
  done: "Done",
  skipped: "Skipped",
  cart_failed: "Cart Failed",
  baseline: "Baseline",
};

export default function StatusBadge({ status }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.new;
  const label = STATUS_LABELS[status] || status;

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${style}`}
    >
      {label}
    </span>
  );
}
