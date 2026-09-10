# PX4 + JSBSim 联仿证据记录

本记录由 `scripts/run_px4_jsbsim_hil.py` 生成。测试对象是本机
PX4 POSIX/SITL 软件进程与 JSBSim 737 研究模型，不是真实飞控板、
ARINC 429 物理总线或实际飞机系统的验证记录。

## 运行配置

- 联仿类型：`PX4 SITL + JSBSim external HIL-style software co-simulation`
- JSBSim 模型：`737`，根目录 `/tmp/civil_aviation_jsbsim_deps/jsbsim`
- PX4 构建：`/tmp/px4-sitl-build-full/bin/px4`
- PX4 控制器配置：`5001_xplane_cessna172 (fixed-wing controller configuration only)`
- 仿真时长：`8.0 s`；传感器周期：`4 ms / 250 Hz`
- 连接范围：`127.0.0.1:4560`（仅回环 TCP）

## 闭环结果

- PX4 已连接：`True`；心跳：`True`
- 发送 `HIL_SENSOR`：`2000`；发送 `HIL_GPS`：`400`
- 接收 `HIL_ACTUATOR_CONTROLS`：`801`
- PX4 返回码：`0`；正常关闭：`True`
- 首状态：`{'timestamp_ms': 0, 'phase': 'cruise', 'latitude_deg': 47.1916344, 'longitude_deg': 122.0, 'altitude_ft': 30000.17, 'true_airspeed_kt': 444.36, 'heading_deg': 225.0, 'vertical_speed_fpm': -0.0, 'pitch_deg': 2.28, 'roll_deg': -0.0}`
- 末状态：`{'timestamp_ms': 7996, 'phase': 'cruise', 'latitude_deg': 47.1800283, 'longitude_deg': 121.9829671, 'altitude_ft': 30001.2, 'true_airspeed_kt': 444.33, 'heading_deg': 225.02, 'vertical_speed_fpm': 16.63, 'pitch_deg': 2.3, 'roll_deg': -0.05}`

## 复现命令

```bash
python3 scripts/run_px4_jsbsim_hil.py \
  --duration-s 8.0 \
  --output artifacts/px4_jsbsim_hil_results.json
```

## 证据边界

- PX4 is a POSIX/SITL software build, not a physical flight-control board.
- The PX4 5001 airframe is a fixed-wing controller configuration, not a certified Cessna or transport-aircraft model.
- JSBSim sensors are a research interface adapter; no ARINC 429 electrical waveform or real aircraft ICD is exercised.
- The TCP link is local loopback and is not evidence of an operational aircraft network path.

因此，本记录可证明 JSBSim 状态、MAVLink HIL 输入和 PX4 执行器反馈在本机软件链路中完成闭环；
不能证明真实飞控板 HITL、ARINC 429 电气波形、真实机型 ICD、适航符合性或运行安全。
