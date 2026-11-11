# 本地 ERPNext 17 新环境

2026-11-11 已从 16 版当天 12:54 的最新备份恢复到独立的 `v17-live` 数据目录，并升级到已验收的 17 开发版本。业务表及 41 份合同的映射核对通过。旧回归数据库保留在另一目录，不用于此环境。

本机访问：http://127.0.0.1:8261 。仅绑定本机地址；其他电脑不能直接使用这个地址。原 16 版保留在 8260。两套数据库独立，修改不会互相同步；同一笔真实业务应指定一个录入环境。

Windows 启动命令：

```powershell
wsl -d ERPNextDev -u root --exec bash /mnt/d/erpnext-v17/extensions/contract_ops/tools/live-stack.sh up -d --pull never
```

停止时将最后的 `up -d --pull never` 改为 `stop`。脚本读取 WSL 私有配置 `/var/lib/erpnext/v17-live/live.env`，数据库和附件位于 OPENCLAW_DB_PATH 对应的专用 WSL 磁盘。不得把环境文件、备份或附件提交到 Git。

服务包含后台、Nginx、Python realtime、worker、scheduler、MariaDB 和两套 Redis。所有服务均设置重启策略，但 Docker 和 ERPNextDev WSL 仍须先运行。使用已验收镜像，不在启动时拉取或构建。

恢复后沿用原有登录信息。新站点未启用测试模式；邮件发送暂时静默，启用实际通知前应由管理员核对邮件配置。不要在本环境运行写入回归数据的测试套件。

本次启动通过桌面和手机登录、合同详情、来源证据、实时连接检查；后台 doctor 确认一个 worker 在线。此范围不等同于 ERPNext 全部模块或财务科目配置已验收。

宿主机 D 盘容量偏紧，镜像构建前必须同时检查 Windows 和 WSL 空间，不能仅依据 WSL 的可用容量。
