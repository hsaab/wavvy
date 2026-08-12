import assert from "node:assert/strict";
import { shouldAutoCloseScanModal } from "../src/components/scanModalClose.js";

function expectClose(name, message, ctx, expected) {
  const actual = shouldAutoCloseScanModal(message, ctx);
  assert.equal(actual, expected, name);
}

// Same field values, different objects: close must use identity, not a value match.
const leftoverComplete = {
  type: "scan_batch_complete",
  payload: { playlists_scanned: 4 },
};
const laterComplete = {
  type: "scan_batch_complete",
  payload: { playlists_scanned: 4 },
};
const progress = {
  type: "scan_batch_progress",
  payload: { current: 1, total: 4 },
};
const unrelated = { type: "file_unmatched", payload: { filename: "track.wav" } };

expectClose(
  "Open Scan Playlists after auto-scan already completed: leftover scan_batch_complete is the mount message and startedByThisModal is false -> should not auto-close",
  leftoverComplete,
  { startedByThisModal: false, messageAtOpen: leftoverComplete },
  false,
);

expectClose(
  "Start a scan from the modal: a later scan_batch_complete object is not the mount message and the ref is true -> should auto-close",
  laterComplete,
  { startedByThisModal: true, messageAtOpen: leftoverComplete },
  true,
);

expectClose(
  "Open the modal while leftover scan_batch_complete is in lastMessage, then click Scan Selected: the leftover event is still messageAtOpen, startedByThisModal now true -> must not close immediately",
  leftoverComplete,
  { startedByThisModal: true, messageAtOpen: leftoverComplete },
  false,
);

expectClose(
  "scan_batch_complete while the modal is open but this instance never called handleScan -> should not auto-close",
  laterComplete,
  { startedByThisModal: false, messageAtOpen: leftoverComplete },
  false,
);

for (const startedByThisModal of [false, true]) {
  const startedLabel = startedByThisModal
    ? "after this modal started a scan"
    : "before this modal started a scan";

  expectClose(
    `scan_batch_progress ${startedLabel} -> should not auto-close`,
    progress,
    { startedByThisModal, messageAtOpen: leftoverComplete },
    false,
  );
  expectClose(
    `unrelated event ${startedLabel} -> should not auto-close`,
    unrelated,
    { startedByThisModal, messageAtOpen: leftoverComplete },
    false,
  );
  expectClose(
    `null message ${startedLabel} -> should not auto-close`,
    null,
    { startedByThisModal, messageAtOpen: leftoverComplete },
    false,
  );
}

console.log("verify-scan-modal-close: all cases passed");
