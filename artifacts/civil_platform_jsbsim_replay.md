# Civil Aviation Security Platform Experiment

This record was produced by `scripts/run_civil_platform.py`. It is a local
research simulation and does not connect to an aircraft, ARINC 429 interface,
serial port, or network service.

## Run

- Engine: `{'requested': 'jsbsim', 'selected': 'jsbsim', 'aircraft': '737'}`
- Duration / sample step: `30000 ms` / `1000 ms`
- ARINC labels: `0o203, 0o210, 0o211, 0o212` (synthetic project labels)
- Frames: `124` emitted, `124` admitted, `0` rejected
- Rejection reasons: `{}`

## Integration inventory

- JSBSim Python: `True` version `1.3.1`
- BlueSky Python: `False` version `None`
- FlightGear executable: `False`
- PX4 source present: `True` at `/home/starlight/winECode/PX4-Autopilot`
- Network opened: `False`; serial opened: `False`

## Flight-state samples

- First: `{'timestamp_ms': 0, 'phase': 'cruise', 'latitude_deg': 47.1916344, 'longitude_deg': 122.0, 'altitude_ft': 30000.17, 'true_airspeed_kt': 444.36, 'heading_deg': 225.0, 'vertical_speed_fpm': -0.0, 'pitch_deg': 2.28, 'roll_deg': -0.0}`
- Last: `{'timestamp_ms': 30000, 'phase': 'cruise', 'latitude_deg': 47.1481146, 'longitude_deg': 121.9361159, 'altitude_ft': 30017.9, 'true_airspeed_kt': 443.93, 'heading_deg': 225.03, 'vertical_speed_fpm': 71.71, 'pitch_deg': 2.38, 'roll_deg': -0.12}`

## Attack observation

- Requested: `replay` at `20000 ms`
- Event: `{'type': 'replay', 'legacy': {'source': 'attacker', 'label_octal': '0o203', 'data': 30007, 'raw_word': '0x01d4dc83', 'sequence': 77, 'parity_ok': True, 'accepted': True, 'reason': 'accepted'}, 'hardened': {'source': 'air_data_computer', 'label_octal': '0o203', 'data': 30007, 'raw_word': '0x01d4dc83', 'sequence': 77, 'parity_ok': True, 'accepted': False, 'reason': 'replay'}}`

## Evidence boundary

JSBSim provides the flight-dynamics state only. The ARINC 429 words use
synthetic labels and a local HMAC/sequence/timestamp overlay. This result
does not establish a real aircraft ICD, LRU behavior, electrical waveform
compliance, PX4 MAVLink HIL operation, or airworthiness approval.
