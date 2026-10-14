import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chmod, mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { testProvider } from '../../credential-setup.ts';

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
  const connection = await testProvider('thirdparty');
  assert.equal((connection as { status: string }).status, 'available');
  assert.equal(JSON.stringify(connection).includes('FAKE_'), false);
  await writeFile(fakePython, '#!/usr/bin/env node\nprocess.stderr.write("接口未收到响应"); process.exit(2);\n');
  const paused = await testProvider('thirdparty');
  assert.equal((paused as { status: string }).status, 'paused');
  assert.match((paused as { message: string }).message, /已暂停/);
  await writeFile(fakePython, '#!/usr/bin/env node\nprocess.stdout.write(JSON.stringify({status:"unavailable", reason:"HTTP 401"}));\n');
  const unauthorized = await testProvider('thirdparty');
  assert.equal((unauthorized as { status: string }).status, 'unavailable');
  assert.match((unauthorized as { message: string }).message, /开发者 SK/);
});
