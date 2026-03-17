import { useEffect, useRef, useState } from 'react'

interface Props {
  content:     string | null
  sessionType?: 'PRE' | 'POST' | 'DRAWDOWN' | 'WEEKLY' | string
  timestamp?:  string
  typewrite?:  boolean
}

const SESSION_STYLES = {
  PRE:      { border: 'border-l-anchor-green/60', label: 'PRE-SESSION',  color: 'text-anchor-green' },
  POST:     { border: 'border-l-anchor-green/80', label: 'POST-SESSION', color: 'text-anchor-green' },
  DRAWDOWN: { border: 'border-l-anchor-red',      label: 'DRAWDOWN',     color: 'text-anchor-red'   },
  WEEKLY:   { border: 'border-l-anchor-muted',    label: 'WEEKLY',       color: 'text-anchor-muted' },
  PRESESSION: { border: 'border-l-anchor-green/60',  label: 'PRE-SESSION',  color: 'text-anchor-green' },
  POSTSESSION:{ border: 'border-l-anchor-green/80',  label: 'POST-SESSION', color: 'text-anchor-green' },
}

function renderMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-anchor-text">$1</strong>')
    .replace(/^### (.+)$/gm, '<h3 class="text-sm font-semibold text-anchor-text mt-3 mb-1">$1</h3>')
    .replace(/^## (.+)$/gm,  '<h2 class="text-sm font-semibold text-anchor-text mt-3 mb-1">$1</h2>')
    .replace(/^- (.+)$/gm,   '<li class="ml-3 list-disc list-inside text-anchor-text/80">$1</li>')
    .replace(/\n\n/g, '</p><p class="mt-2">')
    .replace(/^/, '<p class="mt-0">')
    .concat('</p>')
}

export function AIBriefPanel({ content, sessionType = 'PRE', timestamp, typewrite = false }: Props) {
  const [displayed, setDisplayed] = useState(typewrite ? '' : (content ?? ''))
  const [typing,    setTyping]    = useState(false)
  const indexRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout>>()

  const key = (sessionType ?? 'PRE').toString().toUpperCase()
  const cfg = SESSION_STYLES[key as keyof typeof SESSION_STYLES] ?? SESSION_STYLES['PRE']

  useEffect(() => {
    if (!content) return
    if (!typewrite) { setDisplayed(content); return }

    setDisplayed('')
    setTyping(true)
    indexRef.current = 0

    function tick() {
      indexRef.current++
      setDisplayed(content!.slice(0, indexRef.current))
      if (indexRef.current < content!.length) {
        timerRef.current = setTimeout(tick, 8)
      } else {
        setTyping(false)
      }
    }

    timerRef.current = setTimeout(tick, 200)
    return () => clearTimeout(timerRef.current)
  }, [content, typewrite])

  if (!content) {
    return (
      <div className="p-4 text-sm text-anchor-muted font-mono italic">
        No brief available for this session.
      </div>
    )
  }

  const ts = timestamp
    ? new Date(timestamp).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: true }) + ' ET'
    : null

  return (
    <div className={`border-l-2 ${cfg.border} pl-4`}>
      <div className="flex items-center gap-2 mb-3">
        <span className={`text-xs font-mono font-medium ${cfg.color}`}>{cfg.label}</span>
        {ts && <span className="text-[10px] text-anchor-muted font-mono">{ts}</span>}
        {typing && <span className="text-[10px] text-anchor-muted font-mono animate-glow-pulse">●</span>}
      </div>
      <div
        className={`text-sm text-anchor-text/80 leading-relaxed font-sans ${typing ? 'typewriter-cursor' : ''}`}
        dangerouslySetInnerHTML={{ __html: renderMarkdown(displayed) }}
      />
    </div>
  )
}
