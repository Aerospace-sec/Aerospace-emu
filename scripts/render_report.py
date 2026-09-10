#!/usr/bin/env python3
"""Render the Markdown report to a self-contained printable HTML file."""

from __future__ import annotations

from pathlib import Path

import markdown


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "report" / "arinc429_security_report.md"
OUTPUT = ROOT / "artifacts" / "arinc429_security_report.html"
SERIAL_SOURCE = ROOT / "report" / "serial_interface_security_report.md"
SERIAL_OUTPUT = ROOT / "artifacts" / "serial_interface_security_report.html"
ATG_SOURCE = ROOT / "report" / "5g_atg_security_report.md"
ATG_OUTPUT = ROOT / "artifacts" / "5g_atg_security_report.html"
CHAIN3_SOURCE = ROOT / "report" / "chain3_ground_network_attack_chain.md"
CHAIN3_OUTPUT = ROOT / "artifacts" / "chain3_ground_network_attack_chain.html"


CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
* { box-sizing: border-box; }
body { color: #17202a; font-family: "Noto Serif CJK SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; font-size: 10.5pt; line-height: 1.65; margin: 0 auto; max-width: 920px; }
h1 { border-bottom: 2px solid #123b5d; color: #123b5d; font-size: 22pt; line-height: 1.25; margin: 0 0 18px; padding-bottom: 10px; }
h2 { border-bottom: 1px solid #7c9ab2; color: #123b5d; font-size: 15pt; margin-top: 26px; padding-bottom: 4px; }
h3 { color: #234e70; font-size: 12.5pt; margin-top: 20px; }
p { text-align: justify; }
table { border-collapse: collapse; font-size: 9pt; margin: 12px 0 18px; width: 100%; }
th { background: #eaf1f6; color: #123b5d; font-weight: 700; }
th, td { border: 1px solid #b6c4ce; padding: 5px 6px; vertical-align: top; }
code { background: #eef2f4; border-radius: 2px; padding: 1px 3px; }
pre { background: #17202a; color: #f2f6f8; overflow-wrap: anywhere; padding: 10px; white-space: pre-wrap; }
blockquote { border-left: 4px solid #7c9ab2; color: #455a64; margin-left: 0; padding-left: 12px; }
li { margin-bottom: 4px; }
hr { border: 0; border-top: 1px solid #c8d1d8; }
@media print { a { color: inherit; text-decoration: none; } h2, h3 { break-after: avoid; } table { break-inside: avoid; } }
"""


def _render(source: Path, output: Path, title: str) -> None:
    body = markdown.markdown(
        source.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "toc"],
        output_format="html5",
    )
    html = "<!doctype html>\n<html lang=\"zh-CN\">\n<head><meta charset=\"utf-8\"><title>" + title + "</title><style>" + CSS + "</style></head>\n<body>" + body + "</body>\n</html>\n"
    output.write_text(html, encoding="utf-8")
    print(output)


def main() -> None:
    _render(SOURCE, OUTPUT, "ARINC 429 总线脆弱性分析报告")
    _render(SERIAL_SOURCE, SERIAL_OUTPUT, "RS-422/RS-232 串行接口脆弱性分析报告")
    _render(ATG_SOURCE, ATG_OUTPUT, "5G-ATG 航空地空宽带通信系统脆弱性分析报告")
    _render(CHAIN3_SOURCE, CHAIN3_OUTPUT, "链路三：地面网络接入型攻击链分析报告")


if __name__ == "__main__":
    main()
