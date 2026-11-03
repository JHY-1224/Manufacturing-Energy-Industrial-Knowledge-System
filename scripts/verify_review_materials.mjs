// Local review material UI checks. Real cases stay pending; decisions are tested on a synthetic fixture only.
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root=resolve(process.argv[2]);
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1360,height:950},acceptDownloads:true});
const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
 await page.goto(pathToFileURL(root+'/review72-delivery.html').href);
 assert.equal(await page.locator('article').count(),72);
 assert.equal(await page.locator('article select').evaluateAll(xs=>xs.filter(x=>x.value!=='pending').length),0);
 assert.equal(await page.locator('article input:checked').count(),0);
 await page.locator('#export').click();assert.match(await page.locator('#status').innerText(),/填写审核人/);
 await page.locator('#search').fill('real_quality_53');assert.equal(await page.locator('article:visible').count(),1);
 await page.screenshot({path:root+'/review72-desktop.png',fullPage:false});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:root+'/review72-mobile.png',fullPage:false});
 // A separate synthetic page exercises draft persistence and explicit export.
 await page.goto(pathToFileURL(root+'/review-fixture.html').href);
 await page.locator('#reviewer').fill('Synthetic UI fixture');
 await page.locator('article select').selectOption('approved');
 await page.locator('textarea').fill('Synthetic source contract checked; no real knowledge approved.');
 await page.locator('#export').click();assert.match(await page.locator('#status').innerText(),/核对勾选/);
 for (const box of await page.locator('article input[type=checkbox]').all())await box.check();
 await page.locator('#save').click();await page.reload();assert.equal(await page.locator('select').inputValue(),'pending');
 await page.locator('#restore').click();assert.equal(await page.locator('select').inputValue(),'approved');
 const download=page.waitForEvent('download');await page.locator('#export').click();await (await download).saveAs(root+'/synthetic-ui-decisions.json');
 await page.setViewportSize({width:1360,height:950});
 await page.goto(pathToFileURL(root+'/table-audit-final/entries/bipm_si_2026__p0033_table01.html').href);
 await page.locator('td[data-cell]').first().click();assert.ok(Number(await page.locator('#focus').getAttribute('width'))>0);
 assert.equal(await page.locator('sup').count(),2);
 await page.screenshot({path:root+'/table-cell-desktop.png',fullPage:false});
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:root+'/table-cell-mobile.png',fullPage:false});
 assert.deepEqual(errors,[]);
 await writeFile(root+'/'+(process.argv[3]||'ui-result.json'),JSON.stringify({status:'passed',real_cases:72,real_cases_modified:0,synthetic_export_only:true,desktop_and_mobile:true,cell_highlight:true,page_errors:errors},null,2),{flag:'wx'});
} finally {await browser.close();}
