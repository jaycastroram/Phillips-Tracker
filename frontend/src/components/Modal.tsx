import { useEffect, useRef, type ReactNode } from 'react'

type ModalProps = {
  title: string
  onClose: () => void
  children: ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl'
  /** Extra class on the card, for a dialog that manages its own scrolling. */
  className?: string
}

export function Modal({ title, onClose, children, size = 'md', className }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={dialogRef}
        className={`modal-card modal-card--${size}${className ? ` ${className}` : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        <div className="modal-header">
          <h2 id="modal-title">{title}</h2>
          <button type="button" className="modal-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
