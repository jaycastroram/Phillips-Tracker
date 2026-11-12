type ImageLightboxProps = {
  url: string
  title: string
  onClose: () => void
}

export function ImageLightbox({ url, title, onClose }: ImageLightboxProps) {
  return (
    <div
      className="image-lightbox-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="image-lightbox-card"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <button
          className="image-lightbox-close"
          type="button"
          onClick={onClose}
        >
          Close
        </button>
        <img
          src={url}
          alt={title}
          onClick={(event) => event.stopPropagation()}
        />
      </div>
    </div>
  )
}
