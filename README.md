# MeoW Notify Skill

把 Codex 每轮完成通知推送到手机，可根据**本轮请求与最终回复**生成一句中文脱敏摘要。

这是可安装的 Codex skill，附带独立 Python 通知脚本，无第三方 Python 依赖。需要 Python 3.11+、MeoW 专属推送链接；摘要模式还需要已登录且支持所用参数的近期 Codex CLI。

## 安装 skill

克隆仓库，把 `skills/meow-notify` 整个文件夹复制到 `$CODEX_HOME/skills/`，未设置时为 `~/.codex/skills/`。重新打开 Codex 后，可以说：

> 使用 $meow-notify，配置每次对话完成后向我的手机推送真实内容的脱敏摘要。

也可直接运行脚本：

```bash
python skills/meow-notify/scripts/manage.py install --mode summary
python skills/meow-notify/scripts/manage.py test
python skills/meow-notify/scripts/manage.py status
```

安装时私密输入自己的 `https://api.chuckfang.com/…` 地址；也可通过 `MEOW_ENDPOINT` 或 `--endpoint-env NAME` 读取已设置的环境变量。不要将真实地址写入仓库或命令参数。

`--mode status` 仅发送固定完成状态，不做摘要。安装会保留原通知命令、备份配置，并使用独立的 `notifications/meow-notify-skill/` 运行目录。

## 行为与限制

- 摘要基于真实请求与最终回复，不以关键词模板冒充真实摘要。失败时明确提示“摘要暂不可用”。
- 摘要模式会额外调用 Codex，消耗额度，通常需要额外等待；最多两次生成，共享 120 秒超时。发送至 MeoW 的是通过校验的摘要。
- 在后台发送，事件去重，最多六次投递；失败后可手动 `retry`。服务端接收成功不等于手机已展示。
- 自动摘要会向 Codex 模型传入经过规则过滤的本轮文本。中文姓名等信息无法保证完全脱敏；高敏感场景请选择 `status` 模式。
- 不保存原始事件或完整对话。脱敏摘要队列和配置备份保存在本地，用户可自行清理。日志不包含推送地址或消息正文。
- 支持 Windows 与 POSIX 锁实现；本次实际验证平台为 Windows。没有开机后台服务；下次事件、安装或 `retry` 时恢复待发送队列。
- 当前只接入 `agent-turn-complete`，不包含审批提醒、任意作业监控或定时心跳。

```bash
python skills/meow-notify/scripts/manage.py retry
python skills/meow-notify/scripts/manage.py uninstall
python -m unittest discover -s tests -v
```

卸载仅恢复通知配置，保留本地备份和队列；已启动的投递进程可能继续完成现有任务。

## 接口依据

- [MeoW 官方 API 文档](https://www.chuckfang.com/MeoW/api_doc.html)：HTTPS JSON POST，检查响应 `status=200`。
- [OpenAI Docs：Advanced Configuration](https://learn.chatgpt.com/docs/config-file/config-advanced)：用户级通知命令与事件。

MIT License。此项目为独立集成，与 MeoW 或 OpenAI 无隶属关系。
