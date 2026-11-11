# AGENTS.md

本仓库是 frappe/erpnext 的 fork（Hoevo/erpnext），develop 跟随 upstream；相对 upstream 的全部自定义在 `extensions/contract_ops/`，erpnext 本体保持零改动。

**部署生产**：push `ecs-v<N>` tag 由 GitHub Actions 自动部署 erpnext.hoevo.com（`.github/workflows/ecs-deploy.yml`）；流程、失败语义与回滚手册见 `extensions/contract_ops/infra/ECS-DEPLOY.md`。
