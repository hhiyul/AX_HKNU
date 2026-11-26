import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeTaskResults, isLinkedTask } from '../src/components/registrationState.js'

test('retrying a failed row keeps the previous successful task ID', () => {
  const saved = { proposal_id: 'a', status: 'registered', task: { id: 'task-1' } }
  const result = mergeTaskResults([saved, { proposal_id: 'b', status: 'failed' }], [{ proposal_id: 'b', status: 'registered', task: { id: 'task-2' } }])
  assert.deepEqual(result[0], saved)
  assert.equal(result[1].task.id, 'task-2')
})

test('a late pending lookup cannot downgrade a known successful write', () => {
  const saved = { proposal_id: 'a', status: 'registered', task: { id: 'task-1' } }
  assert.deepEqual(mergeTaskResults([saved], [{ proposal_id: 'a', status: 'unknown' }]), [saved])
})

test('a deleted linked task remains linked and cannot become a new registration', () => {
  assert.equal(isLinkedTask({ task_id: 'task-1', task_status: 'trash' }, undefined), true)
  assert.equal(isLinkedTask({}, { status: 'already_linked', task: { status: 'trash' } }), true)
  assert.equal(isLinkedTask({}, { status: 'failed' }), false)
})
