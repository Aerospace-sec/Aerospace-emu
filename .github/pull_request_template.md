## 题目与范围

- 题目/编号：
- 关联 Issue：
- 变更类型：`feat` / `fix` / `report` / `ci` / `chore`

## 变更说明

- 问题或研究目标：
- 实现/分析内容：
- 未覆盖范围：
- 是否只使用合成数据和本地仿真：是 / 否（若否，说明授权与隔离台架）

## 证据与安全边界

- 证据脚本/报告：
- `simulation_only` / 来源 / 时间 / 可信度是否明确：是 / 否
- 是否涉及真实设备、真实凭据、生产网络或公共频谱：否 / 是（必须先完成安全评审）

## 验证

```text
python -m pytest -q
python scripts/run_cross_model_chain.py --scenario normal
python scripts/run_cross_model_chain.py --scenario injection
git diff --check
```

验证结果：

## 审查清单

- [ ] 题目要求和代码/报告/证据已建立映射。
- [ ] 新行为有行为测试。
- [ ] 未提交 `needed.txt`、密钥、缓存、SQLite 数据库或真实设备数据。
- [ ] 未添加真实航空系统攻击路径或任意原始报文发送功能。
- [ ] 至少一名非作者审查；安全结论/跨域链路/最终报告至少两名审查者。
