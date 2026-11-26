// Coalesce typing, but flush on blur/unmount and serialize revision-based writes.
export function createMemoAutosave(save, delay = 800) {
  let timer, pending, queued = 0, chain = Promise.resolve()
  const flush = () => {
    clearTimeout(timer)
    if (pending === undefined) return chain
    const value = pending
    pending = undefined
    queued += 1
    chain = chain.catch(() => {}).then(() => save(value)).finally(() => { queued -= 1 })
    return chain
  }
  return {
    get busy() { return pending !== undefined || queued > 0 },
    schedule(value) {
      pending = value
      clearTimeout(timer)
      timer = setTimeout(() => { void flush().catch(() => {}) }, delay)
    },
    flush,
  }
}
