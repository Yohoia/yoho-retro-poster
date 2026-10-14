---
name: yoho-retro-poster
description: "当用户要求按 1–3 个颜色生成场景、制作同系列方形无字风景插画海报时使用；直接生成一张 2048×2048 成品与可复现记录。适用于有参考海报或希望沿用本 Skill 视觉规范的创作；不用于排版文字海报、照片修图、标志设计或印刷出血文件。优先使用 OpenAI 官方图像接口，明确不可用时可用独立凭据切换第三方接口。"
metadata:
  version: "0.3.3"
---

# yoho-retro-poster

## 目标

把一个主色（可另给最多两个指定色）转成一个场景，直接生成一张与参考样张接近的 2048×2048 方形无字风景插画。Agent 自行确定景物和构图。用户只需给颜色；地点、季节、情绪和禁用元素均可选。保持样张的视觉语言，避免复制某张样张的具体场景。

## 首次准备

1. 读取 [视觉规范](references/style.md)，从本 Skill 的 `examples/` 中选择 1–3 张不同构图的样张观察。若用户另提供样张，以用户样张为优先视觉依据；内置样张用于沿用系列风格。
2. 检查 Python 3.10+、Node.js 22.18+ 和本机系统凭据服务。若凭据组件尚未安装依赖，在 `scripts/credential-ui` 运行 `npm ci --ignore-scripts`；这是首次安装依赖，先告知用户。
3. 在 Skill 目录运行 `python3 scripts/poster.py config show` 查看固定的服务地址和当前模型；官方与第三方成品模型均默认 `gpt-image-2`。模型名可用 `config set --final-model` 或 `--thirdparty-final-model` 修改，密钥不能写进设置文件。
4. 运行 `python3 scripts/provider_route.py`。若官方凭据存在，它只请求官方 `/v1/models`；明确返回无此模型或 401/403 时才检查第三方。若返回 `pause`，停止，不测试生图，也不自动切换。若返回 `setup_required`，运行输出中的 `node scripts/credential-setup.ts thirdparty` 并把本机链接交给用户亲自保存；若官方凭据缺失而用户要先配置官方，则运行 `node scripts/credential-setup.ts official`。不要让用户在对话里粘贴密钥，不要替用户操作真实密钥页面。
5. 保存后重新运行选路命令，只有 `selected` 才进入生成流程。凭据页仅提供“检查连接”，只确认凭据和模型列表；保存和检查后输入框继续可用，用户可输入新密钥替换并保存后重新检查。模型列表成功不保证真实生图成功。保存中断、后端锁定或接口无响应时先核对状态并暂停。

## 生成流程

1. 取得一个 `#RRGGBB` 主色，可选一到两个额外颜色。缺失颜色时询问用户；其他信息缺失时自行选择合理方向。运行 `python3 scripts/poster.py palette '#主色'`（指定色用重复的 `--accent '#颜色'`），把色板作为构思参考，不机械地把每个色值均匀铺满画面。
2. 根据 [视觉规范](references/style.md)确定一个场景，描述景物、构图和颜色位置。颜色应成为大面积可见的视觉主角；不要把颜色与固定季节或地貌绑定。
3. 在新的任务目录创建 `plan.json`，按 [方案格式](references/plan-format.md)写入一个 `scene` 对象。默认将选定的内置样张（或用户优先指定的样张）写入 `references`，最多三张，仅作风格依据。按 Skill 实际位置填写图片绝对路径，或正确计算相对于方案目录的路径。运行 `python3 scripts/poster.py validate <plan.json>`。方案不合格时先修方案。
4. 告知用户本次将发起一次生图请求且可能产生服务费用。使用选路结果的配置名 `official` 或 `thirdparty`，运行 `node scripts/credential-ui/src/profile.ts run <配置名> -- python3 scripts/poster.py generate <plan.json> --provider <配置名>`，直接生成一张 2048×2048 PNG，默认保存到方案目录的 `output/`。流程无需生成预览或请求选稿；已有完整结果时程序跳过。
5. 查看成品：确认方形、无字、无意外水印、景物清楚、主色占显著面积、色块和留白接近样张。程序核对 API 返回 PNG 的真实尺寸；尺寸不符时说明实际返回值并核对供应商请求分组，不直接断定模型不支持该尺寸。用户提出修改时，修改单场景方案，在新任务目录生成一张新版，保留旧图。若用户明确需要 3508×3508，可加 `--upscale-3508`，这是标注为 `upscaled` 的插值放大导出。

## 凭据与失败边界

- 普通设置和生成记录不包含密钥。官方与第三方使用不同的凭据引用，业务程序分别从 `OPENAI_API_KEY` 与 `AICODEME_API_KEY` 读取；包装器仅注入所选服务的凭据，不回传 Agent。
- 参考图片、场景说明及颜色会发送给选定的图像接口。先用用户有权使用的图片。无网络、无安全凭据后端或模型不支持所选尺寸时停止并说明原因；无接口响应不能推断官方模型不可用。
- 生图失败或用户中断时，已完成的成品保留。只有图片和同名 JSON 都存在才视为完成；遇到一半结果先人工检查再继续。
- API 错误只显示状态码和请求 ID，不打印可能包含敏感信息的响应体。系统凭据存于当前系统用户，不自动同步或删除；本地凭据入口不提供对同用户任意程序的强隔离。

## 输出

向用户展示一张成品。任务目录内保留 `plan.json`、`output/poster-<id>.png` 和同名 JSON 生成记录。报告场景、成品路径、分辨率、是否为放大导出，以及未验证的印刷适用性。不要把运行产物写入本 Skill 或参考图目录。

## 资源导航

- [视觉规范](references/style.md)：规划场景与审核生成图时读取。
- [方案格式](references/plan-format.md)：创建 `plan.json` 时读取。
- `examples/`：16 张内置风景海报样张；构思和审稿时选择性查看，生成时选用最多三张作为风格参考。
- `scripts/poster.py`：确定性的配色、方案检查、API 调用、尺寸检查和导出。
- `scripts/provider_route.py`：官方优先选路；不确定或无响应时暂停。
- `scripts/credential-setup.ts`：对应服务的凭据页与模型测试入口。
- `scripts/credential-ui`：用户亲自填写密钥，业务程序按需读取。
