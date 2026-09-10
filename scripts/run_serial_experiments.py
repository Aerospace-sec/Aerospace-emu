#!/usr/bin/env python3
"""Run the RS-232/RS-422 serial-interface security laboratory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
MODEL = ROOT / "sim" / "serial_lab.py"
sys.path.insert(0, str(ROOT))

from sim.serial_lab import run_serial_scenarios  # noqa: E402


def _markdown(results: dict[str, object], model_sha256: str) -> str:
    interfaces = results["interfaces"]
    normal = results["normal"]
    attacks = results["attacks"]
    injection = attacks["injection"]
    tamper = attacks["tamper_recomputed_crc"]
    replay = attacks["replay"]
    maintenance = normal["rs232"]
    airborne = attacks["airborne_maintenance"]
    flood = attacks["flood"]
    capture = attacks["physical_tap_and_capture"]
    return f"""# RS-422/RS-232 串行接口实验记录

本记录由 `scripts/run_serial_experiments.py` 生成。实验只运行在本地数字模型中，
不打开 `/dev/tty`，不连接真实航电设备、LRU、维护设备或飞机。

- 模型文件 SHA-256：`{model_sha256}`
- Python：`{platform.python_version()}`
- RS-422 配置：`{interfaces['rs422']}`
- RS-232 配置：`{interfaces['rs232']}`
- 合成帧：`{results['model']['synthetic_frame']}`
- CRC：`{results['model']['crc']}`

## 结果摘要

| 场景 | 原生/直接接收端 | 加固网关 | 具体观测 |
|---|---|---|---|
| RS-422 正常航电数据 | `{normal['rs422']['receiver']['accepted']}` | `{normal['rs422']['gateway']['accepted']}` | `{normal['rs422']['effect']}` |
| 物理监听 | 可解析 `{capture['decoded_message_type']}` | 原生链路不提供保密 | 捕获 `{capture['captured_length_bytes']}` 字节，数据字段可见 |
| 未授权注入 | `{injection['native']['accepted']}` | `{injection['hardened']['accepted']}` / `{injection['hardened']['reason']}` | 直接接收后状态为 `{injection['native']['state_after']}` |
| 修改数据并重算 CRC | `{tamper['native']['accepted']}` | `{tamper['hardened']['accepted']}` / `{tamper['hardened']['reason']}` | `crc_valid_after_tamper={tamper['crc_valid_after_tamper']}` |
| 重放 | `{replay['native']['accepted']}` | `{replay['hardened']['accepted']}` / `{replay['hardened']['reason']}` | 首次认证 `{replay['first_hardened']['accepted']}`，再次提交被拒 |
| RS-232 维护配置 | `{maintenance['maintenance_direct_receiver']['accepted']}` | `{maintenance['maintenance_gateway']['accepted']}` | `{maintenance['maintenance_direct_receiver']['effect']}` |
| 飞行中维护命令 | `{airborne['direct_native']['accepted']}` | `{airborne['hardened_gateway']['accepted']}` / `{airborne['hardened_gateway']['reason']}` | 维护端口暴露时原生接收仍可能执行 |
| 突发占线 | `{flood['native_attacker_accepted']}` 个攻击帧 | `{flood['hardened_attacker_rejected']}` 个拒绝 | 合法帧延后 `{flood['native_legal_start_ms']:.2f}` ms；网关后从 0 ms 输出 |

## 全双工观测

RS-422 两个独立差分方向和 RS-232 两个独立发送方向在同一请求时刻的数字模型
起始时间均为 `{normal['rs232']['full_duplex']['a_to_b_start_ms']}` ms，说明模型
使用独立方向时钟；这不等同于实测收发器和线缆电气指标。

## 证据边界

1. RS-232/RS-422 是电气接口标准；帧格式、命令、LRU 名称和字段范围均为本项目合成 ICD。
2. CRC 只用于传输错误检测，不能提供来源认证、访问控制、保密性或抗重放。
3. 物理接入条件需要维护连接器、线束、测试适配器、串行转换器或已被攻陷的 LRU；不能从本实验推出任意远程网络可直接注入。
4. 本实验不证明电压、电平、共模范围、端接、EMI/ESD、串口驱动器、真实 LRU 响应或适航符合性。
"""


def main() -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    results = run_serial_scenarios()
    model_sha256 = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    payload = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model_sha256": model_sha256,
        },
        "results": results,
    }
    (ARTIFACTS / "serial_interface_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (ARTIFACTS / "serial_interface_experiment_log.md").write_text(
        _markdown(results, model_sha256),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
