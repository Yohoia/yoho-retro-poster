import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, writeFile, rm, readFile } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { loadProfile, prepareProfile, profileStatus } from '../src/profile.ts';
import { loadManifest, type CredentialBackend } from '../src/config.ts';
import { startServer } from '../src/server.ts';

test('真实分发配置：正式页面保存后可被对应业务环境读取，状态与参数不含值', async () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const config = JSON.parse(await readFile(path.join(root, 'manifests/profiles.json'), 'utf8'));
  for (const name of Object.keys(config.profiles)) {
    const bindings = await loadProfile(name);
    const manifests = await Promise.all(bindings.map(b => loadManifest(b.manifest)));
    const values = new Map<string, string>();
    const backendFactory = async (ref: string): Promise<CredentialBackend> => ({
      name: 'fake', get: async () => values.get(ref),
      set: async value => { values.set(ref, value); }, delete: async () => { values.delete(ref); },
    });
    const app = await startServer({ manifests, backends: await Promise.all(manifests.map(m => backendFactory(m.credential))) });
    try {
      const headers: Record<string, string> = { Origin: app.origin, Authorization: 'Bearer ' + app.bootstrap, 'X-Local-Request': '1', 'Content-Type': 'application/json' };
      const session = await fetch(app.origin + '/api/session', { method: 'POST', headers });
      headers.Cookie = session.headers.get('set-cookie')!.split(';')[0];
      assert.equal((await fetch(app.origin)).status, 200);
      const meta = await (await fetch(app.origin + '/api/meta', { headers })).json();
      const saved = await fetch(app.origin + '/api/save', { method: 'POST', headers, body: JSON.stringify({ entries: meta.fields.map((f: { credential: string; revision: string }, i: number) => ({ credential: f.credential, revision: f.revision, value: 'TEST_ONLY_PROFILE_' + i })) }) });
      assert.equal((await saved.json()).status, 'saved');
      const status = await profileStatus(bindings, {}, async ref => values.get(ref));
      assert.equal(status.configured, true);
      assert.equal(JSON.stringify(status).includes('TEST_ONLY'), false);
      const plan = await prepareProfile(bindings, ['business-program', '--input', 'a b'], {}, async ref => values.get(ref));
      for (const b of bindings) assert.match(plan.env[b.env]!, /^TEST_ONLY_PROFILE_/);
      assert.equal(JSON.stringify(plan.args).includes('TEST_ONLY'), false);
      assert.deepEqual(plan.args, ['--input', 'a b']);
    } finally { app.close(); }
  }
});

test('已有环境凭据无需读取系统库；缺失或后端失败时不启动业务', async () => {
  const bindings = await loadProfile('default');
  const env = Object.fromEntries(bindings.map(b => [b.env, 'TEST_ONLY_ENV']));
  const noRead = async () => { throw Error('TEST_ONLY_FAILURE'); };
  assert.equal((await profileStatus(bindings, env, noRead)).configured, true);
  assert.equal((await prepareProfile(bindings, ['business'], env, noRead)).env[bindings[0].env], 'TEST_ONLY_ENV');
  await assert.rejects(prepareProfile(bindings, ['business'], {}, async () => undefined), /未配置/);
  await assert.rejects(prepareProfile(bindings, ['business'], {}, noRead), error => error instanceof Error && !error.message.includes('TEST_ONLY'));
});

test('正式业务在环境已有密钥和系统库读取两条路径均只获得所选服务密钥', async () => {
  for (const provider of ['official', 'thirdparty']) {
    const bindings = await loadProfile(provider);
    const selected = provider === 'official' ? 'OPENAI_API_KEY' : 'AICODEME_API_KEY';
    const other = provider === 'official' ? 'AICODEME_API_KEY' : 'OPENAI_API_KEY';
    for (const fromStore of [false, true]) {
      const environment = { ...process.env, OPENAI_API_KEY: 'TEST_ONLY_OFFICIAL', AICODEME_API_KEY: 'TEST_ONLY_THIRDPARTY', KEEP: 'unchanged' };
      if (fromStore) environment[selected] = '';
      const before = { ...environment };
      let reads = 0;
      const child = ['-e', `process.exit(process.env.${selected} && !process.env.${other} && process.env.KEEP === 'unchanged' ? 0 : 1)`];
      const plan = await prepareProfile(bindings, [process.execPath, ...child], environment, async () => {
        reads++; return 'TEST_ONLY_SELECTED_STORE';
      });
      assert.equal(plan.env[other], undefined);
      assert.ok(plan.env[selected]);
      assert.equal(reads, fromStore ? 1 : 0);
      assert.deepEqual(environment, before);
      await new Promise<void>((resolve, reject) => execFile(plan.command, plan.args, { env: plan.env }, error => error ? reject(error) : resolve()));
    }
  }
});

test('真实 profile run CLI 不向业务子进程继承另一服务凭据', async () => {
  const script = fileURLToPath(new URL('../src/profile.ts', import.meta.url));
  for (const provider of ['official', 'thirdparty']) {
    const selected = provider === 'official' ? 'OPENAI_API_KEY' : 'AICODEME_API_KEY';
    const other = provider === 'official' ? 'AICODEME_API_KEY' : 'OPENAI_API_KEY';
    await new Promise<void>((resolve, reject) => execFile(process.execPath, [script, 'run', provider, '--', process.execPath,
      '-e', `process.exit(process.env.${selected} && !process.env.${other} ? 0 : 1)`],
      { env: { ...process.env, OPENAI_API_KEY: 'TEST_ONLY_OFFICIAL', AICODEME_API_KEY: 'TEST_ONLY_THIRDPARTY' } },
      error => error ? reject(error) : resolve()));
  }
});

test('配置拒绝跨目录声明、危险变量与未知业务', async t => {
  const dir = await mkdtemp(path.join(tmpdir(), 'credential-profile-')); t.after(() => rm(dir, { recursive: true, force: true }));
  await mkdir(path.join(dir, 'manifests'));
  for (const binding of [{ manifest: '../outside.json', env: 'API_KEY' }, { manifest: 'default.json', env: 'NODE_OPTIONS' }]) {
    await writeFile(path.join(dir, 'manifests/profiles.json'), JSON.stringify({ version: 1, profiles: { default: [binding] } }));
    await assert.rejects(loadProfile('default', dir));
  }
  await assert.rejects(loadProfile('unknown', dir));
});
