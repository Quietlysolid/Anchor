interface Props { connected: boolean; className?: string }

export function StatusDot({ connected, className = '' }: Props) {
  return (
    <span className={`relative inline-flex h-2 w-2 ${className}`}>
      {connected && (
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-anchor-green opacity-60" />
      )}
      <span className={`relative inline-flex rounded-full h-2 w-2 ${connected ? 'bg-anchor-green' : 'bg-anchor-red'}`} />
    </span>
  )
}
