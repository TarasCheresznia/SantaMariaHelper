import assert from 'node:assert/strict';
import fs from 'node:fs';
const js=fs.readFileSync(new URL('../cloudflare-worker/worker.js',import.meta.url),'utf8');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'))).default;
const original=globalThis.fetch;
let calls=[]; let scripted;
globalThis.fetch=async (url,options)=>{
 calls.push({url, options});
 return {ok:true,json:async()=>({choices:[{message:{content:JSON.stringify(scripted)}}]})};
};
const env={GROQ_API_KEY:'mock-key'};
const post = async (question,rules)=>{
  const res=await worker.fetch(new Request('https://test.example/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,materials:{rules,commands:[]}})}),env);
  return {status:res.status,body:await res.json()};
};
const source={id:'5',topic:'Общие правила сервера',section:'Общие правила',url:'https://forum.santamaria-rp.com/threads/demo.192/',text:'5.7 Запрещено убийство без веской причины (DM). - JAIL 75 - 120 минут/Gunban'};
scripted={status:'verified',answer:'DM — убийство без веской причины. Наказание JAIL 75–120 минут/Gunban.',cited_keys:['R1'],command_keys:[]};
let r=await post('Что такое DM?',[source]);
assert.equal(r.status,200);assert.equal(r.body.status,'verified');assert.equal(r.body.rules[0].text,source.text);
assert.equal(calls.length,1); // no double calls /interpret & /answer
scripted={status:'clarification',answer:'',clarification:'О какой активности идёт речь?',cited_keys:[]};
r=await post('Можно ли союз?',[source]);assert.equal(r.body.status,'clarification');assert.match(r.body.clarification,/активности/);
scripted={status:'verified',answer:'Необоснованный вывод',cited_keys:['R888'],command_keys:[]};
r=await post('Вопрос без доказательств',[source]);assert.equal(r.body.status,'insufficient_evidence');
scripted={status:'insufficient_evidence',answer:'',cited_keys:[]};
r=await post('Какого цвета одежда?',[source]);assert.equal(r.body.status,'insufficient_evidence');
const health=await worker.fetch(new Request('https://test.example/health'),env);
assert.equal((await health.json()).version,'32');
globalThis.fetch=original;
console.log('V32 Worker: 5 simulated integration checks PASS');
