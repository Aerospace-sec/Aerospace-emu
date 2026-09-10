#!/usr/bin/env python3
"""Run the local 5G-ATG security-state laboratory and write evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import platform
import shlex
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
MODEL = ROOT / "sim" / "atg5g_lab.py"
sys.path.insert(0, str(ROOT))

from sim.atg5g_lab import run_atg_scenarios  # noqa: E402


def _markdown(results: dict[str, object], model_sha256: str) -> str:
    architecture = results["architecture"]
    air = results["air_interface"]
    normal = results["normal"]
    captures = results["captures"]
    attacks = results["attacks"]
    interference = attacks["interference"]
    rogue = attacks["rogue_cell"]
    storm = attacks["signaling_storm"]
    boundary = attacks["cabin_to_avionics_boundary"]
    tamper = attacks["data_tamper"]
    replay = attacks["data_replay"]
    session_binding = attacks["session_binding"]
    oam = attacks["oam_route_change"]
    verification = results["_verification"]
    return f"""# 5G-ATG 数字安全实验记录

本记录由 `scripts/run_atg5g_experiments.py` 生成。实验只运行在本地数字模型中，
不发射射频、不创建真实小区、不运行真实 gNB/5GC、不使用 SIM/eSIM，
不连接飞机、机载终端、运营商专网或地面生产系统。

- 模型文件 SHA-256：`{model_sha256}`
- Python：`{platform.python_version()}`
- 平台：`{platform.platform()}`
- 合成 PLMN：`{architecture['plmn']}`
- 合成切片：`{architecture['slice']}`
- 合成 DNN：`{', '.join(architecture['dnns'])}`
- 逻辑链路：`{' -> '.join(architecture['chain'])}`

## 结果摘要

| 场景 | 原生/弱化模型 | 加固模型 | 可复核结论 |
|---|---|---|---|
| 正常注册 | `{air['trusted_registration']['result']}` | `{air['trusted_registration']['result']}` | 5G-AKA、安全模式和 PDU 会话状态均完成 |
| 数字化干扰 | 用户面不可用 | 用户面不可用 | RRC/NAS 丢失抽象为注册超时，必须进入安全降级 |
| 恶意小区诱捕 | `{rogue['legacy']['result']}` | `{rogue['hardened']['result']}` | 广播和初始注册元数据可见；加固 UE 不建立用户面 |
| 注册信令风暴 | 分配 `{storm['native_contexts_allocated']}` 个上下文 | 分配 `{storm['hardened_contexts_allocated']}` 个，拒绝 `{storm['hardened_rejected']}` 个 | 1 秒窗口限速有效 |
| 客舱→航电边界 | `{boundary['native']['accepted']}` / 执行命令 | `{boundary['hardened']['accepted']}` / `{boundary['hardened']['reason']}` | DNN/传输信任不能替代跨域 ACL |
| 用户面篡改 | `{tamper['native']['accepted']}` / 接受错误高度 | `{tamper['hardened']['accepted']}` / `{tamper['hardened']['reason']}` | 应用完整性校验阻止 payload 修改 |
| 用户面重放 | `{replay['native']['accepted']}` | 首次 `{replay['first_hardened']['accepted']}`，再次 `{replay['replayed_hardened']['accepted']}` / `{replay['replayed_hardened']['reason']}` | 序号和新鲜度状态阻止重复业务数据 |
| N3会话伪造 | `{session_binding['teid_forgery']['native']['accepted']}` | TEID `{session_binding['teid_forgery']['hardened']['reason']}`；QFI `{session_binding['qfi_forgery']['hardened']['reason']}` | DNN名称不能替代会话和QoS绑定 |
| OAM 路由变更 | `{oam['native']['accepted']}` | 未授权 `{oam['hardened_unauthorized']['accepted']}`，授权 `{oam['hardened_authorized']['accepted']}` | RBAC、mTLS、双人审批和边界策略同时生效 |
| 被动监听 | Uu 初始信息可见；N3 外层元数据可见 | 应用 TLS 时 payload 不可读 | 广播/传输元数据与业务 payload 需分开评价 |

## 关键观测

### 注册和空口状态

- 可信小区注册结果：`{air['trusted_registration']['reason']}`。
- 恶意小区在加固 UE 下结果：`{rogue['hardened']['reason']}`。
- 恶意小区在弱化 UE 下结果：`{rogue['legacy']['reason']}`；该结果只说明可驻留/诱捕状态，
  不说明真实 5G-AKA 已被破解或真实用户面已经建立。
- 干扰场景结果：`{interference['registration']['reason']}`，`service_available={interference['service_available']}`。

### 用户面和边界

- N3/N6 模型使用合成 TEID、QFI、DNN、S-NSSAI 和应用消息；网关将 TEID、切片、DNN、
  来源区和来源密钥绑定后再交给地面应用。
- 原生路由器把 `CABIN -> AVIONICS` 命令交给合成航电消费者；加固网关以
  `{boundary['hardened']['reason']}` 拒绝，航电命令列表保持为空。
- 篡改后的原始 HMAC 不匹配，网关返回 `{tamper['hardened']['reason']}`；这证明的是数字模型的
  消息完整性策略，不是对 3GPP Uu 加密算法的实现测试。

### 监听边界

- Uu：`broadcast_sib_visible={captures['uu']['broadcast_sib_visible']}`，
  `pre_security_registration_metadata_visible={captures['uu']['pre_security_registration_metadata_visible']}`。
- N3/N6：GTP-U 外层头和 TEID 在传输观察点可见：`{captures['n3_n6']['gtp_u_outer_headers_visible']}`；
  无应用 TLS 时 payload 可读：`{captures['n3_n6']['application_payload_readable_without_app_tls']}`，
  有应用 TLS 时不可直接读取：`{captures['n3_n6']['application_payload_readable_with_app_tls']}`。

## 复现方法

```bash
python3 scripts/run_atg5g_experiments.py
python3 -m pytest -q tests/test_atg5g_lab.py
```

JSON 证据中的 `results` 字段保留全部场景状态，便于报告复核、二次统计和加固前后对比。

本次运行器同步执行并记录回归检查：聚焦测试返回码 `{verification['focused']['returncode']}`，
全量测试返回码 `{verification['full']['returncode']}`。完整stdout/stderr和命令保存在JSON环境段，
验证器会再次独立执行这些测试。

## 证据边界

1. 本实验是 5G-ATG 数字架构与安全状态仿真；它不证明真实射频干扰、伪基站、IMSI/SUPI 捕获、
   gNB/5GC 实现缺陷或任何具体机型的失控结果。
2. PLMN、Cell ID、DNN、S-NSSAI、TEID、QFI、UE ID 和应用消息均为合成项目数据。
3. 3GPP 5G-AKA、NAS/RRC 和用户面保护能力只在真实部署的配置、实现和密钥管理正确时成立；
   本实验重点验证部署边界、应用完整性、会话绑定、限速和 OAM 控制。
4. 真实 gNB、5GC、SIM/eSIM、射频、机载设备、运营专网和维护系统的授权台架测试应另行进行，
   并按项目安全评估、适航审定基础和运行批准流程执行。
"""


def main() -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    results = run_atg_scenarios()
    model_sha256 = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    focused_command = [sys.executable, "-m", "pytest", "-q", "tests/test_atg5g_lab.py"]
    full_command = [sys.executable, "-m", "pytest", "-q"]

    def run_regression(command: list[str]) -> dict[str, object]:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise SystemExit(
                f"regression command failed ({completed.returncode}): {shlex.join(command)}\n"
                f"{completed.stdout}{completed.stderr}"
            )
        return {
            "command": shlex.join(command),
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }

    verification = {
        "focused": run_regression(focused_command),
        "full": run_regression(full_command),
    }
    results_for_log = dict(results)
    results_for_log["_verification"] = verification
    payload = {
        "environment": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "command": shlex.join([sys.executable, *sys.argv]),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model_sha256": model_sha256,
            "traceability_matrix": "report/5g_atg_traceability.json",
            "verification": verification,
        },
        "results": results,
    }
    (ARTIFACTS / "5g_atg_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (ARTIFACTS / "5g_atg_experiment_log.md").write_text(
        _markdown(results_for_log, model_sha256),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
