# 颜色场景海报 Skill

给出 1 个 `#RRGGBB` 主色，最多再给 2 个指定色，即可直接生成一张 2048×2048 方形无字风景插画。Agent 自行确定一个场景和构图，只发起一次图像请求。视觉方向来自 Skill 自带 `examples/` 中的 16 张海报样张：大块色面、简化地貌、宽阔留白、小比例建筑或人物、克制的细颗粒纹理。样张随 Skill 分发包一起提供，安装后可直接使用。

## 安装

让 Agent 安装：

> 请帮我安装这个 Skill：https://github.com/Yohoia/yoho-retro-poster

或使用命令安装：

```sh
npx skills add Yohoia/yoho-retro-poster --skill color-scene-poster
```

仓库根目录就是 Skill 源码。手动安装时，将仓库克隆到宿主的 Skill 目录，并将目录命名为 `color-scene-poster`。安装后按下方说明配置依赖和凭据。

## 使用

在支持 Skill 的 Agent 中加载此目录，然后说：“用 `#D98278` 生成一张山地风景海报。”地点、季节和额外颜色可选。Agent 直接交付一张 2048×2048 PNG 成品。

默认先检查 OpenAI 官方接口；官方凭据缺失或官方模型明确不可用时，才检查 `https://image.aicodeme.xyz/v1`。两边默认都用 `gpt-image-2`。模型名属于普通设置，可查看或分别修改：

```text
python3 scripts/poster.py config show
python3 scripts/poster.py config set --final-model gpt-image-2
python3 scripts/poster.py config set --thirdparty-final-model gpt-image-2
```

普通设置存于当前用户的平台配置目录；可用 `COLOR_SCENE_POSTER_CONFIG` 指定另一个设置文件位置。不要在此文件中保存 API Key。修改模型前请核对它是否支持本流程的图像编辑与目标尺寸。

## 首次配置 API Key

需要 Python 3.10+、Node.js 22.18+、本机浏览器和可用的系统凭据服务。在 `scripts/credential-ui` 中安装锁定版本依赖，随后回到 Skill 目录检查服务：

```text
npm ci --ignore-scripts
cd ../..
python3 scripts/provider_route.py
```

上面的 `cd ../..` 从 `scripts/credential-ui` 返回 Skill 目录。官方凭据缺失时，可运行 `node scripts/credential-setup.ts official` 配置官方 Key。若选路结果为 `setup_required`，运行它给出的 `node scripts/credential-setup.ts thirdparty`，打开本机链接，**由用户自己输入并保存**第三方 Key。两项凭据互不共用；已有运行时 `OPENAI_API_KEY` 或 `AICODEME_API_KEY` 可被对应服务复用。不要把密钥发给 Agent，也不要写入项目配置或 `.env`。保存后重跑选路命令。

凭据页仅提供“检查连接”，查询当前服务的模型列表，不生图。保存或检查后输入框保持可用；若检查失败，可输入新密钥，点击“替换并保存”，再检查连接。检查只能确认凭据和模型列表，不能保证图像端点或所选尺寸可用。若接口无响应，流程暂停，不自动切换到第三方。

第三方检查若返回 HTTP 401，请确认填入的是该服务个人中心创建的开发者 SK，再重新打开第三方凭据页替换；网页登录凭据不能用于图像 API。部分 macOS 独立 Python 安装缺少默认 CA 证书包，程序会在系统证书包可用时使用 `/etc/ssl/cert.pem`，仍保持 TLS 证书校验。

macOS 使用当前用户的 Keychain；Windows 的 Credential Manager 和 Linux 的 Secret Service 已有适配，但尚未在这两个系统实机验证。无安全后端时流程停止，不会退回明文存储。已有值可留空保留，输入新值才替换；保存中断后先检查状态。

## 生成与输出

Agent 创建 `version: 2` 的单场景 `plan.json`；格式见 [方案格式](references/plan-format.md)。也可以手动调用：

```text
python3 scripts/poster.py validate /path/to/plan.json
node scripts/credential-ui/src/profile.ts run official -- python3 scripts/poster.py generate /path/to/plan.json --provider official
```

这些命令以 Skill 目录为当前工作目录。选路结果是 `thirdparty` 时，把命令中的两处 `official` 均替换为 `thirdparty`。Agent 默认从 `examples/` 选 1–3 张作为风格参考，将其实际路径写入 `references`，生成时上传到所选服务；用户另提供样张时优先使用。手动方案可将 `references` 留空，使用文字视觉规范。成品保存为方案旁的 `output/poster-<id>.png`，附带同名非敏感 JSON 记录。已有完整结果会跳过，避免重复付费和覆盖。参考图目录不会被修改。

旧版 `preview`、`final --scene` 和计费 `self-test` 入口已移除；旧多场景方案按方案格式迁移到新目录。已有成品和旧测试记录保留，原有成品模型设置与系统凭据可以继续使用。

需要 3508×3508 文件时，生成命令加 `--upscale-3508`，并安装 Pillow。它是从 2048×2048 插值放大的版本，文件名会标出 `upscaled`，不增加真实细节。印刷色彩模式、出血和纸张适配不属于本 Skill。

## 数据与费用

颜色、场景说明及选用的参考图会发送给选中的图像接口；默认只生成一张成品、发起一次请求，可能产生服务费用。凭据值只由本机凭据包装器提供给业务进程，不进入 Agent 对话、生成记录或命令参数。Prompt、所用模型、服务地址和请求 ID 会存入生成记录，便于复查。

本地凭据输入页降低误贴和误存风险，不能对同一用户下任意可执行程序提供强隔离。macOS 上的第三方 `gpt-image-2` 已验证接口直接返回 2048×2048 成品并完成目视检查；当前单图入口的请求次数、固定尺寸和重复运行保护由回归测试覆盖。生成效果与服务端模型行为有关；官方接口及其他系统尚未完成真实生成验证。第三方若返回与请求不符的尺寸，先核对开发者密钥绑定的请求分组，不将其直接判定为模型不支持该尺寸。

## 验证

在 Skill 目录运行 `python3 -m unittest discover -s tests -v`。凭据组件在 `scripts/credential-ui` 目录运行 `npm run check`、`npm run build`、`npm test` 和（当前 macOS）`npm run test:native`。正式生图需要用户自己的有效凭据与可用额度。
