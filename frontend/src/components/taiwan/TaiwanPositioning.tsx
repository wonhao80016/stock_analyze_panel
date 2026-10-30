import { AlertTriangle, RefreshCw, Scale } from 'lucide-react'
import { Link } from 'react-router-dom'
import type {
  TaiwanIndustryMetricId,
  TaiwanIndustryRankMetric,
  TaiwanIndustryRanking,
  TaiwanPositioningDimension,
  TaiwanPositioningDimensionId,
  TaiwanPositioningMetric,
  TaiwanPositioningMetricId,
  TaiwanPositioningReason,
  TaiwanPositioningResponse,
} from '@/lib/api'
import { cn } from '@/lib/cn'
import { canonicalizeSymbols } from '@/lib/taiwanCompareSymbols'

type Props = {
  symbol: string
  response?: TaiwanPositioningResponse
  isLoading: boolean
  isError: boolean
  isFetching: boolean
  onRetry: () => void
}

const DIMENSION_LABELS: Record<TaiwanPositioningDimensionId, string> = {
  valuation: '估值',
  profitability: '品質',
  growth: '成長',
  momentum: '動能',
  chips: '籌碼',
}

const METRIC_LABELS: Record<TaiwanPositioningMetricId, string> = {
  pe: '本益比',
  pb: '股價淨值比',
  dividend_yield: '殖利率',
  roe: 'ROE',
  operating_margin: '營業利益率',
  revenue_yoy: '月營收年增率',
  return_60d: '近 60 日報酬',
  return_240d: '近 240 日報酬',
  foreign_flow_20d: '外資 20 日買超強度',
}

const GRADE_LABELS: Record<string, string> = {
  cheap: '便宜',
  fair: '合理',
  pricey: '偏貴',
  expensive: '昂貴',
  top: '頂尖',
  good: '良好',
  average: '普通',
  lagging: '落後',
  strong: '強勢',
  neutral: '中性',
  weak: '偏弱',
}

const GRADE_TONES: Record<string, 'positive' | 'flat' | 'negative'> = {
  cheap: 'positive', fair: 'positive', top: 'positive', good: 'positive', strong: 'positive',
  average: 'flat', neutral: 'flat',
  pricey: 'negative', expensive: 'negative', lagging: 'negative', weak: 'negative',
}

const REASON_LABELS: Record<TaiwanPositioningReason, string> = {
  loss_or_not_applicable: '虧損或不適用',
  not_general_industry: '此產業類別不適用（只比一般業）',
  insufficient_history: '交易日數不足（上市未滿期間或當日無成交）',
  incomplete_window: '期間內法人資料不完整',
  non_positive_equity: '母公司權益非正值，不適用',
  no_value: '沒有這檔的資料',
  not_archived: '累積中：快照尚未存到任何一期',
  dataset_unavailable: '資料暫時無法取得',
  insufficient_peers: '同業不足',
}

const NOT_APPLICABLE: Record<string, string> = {
  index: '指數不適用體質與估值定位。',
  etf: 'ETF 沒有公司財報與本益比，不適用體質與估值定位。',
  tdr: 'TDR 的財報與估值屬海外原股公司，不列入全市場與同產業定位。',
}

const INDUSTRY_METRIC_LABELS: Record<TaiwanIndustryMetricId, string> = {
  return_20d: '近 20 日漲跌',
  foreign_flow_20d: '外資 20 日買超強度',
  trust_flow_20d: '投信 20 日買超強度',
  pe: '本益比',
}

const SIGNED_METRICS = new Set<TaiwanPositioningMetricId>([
  'roe', 'operating_margin', 'revenue_yoy', 'return_60d', 'return_240d', 'foreign_flow_20d',
])

const EXCHANGE_LABELS: Record<string, string> = { TWSE: '上市', TPEX: '上櫃' }

function formatNumber(value: number | null, unit: 'times' | 'percent', signed: boolean) {
  if (value == null) return '--'
  if (unit === 'times') return `${value.toFixed(2)} 倍`
  return `${signed && value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function metricContext(metric: TaiwanPositioningMetric) {
  switch (metric.id) {
    case 'roe':
      return `年初至今累計年化・${metric.period ?? '--'}`
    case 'operating_margin':
      return `年初至今累計・${metric.period ?? '--'}`
    case 'revenue_yoy':
      return `單月・資料年月 ${metric.period ?? '--'}`
    case 'return_60d':
    case 'return_240d':
      return `前復權・${metric.as_of ?? '--'}`
    case 'foreign_flow_20d':
      return `外資買賣超 ÷ 同期成交量・截至 ${metric.as_of ?? '--'}`
    default:
      return `資料日期 ${metric.as_of ?? '--'}`
  }
}

function GradeBadge({ grade }: { grade: string | null }) {
  if (!grade) return null
  const tone = GRADE_TONES[grade] ?? 'flat'
  return (
    <span
      data-tone={tone}
      className={cn(
        'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium',
        tone === 'positive' && 'bg-accent/10 text-accent',
        tone === 'flat' && 'bg-elevated text-muted',
        tone === 'negative' && 'bg-amber-500/10 text-amber-600',
      )}
    >
      {GRADE_LABELS[grade] ?? grade}
    </span>
  )
}

function Segments({ dimension }: { dimension: TaiwanPositioningDimension }) {
  const filled = dimension.segments ?? 0
  const label = DIMENSION_LABELS[dimension.id]
  return (
    <span
      role="img"
      aria-label={`${label}構面 5 格中亮 ${filled} 格`}
      data-testid={`positioning-segments-${dimension.id}`}
      data-filled={filled}
      className="flex gap-0.5"
    >
      {Array.from({ length: 5 }, (_, i) => (
        <span key={i} className={cn('h-2 w-5 rounded-sm', i < filled ? 'bg-accent' : 'bg-elevated')} />
      ))}
    </span>
  )
}

function SignedNumber({ value, unit, signed }: { value: number | null; unit: 'times' | 'percent'; signed: boolean }) {
  const colored = signed && value != null && value !== 0
  return (
    <span
      className={cn(
        'font-mono',
        !colored && 'text-foreground',
        colored && value! > 0 && 'text-bull',
        colored && value! < 0 && 'text-bear',
      )}
    >
      {formatNumber(value, unit, signed)}
    </span>
  )
}

function ValueText({ metric }: { metric: TaiwanPositioningMetric }) {
  return <SignedNumber value={metric.value} unit={metric.unit} signed={SIGNED_METRICS.has(metric.id)} />
}

function MetricRow({ metric }: { metric: TaiwanPositioningMetric }) {
  const compared = metric.compared != null ? `${metric.compared.toLocaleString('zh-TW')} 檔比較` : null
  return (
    <li className="space-y-1 border-b border-border/50 py-2 last:border-0" data-testid={`positioning-metric-${metric.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="min-w-0">
          <span className="font-medium text-foreground">{METRIC_LABELS[metric.id]}</span>
          {metric.lower_is_better && <span className="ml-1 text-[11px] text-muted">（越低越便宜）</span>}
          <div className="text-[11px] text-muted">{metricContext(metric)}</div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ValueText metric={metric} />
          <GradeBadge grade={metric.grade} />
        </div>
      </div>
      {metric.status === 'ranked' && metric.percentile != null && metric.score != null ? (
        <div className="flex items-center gap-2 text-[11px] text-muted">
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-elevated">
            <span className="block h-full rounded-full bg-accent/70" style={{ width: `${metric.score}%` }} />
          </span>
          <span className="shrink-0">百分位 {metric.percentile.toFixed(1)}</span>
          {compared && <span className="shrink-0">{compared}</span>}
        </div>
      ) : (
        <p className="text-[11px] text-muted" data-testid={`positioning-reason-${metric.id}`}>
          {metric.status === 'unavailable' ? '無法計算：' : '不排名：'}
          {REASON_LABELS[metric.reason ?? 'no_value']}
          {compared && metric.status === 'not_ranked' ? `（${compared}）` : ''}
        </p>
      )}
    </li>
  )
}

function DimensionBlock({ dimension }: { dimension: TaiwanPositioningDimension }) {
  return (
    <div className="rounded-xl border border-border/80 bg-base p-3" data-testid={`positioning-dimension-${dimension.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-semibold text-foreground">{DIMENSION_LABELS[dimension.id]}</h4>
          <GradeBadge grade={dimension.grade} />
        </div>
        {dimension.score != null ? (
          <div className="flex items-center gap-2 text-[11px] text-muted">
            <Segments dimension={dimension} />
            <span className="font-mono">{dimension.score.toFixed(1)} 分</span>
          </div>
        ) : (
          <span className="text-[11px] text-muted">無法計算</span>
        )}
      </div>
      <ul className="mt-1">
        {dimension.metrics.map(metric => <MetricRow key={metric.id} metric={metric} />)}
      </ul>
    </div>
  )
}

function ValuationDates({ response }: { response: TaiwanPositioningResponse }) {
  const entries = Object.entries(response.valuation_dates)
  if (entries.length === 0) return null
  return (
    <p className="flex flex-wrap items-center gap-2 text-[11px] text-muted" data-testid="positioning-valuation-dates">
      <span>
        估值日期：{entries.map(([exchange, day]) => `${EXCHANGE_LABELS[exchange] ?? exchange} ${day ?? '--'}`).join('／')}
      </span>
      {response.valuation_period_mixed && (
        <span className="rounded-full bg-amber-500/10 px-2 py-0.5 font-medium text-amber-600">期別混雜</span>
      )}
    </p>
  )
}

function IndustryRankRow({ metric }: { metric: TaiwanIndustryRankMetric }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-b border-border/50 py-2 text-xs last:border-0" data-testid={`industry-metric-${metric.id}`}>
      <div className="min-w-0">
        <span className="font-medium text-foreground">{INDUSTRY_METRIC_LABELS[metric.id]}</span>
        {metric.lower_is_better && <span className="ml-1 text-[11px] text-muted">（越低越前面）</span>}
        {metric.period_mixed && (
          <span className="ml-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600">期別混雜</span>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-[11px] text-muted">
        <SignedNumber value={metric.value} unit={metric.unit} signed={metric.id !== 'pe'} />
        {metric.status === 'ranked' && metric.rank != null && metric.compared != null ? (
          <>
            <span className="font-semibold text-foreground" data-testid={`industry-rank-${metric.id}`}>
              第 {metric.rank} / {metric.compared} 名
            </span>
            {metric.percentile != null && <span>百分位 {metric.percentile.toFixed(1)}</span>}
          </>
        ) : (
          <span data-testid={`industry-reason-${metric.id}`}>
            {metric.reason === 'insufficient_peers'
              ? `同業不足（${metric.compared ?? 0} 檔）`
              : `${metric.status === 'unavailable' ? '無法計算' : '不排名'}：${REASON_LABELS[metric.reason ?? 'no_value']}`}
          </span>
        )}
      </div>
    </li>
  )
}

function IndustryBlock({ symbol, industry }: { symbol: string; industry: TaiwanIndustryRanking }) {
  if (!industry.applicable) {
    return (
      <div className="rounded-xl border border-border/80 bg-base p-3" data-testid="positioning-industry">
        <h4 className="text-xs font-semibold text-foreground">同產業排名</h4>
        <p className="mt-1 text-[11px] text-muted" data-testid="industry-not-applicable">
          證券主檔沒有可用的產業分類（未分類），不做同產業排名。
        </p>
      </div>
    )
  }
  const compareSymbols = canonicalizeSymbols([symbol, ...industry.peers.map(peer => peer.symbol)])
  return (
    <div className="rounded-xl border border-border/80 bg-base p-3" data-testid="positioning-industry">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-xs font-semibold text-foreground">同產業排名</h4>
        <span className="text-[11px] text-muted">
          {industry.industry}・上市櫃合併共 {(industry.members ?? 0).toLocaleString('zh-TW')} 檔
        </span>
      </div>
      <ul className="mt-1">
        {industry.metrics.map(metric => <IndustryRankRow key={metric.id} metric={metric} />)}
      </ul>
      {industry.peers.length > 0 && (
        <div className="mt-2 space-y-1" data-testid="industry-peers">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[11px] text-muted">同業近 20 日漲幅前 {industry.peers.length} 名</span>
            <Link
              to={`/stocks/compare?symbols=${encodeURIComponent(compareSymbols.join(','))}`}
              className="flex items-center gap-1 rounded-lg border border-border/60 bg-surface px-1.5 py-0.5 text-[10px] font-medium text-muted transition-colors hover:border-accent/50 hover:text-accent"
            >
              <Scale className="h-3 w-3" />
              加入比較
            </Link>
          </div>
          <ol className="space-y-0.5 text-xs">
            {industry.peers.map(peer => (
              <li key={peer.symbol} className="flex items-center justify-between gap-2">
                <Link to={`/stocks/${encodeURIComponent(peer.symbol)}`} className="min-w-0 truncate text-foreground hover:text-accent">
                  {peer.name ?? peer.symbol}
                  <span className="ml-1 text-[11px] text-muted">{peer.symbol}</span>
                </Link>
                <SignedNumber value={peer.value} unit="percent" signed />
              </li>
            ))}
          </ol>
        </div>
      )}
      <p className="mt-2 text-[11px] leading-5 text-muted">
        第 1 名為最好：本益比最低、其他指標最高；百分位與名次同向，越高越前面；同值共用名次。可排名的同業少於 3 檔時不給名次。
        許多中小型股 20 日投信買賣超為 0，會有大量同值共用名次。
      </p>
    </div>
  )
}

function Body({ response }: { response: TaiwanPositioningResponse }) {
  if (!response.applicable) {
    return (
      <p className="text-xs text-muted" data-testid="positioning-not-applicable">
        {NOT_APPLICABLE[response.not_applicable_reason ?? ''] ?? '體質與估值定位不適用。'}
      </p>
    )
  }
  return (
    <div className="space-y-3">
      <ValuationDates response={response} />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {response.dimensions.map(dimension => <DimensionBlock key={dimension.id} dimension={dimension} />)}
      </div>
      <p className="text-[11px] leading-5 text-muted">
        百分位越高代表在全市場越前面；本益比、股價淨值比反向計分（越便宜分數越高）。構面分數是可排名指標的平均，
        等級不超出構面內最好與最差指標的等級。報酬與年增率紅色為正、綠色為負。
      </p>
      {response.industry && <IndustryBlock symbol={response.symbol} industry={response.industry} />}
    </div>
  )
}

export function TaiwanPositioning({ symbol, response, isLoading, isError, isFetching, onRetry }: Props) {
  const current = response?.symbol === symbol ? response : undefined
  return (
    <section aria-labelledby="taiwan-positioning-heading" className="space-y-3 rounded-2xl border border-border bg-surface p-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id="taiwan-positioning-heading" className="text-sm font-bold text-foreground">體質與估值定位</h3>
          <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">目前參考</span>
        </div>
        <p className="mt-1 max-w-3xl text-[11px] leading-5 text-muted">
          今日全市場橫斷面百分位（上市櫃普通股，不含 ETF、TDR
          {current?.universe_size != null ? `，共 ${current.universe_size.toLocaleString('zh-TW')} 檔` : ''}
          ），只在記憶體計算，不寫入歷史，請勿用於回測或選股。ROE、營業利益率與月營收年增率取自已存快照：回溯取得，可能為更正後數值。
        </p>
      </div>
      {isError ? (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-muted">
          <div className="flex items-center gap-2 font-medium text-rose-500">
            <AlertTriangle className="h-4 w-4" />
            定位資料暫時讀取失敗
          </div>
          <button
            type="button"
            onClick={onRetry}
            disabled={isFetching}
            className="mt-2 flex items-center gap-1.5 rounded-lg border border-border bg-base px-2.5 py-1 text-xs text-muted hover:text-foreground disabled:opacity-60"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isFetching && 'animate-spin')} />
            重試
          </button>
        </div>
      ) : isLoading || current == null ? (
        <div className="flex min-h-20 items-center justify-center text-xs text-muted" data-testid="positioning-loading">
          <RefreshCw className="mr-2 h-4 w-4 animate-spin text-accent" />
          計算全市場定位中...
        </div>
      ) : (
        <Body response={current} />
      )}
    </section>
  )
}
