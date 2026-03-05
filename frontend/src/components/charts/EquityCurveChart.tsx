import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import type { EquityPoint } from '../../types'
import { format } from 'date-fns'

interface Props { data: EquityPoint[] }

export function EquityCurveChart({ data }: Props) {
  if (!data.length) return <div className="h-40 flex items-center justify-center text-muted-foreground text-sm">No equity data yet</div>

  const formatted = data.map(d => ({
    ...d,
    label: format(new Date(d.time), 'MMM d'),
    balance: Number((d.account_balance ?? 0).toFixed(2)),
    equity:  Number((d.account_equity ?? 0).toFixed(2)),
  }))

  const start = formatted[0]?.balance ?? 0

  return (
    <ResponsiveContainer width="100%" height={160}>
      <AreaChart data={formatted} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="equity-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#22c55e" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="label" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
        <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} domain={['auto','auto']} />
        <Tooltip
          contentStyle={{ background: 'hsl(222,84%,8%)', border: '1px solid hsl(217,33%,17%)', borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: '#94a3b8' }}
          formatter={(v: number) => [`$${(v ?? 0).toFixed(2)}`]}
        />
        <ReferenceLine y={start} stroke="#475569" strokeDasharray="3 3" />
        <Area type="monotone" dataKey="equity" stroke="#22c55e" strokeWidth={2} fill="url(#equity-grad)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
