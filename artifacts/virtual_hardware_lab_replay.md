# 虚拟飞控板 + ARINC 429 接口卡 + LRU 联仿证据

本记录由 `scripts/run_virtual_hardware_lab.py` 生成。所有设备均为
确定性软件模型，不打开串口、网络、USB、PCIe、GPIO 或飞机接口。

## 仿真配置

- 引擎：`{'requested': 'jsbsim', 'selected': 'jsbsim', 'aircraft': '737', 'root_dir': '/tmp/civil_aviation_jsbsim_deps/jsbsim'}`
- 时长/步长：`2000 ms / 20 ms`
- 虚拟 ARINC 卡：`{'model': 'V429-LAB-2T4R', 'tx_channels': 2, 'rx_channels': 4, 'bit_rate_bps': 100000, 'word_bits': 32, 'interword_gap_bits': 4, 'tx_fifo_depth': 64, 'loopback': True, 'electrical_model': False}`
- 虚拟 LRU：`SYNTHETIC_AIR_DATA_LRU`，标签 `['0o203', '0o210', '0o211', '0o212']`
- 虚拟 PX4 板：`{'board_model': 'PX4-FMU-VIRTUAL', 'firmware': 'PX4-HIL-virtual', 'hitl_enabled': True, 'sensor_timeout_ms': 100, 'gps_timeout_ms': 500, 'physical_mcu_model': False}`

## 正常闭环

- 合成 ARINC 字：`404`；网关放行：`404`
- 物理卡数字事件：`404`；LRU 接受：`404`
- PX4 HIL_SENSOR：`101`；HIL_GPS：`21`
- 执行器反馈：`101`；失效状态：`0`
- 解锁请求接受：`False`；安全默认未解锁：`True`

## 场景验证

- 场景：`replay`，时间：`1000 ms`
- 结果：`{'type': 'replay', 'legacy_lru': {'accepted': True, 'reason': 'accepted'}, 'hardened_gateway': {'accepted': False, 'reason': 'replay'}, 'hardened_physical_output': False, 'wire': {'tx_channel': 0, 'rx_channel': 0, 'requested_ms': 1000, 'start_ms': 1001.44, 'end_ms': 1001.8, 'word_duration_ms': 0.36, 'bit_rate_bps': 100000, 'raw_word': '0x81d4c083', 'observed_raw_word': '0x81d4c083', 'fault': None}}`

## 健康检查

- virtual_card_ready: `True`
- virtual_card_loopback: `True`
- virtual_lru_active: `True`
- virtual_px4_connected: `True`
- hil_sensor_received: `True`
- hil_gps_received: `True`
- actuator_feedback_received: `True`
- disarmed_safe_default: `True`
- no_real_io: `True`
- closed_cleanly: `True`

## 证据边界

- VirtualArinc429Card models digital words and timing only; it does not model voltage, impedance, line termination, EMI, or an actual vendor SDK.
- VirtualLru uses a synthetic project ICD and does not claim any real aircraft label allocation or LRU behavior.
- VirtualPx4Board is a HIL protocol/state model, not a PX4 MCU, sensor driver, PWM, power, or hardware watchdog emulation.
- This result is not evidence of a physical ARINC 429 waveform, real LRU test, flight-control hardware HITL, or airworthiness approval.

该结果证明的是软件接口、数字字时序、网关判定和 HIL 状态机的闭环；
不能证明 ARINC 429 电压波形、真实接口卡 SDK、真实 LRU、实体 PX4 MCU 或适航符合性。
