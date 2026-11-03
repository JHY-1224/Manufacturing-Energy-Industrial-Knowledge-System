// Local synthetic acceptance only. Requires an explicitly supplied Playwright installation.
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output=resolve(process.env.WORKBENCH_QA_DIR || 'data/runtime/workbench-qa-20261101');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1280,height:645},deviceScaleFactor:1});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
const base='http://127.0.0.1:8080';
async function route(id){await page.goto(`${base}/#/${id}`);await page.waitForFunction(()=>document.querySelector('.wb-health strong')?.textContent==='后端已连接');await page.evaluate(()=>document.fonts.ready);await page.evaluate(()=>window.scrollTo(0,0));}
try{
  if(process.env.QA_CAPTURE_ONLY!=='1'){
    await route('upload');
    const path=resolve(output,'合成制造验收规程.md');
    await writeFile(path,'# 合成制造验收规程\n\n本文仅为软件验收的合成资料，不是实际工艺标准。\n\n## 工艺参数\n\nTEST-ALPHA 工艺的测试温度设定为 36 摄氏度，保持 12 分钟。操作前检查合成设备 A 的状态记录。\n\n## 检验记录\n\n| 字段 | 合成值 |\n| --- | --- |\n| Batch-ID | SYN-001 |\n| QA-CHECK | 已完成 |\n\n## 异常处理\n\nTEST-ALPHA 出现合成异常时停止本次测试，并记录异常编号 SYN-ERR-01。所有结论仅用于验证软件检索。\n');
    await page.locator('input[type=file]').setInputFiles(path);
    await page.getByRole('button',{name:'开始上传入库',exact:true}).click();
    await page.waitForURL(/#\/(tasks|chunks)$/, {timeout:90000});
    if(page.url().endsWith('/tasks'))await page.getByText('1 / 1 · 100%',{exact:true}).waitFor({timeout:90000});
    await route('documents');
    await page.getByLabel('搜索文档',{exact:true}).fill('合成制造验收规程.md');
    const importedRow=page.locator('.wb-table tbody tr').filter({hasText:'合成制造验收规程.md'}).first();
    await importedRow.getByRole('button',{name:'查看切分',exact:true}).click();
    await page.getByRole('heading',{name:'Chunk 详情',exact:true}).waitFor({timeout:90000});
    await page.locator('.wb-chunk-list>button').nth(1).waitFor();
    assert.ok(await page.locator('.wb-chunk-list>button').count()>1);
    await page.getByRole('button',{name:'编辑',exact:true}).click();
    await page.locator('.wb-modal textarea:focus').waitFor();
    await page.locator('.wb-modal textarea').fill('本文仅为软件验收的合成资料，不是实际工艺标准。人工复核编号 SYN-REVIEW-01。');
    await page.locator('.wb-modal').getByRole('button',{name:'保存',exact:true}).click();
    await page.locator('.wb-modal').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'停用检索',exact:true}).click();
    await page.getByRole('button',{name:'启用检索',exact:true}).waitFor();
    await page.getByRole('button',{name:'启用检索',exact:true}).click();
    await page.getByRole('button',{name:'停用检索',exact:true}).waitFor();
    await route('retrieval');
    await page.getByLabel('检索问题',{exact:true}).fill('TEST-ALPHA 测试温度');
    await page.getByRole('button',{name:'开始检索',exact:true}).click();
    await page.locator('.wb-hit').first().waitFor();
    assert.match(await page.locator('.wb-hit').first().innerText(),/36/);
    await route('agent');
    await page.getByRole('button',{name:'新建会话',exact:true}).click();
    await page.locator('.wb-session-row.active').waitFor();
    await page.getByLabel('对话问题',{exact:true}).fill('TEST-ALPHA 的测试温度与保持时间是多少？');
    await page.getByRole('button',{name:'发送',exact:true}).click();
    await page.getByRole('button',{name:'停止生成',exact:true}).waitFor({state:'hidden',timeout:90000});
    await page.locator('.wb-message.assistant').first().waitFor();
    assert.match(await page.locator('.wb-message.assistant').first().innerText(),/36/);
    assert.ok(await page.locator('.wb-source').count()>0);
    await page.getByLabel('对话问题',{exact:true}).fill('我刚问了什么？');
    await page.getByRole('button',{name:'发送',exact:true}).click();
    await page.getByRole('button',{name:'停止生成',exact:true}).waitFor({state:'hidden',timeout:90000});
    assert.match(await page.locator('.wb-message.assistant').last().innerText(),/TEST-ALPHA/);
    await page.reload();await page.getByText('后端已连接',{exact:true}).waitFor();
    await page.locator('.wb-session-row>button:first-child').first().click();
    await page.waitForFunction(()=>document.querySelectorAll('.wb-message').length===4);
    assert.equal(await page.locator('.wb-message').count(),4);
    // Return to an evidence-bearing turn for reference comparison.
    await page.getByLabel('对话问题',{exact:true}).fill('请给出 TEST-ALPHA 的检验记录和来源。');
    await page.getByRole('button',{name:'发送',exact:true}).click();
    await page.getByRole('button',{name:'停止生成',exact:true}).waitFor({state:'hidden',timeout:90000});
  }
  for(const id of ['dashboard','upload','documents','chunks','retrieval','agent']){
    await page.setViewportSize({width:1280,height:({upload:644,documents:653,chunks:640,agent:655})[id]||645});
    await route(id);
    if(id==='chunks'){
      const option=await page.locator('select[aria-label="筛选文档"] option').allTextContents();
      const name=option.find(x=>x.includes('合成制造验收规程'));if(name)await page.getByLabel('筛选文档').selectOption({label:name});
    }
    if(id==='retrieval'){await page.getByLabel('检索问题',{exact:true}).fill('TEST-ALPHA 测试温度');await page.getByRole('button',{name:'开始检索',exact:true}).click();await page.locator('.wb-hit').first().waitFor();}
    if(id==='agent'&&await page.locator('.wb-session-row').count()){
      await page.locator('.wb-session-row>button:first-child').first().click();
      await page.locator('.wb-message').first().waitFor();
      await page.evaluate(()=>{const node=document.querySelector('.wb-conversation');node.scrollTop=node.scrollHeight;});
    }
    await page.screenshot({path:resolve(output,`${id}-desktop.png`)});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`desktop overflow: ${id}`);
  }
  for(const id of ['knowledge','metadata','vector','graph','ragas','api','open']){await route(id);await page.locator('.wb-legacy').waitFor();}
  await page.setViewportSize({width:390,height:844});
  for(const id of ['dashboard','upload','documents','chunks','retrieval','agent']){
    await route(id);
    if(id==='agent'&&await page.locator('.wb-session-row').count()){await page.locator('.wb-session-row>button:first-child').first().click();await page.locator('.wb-message').first().waitFor();}
    await page.screenshot({path:resolve(output,`${id}-mobile.png`)});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`mobile overflow: ${id}`);
  }
  assert.deepEqual(errors,[]);
  await writeFile(resolve(output,'ui-result.json'),JSON.stringify({status:'passed',errors,desktop:[1280,645],mobile:[390,844],syntheticOnly:true},null,2));
  console.log('Workbench browser acceptance passed: '+output);
}catch(error){await page.screenshot({path:resolve(output,'failure.png'),fullPage:true});throw error;}
finally{await browser.close();}
