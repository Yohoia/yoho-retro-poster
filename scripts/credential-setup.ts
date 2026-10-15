import { execFile } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadManifest, PublicError, type CredentialBackend } from './credential-ui/src/config.ts';
import { loadProfile, prepareProfile } from './credential-ui/src/profile.ts';
import { startServer } from './credential-ui/src/server.ts';

type Provider = 'official' | 'thirdparty';
const root = fileURLToPath(new URL('../', import.meta.url));
const providerLabels: Record<Provider, string> = { official: 'OpenAI 官方', thirdparty: '图万物' };

export async function testProvider(provider: Provider,
  readCredential?: (ref: string) => Promise<string | undefined>): Promise<object> {
  const bindings = await loadProfile(provider);
  const python = process.env.PYTHON3 || 'python3';
  const plan = await prepareProfile(bindings, [python, path.join(root, 'scripts', 'poster.py'), 'probe', provider],
    process.env, readCredential);
  // Only the selected provider receives its key, including when both keys exist in the parent environment.
  delete plan.env[provider === 'official' ? 'AICODEME_API_KEY' : 'OPENAI_API_KEY'];
  const result = await new Promise<{ stdout: string; stderr: string; error?: Error }>(resolve => {
    execFile(plan.command, plan.args, { cwd: root, env: plan.env, shell: false,
      timeout: 40_000, maxBuffer: 64 * 1024 },
    (error, stdout, stderr) => resolve({ error: error ?? undefined, stdout, stderr }));
  });
  if (result.error) {
    const http = result.stderr.match(/HTTP (\d{3})/);
    const noResponse = /未收到.*响应|未收到接口响应/.test(result.stderr) ||
      (result.error as NodeJS.ErrnoException).code === 'ETIMEDOUT';
    return { status: 'paused', message: noResponse ? '接口未响应，已暂停；请检查网络后再试。' :
      http ? `接口返回 HTTP ${http[1]}，测试未通过。` : '测试未完成；请检查模型、凭据或服务状态。' };
  }
  let parsed: Record<string, unknown>;
  try { parsed = JSON.parse(result.stdout); }
  catch { return { status: 'paused', message: '接口检查结果无法解析，已暂停。' }; }
  if (parsed.status === 'available') return { status: 'available',
      message: `${providerLabels[provider]}的当前模型已出现在模型列表；此检查不能证明实际生图可用。` };
  if (parsed.status === 'unavailable') return { status: 'unavailable',
      message: parsed.reason === 'HTTP 401' ?
        `${providerLabels[provider]}返回 HTTP 401。请在服务个人中心创建开发者 SK，再在本页替换 API Key；网页登录凭据不能调用图片 API。` :
        parsed.reason === 'HTTP 403' ? `${providerLabels[provider]}返回 HTTP 403，请检查密钥权限。` :
        parsed.reason === 'model_missing' ? `${providerLabels[provider]}的模型列表中没有当前模型。` :
        `${providerLabels[provider]}的凭据或当前模型不可用。` };
  return { status: 'paused', message: '测试结果不明确，已暂停。' };
}

export async function startCredentialSetup(selected: Provider, options: {
  backend?: CredentialBackend; check?: () => Promise<object>;
} = {}) {
  const bindings = await loadProfile(selected);
  const manifests = await Promise.all(bindings.map(binding => loadManifest(binding.manifest)));
  type Completion = { status: 'selected' | 'cancelled'; provider: Provider };
  let finish!: (result: Completion) => void;
  const completed = new Promise<Completion>(resolve => { finish = resolve; });
  const app = await startServer({ manifests, backend: options.backend, ttlMs: 0, autoCheck: true,
    onTest: options.check ?? (() => testProvider(selected)),
    onComplete: result => {
      const status = (result as { status: string }).status;
      if (status === 'ready') finish({ status: 'selected', provider: selected });
      else if (status === 'cancelled') finish({ status: 'cancelled', provider: selected });
    } });
  return { ...app, completed, cancel: () => { finish({ status: 'cancelled', provider: selected }); app.close(); } };
}

async function main() {
  const provider = process.argv[2];
  if (!['official', 'thirdparty'].includes(provider) || process.argv.length !== 3)
    throw new PublicError('用法：node scripts/credential-setup.ts official|thirdparty');
  const selected = provider as Provider;
  const app = await startCredentialSetup(selected);
  process.stdout.write(`${providerLabels[selected]}凭据页（保存后自动检查连接，成功后继续；未完成时持续等待）：\n${app.url}\n`);
  process.once('SIGINT', app.cancel); process.once('SIGTERM', app.cancel);
  try {
    const result = await app.completed;
    process.stdout.write(JSON.stringify(result) + '\n');
    if (result.status !== 'selected') process.exitCode = 2;
  } finally {
    process.removeListener('SIGINT', app.cancel); process.removeListener('SIGTERM', app.cancel);
    app.close();
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(error => {
    process.stderr.write((error instanceof PublicError ? error.message : '凭据页启动失败。') + '\n');
    process.exitCode = 1;
  });
