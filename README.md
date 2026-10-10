# Skill Publisher

把一个 Agent Skill 打包成GitHub、Gitee、WorkBuddy、千问的发行资料，并支持腾讯 SkillHub 社区提交。GitHub、Gitee 支持通过真实 Release 接口上传与核对附件。WorkBuddy、千问支持生成技能包、资料与辅助提交步骤，审核结果需人工确认。SkillHub 社区有独立的自动提交 API，返回 pending_review 时记为 submitted，不能当作 WorkBuddy 官方市场已上架。

项目独立运行，不改原技能，不推原项目的 Git 历史。作品归档永远排除。Python 3.9+，基础使用只需要标准库；复杂 YAML frontmatter 使用本机可选的 PyYAML 校验，没有它时只接受平面标量及 JSON 字段。GitHub 发布需要安装 gh。

## 使用

```bash
python3 scripts/publish.py init --config publish.json
# 编辑 source、metadata、license、仓库配置
python3 scripts/publish.py build --config publish.json
python3 scripts/publish.py publish --release dist/your-skill/0.1.0
# 用户授权且源技能许可证明确后执行发布
python3 scripts/publish.py publish --release dist/your-skill/0.1.0 --execute
python3 scripts/publish.py status --release dist/your-skill/0.1.0
```

配置中的路径相对于配置文件。`examples/vibe-chinese-ci.json` 是可移植的词作技能示例，source 需要编辑。`publish.local.json` 是本机私有配置，不纳入 Git。公开示例保留草稿配置，使用时应为自己的技能明确指定许可证。Skill Publisher 自身的 MIT 许可证不替代源技能的许可证。

发行目录包含通用技能 ZIP、各平台技能 ZIP、白名单源码 ZIP、市场提交资料、校验清单和平台状态。同版本内容必须一致。许可证补齐后应增加版本并重建，不能直接编辑已经生成的 manifest。发布前下载远端已有附件核对，失败后可重试补齐，不覆盖不同内容的同名附件。发布不读取源目录，已经打包的版本可单独搬运。不会自动同步仓库默认分支中的源码。

```bash
python3 scripts/publish.py publish --release dist/your-skill/0.1.0 --platform github --execute
python3 scripts/publish.py record --release dist/your-skill/0.1.0 --platform workbuddy --state submitted --url https://open.workbuddy.cn/ --evidence '控制台显示审核已提交，2026-10-10'
```

环境认证使用 `gh auth login` / `GH_TOKEN`、`GITEE_TOKEN`、`SKILLHUB_TOKEN`。可选私有文件 `~/.config/skill-publisher/credentials.json` 仅读取 GH_TOKEN、GITHUB_TOKEN、GITEE_TOKEN、SKILLHUB_TOKEN、SKILLHUB_API_KEY，文件权限必须为 0600，环境变量优先。不要把凭证写进配置。WorkBuddy 和千问仍需登录官方门户完成提交；本工具不会把 prepared 冒充 published。

## 作为 Skill 安装

把这个项目复制或链接到你的 Agent Skills 发现目录，例如 `~/.codex/skills/skill-publisher`。也可直接让助手读取本项目的 SKILL.md。

自然语言请求示例：“用 skill-publisher 为我的技能准备 GitHub、Gitee、WorkBuddy 和千问发行包，先预览，排除作品。”或：“把准备好的 0.2.0 发行版发布到我配置的 GitHub 和 Gitee 仓库，并辅助提交两个技能市场。”

配置详情见 [references/config.md](references/config.md)，平台能力与官方资料见 [references/platforms.md](references/platforms.md)。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖 CLI 的打包、硬排除、确定性、越界与符号链接、不可变发行版、源删除后发布预览、资产篡改、独立渠道失败继续执行、模拟远端发布重试及 SkillHub multipart 提交与不确定状态守卫。模拟测试不会向真实平台发请求。首次正式上架仍需验证平台账号权限与实际市场规则。
