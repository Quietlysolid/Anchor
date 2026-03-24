interface Props {
  visible:    boolean
  onDismiss?: () => void
}

export function DisconnectedBanner({ visible, onDismiss }: Props) {
  if (!visible) return null

  return (
    <div className="fixed top-0 left-0 right-0 z-50 flex items-center justify-center gap-3 px-4 py-2 bg-anchor-red/90 backdrop-blur-sm text-white text-xs font-mono">
      <span className="animate-glow-pulse">●</span>
      <span>Live updates disconnected. Reconnecting...</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="ml-auto text-white/70 hover:text-white"
        >
          ✕
        </button>
      )}
    </div>
  )
}
