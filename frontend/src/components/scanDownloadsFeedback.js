export function describeScanResult(result) {
  const count = result?.count ?? 0;
  const matched = result?.matched?.length ?? 0;
  const unmatchedRows = result?.unmatched ?? [];
  const names = unmatchedRows
    .map((row) => row.filename)
    .filter(Boolean)
    .join(", ");

  if (result?.folder_missing) {
    return { tone: "warn", text: "Downloads folder is missing." };
  }
  if (count === 0) {
    return { tone: "warn", text: "No WAV or MP3 files found in Downloads." };
  }
  if (matched === 0) {
    return {
      tone: "warn",
      text: names
        ? `Found ${count} file(s) but none matched queue tracks. ${names}`
        : `Found ${count} file(s) but none matched queue tracks.`,
    };
  }
  if (unmatchedRows.length > 0) {
    return {
      tone: "info",
      text: `Matched ${matched} of ${count} file(s). Unmatched: ${names}`,
    };
  }
  return { tone: "info", text: `Matched ${matched} file(s).` };
}
