import { useState, useMemo } from 'react'
import { ChevronUp, ChevronDown } from 'lucide-react'
import { TradeRow } from './TradeRow'
import type { Trade } from '../../types'

type SortKey = 'opened_at' | 'instrument' | 'direction' | 'net_pl'
type SortDir = 'asc' | 'desc'

interface Props { trades: Trade[]; explanations?: Record<string, string> }

const PAGE_SIZE = 25

export function TradeTable({ trades, explanations = {} }: Props) {
  const [sort,    setSort]    = useState<{ key: SortKey; dir: SortDir }>({ key: 'opened_at', dir: 'desc' })
  const [page,    setPage]    = useState(1)
  const [filterPair,    setFilterPair]    = useState('ALL')
  const [filterDir,     setFilterDir]     = useState('ALL')
  const [filterRegime,  setFilterRegime]  = useState('ALL')

  const pairs = ['ALL', ...Array.from(new Set(trades.map(t => t.instrument)))]

  const filtered = useMemo(() => {
    return trades.filter(t => {
      if (filterPair !== 'ALL' && t.instrument !== filterPair) return false
      if (filterDir  !== 'ALL' && t.direction  !== filterDir)  return false
      if (filterRegime !== 'ALL' && t.regime_at_entry !== filterRegime) return false
      return true
    })
  }, [trades, filterPair, filterDir, filterRegime])

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let av: string | number = a[sort.key] ?? ''
      let bv: string | number = b[sort.key] ?? ''
      if (sort.key === 'opened_at') { av = new Date(av as string).getTime(); bv = new Date(bv as string).getTime() }
      return sort.dir === 'asc' ? (av < bv ? -1 : 1) : (av > bv ? -1 : 1)
    })
  }, [filtered, sort])

  const pages  = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const paged  = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  function toggleSort(key: SortKey) {
    setSort(s => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))
    setPage(1)
  }

  function SortIcon({ col }: { col: SortKey }) {
    if (sort.key !== col) return <span className="text-anchor-border ml-1">↕</span>
    return sort.dir === 'asc'
      ? <ChevronUp size={12} className="inline ml-1 text-anchor-green" />
      : <ChevronDown size={12} className="inline ml-1 text-anchor-green" />
  }

  return (
    <div className="space-y-3">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <select
          value={filterPair}
          onChange={e => { setFilterPair(e.target.value); setPage(1) }}
          className="bg-anchor-surface border border-anchor-border text-anchor-text rounded px-2 py-1 font-mono min-w-[9rem] flex-1 sm:flex-none"
        >
          {pairs.map(p => <option key={p} value={p}>{p === 'ALL' ? 'All pairs' : p.replace('_', '/')}</option>)}
        </select>

        <select
          value={filterDir}
          onChange={e => { setFilterDir(e.target.value); setPage(1) }}
          className="bg-anchor-surface border border-anchor-border text-anchor-text rounded px-2 py-1 font-mono min-w-[9rem] flex-1 sm:flex-none"
        >
          <option value="ALL">All directions</option>
          <option value="LONG">Long</option>
          <option value="SHORT">Short</option>
        </select>

        <select
          value={filterRegime}
          onChange={e => { setFilterRegime(e.target.value); setPage(1) }}
          className="bg-anchor-surface border border-anchor-border text-anchor-text rounded px-2 py-1 font-mono min-w-[9rem] flex-1 sm:flex-none"
        >
          <option value="ALL">All regimes</option>
          <option value="TRENDING">Trending</option>
          <option value="RANGING">Ranging</option>
          <option value="VOLATILE">Volatile</option>
        </select>

        <span className="w-full sm:w-auto sm:ml-auto text-anchor-muted font-mono text-right">{filtered.length} trades</span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-anchor-border">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="bg-anchor-surface border-b border-anchor-border">
            <tr className="text-[11px] font-mono text-anchor-muted uppercase tracking-wide">
              {([
                ['opened_at',  'Date',   'px-3 text-left'],
                ['instrument', 'Pair',   'px-3 text-left'],
                ['direction',  'Dir',    'px-3 text-left'],
                [null,         'Entry',  'px-3 text-right hidden sm:table-cell'],
                [null,         'Exit',   'px-3 text-right hidden sm:table-cell'],
                ['net_pl',     'P&L',    'px-3 text-right'],
                [null,         'Hold',   'px-3 text-right hidden md:table-cell'],
                [null,         'Regime', 'px-3 text-left  hidden lg:table-cell'],
                [null,         '',       'px-3 w-8'],
              ] as [SortKey | null, string, string][]).map(([key, label, cls], i) => (
                <th
                  key={i}
                  className={`py-2.5 font-medium ${cls} ${key ? 'cursor-pointer hover:text-anchor-text' : ''}`}
                  onClick={() => key && toggleSort(key)}
                >
                  {label}{key && <SortIcon col={key} />}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.length === 0 ? (
              <tr><td colSpan={9} className="py-10 text-center text-anchor-muted font-mono text-sm italic">No trades match filters.</td></tr>
            ) : (
              paged.map(t => <TradeRow key={t.id} trade={t} explanation={explanations[t.id]} />)
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex flex-wrap items-center justify-center gap-1">
          {Array.from({ length: pages }, (_, i) => i + 1).map(p => (
            <button
              key={p}
              type="button"
              onClick={() => setPage(p)}
              className={`w-7 h-7 rounded text-xs font-mono transition-colors
                ${p === page
                  ? 'bg-anchor-green text-anchor-void font-semibold'
                  : 'text-anchor-muted hover:text-anchor-text hover:bg-anchor-surface'
                }`}
            >
              {p}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
