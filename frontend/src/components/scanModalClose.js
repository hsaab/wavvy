export function shouldAutoCloseScanModal(message, { startedByThisModal, messageAtOpen }) {
  return (
    startedByThisModal === true &&
    message != null &&
    message !== messageAtOpen &&
    message.type === "scan_batch_complete"
  );
}
