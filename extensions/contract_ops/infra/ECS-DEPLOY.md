# ECS 生产部署（erpnext.hoevo.com）

生产环境：阿里云 ECS（`ssh ecs`，Debian 11）。frappe_docker compose（`/data/erpnext/repo`）运行自建镜像 `erpnext-hoevo:<tag>`，站点 erpnext.hoevo.com（ERPNext v17 + contract_ops）。2026-11-11 由 v16 stock 镜像升级切换；2026-11-11 起部署由 GitHub Actions 自动执行（`.github/workflows/ecs-deploy.yml`，模式参考 Hoevo/qrcode_backend 的 ci.yml deploy-prod：self-hosted runner ssh 到 ecs 跑 update.sh）。

## 发布步骤

前提：develop 已在本地 v17-live 环境或 `tools/verify_*.py` 验收通过；且 `ecs-deploy.yml` 已合入 develop（tag push 触发的 workflow 取 tag commit 树里的文件版本，tag commit 上没有它就不会触发）。

1. **打 tag**（本地仓库，指向要发布的 commit）：

   ```bash
   git tag -a ecs-v3 <commit> -m "发布说明"
   ```

   tag 严格递增 `ecs-v<N>`，一个 tag 一次更新窗口（workflow 的准入 job 会拒绝非递增 tag 与不在 develop 历史内的 tag）。
   完成判据：`git rev-parse ecs-v<N>^{commit}` 返回预期 commit。

2. **push tag → 自动部署**（约 20–30 分钟停机）：

   ```bash
   git push origin ecs-v<N>
   ```

   push 后 GitHub Actions「ECS Deploy」自动执行：准入校验 → hoevo-ci self-hosted runner `ssh ecs` 运行
   `bash /data/erpnext/update.sh <tag>`，日志流式回传 Actions run 页面。
   失败语义不变（update.sh 已固化，见下节）。
   修复后重跑：Actions 页对该 run Re-run，或 workflow_dispatch 指定同 tag（dispatch 仅允许最新 tag，拒绝向更旧版本回滚镜像；重跑会覆盖 `pre-<tag>-db.tgz` 快照，见失败语义）。
   dispatch 还支持 dry_run：只做准入校验与 ssh 连通性检查。避免在 push run 进行中 dispatch 同 tag（concurrency 只排队不取消，会串行开出第二个停机窗口）。

   SSH agent 或网络不稳时的备用通道（已验证可用）：

   ```bash
   git -c credential.helper='!gh auth git-credential' push https://github.com/Hoevo/erpnext.git ecs-v<N>
   ```

3. **人工门禁**（自动门禁全过后）：
   - 登录 https://erpnext.hoevo.com —— v17 迁移使旧会话失效，需重新登录属预期
   - 侧栏出现「合同与票款」工作台
   - realtime 正常（devtools 无 socket 401）

   完成判据：三项全过。

## 失败语义（update.sh 已固化）

- migrate 失败 → 服务保持停机，人工决策：修复后重跑，或恢复本次 db 快照。绝不自动回滚镜像——旧代码跑已迁移库是不受支持状态。
- `migrate_legacy_contracts` 的 legacy 指纹 preflight 抛错 = 旧合同数据被改动过：找数据负责人核实，重试无效。
- **Actions job 超时/被取消（ssh 无 PTY）**：远端 update.sh 收到断连后命运不定（半途中断或短暂孤儿），状态视同 migrate 失败处理——登服务器确认容器与 update.sh 进程状态后人工决策。
- **重跑同 tag 会覆盖 `pre-<tag>-db.tgz`**：第二次快照的库已是上一轮迁移后状态，会污染迁移前回滚锚点。若第一次快照仍需保留，重跑前先在服务器上将其改名移走。

## 回滚

- **migrate 前失败**（构建/门禁挂）：`.env` 删 `CUSTOM_IMAGE`/`CUSTOM_TAG`/`PULL_POLICY` 三行 → `compose.sh up -d`。库未动，秒级恢复。
- **migrate 后回滚**：`compose.sh stop`（含 db）→ 清空 `/data/erpnext/data/db` → 解开 `/data/erpnext/backups/pre-<tag>-db.tgz`（root 属主，需 sudo）→ 还原 `pre-<tag>-site-config.json` → `.env` 还原 → `up -d`。site_config.json 必须随库一起回滚（install/update 会把 app 写进它的 installed_apps）。

## 维护注意

- 部署通道依赖 Hoevo org Default runner 组的 `self-hosted, ci` runner（hoevo-ci-3/5/6/7、attach-ci 等，与 qrcode_backend deploy 共用）：runner 侧 `ssh ecs`（ecs-user + `~/.ssh/new-office.pem`）→ 服务器执行 update.sh 需要的 docker 组成员与 NOPASSWD sudo（`sudo -n tar` 快照）均为现有 ecs-user 配置，无新增服务器侧改动。runner 或 ssh 通道异常时可回退手动方式：`ssh ecs` 后 `bash /data/erpnext/update.sh <tag>`。
- 飞书通知为可选增强：在 Hoevo/erpnext 仓库 secrets 配置 `FEISHU_BOT_WEBHOOK`（值同 qrcode_backend）即启用，未配置时 workflow 仅告警跳过。（2026-11-11 已配置）
- 可选强化：workflow 使用 `environment: prod`，在 repo Settings → Environments 给 prod 配 required reviewers 即可获得部署前人工审批，无需改代码。

- 基础镜像 digest 冻结在本目录 `Dockerfile`（当前 `sha256:26f105ae...`，与本地验收镜像同基线）。fork 跟随 upstream develop：upstream 前端（frappe/erpnext 的 JS/CSS）有变更时，打 tag 前先 bump digest 并本地重建验证，否则镜像内资产停在旧时点。
- 服务器直连 GitHub 不通；`/data/erpnext/src` 已配 ghfast.top 代理（仓库局部 `git config insteadOf`）。tag 完整性由内容寻址保证：fetch 后 `git rev-parse <tag>^{commit}` 与发布记录比对。
- db 数据目录属主 uid 999，快照必须 `sudo tar`（update.sh 已固化）。
- 系统盘仅剩 ~9G：每个新 tag 约 +2G 构建层，定期清旧 tag 镜像；`frappe/erpnext:v16.54.0` 保留作回滚依赖。
- 站点库当前为空（业务数据在本地 v16/v17-live）；数据迁移到 ECS 是独立任务，届时才会触发 legacy 合同迁移与 41 份映射验收。
