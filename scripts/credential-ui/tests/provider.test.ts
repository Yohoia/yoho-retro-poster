import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chmod, mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { startCredentialSetup, testProvider } from '../../credential-setup.ts';

test('测试回调只注入所选服务的凭据，并仅返回脱敏结论', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'poster-provider-test-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const fakePython = path.join(directory, 'fake-python');
  await writeFile(fakePython, `#!/usr/bin/env node
const selected = process.argv.at(-1);
if (!process.env.AICODEME_API_KEY || process.env.OPENAI_API_KEY) process.exit(3);
if (selected !== 'thirdparty' || process.argv.at(-2) !== 'probe') process.exit(4);
process.stdout.write(JSON.stringify({status: 'available'}));
`);
  await chmod(fakePython, 0o700);
  const oldPython = process.env.PYTHON3, oldThird = process.env.AICODEME_API_KEY, oldOfficial = process.env.OPENAI_API_KEY;
  process.env.PYTHON3 = fakePython;
  process.env.AICODEME_API_KEY = 'FAKE_THIRD_PARTY_ONLY';
  process.env.OPENAI_API_KEY = 'FAKE_OFFICIAL_MUST_NOT_LEAK';
  t.after(() => {
    if (oldPython === undefined) delete process.env.PYTHON3; else process.env.PYTHON3 = oldPython;
    if (oldThird === undefined) delete process.env.AICODEME_API_KEY; else process.env.AICODEME_API_KEY = oldThird;
    if (oldOfficial === undefined) delete process.env.OPENAI_API_KEY; else process.env.OPENAI_API_KEY = oldOfficial;
  });
  const connection = await testProvider('thirdparty', async () => undefined);
  assert.equal((connection as { status: string }).status, 'available');
  assert.equal(JSON.stringify(connection).includes('FAKE_'), false);
  await writeFile(fakePython, '#!/usr/bin/env node\nprocess.stderr.write("接口未收到响应"); process.exit(2);\n');
  const paused = await testProvider('thirdparty', async () => undefined);
  assert.equal((paused as { status: string }).status, 'paused');
  assert.match((paused as { message: string }).message, /已暂停/);
  await writeFile(fakePython, '#!/usr/bin/env node\nprocess.stdout.write(JSON.stringify({status:"unavailable", reason:"HTTP 401"}));\n');
  const unauthorized = await testProvider('thirdparty', async () => undefined);
  assert.equal((unauthorized as { status: string }).status, 'unavailable');
  assert.match((unauthorized as { message: string }).message, /开发者 SK/);
});

test('正式等待入口：空输入和错误密钥均不继续，保存正确密钥后自动检查并完成', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'poster-await-test-'));
  t.after(() => rm(directory, {recursive:true,force:true}));
  const fakePython = path.join(directory, 'fake-python');
  await writeFile(fakePython, `#!${process.execPath}\nconst available = process.env.AICODEME_API_KEY === 'TEST_ONLY_CORRECT' && !process.env.OPENAI_API_KEY;\nprocess.stdout.write(JSON.stringify(available ? {status:'available'} : {status:'unavailable',reason:'HTTP 401'}));\n`);
  await chmod(fakePython, 0o700);
  const before = {...process.env};
  process.env.PYTHON3 = fakePython;
  process.env.AICODEME_API_KEY = 'TEST_ONLY_STALE_ENV';
  process.env.OPENAI_API_KEY = 'TEST_ONLY_OTHER';
  t.after(() => {
    for (const key of ['PYTHON3','AICODEME_API_KEY','OPENAI_API_KEY']) {
      if (before[key] === undefined) delete process.env[key]; else process.env[key] = before[key];
    }
  });
  let value: string | undefined;
  let checks = 0, continued = false;
  const backend = {name:'test-memory', get:async()=>value, set:async (next:string)=>{value=next;}, delete:async()=>{value=undefined;}};
  const app = await startCredentialSetup('thirdparty', {backend, check:()=>{
    checks++; return testProvider('thirdparty', async()=>value);
  }});
  t.after(app.close);
  app.completed.then(()=>{continued=true;});
  const headers: Record<string,string> = {Origin:app.origin,'X-Local-Request':'1','Content-Type':'application/json',Authorization:'Bearer '+app.bootstrap};
  const session = await fetch(app.origin+'/api/session',{method:'POST',headers,body:'{}'});
  headers.Cookie = session.headers.get('set-cookie')!.split(';')[0];
  assert.doesNotMatch(session.headers.get('set-cookie')!, /Max-Age=0/);
  const meta = ()=>fetch(app.origin+'/api/meta',{headers}).then(r=>r.json());
  const save = async (secret:string)=>{
    const current = await meta();
    const response = await fetch(app.origin+'/api/save',{method:'POST',headers,body:JSON.stringify({entries:[{
      credential:current.credential,revision:current.revision,value:secret,replaceExisting:current.configured
    }]})});
    return {code:response.status,result:await response.json()};
  };
  assert.equal(checks,0); assert.equal(continued,false);
  assert.equal((await save('')).code,400);
  assert.equal(checks,0); assert.equal(continued,false);
  const failed = await save('TEST_ONLY_BAD');
  assert.equal(failed.result.status,'saved');
  assert.equal(failed.result.connection.status,'unavailable');
  assert.match(failed.result.connection.message,/HTTP 401/);
  assert.equal(checks,1); assert.equal(continued,false);
  const ready = await save('TEST_ONLY_CORRECT');
  assert.equal(ready.result.status,'ready');
  assert.equal(ready.result.connection.status,'available');
  assert.deepEqual(await app.completed,{status:'selected',provider:'thirdparty'});
  assert.equal(continued,true); assert.equal(checks,2);
  assert.doesNotMatch(JSON.stringify([failed,ready]),/TEST_ONLY/);
});

test('等待入口可取消；连接程序异常时保留等待并脱敏', async t => {
  let value: string | undefined;
  const app = await startCredentialSetup('official', {
    backend:{name:'test-memory',get:async()=>value,set:async next=>{value=next;},delete:async()=>{}},
    check:async()=>{throw Error('TEST_ONLY_MUST_NOT_LEAK');}
  });
  t.after(app.close);
  let continued=false; app.completed.then(()=>{continued=true;});
  const headers: Record<string,string> = {Origin:app.origin,'X-Local-Request':'1','Content-Type':'application/json',Authorization:'Bearer '+app.bootstrap};
  const session = await fetch(app.origin+'/api/session',{method:'POST',headers,body:'{}'});
  headers.Cookie=session.headers.get('set-cookie')!.split(';')[0];
  const meta = await (await fetch(app.origin+'/api/meta',{headers})).json();
  const result = await (await fetch(app.origin+'/api/save',{method:'POST',headers,body:JSON.stringify({value:'TEST_ONLY_INPUT',revision:meta.revision})})).json();
  assert.equal(result.connection.status,'paused');
  assert.doesNotMatch(JSON.stringify(result),/TEST_ONLY/);
  assert.equal(continued,false);
  app.cancel();
  assert.equal((await app.completed).status,'cancelled');
});
