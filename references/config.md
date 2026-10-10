# 配置与产物

Python 3.9+，基础打包和接口只使用标准库。配置是 JSON。所有 source 和 output 路径相对于配置文件目录解析。若有 PyYAML，会校验整个 YAML frontmatter；没有它时只接受平面字段，值为标量或合法 JSON。缩进、块标量等复杂 YAML 需要安装 PyYAML，不能跳过校验。

| 字段 | 含义 |
| --- | --- |
| source | 原技能目录，可相对或绝对路径；只有 build 读取它 |
| output | 发行目录，不能位于 source 中 |
| metadata | name、version、description、description_zh、description_en、author 必填；name 与 SKILL.md 保持一致，version 使用 SemVer |
| include | 公共文件白名单，默认 SKILL.md、scripts/**、references/**、README.md、LICENSE |
| exclude | 额外排除 glob，例如 references/process-log.md |
| license | 白名单中的非空许可证文件相对路径；null 可以生成草稿，不能 execute |
| platforms | github、gitee、qianwen、skillhub 的对象，可设置 enabled=false |
| repository | GitHub/Gitee 的 owner/repo；必须由用户指定 |
| SkillHub 字段 | slug、displayName、summary、license、homepage、tags、changelog；前三项可由 metadata 推导，license 为用户明确的许可证标识 |
| target_commitish | 发版标签对应已有仓库中的真实 ref；省略时使用默认分支 |

`**/` 可以匹配零层目录。任意层级的 archives/archive、.git、.env*、credentials、secrets、私钥扩展名、依赖与缓存目录始终排除。符号链接被拒绝。白名单并不能自动判断普通文件中的敏感内容，请在发布前检查 manifest 和 ZIP。不要把 token、个人路径或私密说明放进 metadata。

产物位于 `output/name/version/`。`skill.zip`、`qianwen.zip`、`source.zip` 的根目录是 SKILL.md。每个平台的 submission JSON 包含公开字段、入口和提交步骤。千问 ZIP 使用保守的 10 MiB 上限，其来源是自定义 Skill 上传限制，市场实际限制仍需在提交时核实。

ZIP 固定时间戳和排序。同版本不同内容被拒绝。manifest 记录源文件与全部发行资产的 SHA-256。公开 manifest 不保存 source/output 或认证信息；status 保存各平台的操作结果。publish 只从发行目录读取并验证 manifest 与资产。

状态包括 prepared、submitting、submitted、published、failed、uncertain。千问的 submitted/published 来自人工确认记录；SkillHub 来自 API 回执。SkillHub POST 前写 submitting，响应不确定则记 uncertain，保留状态并禁止自动重复提交同版本；需要在门户核实后 record。写状态使用排他锁和原子替换。异常退出留下锁时，先核实锁中的进程已结束，再删除锁文件。校验值用于检测意外修改，不是数字签名；将发行目录保存在可信位置。

SkillHub 启用时增加 skillhub.zip 与 skillhub-submission.json。其包根部 SKILL.md 包含 slug、displayName、version、summary、license，并保留通用 name、description。license 为空只能准备草稿。

旧配置中的 workbuddy 字段不再支持，即使 enabled=false 也会被拒绝。使用前删除该字段。历史发行目录保持原样；重新构建须增加版本，publish 不接受已移除的平台。
