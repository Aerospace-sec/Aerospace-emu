# 航空协议安全锦标赛

团队项目：航空协议安全分析、风险链路建模、缓解方案和本地数字仿真验证。

本仓库服务于“航空协议安全锦标赛”初赛。题目要求覆盖三类工作：协议/接口脆弱性分析、跨域跨协议风险链路、脆弱性缓解与全生命周期安全管控。当前仓库优先使用合成数据、本地模型和可复现实验；不连接真实航空器、机场生产网、空管网、公共频谱或未经授权的真实设备。

## 题目映射

| 比赛方向 | 当前仓库内容 | 证据入口 |
| --- | --- | --- |
| 类型一：ARINC 429 | 32 位字、奇偶校验、注入/重放/篡改/洪泛、受控网关 | `sim/arinc429_lab.py`、`report/arinc429_security_report.md` |
| 类型一：RS-422/RS-232 | 合成串行帧、CRC、认证、新鲜度、维护边界和速率失配 | `sim/serial_lab.py`、`report/serial_interface_security_report.md` |
| 类型一：5G-ATG | UE、ATG gNB、专属核心网、N3/N6、OAM和跨域边界 | `sim/atg5g_lab.py`、`report/5g_atg_security_report.md` |
| 类型二：地面网络接入型链路 | 地面网络 → 5GC/OAM → N6 → 串行/A429 → 航电消费者 | `report/chain3_ground_network_attack_chain.md` |
| 类型三：缓解与管控 | 受控入口、来源认证、新鲜度、语义策略、限速、隔离和证据追溯 | `sim/`、`report/`、`sim/twin/chain.py` |
| 平台化验证 | 设备注册、拓扑、状态过期、统一时钟、事件因果链和证据摘要 | `config/twin_devices.json`、`web/`、`sim/twin/` |

题目文件的正式副本不提交到仓库；以比赛组委会发布版本为准。新增题目必须先建立题目编号、责任人、分析报告、实验入口、证据文件和测试，再合并到 `main`。

## 快速开始

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip pytest
python -m pytest -q
```

运行已有实验：

```bash
python scripts/run_atg5g_experiments.py
python scripts/run_serial_experiments.py
python scripts/run_experiments.py
python scripts/run_virtual_hardware_lab.py --engine kinematic --attack none
python scripts/run_cross_model_chain.py --scenario normal
```

启动本地设备孪生浏览器：

```bash
python scripts/run_twin_server.py \
  --database artifacts/twin-runtime/twin.sqlite3 \
  --port 8765
```

打开 `http://127.0.0.1:8765/`。只读 API：

```text
GET /api/health
GET /api/devices
GET /api/topology
GET /api/events?after=0
GET /api/chain?scenario=normal
```

## 统一事件链和证据

`sim/twin/chain.py` 将下列语义阶段关联为一条因果链：


```text
5G UE → N6 → 跨域网关 → RS-422 串行桥 → ARINC 429 → 虚拟 LRU → PX4 虚拟 HIL
```

每个事件必须包含：

- `event_id`、`sequence`、统一 `timestamp_ms`；
- `source_device`、`target_device`、`parent_event_id`；
- `status` 和结构化 `payload`；
- 对应的 `EvidenceRecord`、模型引用和 SHA-256 摘要。

负向场景在跨域网关拒绝后必须阻断下游事件，并保持 `physical_output=false`。所有当前证据标记为 `virtual_model`，不得表述为真实飞机已被攻破或真实飞行后果。

## 一键运行全部仿真

使用统一编排器运行 ARINC 429、RS-232/RS-422、5G-ATG、民航平台、虚拟硬件、跨模型事件链和报告渲染：

```bash
python3 scripts/run_all_simulations.py --profile quick
```

`quick` 使用本地运动学模型和 10 秒民航平台仿真，适合日常开发；需要完整 180 秒民航平台时使用：

```bash
python3 scripts/run_all_simulations.py --profile full
```

每个阶段的 stdout/stderr 和总清单写入 `artifacts/one-click/manifest.json`。清单记录命令、返回码、耗时和失败阶段；任一阶段失败时编排器返回非零码，避免把部分结果误报为完整仿真。可先查看执行计划：

```bash
python3 scripts/run_all_simulations.py --profile quick --dry-run
```

全部模型仍是本地、确定性、simulation-only 实验，不打开真实网络、串口、无线设备或航空器接口。

## 团队 Git 管理规则

### 分支

- `main`：可提交、可复现、可交付版本；禁止直接推送。
- `feature/<题目编号>-<短名>`：新题目、模型和功能。
- `fix/<短名>`：缺陷修复。
- `report/<题目编号>-<短名>`：报告、证据和可视化更新。
- `chore/<短名>`：CI、依赖、工程维护。

分支从最新 `main` 创建。一个分支只解决一个可审查主题；不要把个人环境、临时数据库、密钥、真实设备凭据或无关格式化混入提交。

### 提交

采用 Conventional Commits：

```text
feat(arinc429): add freshness admission evidence
fix(twin): mark expired observation as stale
report(q2): add replay impact analysis
ci: run cross-model regression
chore: update experiment metadata
```

规则：

1. 标题使用英文动词开头，最多 72 个字符；
2. 正文说明问题、方案、验证命令和仿真边界；
3. 一个提交保持一个逻辑变更；
4. 不提交 `needed.txt`、`.venv/`、缓存、SQLite 运行库、日志、密钥和真实设备数据；
5. 实验结果必须可由脚本重生成，报告必须链接结构化证据；
6. 不重写共享 `main` 历史，不使用未经团队同意的强制推送。

### Pull Request

所有变更通过 PR 合并：

1. PR 标题符合 Conventional Commits；
2. 描述题目编号、变更范围、威胁模型、仿真/真实边界和验证命令；
3. 至少一名非作者成员审查；涉及安全结论、跨域边界或题目报告时至少两名审查者；
4. CI 全部通过，作者解决所有阻塞性评论；
5. 使用 Squash merge，合并后删除短期分支；
6. 只有维护者可以合并 `main`。

新增队员先阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)，再从 issue 或题目任务清单领取工作。题目责任分配、审查人和截止时间记录在 issue/PR 中，不写入个人本地文件。

## CI/CD

工作流位于 `.github/workflows/ci.yml`，在 PR、`main` 推送和手动触发时运行：

- Python 3.11、3.12 测试矩阵；
- `python -m pytest -q`；
- `python scripts/run_cross_model_chain.py --scenario normal`；
- `python scripts/run_cross_model_chain.py --scenario injection`；
- `git diff --check`；
- `main` 成功后上传可复现的测试和链路证据包。

CI 不连接真实网络、设备、串口或无线接口。部署阶段只发布测试证据和报告归档，不自动向真实航空系统部署代码。任何未来部署目标必须另行经过团队批准、授权测试台架和安全评审。

## 完善版仿真方案

分层架构、统一仿真时钟、`MessageEnvelope`、协议扩展路线、故障目录、证据回放、题目覆盖矩阵和阶段验收门槛见 [`docs/simulation_architecture.md`](docs/simulation_architecture.md)。当前跨模型链已经逐跳携带语义消息摘要和策略决策；下一步按 AFDX、ARINC 825、维护以太网、USB、ADS-B/ACARS/GPS/Wi-Fi 顺序扩展独立协议适配器。

AFDX 第一版虚拟链路模型已加入：`sim/afdx_lab.py`，覆盖 VL、BAG、帧长度、A/B 冗余副本、重复去重和配置/队列负向场景。一键运行会自动执行 `scripts/run_afdx_experiments.py`。

ARINC 825 第一版 CAN 模型已加入：`sim/arinc825_lab.py`，覆盖节点授权、标准仲裁、载荷长度、重放、错误被动化和总线负载。一键运行会自动执行 `scripts/run_arinc825_experiments.py`。

## 仿真和安全边界

- ARINC 429 奇偶校验、CRC、TEID/QFI、认证成功均不等于来源授权或业务授权；
- 仿真状态不等于真实设备状态，合成 ICD 不代表任何具体机型；
- 浏览器仅读取后端状态，不直接连接真实设备；
- 真实设备接入若获批准，只能通过后端、白名单、审计、配置快照和隔离台架，默认只读；
- 禁止在本仓库实现面向真实航空系统的任意原始报文发送、公共频谱操作、横向移动或攻击按钮；
- 报告应同时记录攻击前提、设备/技术/时间成本、检测概率、影响和防御/恢复措施。

## 目录

```text
config/       设备孪生注册清单
sim/          协议、飞行模型、虚拟硬件和孪生核心
web/          本地只读孪生页面与 API
scripts/      实验、验证、渲染和服务入口
report/       题目分析、攻击链和证据追溯
artifacts/    可重生成的实验结果与报告渲染产物
tests/        协议、平台、事件链和 API 行为测试
.github/      CI/CD、PR 模板和代码所有者规则
```

## 许可与资料

仅使用团队有权使用的资料、合成数据和授权测试环境。厂商手册、真实 ICD、飞机网络图和比赛内部材料不得在未经许可的情况下提交到公开仓库。