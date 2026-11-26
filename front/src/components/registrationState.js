// A response for a retry contains only that attempt's rows. Keep earlier successes.
export function mergeTaskResults(previous, incoming) {
  const merged = new Map(previous.map((row) => [row.proposal_id, row]))
  for (const row of incoming) {
    const old = merged.get(row.proposal_id)
    if (row.status === 'unknown' && ['registered', 'already_linked'].includes(old?.status)) continue
    merged.set(row.proposal_id, row)
  }
  return [...merged.values()]
}

export function isLinkedTask(row, result) {
  return Boolean(row.task_id || ['registered', 'already_linked'].includes(result?.status))
}
