# AGENTS.md

本仓库是 frappe/erpnext 的 fork（Hoevo/erpnext），develop 跟随 upstream；相对 upstream 的全部自定义在 `extensions/contract_ops/`，erpnext 本体保持零改动。

**部署生产**：打 `ecs-v<N>` tag 更新 erpnext.hoevo.com；流程、失败语义与回滚手册见 `extensions/contract_ops/infra/ECS-DEPLOY.md`。
