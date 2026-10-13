# J-codex-workflow 项目

产品规格见 `PRD.md`，接口与状态契约见 `docs/CONTRACT.md`。运行技能位于 `plugin/skills/codex-workflow/SKILL.md`。

源码在 `plugin/`。`scripts/package.py` 生成本机 `dist/marketplace/`，其中 MCP 启动路径按构建位置生成；移动工程后重新构建。修改包内引用时同时检查生成的安装包。

状态测试使用临时 SQLite 数据库或 `CODEX_WORKFLOW_DATA_DIR`，避免测试触及默认用户数据库。开发预览 `scripts/dev_host.py` 仅用于 UI 样例验证，不能作为 Codex 原生接入证据。

完整测试：`python3 -m unittest discover -s tests -p 'test_*.py' -v`。HTML 的脚本和样式有 CSP 哈希，改动后需要更新并验证哈希。
