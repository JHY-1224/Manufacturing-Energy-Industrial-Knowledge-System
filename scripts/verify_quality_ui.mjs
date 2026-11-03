// Writes only to the explicitly identified synthetic backend on port 18061.
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
const output=resolve(process.env.QUALITY_QA_DIR);
const fixture=JSON.parse(await readFile(resolve(output,'ui-server.json'),'utf8'));
assert.equal(fixture.port,18061);assert.ok(fixture.store.endsWith('/ui-fixture'));
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage(),errors=[],attempt=String(Date.now());
page.on('pageerror',error=>errors.push(error.message));
page.on('dialog',dialog=>dialog.accept());
const backend='http://127.0.0.1:18061/api/v2/workbench',front='http://127.0.0.1:8080';
async function call(path,method='GET',body){const response=await context.request.fetch(backend+path,{method,data:body});assert.ok(response.ok(),await response.text());return response.json();}
const initial=await call('/summary');assert.equal(initial.document_count,1);
assert.equal((await call('/documents/'+fixture.document_id)).source_name,'SYN-QA-REVIEW.md');
const priorHistory=(await call('/documents/'+fixture.document_id+'/review/history')).total;
const priorState=(await call('/documents/'+fixture.document_id+'/review')).state;
await page.route('**/api/**',async route=>{
 const request=route.request(),url=new URL(request.url());
 const response=await context.request.fetch('http://127.0.0.1:18061'+url.pathname+url.search,{method:request.method(),headers:request.headers(),data:request.postDataBuffer()||undefined});
 await route.fulfill({response});
});
async function review(){await page.goto(front+'/#/reviews');await page.getByRole('heading',{name:'资料审核与发布',exact:true}).waitFor();await page.locator('.wb-review-grid .wb-task-list button').filter({hasText:'SYN-QA-REVIEW.md'}).click();await page.getByLabel('审核人',{exact:true}).waitFor();}
async function fill(note){await page.getByLabel('审核人',{exact:true}).fill('合成验收审核人');await page.getByLabel('审核说明',{exact:true}).fill(note);await page.getByLabel('适用范围',{exact:true}).fill('只适用于合成软件验收，不用于现场生产。');}
async function retrieve(published){await page.goto(front+'/#/retrieval');await page.locator('.wb-search-options select').first().selectOption('manufacturing');await page.getByLabel('只检索已审核发布资料',{exact:true}).setChecked(published);await page.getByLabel('检索问题',{exact:true}).fill('SYN-REVIEW-TEMP 温度');await page.getByRole('button',{name:'开始检索',exact:true}).click();await page.locator('p.wb-muted').filter({hasText:/^Trace /}).waitFor();}
try{
 await review();await fill('已核对合成来源、42 °C 单位和仅测试适用范围。');
 await page.getByRole('button',{name:priorState==='withdrawn'?'恢复为审核草稿':'保存为审核草稿',exact:true}).click();await page.getByText('审核记录已保存：审核草稿',{exact:true}).waitFor();
 await fill('核对当前修订后发布合成验收记录。');await page.getByRole('button',{name:'发布当前修订',exact:true}).click();await page.getByText('审核记录已保存：已审核发布',{exact:true}).waitFor();
 assert.equal((await call('/documents/'+fixture.document_id+'/review')).state,'published');
 await page.screenshot({path:resolve(output,'review-published-desktop.png')});
 await retrieve(true);await page.locator('.wb-hit').first().waitFor();
 assert.ok(await page.getByLabel('表格与中英术语增强',{exact:true}).isChecked());
 await page.screenshot({path:resolve(output,'published-only-retrieval.png')});
 const doc=await call('/documents/'+fixture.document_id),chunks=(await call('/chunks?document_id='+fixture.document_id)).items;
 const selected=chunks.find(c=>c.content.includes('SYN-QA-REVIEW'))||chunks[0];
 await call('/chunks/'+selected.chunk_id,'PATCH',{expected_revision:doc.revision_id,content:selected.content+'\n合成修订2，需要重新审核。'});
 assert.equal((await call('/documents/'+fixture.document_id+'/review')).state,'needs_review');
 await retrieve(true);await page.getByText('当前知识库和过滤条件下没有可用证据。',{exact:true}).waitFor();
 await review();await fill('修订后复核，先保存草稿。');await page.getByRole('button',{name:'保存为审核草稿',exact:true}).click();await page.getByText('审核记录已保存：审核草稿',{exact:true}).waitFor();
 await fill('合成资料撤回演练，原件与历史应保留。');await page.getByRole('button',{name:'撤回资料',exact:true}).click();await page.getByText('审核记录已保存：已撤回',{exact:true}).waitFor();
 await page.screenshot({path:resolve(output,'review-withdrawn-desktop.png')});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:resolve(output,'review-mobile-390.png'),fullPage:true});
 await retrieve(false);await page.getByText('当前知识库和过滤条件下没有可用证据。',{exact:true}).waitFor();
 const current=await call('/documents/'+fixture.document_id+'/review');assert.equal(current.state,'withdrawn');
 const history=await call('/documents/'+fixture.document_id+'/review/history');assert.equal(history.total,priorHistory+4);
 const source=await context.request.get(backend+'/documents/'+fixture.document_id+'/source');assert.ok(source.ok());assert.match(await source.text(),/42/);
 assert.deepEqual(errors,[]);
 await writeFile(resolve(output,'ui-result.json'),JSON.stringify({status:'passed',synthetic_only:true,errors,checks:['draft','publish','published_only_hit','edit_invalidates_approval','published_only_empty_after_edit','withdrawal_excludes_reference_search','history_and_original_retained','table_option_visible','mobile_390_no_overflow']},null,2));
 console.log('Knowledge quality UI acceptance passed: '+output);
}catch(error){await page.screenshot({path:resolve(output,'ui-failure-'+attempt+'.png'),fullPage:true});throw error;}
finally{await browser.close();}
