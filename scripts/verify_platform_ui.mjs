// Read-only localhost acceptance. Inject failures only inside this fresh browser.
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const output=resolve(process.env.PLATFORM_QA_DIR);await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const root='http://127.0.0.1:8080';
const ready=()=>page.getByText('后端已连接',{exact:true}).waitFor({state:'attached',timeout:30000});
try{
  await page.route('**/api/v2/workbench/summary',route=>route.fulfill({status:500,body:''}));
  await page.goto(root+'/#/dashboard');
  await page.getByText('行业分类已显示，资料数量等待连接后加载。',{exact:false}).waitFor();
  assert.equal(await page.locator('[data-industry-id]').count(),6);
  assert.match(await page.locator('[data-industry-id=materials]').innerText(),/— 篇文档/);
  await page.screenshot({path:resolve(output,'initial-offline.png')});
  await page.unroute('**/api/v2/workbench/summary');await ready();
  assert.equal(await page.locator('[data-industry-id]').count(),6);
  assert.match(await page.locator('[data-industry-id=consumer_goods]').innerText(),/1105 篇文档/);
  const before=await page.locator('[data-industry-id=automotive]').innerText();
  await page.route('**/api/v2/workbench/summary',route=>route.fulfill({status:500,body:''}));
  await page.locator('.wb-header button').click();
  await page.getByText('当前展示',{exact:false}).waitFor();
  assert.equal(await page.locator('[data-industry-id=automotive]').innerText(),before);
  await page.unroute('**/api/v2/workbench/summary');await page.getByRole('button',{name:'立即重试'}).click();await ready();
  assert.equal(await page.locator('.wb-alert.error').count(),0);
  await page.screenshot({path:resolve(output,'dashboard-desktop.png'),fullPage:true});
  await page.locator('[data-industry-id=consumer_goods] .wb-domain-entry').click();
  await page.waitForURL(/#\/documents$/);await page.locator('.wb-image-card').first().waitFor();
  assert.equal(await page.getByLabel('文档知识库',{exact:true}).inputValue(),'cosmetics_manufacturing');
  await page.goto(root+'/#/dashboard');await ready();
  await page.locator('[data-industry-id=materials] .wb-domain-entry').click();
  await page.waitForURL(/#\/documents$/);await page.locator('.wb-image-card').first().waitFor();
  assert.equal(await page.getByLabel('文档知识库',{exact:true}).inputValue(),'materials');
  await page.waitForFunction(()=>document.querySelector('.wb-image-card img')?.naturalWidth>0);
  assert.equal(await page.locator('.wb-image-gallery>.wb-image-card').count(),50);
  const value=await page.getByLabel('资料分类',{exact:true}).locator('option').nth(1).getAttribute('value');
  const categoryResponse=page.waitForResponse(r=>r.url().includes('/documents?')&&new URL(r.url()).searchParams.get('category')===value);
  await page.getByLabel('资料分类',{exact:true}).selectOption(value);
  const filtered=await (await categoryResponse).json();assert.ok(filtered.total>0&&filtered.total<430);
  assert.ok(filtered.items.every(d=>d.assigned_category===value));
  await page.waitForFunction(count=>document.querySelector('.wb-document-filters')?.textContent.includes(count+' 份 · 每页'),filtered.total);
  await page.waitForFunction(()=>{const images=[...document.querySelectorAll('.wb-image-gallery>.wb-image-card img')].filter(e=>e.getBoundingClientRect().top<innerHeight);return images.length&&images.every(e=>e.complete&&e.naturalWidth>0);});
  await page.screenshot({path:resolve(output,'documents-desktop.png')});
  await page.getByLabel('搜索文档',{exact:true}).fill('DOES-NOT-EXIST-QUALITY-TEST');
  await page.getByText('没有符合筛选条件的资料。',{exact:true}).waitFor();
  await page.getByLabel('搜索文档',{exact:true}).fill('');
  await page.getByLabel('文档知识库',{exact:true}).selectOption('cosmetics_manufacturing');
  await page.getByRole('button',{name:'找相似图',exact:true}).first().waitFor();
  await page.getByRole('button',{name:'找相似图',exact:true}).first().click();
  await page.locator('.wb-similar .wb-image-card').first().waitFor({timeout:30000});
  assert.ok(await page.locator('.wb-similar .wb-image-card').count()>0);
  await page.locator('.wb-similar').screenshot({path:resolve(output,'similar-images.png')});
  await page.locator('.wb-image-gallery>.wb-image-card').filter({has:page.locator('img')}).first().getByRole('button',{name:'原图与证据',exact:true}).click();
  await page.waitForURL(/#\/chunks$/);await page.locator('.wb-original-image img').waitFor();
  await page.waitForFunction(()=>document.querySelector('.wb-original-image img')?.naturalWidth>0);
  await page.screenshot({path:resolve(output,'chunk-evidence.png')});
  await page.goto(root+'/#/retrieval');await ready();
  await page.locator('.wb-search-options select').first().selectOption('materials');
  assert.ok(await page.getByLabel('关联图片 OCR',{exact:false}).isChecked());
  await page.getByLabel('检索问题',{exact:true}).fill('硬质 PVC 管材 高速混合 低速搅拌');
  await page.getByRole('button',{name:'开始检索',exact:true}).click();
  await page.locator('.wb-hit').first().waitFor({timeout:90000});
  await page.getByText('图片介绍 + OCR 上下文 · 自动识别未审核',{exact:false}).first().waitFor();
  await page.screenshot({path:resolve(output,'retrieval-ocr-context.png')});
  for(const route of ['dashboard','documents','chunks','retrieval','tasks']){
    await page.setViewportSize({width:390,height:844});await page.goto(root+'/#/'+route);await ready();
    await page.evaluate(()=>document.fonts.ready);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile overflow: '+route);
    await page.screenshot({path:resolve(output,route+'-mobile.png')});
  }
  assert.deepEqual(errors,[]);
  await writeFile(resolve(output,'result.json'),JSON.stringify({status:'passed',readOnlyMainLibrary:true,
    checks:['initial_500_catalog','automatic_recovery','stale_snapshot_retained','manual_recovery','six_industries','cosmetics_child','consumer_card_opens_cosmetics','category_filter','gallery_50','search_empty','local_image_preview','same_scope_similar_images','real_retrieval_ocr_context','mobile_390_no_overflow'],errors},null,2));
  console.log('UI acceptance passed: '+output);
}catch(e){await page.screenshot({path:resolve(output,'failure.png'),fullPage:true});throw e;}
finally{await browser.close();}
