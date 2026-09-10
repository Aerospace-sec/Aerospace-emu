#!/usr/bin/env python3
"""Run the contained lab and emit machine-readable evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
MODEL = ROOT / "sim" / "arinc429_lab.py"

sys.path.insert(0, str(ROOT))

from sim.arinc429_lab import run_scenarios


def _evidence_markdown(results: dict[str, object], model_sha256: str) -> str:
    injection = results["injection"]
    replay = results["replay"]
    tamper = results["parity_recomputed_tamper"]
    semantic = results["semantic_range"]
    flood = results["flood"]
    passive = results["passive_observation"]
    parity = results["parity_baseline"]
    rate = results["rate_mismatch"]

    return f"""# ARINC 429 实验记录

本记录由 `scripts/run_experiments.py` 生成，实验只在本地逻辑仿真器中运行，不连接串口、网络或任何真实航电设备。

- 模型文件 SHA-256：`{model_sha256}`
- Python：`{platform.python_version()}`
- 字长：`{results['model']['word_bits']}` bit
- 仿真标签：`{results['model']['test_label_octal']}`（合成测试标签）
- 仿真速率：`{', '.join(str(rate) for rate in results['model']['bit_rates_bps'])}` bit/s

## 结果摘要

| 场景 | 原生接收端 | 受控网关 | 观测结论 |
|---|---|---|---|
| 未授权注入 | `{injection['legacy']['accepted']}` | `{injection['hardened']['accepted']}` / `{injection['hardened']['reason']}` | 原生端不识别来源；网关来源白名单生效 |
| 重放 | `{replay['legacy']['accepted']}` | `{replay['immediate_replay']['accepted']}` / `{replay['immediate_replay']['reason']}` | 原生端无新鲜度；网关序号拒绝重复帧 |
| 重算奇偶后的篡改 | `{tamper['legacy']['accepted']}` | `{tamper['hardened']['accepted']}` / `{tamper['hardened']['reason']}` | 奇偶校验仍为 `{tamper['parity_valid_after_tamper']}`，但 MAC 不匹配 |
| 语义越界 | `{semantic['legacy']['accepted']}` | `{semantic['hardened']['accepted']}` / `{semantic['hardened']['reason']}` | 原生端只做格式级检查；网关执行标签策略 |
| 连续占用 | `{flood['legacy_deadline_missed']}`（超时） | `{flood['hardened_deadline_missed']}`（未超时） | 入口过滤后合法帧在 `{flood['hardened_legitimate_start_ms']:.2f}` ms 开始发送 |
| 速率失配 | 接收端有效字：`{rate['receiver_valid_word']}` | 端口配置控制 | `{rate['expected_bit_rate']}`→`{rate['actual_bit_rate']}` bit/s；字时长 `{rate['expected_word_duration_ms']:.2f}`→`{rate['actual_word_duration_ms']:.2f}` ms |
| 被动观测 | `data={passive['captured_data']}` 可见 | 不由 MAC 提供保密性 | 若需保密，必须在受控边界增加加密/分区 |

## 复核要点

1. `legacy` 仅模拟奇偶校验和标签白名单，不能代表所有机载接收机实现。
2. `hardened` 的 MAC、序号和时间戳是系统级安全封装，不是原生 32 位 ARINC 429 字的新增字段。
3. 物理绕过受控入口的总线占用仍是残余风险，不能由接收端 MAC 单独消除；须依靠物理隔离、端口控制、故障遏制和架构级监测。
4. 单比特错误基线结果为 `parity_ok={parity['single_bit_flip_parity_ok']}`；该校验用于传输错误检测，不等同于来源认证。
"""


def main() -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    results = run_scenarios()
    model_sha256 = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    payload = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model_sha256": model_sha256,
        },
        "results": results,
    }
    (ARTIFACTS / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (ARTIFACTS / "experiment_log.md").write_text(
        _evidence_markdown(results, model_sha256),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
