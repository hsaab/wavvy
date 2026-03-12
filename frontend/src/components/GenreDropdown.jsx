const GENRES = ["Disco+Melodic", "House", "Worldtech", "Tech House"];

export default function GenreDropdown({ value, onChange, disabled }) {
  return (
    <select
      value={value || ""}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={disabled}
      className="bg-base-700 border border-base-600 text-gray-200 text-xs rounded
                 px-2 py-1 focus:outline-none focus:border-accent
                 disabled:opacity-40 cursor-pointer"
    >
      <option value="">--</option>
      {GENRES.map((g) => (
        <option key={g} value={g}>
          {g}
        </option>
      ))}
    </select>
  );
}
