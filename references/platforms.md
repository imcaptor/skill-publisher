# 平台发布

## GitHub

安装 GitHub CLI，执行 `gh auth login`，或者在环境设置 GH_TOKEN。配置 `repository` 为已存在的仓库。用户必须有创建 Release、tag 和上传附件的权限。

CLI 查询远端所有 Release，找到版本标签后逐个下载同名资产并核对 SHA-256，缺失资产才上传。已经成功的本地状态不能跳过远端核对。创建 Release 使用 `v{version}` 标签；可配置 target_commitish，否则 gh 使用仓库默认分支。命令不创建仓库、不推源码，不触碰源项目 Git 历史。`source.zip` 是白名单源码分发产物。GitHub 自动生成的仓库源码归档来自远端 tag，可能与 source.zip 不同。

官方文档：[gh release](https://cli.github.com/manual/gh_release)、[Release API](https://docs.github.com/en/rest/releases/releases)。

## Gitee

配置已有仓库的 owner/repo，把个人访问令牌放入 `GITEE_TOKEN`。使用 `/api/v5/repos/{owner}/{repo}/releases` 查询与创建 Release，使用 `/{release.id}/attach_files` 上传 multipart `file` 与 `access_token`。缺省 target_commitish 从仓库 API 的 default_branch 读取。先检查远端资产，上传后再次下载核对；无法验证或同名内容不同即失败，不删除已有资产。

命令不推仓库源码。`source.zip` 是经过白名单的源码产物。若需要仓库浏览完整源码，应另建清洁仓库再导入白名单内容，不推原项目历史。

官方参考：[Gitee release CLI 源码](https://gitee.com/gitee-frontend/gitee-release-cli/blob/master/src/index.js)、[Gitee API v5](https://gitee.com/api/v5/swagger)。自动适配器有模拟测试，仍需首次实际发布验证账号权限及 API 行为。

## WorkBuddy

入口：[开放平台](https://open.workbuddy.cn/)，[发布文档](https://open.workbuddy.cn/docs)。使用 workbuddy.zip 和 workbuddy-submission.json，包里包含技能名称目录，SKILL.md 增加 name、description、description_zh、description_en、version、author。

登录、入驻和认证后新建技能，填写资料，上传、测试、提交审核。图标等平台需要的资料由实际提交页决定。本工具准备包和文本，不虚构市场发布 API。可用浏览器协助填写；账号实名与验证码需要操作者完成。确认审核提交后 record submitted，确认公开链接后 record published。

## 千问

入口：[千问技能广场](https://www.qianwenai.com/hub/skills)，[技能市场发布](https://platform.qianwenai.com/docs/agent-infra/skills/publish)。使用 qianwen.zip 和 qianwen-submission.json，根目录包含 SKILL.md。

在技能市场上传 ZIP，填写资料，测试并提交审核。自定义 Skill API 是托管环境安装能力，不等于市场上架 API；本项目提供市场辅助流程。办公环境个人导入也不等于公开上架。记录状态遵循与 WorkBuddy 相同的证据要求。

平台文档与页面可能调整，首次提交前核实官方当前字段及大小限制。所有执行错误均隐藏请求响应与凭证；先独立检查账号认证状态，再重试。

## 腾讯 SkillHub 社区

WorkBuddy 已接入 SkillHub 市场，支持浏览、搜索和安装社区技能，见[官方更新日志](https://www.workbuddy.cn/docs/workbuddy/Changelog)。SkillHub 社区发布与 WorkBuddy 开放平台的独立审核入口分别记录，社区上架不代表已经通过开放平台审核。入口：[SkillHub](https://skillhub.cn/)，[官方 CLI 发布教程](https://skillhub.cn/tutorials#publish-via-cli)，[Agent 发布规范](https://skillhub.cn/ai/release.md)。接口契约也核对了官方安装包中的 CLI 源码。

设置 SKILLHUB_TOKEN 或 SKILLHUB_API_KEY，值使用 skh_ 前缀。先 GET `https://api.skillhub.cn/api/v1/auth/me` 验证账号，再 POST `/api/v1/community/skills/publish`。Authorization 使用 Bearer。请求包含 payload JSON，以及多个重复的 files 字段，每个文件的 filename 是相对路径；不是直接上传一个 ZIP。

配置中的 SkillHub license 必须由用户明确指定，且源技能同时具有已配置的许可证文件。上传仅从校验后的 skillhub.zip 读取，不读源项目。API 回执 skillId、status、publicUrl 保存到 status。pending_review 记录为 submitted；只有明确的 published 回执才记录为 published。

此 API 没有本项目已验证可用的同版本远端核对接口。因此 POST 前持久化 submitting，网络失败或不完整响应记 uncertain，禁止盲目重试。同版本已有 receipt 也不会再提交。到门户核实实际结果后可用 record 写 submitted/published 证据；若需要更改内容，增加版本重新构建。
