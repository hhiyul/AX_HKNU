export function runOutcomeText(outcome) {
  if (!outcome) return ''
  if (outcome.status === 'unknown') return '응답 결과를 확인할 수 없습니다. 자동 재전송하지 않았습니다.'
  if (outcome.status === 'stopped') return '중단한 요청입니다. 생성된 부분 답변은 유지합니다.'
  if (outcome.status === 'failed') return outcome.delivery === 'not_dispatched'
    ? '요청을 전송하지 못했습니다. 자동 재전송하지 않았습니다.'
    : '요청을 완료하지 못했습니다. 자동 재전송하지 않았습니다.'
  return ''
}
