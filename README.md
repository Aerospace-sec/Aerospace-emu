# ARINC 429 总线脆弱性分析与仿真验证

本目录包含一套面向民用航空机载航电场景的本地逻辑仿真、JSBSim 飞行动力学适配器和中文分析报告。默认 ARINC 429 实验不连接真实设备；另提供 PX4 SITL + JSBSim 的回环软件联仿入口。测试标签 `0o203` 是合成标签，不代表任何具体机型的真实标签分配。

## 快速复现

```bash
python3 -m pytest -q
python3 scripts/run_experiments.py
python3 scripts/run_civil_platform.py --engine auto --attack injection
```

前两条命令会更新：

- `artifacts/results.json`：含环境信息、模型 SHA-256 和全部结构化结果；
- `artifacts/experiment_log.md`：便于归档的实验记录。

正式分析见 [`report/arinc429_security_report.md`](report/arinc429_security_report.md)。

## 组合民航仿真平台

[`platform/README.md`](platform/README.md) 记录了基于 JSBSim/FlightGear、BlueSky/OpenAP、ARINC 429 和 PX4 HIL 接口的组合方案。当前可直接运行的是确定性的民航运动学模型、ARINC 429 安全网关和攻击事件记录；如果本机安装 JSBSim，`--engine auto` 会尝试使用 JSBSim，否则会明确记录运动学后备引擎。PX4 联仿由独立脚本显式启动，不会被普通 ARINC 429 实验隐式连接。

平台输出 [`artifacts/civil_platform_results.json`](artifacts/civil_platform_results.json) 包含飞行阶段、合成航电标签、原生接收端与安全网关判定，以及未授权注入、重放和重算奇偶篡改的前后对照。真实 JSBSim 接入记录见 [`artifacts/civil_platform_jsbsim_30s.md`](artifacts/civil_platform_jsbsim_30s.md)。

PX4 + JSBSim 软件联仿可用以下命令复现：

```bash
python3 scripts/run_px4_jsbsim_hil.py \
  --duration-s 8 \
  --output artifacts/px4_jsbsim_hil_results.json
```

本机已验证 `HIL_SENSOR=2000`、`HIL_GPS=400`、`HIL_ACTUATOR_CONTROLS=799`，PX4 返回码为 `0`。证据见 [`artifacts/px4_jsbsim_hil_results.json`](artifacts/px4_jsbsim_hil_results.json) 和 [`artifacts/px4_jsbsim_hil_results.md`](artifacts/px4_jsbsim_hil_results.md)。该结果属于外部 HIL 风格软件联仿，不等同于真实飞控板 HITL、ARINC 429 物理波形测试或适航验证。

### 虚拟硬件联仿

虚拟硬件层把实体接口抽象为明确的软件模型：`V429-LAB-2T4R` 虚拟
ARINC 429 卡、使用合成 ICD 的 `SYNTHETIC_AIR_DATA_LRU` 和默认不解锁的
`PX4-FMU-VIRTUAL` HIL 端点。运行 2 秒 JSBSim 737 正常场景：

```bash
PYTHONPATH=/tmp/civil_aviation_jsbsim_deps \
python3 scripts/run_virtual_hardware_lab.py \
  --engine jsbsim --duration-s 2 --step-ms 20 --attack none \
  --output artifacts/virtual_hardware_lab_results.json
```

攻击复测可将 `--attack` 改为 `injection`、`replay`、`tamper`、`flood` 或
`rate_mismatch`。本次证据见 [`artifacts/virtual_hardware_lab_results.md`](artifacts/virtual_hardware_lab_results.md)、
[`artifacts/virtual_hardware_lab_injection.json`](artifacts/virtual_hardware_lab_injection.json)、
[`artifacts/virtual_hardware_lab_replay.json`](artifacts/virtual_hardware_lab_replay.json)、
[`artifacts/virtual_hardware_lab_tamper.json`](artifacts/virtual_hardware_lab_tamper.json)、
[`artifacts/virtual_hardware_lab_rate_mismatch.json`](artifacts/virtual_hardware_lab_rate_mismatch.json)
和 [`artifacts/virtual_hardware_lab_flood.json`](artifacts/virtual_hardware_lab_flood.json)。
这是数字接口/状态机仿真，不是实体卡、实体飞控板、真实 LRU 或物理波形验证。

## 仿真边界

`sim/arinc429_lab.py`实现 32 位字字段、奇偶校验、原生接收端、带来源认证/新鲜度/语义/限速控制的受控入口，以及 12.5 kbps/100 kbps 的线速排队估算。网关中的 HMAC-SHA-256 只用于可复现实验；实际机载系统必须依据项目安全架构、密钥管理和适航审定基础选择经批准的密码方案。

实验验证的是协议和接收逻辑的安全属性，不是电气波形、EMI、线缆终端、具体 LRU、具体 ICD 或飞控软件的适航验证。报告中对这些边界均有标注。

## 第6题：RS-422/RS-232 串行接口

正式分析见 [`report/serial_interface_security_report.md`](report/serial_interface_security_report.md)。运行本地数字串行实验：

```bash
python3 scripts/run_serial_experiments.py
```

证据见 [`artifacts/serial_interface_results.json`](artifacts/serial_interface_results.json)
和 [`artifacts/serial_interface_experiment_log.md`](artifacts/serial_interface_experiment_log.md)。实验覆盖 RS-422 双差分对全双工航电链路、RS-232 GSE 维护链路、明文监听、合法 CRC 注入、重算 CRC 篡改、重放、维护命令、波特率失配和突发占线。帧格式和命令码是合成项目 ICD；本实验不打开真实串口，不替代真实电平、波形、EMI、LRU 或适航验证。

## 第5题：5G-ATG航空地空宽带通信系统

正式分析见 [`report/5g_atg_security_report.md`](report/5g_atg_security_report.md)。运行5G-ATG全链路数字安全状态实验：

```bash
python3 scripts/run_atg5g_experiments.py
python3 -m pytest -q tests/test_atg5g_lab.py
```

证据见 [`artifacts/5g_atg_results.json`](artifacts/5g_atg_results.json) 和
[`artifacts/5g_atg_experiment_log.md`](artifacts/5g_atg_experiment_log.md)。模型覆盖合成机载5G UE、ATG NR gNB、AMF/SMF/UPF专属核心网、N6地面应用、客舱/航电边界和OAM管理边界，验证正常注册、数字化干扰、恶意小区前置可见性、信令风暴、用户面篡改/重放、TEID/QFI会话伪造、客舱到航电注入和OAM路由变更。

该实验是本地数字架构与安全状态仿真：不发射RF、不创建真实小区、不运行真实gNB/5GC、不捕获真实IMSI/SUPI、不连接飞机或运营商专网。它证明模型中的协议状态、路由绑定和边界策略，不替代真实射频屏蔽台架、实体UE/gNB/5GC、机载ICD、适航安全评估或运行批准。

### 第5题证据追溯与反向核验

报告引用、量化数据、场景结论和实验过程的映射见
[`report/5g_atg_traceability_matrix.md`](report/5g_atg_traceability_matrix.md)，机器登记表见
[`report/5g_atg_traceability.json`](report/5g_atg_traceability.json)。运行：

```bash
python3 scripts/render_atg5g_traceability.py
python3 scripts/run_atg5g_experiments.py
python3 scripts/render_report.py
python3 scripts/verify_atg5g_evidence.py
```

验证器会重新运行模型、深比较归档 JSON、解析证据路径和代码锚点、重跑第5题测试，并输出
[`artifacts/5g_atg_provenance.json`](artifacts/5g_atg_provenance.json) 和
[`artifacts/5g_atg_verification.log`](artifacts/5g_atg_verification.log)。

## 链路三：地面网络接入型

链路三报告见 [`report/chain3_ground_network_attack_chain.md`](report/chain3_ground_network_attack_chain.md)，包含地面保障网络/供应商跳板/5GC OAM/串口服务器到 N6、跨域网关、串行/A429 和机载消费者的编号化攻击链、信任边界、权限变化、检测点、风险量化、敏感性分析、证据等级和待实装验证条件。

生成可打印 HTML：

```bash
python3 scripts/render_report.py
```

输出 [`artifacts/chain3_ground_network_attack_chain.html`](artifacts/chain3_ground_network_attack_chain.html)。报告仅使用本地合成模型和既有证据，不连接真实航空器、机场生产网、空管网或公共移动网络。
