import type { ReactNode, CSSProperties } from 'react'

interface Props {
  children:   ReactNode
  glowColor?: 'green' | 'red' | 'none'
  className?: string
  padding?:   boolean
  style?:     CSSProperties
}

export function GlowCard({ children, glowColor = 'none', className = '', padding = true, style }: Props) {
  const shadow =
    glowColor === 'green' ? 'shadow-glow-green' :
    glowColor === 'red'   ? 'shadow-glow-red'   : ''

  return (
    <div style={style} className={`
      bg-anchor-surface
      border border-anchor-border/60
      rounded-xl
      ${shadow}
      ${padding ? 'p-4' : ''}
      ${className}
    `}>
      {children}
    </div>
  )
}
