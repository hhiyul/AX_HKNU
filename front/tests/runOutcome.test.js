import test from 'node:test'
import assert from 'node:assert/strict'
import { runOutcomeText } from '../src/components/runOutcome.js'

test('unknown delivery cannot be presented as a request that was never sent', () => {
  const unknown = runOutcomeText({ status: 'unknown', delivery: 'unknown' })
  assert.match(unknown, /결과를 확인할 수 없습니다/)
  assert.doesNotMatch(unknown, /전송하지 못/)
  assert.match(runOutcomeText({ status: 'failed', delivery: 'not_dispatched' }), /전송하지 못/)
  assert.equal(runOutcomeText({ status: 'completed' }), '')
})
