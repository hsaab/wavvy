import { useEffect } from "react";

const VARIANT_STYLES = {
  success: "bg-green-500/15 border-green-500/30 text-green-400",
  error: "bg-red-500/15 border-red-500/30 text-red-400",
  info: "bg-accent/15 border-accent/30 text-accent",
};

const AUTO_DISMISS_MS = 5000;

export default function Toast({ toasts, onDismiss }) {
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastItem({ toast, onDismiss }) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  const style = VARIANT_STYLES[toast.variant] || VARIANT_STYLES.info;

  return (
    <div
      className={`border rounded-lg px-4 py-3 text-sm shadow-lg backdrop-blur-sm
                  animate-slide-in ${style}`}
    >
      <div className="flex items-start gap-2">
        <span className="flex-1">{toast.message}</span>
        <button
          onClick={() => onDismiss(toast.id)}
          className="text-current opacity-50 hover:opacity-100 transition-opacity text-xs mt-0.5"
        >
          &times;
        </button>
      </div>
    </div>
  );
}
