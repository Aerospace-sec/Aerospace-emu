# Civil Aviation Security Platform Experiment

This record was produced by `scripts/run_civil_platform.py`. It is a local
research simulation and does not connect to an aircraft, ARINC 429 interface,
serial port, or network service.

## Run

- Engine: `{'requested': 'auto', 'selected': 'kinematic', 'aircraft': '737', 'fallback_reason': 'JSBSim Python package is not installed; use --engine kinematic or install jsbsim'}`
- Duration / sample step: `5000 ms` / `1000 ms`
- ARINC labels: `0o203, 0o210, 0o211, 0o212` (synthetic project labels)
- Frames: `24` emitted, `24` admitted, `0` rejected
- Rejection reasons: `{}`

## Integration inventory

- JSBSim Python: `False` version `None`
- BlueSky Python: `False` version `None`
- FlightGear executable: `False`
- PX4 source present: `True` at `/home/starlight/winECode/PX4-Autopilot`
- Network opened: `False`; serial opened: `False`

## Flight-state samples

- First: `{'timestamp_ms': 0, 'phase': 'takeoff_climb', 'latitude_deg': 31.197, 'longitude_deg': 121.334, 'altitude_ft': 100.0, 'true_airspeed_kt': 120.0, 'heading_deg': 90.0, 'vertical_speed_fpm': 3600.0, 'pitch_deg': 8.0, 'roll_deg': 0.0}`
- Last: `{'timestamp_ms': 5000, 'phase': 'takeoff_climb', 'latitude_deg': 31.197, 'longitude_deg': 121.3373286, 'altitude_ft': 400.0, 'true_airspeed_kt': 127.5, 'heading_deg': 90.0, 'vertical_speed_fpm': 3600.0, 'pitch_deg': 8.0, 'roll_deg': 0.81}`

## Attack observation

- Requested: `injection` at `2000 ms`
- Event: `{'type': 'injection', 'legacy': {'source': 'attacker', 'label_octal': '0o203', 'data': 1220, 'raw_word': '0x00131083', 'sequence': None, 'parity_ok': True, 'accepted': True, 'reason': 'accepted'}, 'hardened': {'source': 'attacker', 'label_octal': '0o203', 'data': 1220, 'raw_word': '0x00131083', 'sequence': None, 'parity_ok': True, 'accepted': False, 'reason': 'unauthorized_source'}}`

## Evidence boundary

JSBSim provides the flight-dynamics state only. The ARINC 429 words use
synthetic labels and a local HMAC/sequence/timestamp overlay. This result
does not establish a real aircraft ICD, LRU behavior, electrical waveform
compliance, PX4 MAVLink HIL operation, or airworthiness approval.
