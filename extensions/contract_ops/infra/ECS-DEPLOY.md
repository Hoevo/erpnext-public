# ECS 生产部署（erpnext.hoevo.com）

生产环境：阿里云 ECS（`ssh ecs`，Debian 11）。frappe_docker compose（`/data/erpnext/repo`）运行自建镜像 `erpnext-hoevo:<tag>`，站点 erpnext.hoevo.com（ERPNext v17 + contract_ops）。2026-11-11 由 v16 stock 镜像升级切换，此后仅通过本文的 tag 流程更新。

## 发布步骤

前提：develop 已在本地 v17-live 环境或 `tools/verify_*.py` 验收通过。

1. **打 tag**（本地仓库，指向要发布的 commit）：

   ```bash
   git tag -a ecs-v3 <commit> -m "发布说明"
   ```

   tag 严格递增 `ecs-v<N>`，一个 tag 一次更新窗口。
   完成判据：`git rev-parse ecs-v<N>^{commit}` 返回预期 commit。

2. **push tag**：

   ```bash
   git push origin ecs-v<N>
   ```

   SSH agent 或网络不稳时的备用通道（已验证可用）：

   ```bash
   git -c credential.helper='!gh auth git-credential' push https://github.com/Hoevo/erpnext.git ecs-v<N>
   ```

   完成判据：`git ls-remote --tags origin ecs-v<N>` 可见。

3. **服务器执行**（约 20–30 分钟停机）：

   ```bash
   ssh ecs
   bash /data/erpnext/update.sh ecs-v<N>
   ```

   脚本内部序列（fetch → 构建 → 镜像自检 → 停栈 → db 冷快照 → migrate → 起栈 → 自动门禁）以 `/data/erpnext/update.sh` 为准，此处不复述。

4. **人工门禁**（自动门禁全过后）：
   - 登录 https://erpnext.hoevo.com —— v17 迁移使旧会话失效，需重新登录属预期
   - 侧栏出现「合同与票款」工作台
   - realtime 正常（devtools 无 socket 401）

   完成判据：三项全过。

## 失败语义（update.sh 已固化）

- migrate 失败 → 服务保持停机，人工决策：修复后重跑，或恢复本次 db 快照。绝不自动回滚镜像——旧代码跑已迁移库是不受支持状态。
- `migrate_legacy_contracts` 的 legacy 指纹 preflight 抛错 = 旧合同数据被改动过：找数据负责人核实，重试无效。

## 回滚

- **migrate 前失败**（构建/门禁挂）：`.env` 删 `CUSTOM_IMAGE`/`CUSTOM_TAG`/`PULL_POLICY` 三行 → `compose.sh up -d`。库未动，秒级恢复。
- **migrate 后回滚**：`compose.sh stop`（含 db）→ 清空 `/data/erpnext/data/db` → 解开 `/data/erpnext/backups/pre-<tag>-db.tgz`（root 属主，需 sudo）→ 还原 `pre-<tag>-site-config.json` → `.env` 还原 → `up -d`。site_config.json 必须随库一起回滚（install/update 会把 app 写进它的 installed_apps）。

## 维护注意

- 基础镜像 digest 冻结在本目录 `Dockerfile`（当前 `sha256:26f105ae...`，与本地验收镜像同基线）。fork 跟随 upstream develop：upstream 前端（frappe/erpnext 的 JS/CSS）有变更时，打 tag 前先 bump digest 并本地重建验证，否则镜像内资产停在旧时点。
- 服务器直连 GitHub 不通；`/data/erpnext/src` 已配 ghfast.top 代理（仓库局部 `git config insteadOf`）。tag 完整性由内容寻址保证：fetch 后 `git rev-parse <tag>^{commit}` 与发布记录比对。
- db 数据目录属主 uid 999，快照必须 `sudo tar`（update.sh 已固化）。
- 系统盘仅剩 ~9G：每个新 tag 约 +2G 构建层，定期清旧 tag 镜像；`frappe/erpnext:v16.54.0` 保留作回滚依赖。
- 站点库当前为空（业务数据在本地 v16/v17-live）；数据迁移到 ECS 是独立任务，届时才会触发 legacy 合同迁移与 41 份映射验收。
