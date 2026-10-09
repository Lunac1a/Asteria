import test from 'node:test';
import assert from 'node:assert/strict';
import {validType,parsePending,conversationUrl,chatError,readingText} from '../lib/chat-model.ts';
test('only real session types are accepted; legacy modes are not types',()=>{
 assert.equal(validType('learning'),'learning');assert.equal(validType('questioning'),'questioning');
 for(const value of ['direct','socratic','finished','',null])assert.equal(validType(value),null);
});
test('recovery cannot cross workspace, session or type',()=>{
 const pending={id:'11111111-1111-1111-1111-111111111111',question:'Why?',workspace:'a',session:'b',type:'learning'};
 assert.deepEqual(parsePending(JSON.stringify(pending),'a','b','learning'),pending);
 assert.equal(parsePending(JSON.stringify(pending),'other','b','learning'),null);
 assert.equal(parsePending(JSON.stringify(pending),'a','other','learning'),null);
 assert.equal(parsePending(JSON.stringify(pending),'a','b','questioning'),null);
 assert.equal(parsePending('broken','a','b','learning'),null);
});
test('direct links preserve workspace and session without local preferences',()=>{
 assert.equal(conversationUrl('a/b','c&d'),'/dashboard/chat?workspace=a%2Fb&session=c%26d');
});

test('pending turn retains the original UI fallback for retries',()=>{
 const turn={id:'11111111-1111-1111-1111-111111111111',question:'A*',workspace:'a',session:'b',type:'learning',uiLocale:'zh-CN'};
 assert.equal(parsePending(JSON.stringify(turn),'a','b','learning').uiLocale,'zh-CN');
});
test('errors expose actions without internal routing details',()=>{
 assert.match(chatError(new Error('Embedding timeout')),/materials/);
 assert.match(chatError(new Error('API key missing')),/Settings/);
 assert.doesNotMatch(chatError(new Error('provider stack secret')),/secret|stack/);
 assert.match(chatError(new Error('The model provider is unavailable. Check Settings and retry.')),/provider is unavailable/);
 assert.match(chatError(new Error('The model returned an invalid routing format')),/incomplete response/);
 assert.match(chatError(new Error('The model timed out')),/took too long/);
 assert.match(chatError(new Error('Material search failed')),/materials/);
});
test('presentation removes only known backend wrappers with matching evidence metadata',()=>{
 const text='### Model knowledge (unverified)\nAn explanation.';
 assert.equal(readingText({role:'assistant',content:text,answer_basis:'general'}),'An explanation.');
 assert.equal(readingText({role:'user',content:text,answer_basis:'general'}),text);
 assert.equal(readingText({role:'assistant',content:text}),text);
 assert.equal(readingText({role:'assistant',content:'### Document evidence\nFact [1]\n### Model knowledge (unverified)\nContext',answer_basis:'hybrid'}),'### From your materials\nFact [1]\n### General explanation (not confirmed by your materials)\nContext');
});
