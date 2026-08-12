import assert from "node:assert/strict";
import { shouldAutoCloseScanModal } from "../src/components/scanModalClose.js";

function expectClose(name, ctx, expected) {
  const actual = shouldAutoCloseScanModal(ctx);
  assert.equal(actual, expected, name);
}

expectClose(
  "Open Scan Playlists after auto-scan already completed: leftover scan_batch_complete must not auto-close",
  { httpScanSucceeded: false },
  false,
);

expectClose(
  "Start a scan from the modal: this modal's POST /api/scan returned ok -> should auto-close",
  { httpScanSucceeded: true },
  true,
);

expectClose(
  "Open the modal then click Scan Selected: HTTP still in flight, leftover complete must not close immediately",
  { httpScanSucceeded: false },
  false,
);

expectClose(
  "In-flight auto-scan completes after Scan Selected: uncorrelated scan_batch_complete must not auto-close",
  { httpScanSucceeded: false },
  false,
);

expectClose(
  "triggerScan failed: a later scan_batch_complete must not auto-close",
  { httpScanSucceeded: false },
  false,
);

expectClose(
  "scan_batch_complete while the modal is open but this instance never called handleScan -> should not auto-close",
  { httpScanSucceeded: false },
  false,
);

expectClose(
  "httpScanSucceeded omitted -> should not auto-close",
  {},
  false,
);

console.log("verify-scan-modal-close: all cases passed");
