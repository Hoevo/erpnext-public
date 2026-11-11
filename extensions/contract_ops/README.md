# Contract Ops：原生合同扩展

基于 ERPNext 17 的 `Contract`，通过独立 Frappe App 扩展销售合同票款业务，不修改原生 Contract 控制器、字段、模板、签署和履约逻辑。源码位于本仓库 `extensions\contract_ops`，安装时作为独立应用放到 Bench 的 apps 目录。

业务人员操作请阅读[同事试用操作指南](docs/同事试用操作指南.md)。指南直接适用于17新环境，使用管理员提供的地址和个人账号，按实际合同、税票及到账依据录入。

## 与 0.6.0 的区别

| 方面 | 0.6.0 | 0.7.0 开发版 |
|---|---|---|
| 合同主档 | 自建 Sales Contract | 原生 Contract，Customer 类型且启用销售票款管理 |
| 客户、期限、修订 | 自建字段 | 复用 party_name、start_date、end_date、amended_from |
| 正文、模板、签署、履约 | 未实现同等能力 | 原生 Contract 提供，扩展 hooks 不替换其控制器 |
| 金额、合同组、票款顺序 | App 自建 | 保留为 Contract 的附加字段 |
| 税票、期次、款项分配 | 独立业务单据 | 保留原单据与规则，合同链接目标改为 Contract |
| 续签周期与提醒 | App 自建 | 保留周期、历史、去重及负责人规则 |
| 收入项目与来源 | App 自建 | 保留来源，年度合同链接指向 Contract |
| 应收与收款记账 | 标准 Sales Invoice / Payment Entry | 继续使用标准财务单据，不新增第二套记账 |

普通客户合同、供应商和员工合同不必启用销售票款管理，不要求填写本扩展的金额与续签设置。原生合同列表增加“新增销售合同”入口，已有状态指示器保留。

## 迁移原则

只在恢复备份的隔离站点执行首次迁移。`after_migrate` 在元数据同步后运行幂等迁移：

- 原编号、合同金额、期限及业务字段保留；customer 映射为原生 party_name。
- 原表作为只读档案保留；每份原生合同保留完整来源行、来源 SHA256、操作版本和身份映射。
- 原日期不推断签署时间；旧合同没有签署人或签名，因此原生签署状态保持未确认。旧签订日期只作为来源值展示。正文未转录时明确提示查阅原件，不把备注冒充合同条款。
- 已提交、取消状态保留，不触发一次新的签署或财务提交；不生成票款、计划、通知或新的会计分录。
- 合同关联保持原编号，附件、评论、版本及待办的动态关联改指向原生 Contract。
- 任一编号冲突或来源校验不一致会阻止迁移；不覆盖已有原生合同。正式站点切换前必须备份并执行恢复与迁移核对。

已提交仅表示单据流程状态，不代替原生的已签署状态。后补签署信息必须有实际依据，禁止为迁移补造签署人或时间。原生 Template 可帮助起草未来合同，不能回写历史合同原件。

## 验证

在仓库根目录执行离线检查：

```powershell
python extensions\contract_ops\tools\check_extension.py
python -m unittest discover -s extensions\contract_ops\offline_tests -v
```

隔离环境使用 `infra\test.compose.yaml`，密码文件保存在 Git 忽略的本地目录，所有数据库目录使用 OPENCLAW_DB_PATH。端口为临时验收端口 8261，数据库、站点及 Redis 与 8260 的 16 版分开。测试完成应停止临时服务。

`infra\initialize.sh` 从已提供的恢复文件创建隔离站点，并按 before → migrate → after 顺序核对；拒绝覆盖已有站点。恢复文件与站点目录属于本地私有数据，不提交代码。初始化时需将 sites、logs 和 restore 目录授予镜像中的 frappe 用户（UID 1000）访问权限。17 版独立 realtime 服务需要 common config 的 webserver_host=backend、webserver_port=8000；Nginx 模板保留真实浏览器 Origin，让原生的同源检查正常工作。

准备环境时同时检查 Windows 宿主机与 WSL 内部的可用容量。开发镜像和构建缓存会占用数 GB，WSL 内显示的大容量不代表宿主机实际可用空间。

在恢复了 0.6.0 业务备份的隔离站点，先运行 `contract_ops.tests.preservation.snapshot` 的 before 阶段，再迁移，随后运行 after 阶段；比较旧字段、业务表行数及逐合同映射。该检查必须在新增测试数据之前执行。

实际站点回归入口：`contract_ops.tests.test_native_contract.run`、`test_planning.run`、`test_two_flows.run`、`test_merged_vat.run`、`test_renewal.run` 和 `test_renewal_permissions.run`。这些测试会写入、提交测试数据，只允许在约定的隔离站点执行。离线测试不能替代这些数据库及网页验收，也不能证明所有 ERPNext 17 模块已完成升级验收。

## 本次与旧版对比结果（2026-11-10）

- ERPNext 原生 Contract 与 Contract Template 源码保持不变；金额、FIFO、工作日和续期日期的三份纯规则文件与 0.6.0 的 SHA256 一致。
- 从 16 版备份恢复的 41 份合同按原编号映射到 Contract。16 张核对表的所有原字段与行数保持；62 张应收、42 张税票、49 笔收款、295 条总账没有新增或改写。
- 旧表、逐行来源快照、原件附件和 40 条收入项目合同关系保留；来源中的旧归档标记也保留。旧编号规则保存在来源快照，新合同使用原生 CON 编号。
- 16 项离线检查、160 项真实数据库检查和 6 组桌面／手机网页流程通过。签署保护、模板、履约进度与不同角色的权限均有实际回归。
- 原始签署人及签名缺失的旧合同保持未确认，不自动勾选“已签”。正式 16 版站点未执行合同迁移；本次只验证隔离副本，不代表全部 ERPNext 模块已完成生产升级验收。
