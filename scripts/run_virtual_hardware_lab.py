#!/usr/bin/env python3
"""Run the deterministic virtual PX4/ARINC 429/LRU lab."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.virtual_hardware_lab import run_virtual_hardware_lab  # noqa: E402


def _markdown(result: dict[str, object]) -> str:
    platform = result["platform"]
    card = result["card"]
    lru = result["lru"]
    board = result["board"]
    normal = result["normal"]
    attack = result["attack"]
    health = result["health"]
    lines = [
        "# 虚拟飞控板 + ARINC 429 接口卡 + LRU 联仿证据",
        "",
        "本记录由 `scripts/run_virtual_hardware_lab.py` 生成。所有设备均为",
        "确定性软件模型，不打开串口、网络、USB、PCIe、GPIO 或飞机接口。",
        "",
        "## 仿真配置",
        "",
        f"- 引擎：`{platform['engine']}`",
        f"- 时长/步长：`{platform['duration_ms']} ms / {platform['step_ms']} ms`",
        f"- 虚拟 ARINC 卡：`{card['profile']}`",
        f"- 虚拟 LRU：`{lru['name']}`，标签 `{lru['accepted_labels']}`",
        f"- 虚拟 PX4 板：`{board['profile']}`",
        "",
        "## 正常闭环",
        "",
        f"- 合成 ARINC 字：`{normal['frames_emitted']}`；网关放行：`{normal['secure_frames_admitted']}`",
        f"- 物理卡数字事件：`{normal['wire_events']}`；LRU 接受：`{normal['lru_accepted']}`",
        f"- PX4 HIL_SENSOR：`{board['sensor_count']}`；HIL_GPS：`{board['gps_count']}`",
        f"- 执行器反馈：`{board['actuator_feedback_count']}`；失效状态：`{board['failsafe_count']}`",
        f"- 解锁请求接受：`{board['arm_request_accepted']}`；安全默认未解锁：`{board['armed'] is False}`",
        "",
        "## 场景验证",
        "",
        f"- 场景：`{attack['requested']}`，时间：`{attack['attack_at_ms']} ms`",
        f"- 结果：`{attack['event']}`",
        "",
        "## 健康检查",
        "",
    ]
    lines.extend(f"- {key}: `{value}`" for key, value in health.items())
    lines.extend(
        [
            "",
            "## 证据边界",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in result["limitations"])
    lines.extend(
        [
            "",
            "该结果证明的是软件接口、数字字时序、网关判定和 HIL 状态机的闭环；",
            "不能证明 ARINC 429 电压波形、真实接口卡 SDK、真实 LRU、实体 PX4 MCU 或适航符合性。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-s", type=float, default=2.0)
    parser.add_argument("--step-ms", type=int, default=20)
    parser.add_argument("--engine", choices=("auto", "kinematic", "jsbsim"), default="kinematic")
    parser.add_argument("--aircraft", default="737")
    parser.add_argument("--jsbsim-root", default="/tmp/civil_aviation_jsbsim_deps/jsbsim")
    parser.add_argument(
        "--attack",
        choices=("none", "injection", "replay", "tamper", "flood", "rate_mismatch"),
        default="none",
    )
    parser.add_argument("--attack-at-ms", type=int, default=1_000)
    parser.add_argument("--card-rate-bps", type=int, choices=(12_500, 100_000), default=100_000)
    parser.add_argument("--lru-rate-bps", type=int, choices=(12_500, 100_000), default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "virtual_hardware_lab_results.json",
    )
    parser.add_argument("--markdown-output", type=Path, default=None)
    args = parser.parse_args()
    result = run_virtual_hardware_lab(
        duration_ms=round(args.duration_s * 1_000),
        step_ms=args.step_ms,
        engine=args.engine,
        aircraft=args.aircraft,
        jsbsim_root=args.jsbsim_root,
        attack=args.attack,
        attack_at_ms=args.attack_at_ms,
        card_bit_rate_bps=args.card_rate_bps,
        lru_bit_rate_bps=args.lru_rate_bps,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_output = args.markdown_output or args.output.with_suffix(".md")
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "markdown_output": str(markdown_output),
        "health": result["health"],
        "normal": result["normal"],
        "attack": result["attack"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["health"]["closed_cleanly"] or not result["health"]["virtual_card_ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
