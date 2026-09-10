#!/usr/bin/env python3
"""Render the machine-readable 5G-ATG traceability registry as Markdown."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "report" / "5g_atg_traceability.json"
OUTPUT = ROOT / "report" / "5g_atg_traceability_matrix.md"


def _cell(value: Any) -> str:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def _evidence(items: list[dict[str, Any]]) -> str:
    rendered = []
    for item in items:
        location = item.get("path", "")
        if item.get("json_path"):
            location += f"#{item['json_path']}"
        if item.get("anchor"):
            location += f"::{item['anchor']}"
        rendered.append(f"`{location}`")
    return "<br>".join(rendered)


def _render(data: dict[str, Any]) -> str:
    lines = [
        "# 第5题 5G-ATG 可追溯性与反向核验矩阵",
        "",
        "本文件由 `scripts/render_atg5g_traceability.py` 根据 `report/5g_atg_traceability.json` 生成。",
        "它把报告中的规范性引用、工程推导、量化数据、实验结论和过程留痕连接到可复核的本地文件、JSON路径、代码锚点和测试断言。",
        "",
        "> 证据边界：`SRC-*` 是外部标准/规章来源；`LOC-*` 是本地模型、测试和证据文件；`C-*` 是技术结论；`Q-*` 是量化数据；`AUD-*` 是过程步骤。",
        "> 3GPP条目目前登记为系列级官方索引。正式提交前应把项目采用的3GPP版本、RTCA/EUROCAE受控修订、SAE版本和CCAR修订号填入配置基线；本矩阵不伪造未核实的版本号或条款号。",
        "",
        "## 1. 引用来源登记",
        "",
        "| ID | 引用 | 定位/覆盖范围 | 官方入口或受控来源 | 适用性质 | 版本状态 |",
        "|---|---|---|---|---|---|",
    ]
    for source in data["source_registry"]:
        lines.append("| " + " | ".join(_cell(source.get(key, "")) for key in ("id", "citation", "locator", "url", "use", "version_status")) + " |")

    lines += [
        "",
        "## 2. 本地证据登记",
        "",
        "| ID | 文件 | 代码/文档锚点 | 用途 |",
        "|---|---|---|---|",
    ]
    for source in data["local_evidence_registry"]:
        lines.append("| " + " | ".join((_cell(source["id"]), f"`{source['path']}`", _cell(source["anchors"]), _cell(source["purpose"]))) + " |")

    lines += [
        "",
        "## 3. 规范性和架构结论",
        "",
        "| ID | 报告位置 | 结论 | 外部来源 | 本地证据 | 推导/限制 |",
        "|---|---|---|---|---|---|",
    ]
    for claim in data["normative_and_architecture_claims"]:
        lines.append("| " + " | ".join((_cell(claim["id"]), _cell(claim["report_location"]), _cell(claim["statement"]), _cell(claim["sources"]), _evidence(claim["local_evidence"]), _cell(claim.get("derivation", claim.get("limitation", ""))))) + " |")

    lines += [
        "",
        "## 4. 量化数据追溯",
        "",
        "| ID | 报告位置 | 量化陈述 | JSON/代码证据 | 测试断言 |",
        "|---|---|---|---|---|",
    ]
    for claim in data["quantitative_claims"]:
        lines.append("| " + " | ".join((_cell(claim["id"]), _cell(claim["report_location"]), _cell(claim["statement"]), _evidence(claim["evidence"]), f"`{claim['test']}`")) + " |")

    lines += [
        "",
        "## 5. 场景化技术结论",
        "",
        "| ID | 报告位置 | 技术结论 | 外部来源 | 实验/测试证据 | 限制条件 |",
        "|---|---|---|---|---|---|",
    ]
    for claim in data["scenario_conclusions"]:
        lines.append("| " + " | ".join((_cell(claim["id"]), _cell(claim["report_location"]), _cell(claim["statement"]), _cell(claim["sources"]), _evidence(claim["evidence"]), _cell(claim["limitation"]))) + " |")

    lines += [
        "",
        "## 6. 分析过程留痕",
        "",
        "| ID | 步骤 | 命令 | 留痕文件 |",
        "|---|---|---|---|",
    ]
    for step in data["audit_steps"]:
        lines.append("| " + " | ".join((_cell(step["id"]), _cell(step["step"]), f"`{step.get('command', 'source registry / authored input')}`", _cell(step.get("evidence", [])))) + " |")

    lines += [
        "",
        "## 7. 评审反向核验路径",
        "",
        "1. 从报告章节或表格定位 `C-*`/`Q-*` 条目，再在本矩阵中找到对应的外部来源和本地证据。",
        "2. 对量化数据打开 `artifacts/5g_atg_results.json`，按矩阵中的 `#results...` JSON路径取值；不要依赖人工转抄的摘要。",
        "3. 打开对应模型锚点和测试函数，核对输入变化、判定顺序、拒绝原因和下游状态是否一致。",
        "4. 运行 `python3 scripts/verify_atg5g_evidence.py`，确认新鲜模型输出与归档JSON一致、所有路径/锚点存在、测试通过、哈希清单完整。",
        "5. 最后运行 `python3 scripts/render_report.py`，确认HTML报告由当前Markdown和当前矩阵生成。",
        "",
        "## 8. 交叉验证原则",
        "",
        "- 外部来源负责证明协议/标准语义；不把标准能力直接当成具体部署已经正确。",
        "- 模型负责证明合成场景中的状态转换；测试负责证明关键前后条件不会被无意改坏。",
        "- JSON负责保存实际观测值；Markdown只作人类阅读摘要；验证器比较两者对应的机器数据。",
        "- 报告中的危害分级是结合民航资产、利用前提、边界和限制的工程判断；具体机型仍需FHA、SSA和适用审定基础复核。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    OUTPUT.write_text(_render(data), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
