import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {dateText} from '../lib/workspace-model.ts';
const source = fs.readFileSync(new URL('../lib/locale.ts', import.meta.url), 'utf8');
const messages = fs.readFileSync(new URL('../lib/messages.ts', import.meta.url), 'utf8');
const data = s => 'data:text/javascript;base64,' + Buffer.from(ts.transpile(s, {module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020})).toString('base64');
const locale = await import(data(source.replace("'./messages'",JSON.stringify(data(messages)))));
test('first visit follows browser language; manual choice overrides it',()=>{
 assert.equal(locale.resolveLocale(null,['zh-CN','en']),'zh-CN');
 assert.equal(locale.resolveLocale(null,['en-AU','zh-CN']),'en');
 assert.equal(locale.resolveLocale(null,['fr-FR']),'en');
 assert.equal(locale.resolveLocale('en',['zh-CN']),'en');
 assert.equal(locale.resolveLocale('zh-CN',['en']),'zh-CN');
 assert.equal(locale.resolveLocale('invalid',['zh-Hans']),'zh-CN');
});
test('language subscriptions update immediately without changing content',()=>{
 let updates=0;const stop=locale.subscribeLocale(()=>updates++);
 locale.setLocale('zh-CN',false);assert.equal(locale.t('Materials'),'资料');
 assert.equal(locale.uiError('Invalid email or password'),'邮箱或密码不正确');
 const name='My Notes {private}.pdf';
 assert.equal(locale.t('Actions for {name}',{name}),name+'的操作');
 locale.setLocale('en',false);assert.equal(locale.t('Materials'),'Materials');
 assert.equal(updates,2);stop();
});
test('placeholder values are preserved and date formats follow locale',()=>{
 assert.equal(locale.translate('zh-CN','{count} materials',{count:2}),'2 份资料');
 assert.equal(locale.translate('en','{count} materials',{count:2}),'2 materials');
 assert.match(dateText('2026-10-10T00:00:00Z','zh-CN'),/2026年10月10日/);
 assert.match(dateText('2026-10-10T00:00:00Z','en'),/Oct 10, 2026/);
});
test('every catalogue interpolation retains its original parameters',()=>{
 const {zhCN}=awaitableMessages;
 for(const [en,zh]of Object.entries(zhCN)){
  assert.deepEqual([...en.matchAll(/\{(\w+)\}/g)].map(x=>x[1]).sort(),[...zh.matchAll(/\{(\w+)\}/g)].map(x=>x[1]).sort(),en);
 }
});
const awaitableMessages=await import(data(messages));

test('Chinese UI consistently calls sessions conversations without rewriting content',()=>{
 for(const [key,value] of Object.entries(awaitableMessages.zhCN)) assert.ok(!value.includes('会话'),key);
 assert.equal(locale.translate('zh-CN','Sessions'),'对话');
 assert.equal(locale.translate('zh-CN','Learning session not found'),'找不到此学习对话');
 const title='会话记录 — My original notes';
 assert.equal(locale.translate('zh-CN','Close {title}',{title}),'关闭'+title);
});
