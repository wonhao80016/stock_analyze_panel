// The Taiwan daily K writer and corporate_actions never read daily_data_provider / adj_factor_provider;
// the backend reads both back as official and refuses custom values.
export const FIXED_TAIWAN_DATASETS = new Set(['daily', 'adj_factor'])
export const FIXED_TAIWAN_NOTE = '台股日K與除權因子固定由台灣官方資料源提供:日K由台股盤後更新流程寫入,除權因子來自公司行動表,兩者都不經資料來源路由,切換無效。'

/** True when switching to the source would route at least one dataset to it. */
export function hasRoutableDataset(datasets: string[]): boolean {
  return datasets.some(ds => ds === 'realtime' || ds === 'minute')
}

export const NOT_ROUTABLE_HINT = `此來源只提供日K或除權因子,無法切換為目前資料來源。${FIXED_TAIWAN_NOTE}`
