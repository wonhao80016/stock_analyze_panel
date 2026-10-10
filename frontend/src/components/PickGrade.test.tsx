import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PickGradeBadge } from './PickGrade'

describe('PickGradeBadge', () => {
  it('tags a limit-up-locked pick without changing its grade', () => {
    render(
      <PickGradeBadge
        row={{ symbol: '2330.TWSE', score: 0.82, grade: 'buy', grade_reason: null, limit_up_locked: true }}
        topN={5}
      />,
    )

    expect(screen.getByText('買進')).toBeInTheDocument()
    expect(screen.getByText('漲停鎖死')).toHaveAttribute('title', '價格已在漲停價，買單難以成交')
  })

  it('shows no limit-up tag on other picks', () => {
    render(
      <PickGradeBadge
        row={{ symbol: '2330.TWSE', score: 0.82, grade: 'watch', grade_reason: 'outside_top_n', limit_up_locked: false }}
        topN={5}
      />,
    )

    expect(screen.getByText('觀察')).toBeInTheDocument()
    expect(screen.queryByText('漲停鎖死')).not.toBeInTheDocument()
  })

  it('names the scoring fields an unscored pick lacks', () => {
    render(
      <PickGradeBadge
        row={{
          symbol: '0050.TWSE',
          score: null,
          grade: 'watch',
          grade_reason: 'score_unavailable',
          missing_scoring_fields: ['turnover_rate', 'distance_from_low_60d'],
        }}
      />,
    )

    expect(screen.getByText('觀察・資料不足')).toHaveAttribute('title', '評分欄位缺值：換手率、距60日低點')
  })

  it('names each composite child that could not score the pick', () => {
    render(
      <PickGradeBadge
        row={{
          symbol: '0050.TWSE',
          score: null,
          grade: 'watch',
          grade_reason: 'score_unavailable',
          missing_scoring_fields: ['turnover_rate', 'vol_ratio_5d'],
          missing_scoring_children: [
            { strategy_id: 'a', name: '多頭排列', fields: ['turnover_rate'] },
            { strategy_id: 'b', name: '低波動', fields: ['turnover_rate', 'vol_ratio_5d'] },
          ],
        }}
      />,
    )

    expect(screen.getByText('觀察・資料不足')).toHaveAttribute(
      'title',
      '評分欄位缺值：多頭排列（換手率）；低波動（換手率、5日量比）',
    )
  })

  it('keeps the generic text when the missing fields are unknown', () => {
    render(<PickGradeBadge row={{ symbol: '0050.TWSE', score: null, grade: 'watch', grade_reason: 'score_unavailable' }} />)

    expect(screen.getByText('觀察・資料不足')).toHaveAttribute('title', '評分資料不足（分數 —）')
  })
})
