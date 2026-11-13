"""Exercise the actual inline chat renderer with a minimal DOM and no network."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_stream_and_replay_join_adjacent_deltas_without_inventing_newlines():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the browser renderer regression test")
    html = (Path(__file__).parents[1] / "app/ai/p5/ui.html").read_text(encoding="utf-8")
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const code = require('node:fs').readFileSync(0, 'utf8');
const elements = new Map();
function element() {
  return {children: [], textContent: '', className: '', scrollHeight: 0,
    append(child){this.children.push(child)}, replaceChildren(){this.children=[]}};
}
const doc = {getElementById(id){if (!elements.has(id)) elements.set(id, element()); return elements.get(id)}, createElement(){return element()}, createTextNode(text){return {textContent:text}}};
function part(calls,reported,missing,pending,input,output){return {calls,reported,missing,pending,not_dispatched:0,input_tokens:input,output_tokens:output,total_tokens:input+output}}
const completeUsage={measurement:'openai',parts:{compression:part(1,1,0,0,65000,100),generation:part(2,2,0,0,1500,200),web_search:part(1,1,0,0,100,20)}};
const events = [
 {run_id:'replay',seq:1,kind:'user_message',content:'hello'},
 {run_id:'replay',seq:6,kind:'context_usage',content:JSON.stringify({input_tokens:60000,model_window:1050000,output_reserve:8000,safety_reserve:8000,compression_threshold:64000,measurement:'openai'})},
 {run_id:'replay',seq:7,kind:'generation_usage_pending',content:''},
 {run_id:'replay',seq:8,kind:'generation_usage',content:JSON.stringify({input_tokens:60000,output_tokens:100,total_tokens:60100})},
 {run_id:'replay',seq:9,kind:'run_usage',content:JSON.stringify(completeUsage)},
 {run_id:'replay',seq:2,kind:'delta',content:'one'},
 {run_id:'replay',seq:3,kind:'delta',content:' two'},
 {run_id:'replay',seq:4,kind:'delta',content:'\nthree'},
 {run_id:'replay',seq:5,kind:'completed',content:'done'}
];
const context = {document:doc, console, part, confirm:()=>true,
 fetch:async (url)=>{if(url==='/p5/config') return new Promise(()=>{}); return {ok:true,json:async()=>url.endsWith('/messages')?[{role:'user',content:'legacy user'},{role:'assistant',content:'legacy reply'},{role:'user',content:'hello'},{role:'assistant',content:'one two\nthree'}]:events}},
 window:{open(){}}, EventSource:class {close(){}}};
const checks = `
addEvent('live',{seq:1,kind:'delta',content:'가'});
addEvent('live',{seq:2,kind:'delta',content:'나'});
addEvent('live',{seq:2,kind:'delta',content:'나'});
addEvent('live',{seq:3,kind:'delta',content:'\\n다'});
if ($('timeline').children.length!==1 || $('timeline').children[0].textContent!=='가나\\n다') throw Error('live delta rendering');
addEvent('live',{seq:4,kind:'tool_started',content:'조회 중'});
addEvent('live',{seq:5,kind:'delta',content:'라'});
if ($('timeline').children.length!==3 || $('timeline').children[2].textContent!=='라') throw Error('status boundary');
addEvent('live',{seq:6,kind:'context_usage',content:JSON.stringify({input_tokens:70000,model_window:1050000,output_reserve:8000,safety_reserve:8000,compression_threshold:64000,measurement:'synthetic'})});
if (!$('contextHeadline').textContent.includes('70,000') || !$('contextHeadline').textContent.includes('합성 추정')) throw Error('input meter');
if (!$('contextBudget').textContent.includes('964,000') || !$('contextThreshold').textContent.includes('64,000')) throw Error('reservation meter');
addEvent('live',{seq:9,kind:'run_usage',content:JSON.stringify({measurement:'openai',parts:{compression:part(1,1,0,0,100,10),generation:part(1,0,0,1,0,0),web_search:part(0,0,0,0,0,0)}})});
if (!$('runUsage').textContent.includes('부분 사용량') || !$('runUsage').textContent.includes('110')) throw Error('partial total');
addEvent('live',{seq:7,kind:'generation_usage_pending',content:''});
addEvent('live',{seq:8,kind:'failed',content:'failed'});
if (!$('generationUsage').textContent.includes('확인할 수 없습니다')) throw Error('unknown usage');
if (!$('runUsage').textContent.includes('미확인 1건') || !$('runUsage').textContent.includes('진행 중 0건')) throw Error('interrupted total');
sid='synthetic';
loadHistory(null).then(()=>{
  const children=$('timeline').children;
  if(children.length!==5 || children[0].textContent!=='나: legacy user' || children[1].textContent!=='AI: legacy reply' || children[3].textContent!=='one two\\nthree') throw Error('replay and legacy history rendering');
  if (!$('contextHeadline').textContent.includes('60,000') || !$('contextHeadline').textContent.includes('OpenAI 계수')) throw Error('replay count');
  if (!$('generationUsage').textContent.includes('60,100')) throw Error('replay usage');
  if (!$('runUsage').textContent.includes('66,920') || !$('runUsageParts').textContent.includes('웹 검색')) throw Error('replay total');
  console.log('live and replay rendering passed');
}).catch(e=>{console.error(e);process.exitCode=1});
`;
context.process=process;
vm.runInNewContext(code + '\n' + checks, context);
"""
    completed = subprocess.run(
        [node, "-e", harness], input=script, text=True, encoding="utf-8", capture_output=True, timeout=15
    )
    assert completed.returncode == 0, completed.stderr


def test_generated_record_is_editable_without_overwriting_or_automatic_save():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the browser renderer regression test')
    html = (Path(__file__).parents[1] / 'app/ai/p5/ui.html').read_text(encoding='utf-8')
    script = html.split('<script>', 1)[1].split('</script>', 1)[0]
    harness = r"""
const vm=require('node:vm'), elements=new Map(), calls=[];
function element(){return {children:[],value:'',textContent:'',className:'',scrollHeight:0,append(...children){this.children.push(...children)},replaceChildren(){this.children=[]}}}
const doc={getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement(){return element()},createTextNode(text){return {textContent:text}}};
const old={run_id:'old',seq:1,kind:'record_candidate',content:JSON.stringify({title:'과거 제목',body:'과거 내용'})};
const context={document:doc,console,process,confirm:()=>true,window:{open(){}},EventSource:class{close(){}},fetch:async(url,options={})=>{
  if(url==='/p5/config')return new Promise(()=>{});
  calls.push({url,options});return {ok:true,json:async()=>url.endsWith('/messages')?[]:url.endsWith('/events')?[old]:{revision:2}};
}};
const checks=`
$('recordTitle').value='기존 제목'; $('recordBody').value='사용자 편집';
let p=JSON.stringify({title:'제안 제목',body:'제안 내용'});
addEvent('live',{seq:1,kind:'record_candidate',content:p});
if($('recordTitle').value!=='기존 제목'||$('recordBody').value!=='사용자 편집\\n\\n제안 제목\\n제안 내용')throw Error('existing draft overwritten or proposal omitted');
if(!$('recordState').textContent.includes('미저장'))throw Error('not marked unsaved');
addEvent('live',{seq:1,kind:'record_candidate',content:p});
if($('recordBody').value.split('제안 내용').length!==2)throw Error('duplicate live event appended');
if(calls.some(x=>x.options.method==='PUT'))throw Error('proposal saved automatically');
$('recordTitle').value='직접 수정 제목'; $('recordBody').value='직접 수정 본문';
sid='synthetic';loadHistory(null).then(async()=>{
 if($('recordTitle').value!=='직접 수정 제목'||$('recordBody').value!=='직접 수정 본문')throw Error('history replay modified draft');
 let oldButton=$('timeline').children[0].children[1];oldButton.onclick();
 if(!$('recordBody').value.includes('과거 내용'))throw Error('historical proposal unavailable');
 $('recordTitle').value='';$('recordBody').value='';
 addEvent('fresh',{seq:1,kind:'record_candidate',content:p});
 if($('recordTitle').value!=='제안 제목'||$('recordBody').value!=='제안 내용')throw Error('new proposal did not populate fields');
 $('recordTitle').value='수정한 제목';$('recordBody').value='수정한 본문';
 await $('saveRecord').onclick();
 let save=calls.find(x=>x.options.method==='PUT'),body=JSON.parse(save.options.body);
 if(body.title!=='수정한 제목'||body.body!=='수정한 본문'||!body.approved)throw Error('explicit save did not use edited values');
 console.log('editable record proposal passed');
}).catch(e=>{console.error(e);process.exitCode=1});`;
context.calls=calls;vm.runInNewContext(require('node:fs').readFileSync(0,'utf8')+'\n'+checks,context);
"""
    completed = subprocess.run([node, '-e', harness], input=script, text=True, encoding='utf-8', capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr


def test_search_sources_are_clickable_after_streaming_and_safe():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the browser renderer regression test')
    html = (Path(__file__).parents[1] / 'app/ai/p5/ui.html').read_text(encoding='utf-8')
    script = html.split('<script>', 1)[1].split('</script>', 1)[0]
    harness = r"""
const vm=require('node:vm'),elements=new Map();
function element(tag='div') {return {tag,children:[],textContent:'',className:'',scrollHeight:0,append(...x){this.children.push(...x)},replaceChildren(...x){this.children=x}}}
const doc={getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement(tag){return element(tag)},createTextNode(text){return {textContent:text}}};
const context={document:doc,URL,console,process,confirm:()=>true,window:{open(){}},EventSource:class{close(){}},fetch:async()=>new Promise(()=>{})};
const checks=`
addEvent('live',{seq:1,kind:'delta',content:'출처 https://exam'});
addEvent('live',{seq:2,kind:'delta',content:'ple.com/a 및 javascript:alert(1)'});
addEvent('live',{seq:3,kind:'completed',content:'완료'});
let answer=$('timeline').children[0];let links=answer.children.filter(x=>x.tag==='a');
if(links.length!==1||links[0].href!=='https://example.com/a'||links[0].rel!=='noopener noreferrer')throw Error('stream link not safely rendered');
line('status','출처: [공식 문서](https://example.org/info).');
let sourceLinks=$('timeline').children[2].children.filter(x=>x.tag==='a');
if(sourceLinks.length!==1||sourceLinks[0].href!=='https://example.org/info'||sourceLinks[0].textContent!=='공식 문서')throw Error('source link not clickable');
console.log('safe search link rendering passed');`;
vm.runInNewContext(require('node:fs').readFileSync(0,'utf8')+'\n'+checks,context);
"""
    completed = subprocess.run([node, '-e', harness], input=script, text=True, encoding='utf-8', capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
