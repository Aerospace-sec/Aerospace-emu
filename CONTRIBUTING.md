# 贡献指南

## 开始工作

1. 阅读 `README.md` 和比赛题目对应章节。
2. 从最新 `main` 创建单主题分支：`feature/<题目编号>-<短名>`、`report/<题目编号>-<短名>` 或 `fix/<短名>`。
3. 在 issue 中登记题目编号、负责人、审查人、交付物和验证命令。
4. 不把真实设备、真实凭据、厂商保密资料或未经授权的测试数据放入仓库。

## 交付要求

协议分析必须同时说明原生设计、部署/实现缺陷、触发前提、设备与技术成本、检测概率、业务影响和缓解措施。跨域链路必须说明入口、节点、权限变化、横向路径、攻击参数、检测点和最终目标；在本仓库中使用合成/防御性模型表达，不执行真实攻击。

新增或修改代码必须：

- 保持现有测试通过；
- 为新行为增加行为测试；
- 为实验结果提供可重生成脚本和结构化证据；
- 明确 `simulation_only`、来源、观测时间、可信度和证据等级；
- 不绕过状态过期、来源认证、语义策略、限速或跨域控制。

## 提交和 PR

提交使用 Conventional Commits，例如：

```text
feat(q1): add AFDX virtual-link model
report(q6): document RS-422 replay boundary
fix(twin): preserve stale state after adapter timeout
```

PR 必须说明：

- 对应题目/issue；
- 变更和未覆盖范围；
- 仿真与真实系统边界；
- 验证命令及结果；
- 是否新增或更新报告证据。

至少一名非作者审查；安全结论、跨域链路和最终报告至少两名审查者。CI 未通过或存在未解决阻塞性评论时不得合并。维护者使用 Squash merge，禁止直接推送 `main` 和共享分支强制推送。

## 本地验证

```bash
python -m pytest -q
python scripts/run_cross_model_chain.py --scenario normal
python scripts/run_cross_model_chain.py --scenario injection
git diff --check
```
