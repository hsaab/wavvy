/**
 * Close only after this modal's POST /api/scan returns ok.
 * scan_batch_complete has no scan id, so a later complete can belong to
 * App's in-flight auto-scan (or any other caller).
 */
export function shouldAutoCloseScanModal({ httpScanSucceeded }) {
  return httpScanSucceeded === true;
}
