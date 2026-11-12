import { Modal } from './Modal'

type ConfirmModalProps = {
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmModal({
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  return (
    <Modal title={title} onClose={onCancel} size="sm">
      <div className="modal-body">
        <p>{message}</p>
      </div>
      <div className="modal-actions">
        <button type="button" className="secondary-action" onClick={onCancel}>
          {cancelLabel}
        </button>
        <button
          type="button"
          className={danger ? 'danger-action' : 'primary-action'}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  )
}
