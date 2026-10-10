import { Fragment } from 'react'
import { useVolumeUnit, type VolumeUnit } from '@/lib/volumeUnit'

const OPTIONS: { unit: VolumeUnit; label: string; title: string }[] = [
  { unit: 'shares', label: '股', title: '成交量以股顯示' },
  { unit: 'lots', label: '張', title: '成交量以張顯示（1 張 = 1,000 股）' },
]

export function VolumeUnitToggle({ className = '' }: { className?: string }) {
  const [unit, setUnit] = useVolumeUnit()
  return (
    <div role="group" aria-label="成交量單位" className={`inline-flex items-center rounded bg-elevated overflow-hidden ${className}`}>
      {OPTIONS.map((option, index) => (
        <Fragment key={option.unit}>
          {index > 0 && <span className="w-px h-3 bg-border/40" />}
          <button
            type="button"
            aria-pressed={unit === option.unit}
            title={option.title}
            onClick={() => setUnit(option.unit)}
            className={`px-2.5 py-0.5 text-[10px] font-mono cursor-pointer transition-colors ${
              unit === option.unit
                ? 'bg-accent/20 text-accent'
                : 'text-muted hover:text-secondary'
            }`}
          >
            {option.label}
          </button>
        </Fragment>
      ))}
    </div>
  )
}
