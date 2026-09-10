# 5G-ATG 数字安全实验记录

本记录由 `scripts/run_atg5g_experiments.py` 生成。实验只运行在本地数字模型中，
不发射射频、不创建真实小区、不运行真实 gNB/5GC、不使用 SIM/eSIM，
不连接飞机、机载终端、运营商专网或地面生产系统。

- 模型文件 SHA-256：`0c674732490ddc933e9f7e60f4313b136f0502228fb5d9de18e9c6a1e6bf15b4`
- Python：`3.14.7`
- 平台：`Linux-7.1.9-zen1-2-zen-x86_64-with-glibc2.44`
- 合成 PLMN：`999-99`
- 合成切片：`sst=1;sd=ATG001`
- 合成 DNN：`atg.cabin, atg.maint, atg.avionics`
- 逻辑链路：`aircraft_5g_ue -> ATG NR gNB -> AMF/SMF/UPF dedicated core -> N6 ground application -> cabin-avionics boundary -> OAM/OSS management boundary`

## 结果摘要

| 场景 | 原生/弱化模型 | 加固模型 | 可复核结论 |
|---|---|---|---|
| 正常注册 | `registered` | `registered` | 5G-AKA、安全模式和 PDU 会话状态均完成 |
| 数字化干扰 | 用户面不可用 | 用户面不可用 | RRC/NAS 丢失抽象为注册超时，必须进入安全降级 |
| 恶意小区诱捕 | `camped_untrusted_cell` | `rogue_cell_rejected` | 广播和初始注册元数据可见；加固 UE 不建立用户面 |
| 注册信令风暴 | 分配 `50` 个上下文 | 分配 `10` 个，拒绝 `40` 个 | 1 秒窗口限速有效 |
| 客舱→航电边界 | `True` / 执行命令 | `False` / `cabin_to_avionics_boundary` | DNN/传输信任不能替代跨域 ACL |
| 用户面篡改 | `True` / 接受错误高度 | `False` / `authentication` | 应用完整性校验阻止 payload 修改 |
| 用户面重放 | `True` | 首次 `True`，再次 `False` / `replay` | 序号和新鲜度状态阻止重复业务数据 |
| N3会话伪造 | `True` | TEID `session_binding`；QFI `qfi_binding` | DNN名称不能替代会话和QoS绑定 |
| OAM 路由变更 | `True` | 未授权 `False`，授权 `True` | RBAC、mTLS、双人审批和边界策略同时生效 |
| 被动监听 | Uu 初始信息可见；N3 外层元数据可见 | 应用 TLS 时 payload 不可读 | 广播/传输元数据与业务 payload 需分开评价 |

## 关键观测

### 注册和空口状态

- 可信小区注册结果：`5g_aka_and_security_mode_complete`。
- 恶意小区在加固 UE 下结果：`5g_aka_or_network_trust_failed`。
- 恶意小区在弱化 UE 下结果：`5g_aka_or_network_trust_failed`；该结果只说明可驻留/诱捕状态，
  不说明真实 5G-AKA 已被破解或真实用户面已经建立。
- 干扰场景结果：`uu_interference_or_signal_blocking`，`service_available=False`。

### 用户面和边界

- N3/N6 模型使用合成 TEID、QFI、DNN、S-NSSAI 和应用消息；网关将 TEID、切片、DNN、
  来源区和来源密钥绑定后再交给地面应用。
- 原生路由器把 `CABIN -> AVIONICS` 命令交给合成航电消费者；加固网关以
  `cabin_to_avionics_boundary` 拒绝，航电命令列表保持为空。
- 篡改后的原始 HMAC 不匹配，网关返回 `authentication`；这证明的是数字模型的
  消息完整性策略，不是对 3GPP Uu 加密算法的实现测试。

### 监听边界

- Uu：`broadcast_sib_visible=True`，
  `pre_security_registration_metadata_visible=True`。
- N3/N6：GTP-U 外层头和 TEID 在传输观察点可见：`True`；
  无应用 TLS 时 payload 可读：`True`，
  有应用 TLS 时不可直接读取：`False`。

## 复现方法

```bash
python3 scripts/run_atg5g_experiments.py
python3 -m pytest -q tests/test_atg5g_lab.py
```

JSON 证据中的 `results` 字段保留全部场景状态，便于报告复核、二次统计和加固前后对比。

本次运行器同步执行并记录回归检查：聚焦测试返回码 `0`，
全量测试返回码 `0`。完整stdout/stderr和命令保存在JSON环境段，
验证器会再次独立执行这些测试。

## 证据边界

1. 本实验是 5G-ATG 数字架构与安全状态仿真；它不证明真实射频干扰、伪基站、IMSI/SUPI 捕获、
   gNB/5GC 实现缺陷或任何具体机型的失控结果。
2. PLMN、Cell ID、DNN、S-NSSAI、TEID、QFI、UE ID 和应用消息均为合成项目数据。
3. 3GPP 5G-AKA、NAS/RRC 和用户面保护能力只在真实部署的配置、实现和密钥管理正确时成立；
   本实验重点验证部署边界、应用完整性、会话绑定、限速和 OAM 控制。
4. 真实 gNB、5GC、SIM/eSIM、射频、机载设备、运营专网和维护系统的授权台架测试应另行进行，
   并按项目安全评估、适航审定基础和运行批准流程执行。
