// Real Keycloak Authorization Code + PKCE browser test, synthetic fixtures only.
import { chromium } from '@playwright/test';
import { execFileSync } from 'node:child_process';

const origin='https://127.0.0.1:8444';
const browser=await chromium.launch({headless:true});
async function login(name,password,expectedUser,expectedTicket,forbiddenTicket){
  const context=await browser.newContext({ignoreHTTPSErrors:true});
  const page=await context.newPage();
  try{
    await page.goto(origin+'/api/v1/dev/oidc/start',{waitUntil:'domcontentloaded'});
    await page.locator('#username').fill(name);
    await page.locator('#password').fill(password);
    await page.locator('#kc-login').click();
    try {
      await page.waitForURL(origin+'/account',{timeout:15000});
    } catch (error) {
      console.error('OIDC navigation result URL:',page.url());
      console.error('OIDC displayed messages:',(await page.locator('h1,h2,[role="alert"],.alert-error,.kc-feedback-text').allTextContents()).join(' | ').slice(0,800));
      throw error;
    }
    const who=await context.request.get(origin+'/api/v1/auth/me');
    if(who.status()!==200) throw Error(name+' session status '+who.status()+' '+await who.text());
    const me=await who.json();
    if(me.subjectId!==expectedUser)throw Error(name+' incorrect user binding '+JSON.stringify(me));
    const response=await context.request.get(origin+'/api/v1/dev/account/tickets');
    if(response.status()!==200)throw Error(name+' tickets '+response.status()+' '+await response.text());
    const items=(await response.json()).items;
    if(items.length!==1||items[0].id!==expectedTicket||items.some(x=>x.id===forbiddenTicket))
      throw Error(name+' BOLA leakage '+JSON.stringify(items));
    return context;
  }catch(error){await context.close();throw error;}
}
try{
 const a=await login('synthetic-a','synthetic-password-a','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
   'dddddddd-dddd-4ddd-8ddd-dddddddddddd','ffffffff-ffff-4fff-8fff-ffffffffffff');
 const b=await login('synthetic-b','synthetic-password-b','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
   'ffffffff-ffff-4fff-8fff-ffffffffffff','dddddddd-dddd-4ddd-8ddd-dddddddddddd');
 execFileSync('python',['-c',`import psycopg,os
url=os.environ['ROBOport_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
with psycopg.connect(url) as db:
 db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'")
`],{stdio:'inherit'});
 const after=await b.request.get(origin+'/api/v1/dev/account/tickets');
 if(after.status()!==200||(await after.json()).items.length!==0)throw Error('revoked link still exposes tickets');
 execFileSync('python',['-c',`import psycopg,os
url=os.environ['ROBOport_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
with psycopg.connect(url) as db:
 db.execute("UPDATE user_session SET revoked_at=now() WHERE user_id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'")
`],{stdio:'inherit'});
 const denied=await b.request.get(origin+'/api/v1/auth/me');
 if(denied.status()!==401)throw Error('revoked Keycloak-linked session still valid');
 await a.close();await b.close();
 console.log('LIVE KEYCLOAK PKCE + A/B BOLA + REVOKE: PASS');
}finally{
 await browser.close();
}
