import test from 'node:test';
import assert from 'node:assert/strict';
import {fileRecovery,sessionLabel,sortMaterials,chatPath,workspacePath} from '../lib/workspace-model.ts';

test('scans and protected files require replacement; service failures allow retry',()=>{
  assert.equal(fileRecovery('No readable text. Scanned PDFs need OCR and are not supported.').action,'replace');
  assert.equal(fileRecovery('Encrypted PDFs are not supported').action,'replace');
  assert.equal(fileRecovery('Maximum 100 PDF pages').action,'replace');
  assert.equal(fileRecovery('Index worker failed. Check model cache, file format and resource limits.').action,'retry');
  assert.equal(fileRecovery('Indexing timed out').action,'retry');
  assert.equal(fileRecovery('Workspace index is full. Delete some documents.').action,'space');
});
test('legacy conversations never acquire a fictional type or lifecycle',()=>{
  assert.equal(sessionLabel({learning_mode:'socratic'}),'Conversation');
  assert.equal(sessionLabel({learning_status:'finished'}),'Conversation');
  assert.equal(sessionLabel({session_type:'questioning',learning_status:'finished'}),'Questioning');
  assert.equal(sessionLabel({session_type:'learning',learning_status:'finished'}),'Learning · Finished');
});
test('material ordering handles timezone offsets and preserves source data',()=>{
  const items=[{id:'a',name:'Z.txt',created_at:'2026-10-09T08:00:00Z'},{id:'b',name:'A.md',created_at:'2026-10-09T20:00:00+11:00'}];
  assert.deepEqual(sortMaterials(items,'recent').map(d=>d.id),['b','a']);
  assert.deepEqual(sortMaterials(items,'oldest').map(d=>d.id),['a','b']);
  assert.deepEqual(sortMaterials(items,'name').map(d=>d.id),['b','a']);
  assert.equal(items[0].id,'a');
});
test('overview, new draft and restored conversation keep distinct destinations',()=>{
  assert.equal(workspacePath('a/b'),'/dashboard/workspaces/a%2Fb');
  assert.equal(chatPath('space'),'/dashboard/chat?workspace=space&new=1');
  assert.equal(chatPath('space','old'),'/dashboard/chat?workspace=space&session=old');
});
