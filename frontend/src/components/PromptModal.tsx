import { useState, type FormEvent } from 'react'
import { Modal } from './Modal'

type PromptModalProps = {
  title: string
  label: string
  placeholder?: string
  inputType?: 'text' | 'password'
  minLength?: number
  submitLabel?: string
  cancelLabel?: string
  onSubmit: (value: string) => void
  onCancel: () => void
}

export function PromptModal({
  title,
  label,
  placeholder = '',
  inputType = 'text',
  minLength,
  submitLabel = 'Submit',
  cancelLabel = 'Cancel',
  onSubmit,
  onCancel,
}: PromptModalProps) {
  const [value, setValue] = useState('')

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (minLength && value.length < minLength) return
    onSubmit(value)
  }

  return (
    <Modal title={title} onClose={onCancel} size="sm">
      <form onSubmit={handleSubmit}>
        <div className="modal-body">
          <label>
            {label}
            <input
              autoFocus
              type={inputType}
              value={value}
              minLength={minLength}
              placeholder={placeholder}
              onChange={(e) => setValue(e.target.value)}
            />
          </label>
        </div>
        <div className="modal-actions">
          <button type="button" className="secondary-action" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button type="submit" className="primary-action" disabled={minLength ? value.length < minLength : !value}>
            {submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  )
}
