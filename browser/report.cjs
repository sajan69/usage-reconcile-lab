const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
(async () => {
  const root = path.join(__dirname, 'artifacts');
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE,
      args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']} : {}) });
  const checks = [];
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    const dialogs = [], errors = [], network = [];
    page.on('dialog', async d => { dialogs.push(d.message()); await d.dismiss(); });
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', req => { if (/^https?:/.test(req.url())) network.push(req.url()); });
    await page.goto(pathToFileURL(path.join(root, 'comparison.html')).href);
    assert.equal(await page.getByRole('heading',{name:'Usage comparison',exact:true}).count(), 1);
    assert.ok((await page.locator('body').innerText()).includes('13 conditional metric comparisons; 11 withheld'));
    assert.ok((await page.locator('body').innerText()).includes('Unknown / unobserved'));
    checks.push('worked totals and explicit unknowns');
    const provenance = page.getByText('Snapshot provenance and quality',{exact:true});
    await provenance.click();
    assert.ok(await page.locator('details').last().getAttribute('open') !== null);
    assert.ok((await page.locator('details').last().innerText()).includes('synthetic-org'));
    checks.push('expandable provenance');
    await page.screenshot({path:path.join(root,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
    assert.ok(await page.locator('.scroll').first().evaluate(el=>el.scrollWidth > el.clientWidth));
    await page.screenshot({path:path.join(root,'mobile.png'),fullPage:true});
    checks.push('390px layout with scrollable tables');
    await page.goto(pathToFileURL(path.join(root,'probe.html')).href);
    assert.ok((await page.locator('body').innerText()).includes('<img src=x onerror="alert(1)">'));
    assert.equal(await page.locator('img,script').count(),0);
    assert.deepEqual(dialogs,[]); assert.deepEqual(errors,[]); assert.deepEqual(network,[]);
    checks.push('inert hostile labels and no network');
    fs.writeFileSync(path.join(root,'browser-checks.json'), JSON.stringify({checks,passed:checks.length},null,2));
    console.log(JSON.stringify({checks,passed:checks.length}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
