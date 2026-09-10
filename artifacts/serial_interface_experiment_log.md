# RS-422/RS-232 串行接口实验记录

本记录由 `scripts/run_serial_experiments.py` 生成。实验只运行在本地数字模型中，
不打开 `/dev/tty`，不连接真实航电设备、LRU、维护设备或飞机。

- 模型文件 SHA-256：`b0c338abfc2e4f81068c214ee49b1eaa2d5d66d89edc4847fcb1f078e1a23497`
- Python：`3.14.7`
- RS-422 配置：`{'name': 'RS422-AVIONICS-LRU', 'standard': 'TIA-422-B', 'differential': True, 'full_duplex': True, 'baud_rate': 115200, 'uart_format': '8N1', 'frame_bits': 10, 'independent_signal_pairs': 2, 'connector_scope': 'aircraft_equipment_interconnect', 'termination_required': True, 'cryptographic_authentication': False, 'cryptographic_confidentiality': False}`
- RS-232 配置：`{'name': 'RS232-GSE-MAINT', 'standard': 'TIA-232-F', 'differential': False, 'full_duplex': True, 'baud_rate': 115200, 'uart_format': '8N1', 'frame_bits': 10, 'independent_signal_pairs': 1, 'connector_scope': 'ground_maintenance_connector', 'termination_required': False, 'cryptographic_authentication': False, 'cryptographic_confidentiality': False}`
- 合成帧：`SYNC|VERSION|TYPE|SEQUENCE|LENGTH|JSON_PAYLOAD|CRC16`
- CRC：`CRC-16/CCITT-FALSE`

## 结果摘要

| 场景 | 原生/直接接收端 | 加固网关 | 具体观测 |
|---|---|---|---|
| RS-422 正常航电数据 | `True` | `True` | `air_data_updated:30000ft@0ms` |
| 物理监听 | 可解析 `AIR_DATA_UPDATE` | 原生链路不提供保密 | 捕获 `63` 字节，数据字段可见 |
| 未授权注入 | `True` | `False` / `unauthorized_source` | 直接接收后状态为 `{'airspeed_kt': 80, 'altitude_ft': 500, 'valid': True}` |
| 修改数据并重算 CRC | `True` | `False` / `authentication` | `crc_valid_after_tamper=True` |
| 重放 | `True` | `False` / `replay` | 首次认证 `True`，再次提交被拒 |
| RS-232 维护配置 | `True` | `True` | `configuration_loaded:CAL-ATTACK` |
| 飞行中维护命令 | `True` | `False` / `maintenance_not_available` | 维护端口暴露时原生接收仍可能执行 |
| 突发占线 | `64` 个攻击帧 | `64` 个拒绝 | 合法帧延后 `72.22` ms；网关后从 0 ms 输出 |

## 全双工观测

RS-422 两个独立差分方向和 RS-232 两个独立发送方向在同一请求时刻的数字模型
起始时间均为 `0.0` ms，说明模型
使用独立方向时钟；这不等同于实测收发器和线缆电气指标。

## 证据边界

1. RS-232/RS-422 是电气接口标准；帧格式、命令、LRU 名称和字段范围均为本项目合成 ICD。
2. CRC 只用于传输错误检测，不能提供来源认证、访问控制、保密性或抗重放。
3. 物理接入条件需要维护连接器、线束、测试适配器、串行转换器或已被攻陷的 LRU；不能从本实验推出任意远程网络可直接注入。
4. 本实验不证明电压、电平、共模范围、端接、EMI/ESD、串口驱动器、真实 LRU 响应或适航符合性。
