import { useState, useRef, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";

export default function PlaylistPicker({
  allPlaylists,
  selected,
  onChange,
  disabled,
}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const btnRef = useRef(null);
  const dropdownRef = useRef(null);
  const searchRef = useRef(null);

  const updatePosition = useCallback(() => {
    if (!btnRef.current) return;
    const rect = btnRef.current.getBoundingClientRect();
    setPos({
      top: rect.bottom + 4,
      left: Math.max(8, rect.right - 256),
    });
  }, []);

  useEffect(() => {
    if (open) {
      updatePosition();
      if (searchRef.current) searchRef.current.focus();
    }
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e) => {
      if (
        btnRef.current?.contains(e.target) ||
        dropdownRef.current?.contains(e.target)
      )
        return;
      setOpen(false);
      setFilter("");
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  const selectedSet = new Set(selected || []);
  const count = selectedSet.size;

  const filtered = filter
    ? allPlaylists.filter((p) =>
        p.toLowerCase().includes(filter.toLowerCase()),
      )
    : allPlaylists;

  const toggle = (name) => {
    const next = new Set(selectedSet);
    if (next.has(name)) {
      next.delete(name);
    } else {
      next.add(name);
    }
    onChange([...next]);
  };

  const clearAll = (e) => {
    e.stopPropagation();
    onChange([]);
  };

  const dropdown = open
    ? createPortal(
        <div
          ref={dropdownRef}
          className="fixed z-[9999] w-64 bg-base-800 border border-base-600 rounded-lg shadow-xl overflow-hidden"
          style={{ top: pos.top, left: pos.left }}
        >
          <div className="p-2 border-b border-base-600">
            <input
              ref={searchRef}
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search playlists..."
              className="w-full bg-base-700 border border-base-600 text-gray-200 text-xs rounded
                         px-2 py-1.5 focus:outline-none focus:border-accent placeholder:text-gray-500"
            />
          </div>

          <div className="max-h-64 overflow-y-auto overscroll-contain">
            {filtered.length === 0 ? (
              <div className="px-3 py-4 text-xs text-gray-500 text-center">
                No playlists found
              </div>
            ) : (
              filtered.map((name) => {
                const isChecked = selectedSet.has(name);
                return (
                  <label
                    key={name}
                    className={`flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer
                      transition-colors hover:bg-base-700
                      ${isChecked ? "text-purple-300" : "text-gray-300"}`}
                  >
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => toggle(name)}
                      className="accent-purple-500 flex-shrink-0"
                    />
                    <span className="truncate">{name}</span>
                  </label>
                );
              })
            )}
          </div>

          {count > 0 && (
            <div className="px-3 py-2 border-t border-base-600 text-xs text-gray-400 flex justify-between items-center">
              <span>{count} selected</span>
              <button
                onClick={() => {
                  onChange([]);
                  setFilter("");
                }}
                className="text-purple-400 hover:text-purple-300 transition-colors"
              >
                Clear all
              </button>
            </div>
          )}
        </div>,
        document.body,
      )
    : null;

  return (
    <div>
      <button
        ref={btnRef}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded border transition-colors
          ${
            count > 0
              ? "bg-purple-500/20 border-purple-500/40 text-purple-300 hover:bg-purple-500/30"
              : "bg-base-700 border-base-600 text-gray-400 hover:border-gray-500"
          }
          disabled:opacity-40 disabled:cursor-not-allowed`}
      >
        <svg
          className="w-3.5 h-3.5 flex-shrink-0"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2z"
          />
        </svg>
        {count > 0 ? (
          <>
            <span className="font-medium">{count}</span>
            <button
              onClick={clearAll}
              className="ml-0.5 hover:text-purple-100 transition-colors"
              title="Clear all"
            >
              &times;
            </button>
          </>
        ) : (
          <span>--</span>
        )}
      </button>
      {dropdown}
    </div>
  );
}
