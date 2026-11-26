import test from 'node:test'
import assert from 'node:assert/strict'
import { createMemoAutosave } from '../src/components/memoAutosave.js'

test('navigation flush saves the latest edit before the debounce deadline', async () => {
  const writes = []
  const saver = createMemoAutosave(async value => { writes.push(value) }, 60_000)
  saver.schedule('old')
  saver.schedule('latest')
  await saver.flush()
  await saver.flush() // blur followed by unmount must not write twice
  assert.deepEqual(writes, ['latest'])
  assert.equal(saver.busy, false)
})

test('edits during an in-flight save wait for its new revision', async () => {
  let release, revision = 1
  const first = new Promise(resolve => { release = resolve })
  const writes = []
  const saver = createMemoAutosave(async value => {
    writes.push([value, revision])
    if (value === 'first') await first
    revision += 1
  }, 60_000)
  saver.schedule('first')
  const one = saver.flush()
  await Promise.resolve(); await Promise.resolve()
  saver.schedule('second')
  const two = saver.flush()
  assert.deepEqual(writes, [['first', 1]])
  release()
  await Promise.all([one, two])
  assert.deepEqual(writes, [['first', 1], ['second', 2]])
})
