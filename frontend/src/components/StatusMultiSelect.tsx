type StatusMultiSelectProps = {
  allOptions: string[]
  selected: string[]
  onChange: (next: string[]) => void
}

export function StatusMultiSelect({ allOptions, selected, onChange }: StatusMultiSelectProps) {
  function toggle(label: string) {
    onChange(selected.includes(label) ? selected.filter((s) => s !== label) : [...selected, label])
  }

  return (
    <details className="status-multi-select">
      <summary>
        <span>{selected.length ? `${selected.length} selected` : 'Choose statuses'}</span>
      </summary>
      <div className="status-multi-menu">
        {allOptions.map((label) => (
          <label key={label} className="status-option">
            <input
              checked={selected.includes(label)}
              type="checkbox"
              onChange={() => toggle(label)}
            />
            {label}
          </label>
        ))}
        {allOptions.length === 0 && (
          <span className="muted">No statuses available yet.</span>
        )}
      </div>
      {selected.length > 0 && (
        <div className="selected-status-chips">
          {selected.map((label) => (
            <span key={label}>{label}</span>
          ))}
        </div>
      )}
    </details>
  )
}
