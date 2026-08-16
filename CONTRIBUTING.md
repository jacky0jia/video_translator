# Contributing

感谢你帮助改进 Subtitle Translator。

## 开始之前

- 对缺陷或功能建议，请先创建 GitHub Issue，说明操作系统、复现步骤和预期行为。
- 不要提交视频、模型、生成文件、`config.yaml`、API Key 或包含本机路径的任务历史。
- 大功能请先讨论设计，避免实现方向与主路线冲突。

## 本地验证

提交 Pull Request 前请运行：

```powershell
python -m compileall -q app

cd app/frontend
npm ci
npm run build
cd ../..

python -c "from app.main import app; assert app.title"
```

如果修改了转录、翻译、配音或视频渲染流程，请在 PR 中说明使用的 provider、测试语言和产物类型。真实模型或 GPU 验证无法在 CI 中覆盖，相关修改需要提供人工验证结果。

## 代码与提交

- 保持后端 API、任务历史格式和前端调用的向后兼容，除非变更已经明确讨论。
- 新增行为应说明验证方法；维护者会在合并前运行内部自动化测试。
- 提交信息应简洁说明目的，例如 `fix: preserve translation on retry`。

贡献代码即表示你同意按仓库的 MIT License 提供该贡献。
