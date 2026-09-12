import assert from 'node:assert/strict';
import { mock, test } from 'node:test';

let update;
let emptySnapshot = false;
mock.module('./dist/bridge/grpc.js', { namedExports: {
  streamAgentState(_port, _csrf, _id, callback) {
    update = callback;
    queueMicrotask(() => update(emptySnapshot ? {status:'CASCADE_RUN_STATUS_IDLE'} : {status: 'CASCADE_RUN_STATUS_RUNNING', mainTrajectoryUpdate: {
      stepsUpdate: {indices: [0], steps: [{plannerResponse: {response: 'old answer'}}], totalLength: 1}
    }}));
    return () => {};
  },
}});
const {streamResponse} = await import('./dist/converter.js');
test('indexed deltas preserve history boundary and accept a shorter new answer', async () => {
  const chunks = [];
  for await (const chunk of streamResponse(0, '', '', 'existing', '', 1000, async () => {
    queueMicrotask(() => {
      update({status:'CASCADE_RUN_STATUS_RUNNING', mainTrajectoryUpdate:{stepsUpdate:{
        indices:[1], steps:[{type:'CORTEX_STEP_TYPE_USER_INPUT'}], totalLength:2
      }}});
      update({status:'CASCADE_RUN_STATUS_RUNNING', mainTrajectoryUpdate:{stepsUpdate:{
        indices:[2], steps:[{plannerResponse:{response:'new'}}], totalLength:3
      }}});
      update({status:'CASCADE_RUN_STATUS_IDLE'});
    });
  })) chunks.push(chunk);
  assert.equal(chunks.filter(x=>x.type==='content_delta').map(x=>x.text).join(''), 'new');
  assert.equal(chunks.at(-1).type, 'done');
});
test('empty new cascade sends after initial status without stepsUpdate', async () => {
  emptySnapshot = true;
  let sent = false;
  const chunks = [];
  for await (const c of streamResponse(0, '', '', 'new', '', 1000, async () => {
    sent = true;
    queueMicrotask(() => update({status:'CASCADE_RUN_STATUS_IDLE', mainTrajectoryUpdate:{stepsUpdate:{
      indices:[0,1], steps:[{type:'CORTEX_STEP_TYPE_USER_INPUT'}, {plannerResponse:{response:'fresh'}}], totalLength:2
    }}}));
  })) chunks.push(c);
  assert.ok(sent);
  assert.equal(chunks[0].text, 'fresh');
  assert.equal(chunks.at(-1).type, 'done');
});
