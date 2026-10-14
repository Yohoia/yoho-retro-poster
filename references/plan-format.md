# 单场景方案格式

把方案保存为 UTF-8 JSON，`version` 为 `2`，`scene` 是一个对象。路径相对于 `plan.json` 所在目录；`references` 可省略，最多三张 PNG、JPG 或 WebP，单张不超过 20 MB。参考图会上传给选定服务的图像编辑接口，仅作为风格参照。

```json
{
  "version": 2,
  "main_color": "#D98278",
  "accents": ["#F4C7A5"],
  "brief": "安静的山地旅行氛围，没有文字",
  "references": [],
  "scene": {
    "id": "open-ridge",
    "title": "开阔山脊",
    "description": "缓缓起伏的山地与一间远处小屋",
    "composition": "低地平线，大面积天空，沿坡线引导视线",
    "palette_use": "主色用于近处山丘，浅色用于天空，深色用于小屋和坡面阴影"
  }
}
```

`id` 只能用小写字母、数字与短横线。`title` 是成品场景名；`description`、`composition`、`palette_use` 会进入提示词。示例不规定颜色和景物的固定对应关系。

实际生成默认从本 Skill 的 `examples/` 中选 1–3 张样张，将图片的实际绝对路径写入 `references`，也可使用相对于方案目录的有效路径。用户另提供参考图时优先使用用户图片。上面的空数组用于展示无参考图的格式；用户要求仅使用文字风格时可保留为空。

旧 `version: 1` 多场景方案需在新任务目录中改成 `version: 2`，将需要生成的一个场景写入 `scene` 并移除 `scenes`。程序拒绝多场景方案，不会自动批量生成。生成入口为 `generate <plan.json>`，无需 `--scene`。
