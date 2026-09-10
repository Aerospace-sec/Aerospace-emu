# Civil Aviation Security Simulation Platform

This directory documents the local composite testbed. The executable
implementation is in `sim/civil_aviation_platform.py` and the command-line
runner is `scripts/run_civil_platform.py`.

## Architecture

```text
CivilScenario / JSBSim adapter
              |
              v
     AircraftState stream
              |
              v
   ARINC 429 sensor publisher
              |
              v
   SecureGateway / LegacyReceiver
              |
              v
        JSON evidence trace
```

The ARINC 429 security platform is intentionally local and deterministic. It
does not open a socket, serial port, ARINC 429 interface card, FPGA
transceiver, or aircraft system. PX4 + JSBSim software co-simulation is a
separate explicit runner described below and uses only `127.0.0.1`.

## Run

Use the built-in deterministic model:

```bash
python3 scripts/run_civil_platform.py \
  --engine kinematic \
  --duration-s 180 \
  --step-ms 1000 \
  --output artifacts/civil_platform_results.json
```

Run an authorized-lab attack scenario:

```bash
python3 scripts/run_civil_platform.py --attack injection
python3 scripts/run_civil_platform.py --attack replay
python3 scripts/run_civil_platform.py --attack tamper
```

`--engine auto` tries the JSBSim Python binding and records a fallback to the
kinematic model when the binding or aircraft data is unavailable. `--engine
jsbsim` fails explicitly in that case. The default JSBSim model is the open
`737` cruise data set and applies longitudinal trim; this is a research model and not a certified aircraft model.
Select another installed JSBSim model with `--aircraft`. The optional PX4
source path is only reported as an integration target:

```bash
python3 scripts/run_civil_platform.py \
  --px4-root /home/starlight/winECode/PX4-Autopilot
```

Each run writes both JSON data and a Markdown evidence record. Use
`--markdown-output` to select the latter path explicitly.

## PX4 + JSBSim software co-simulation

The repository includes `scripts/run_px4_jsbsim_hil.py`. It starts a PX4
POSIX/SITL process, accepts its `simulator_mavlink` TCP connection on
`127.0.0.1:4560`, maps the JSBSim `737` state to `HIL_SENSOR` at 250 Hz and
`HIL_GPS` at 50 Hz, and records `HIL_ACTUATOR_CONTROLS` feedback.

Prerequisites are a PX4 SITL build at `/tmp/px4-sitl-build-full/bin/px4`, its
rootfs at `/tmp/px4-sitl-build-full`, JSBSim aircraft data at
`/tmp/civil_aviation_jsbsim_deps/jsbsim`, and `pymavlink` available through the
PX4 MAVLink submodule path. Run:

```bash
python3 scripts/run_px4_jsbsim_hil.py \
  --duration-s 8 \
  --output artifacts/px4_jsbsim_hil_results.json
```

The runner writes JSON and Markdown evidence. A successful local run recorded
2,000 `HIL_SENSOR` messages, 400 `HIL_GPS` messages, 799 actuator feedback
messages, one heartbeat, and PX4 return code 0. The PX4 airframe is only a
fixed-wing controller configuration; the JSBSim 737 data is a research model.
This is software co-simulation, not physical flight-controller HITL. Real
ARINC 429 HIL still requires an approved interface card or FPGA/electrical
transceiver and a target LRU/ICD test bench.

## Virtual hardware lab

To exercise the hardware boundary before hardware is available, run:

```bash
PYTHONPATH=/tmp/civil_aviation_jsbsim_deps \
python3 scripts/run_virtual_hardware_lab.py \
  --engine jsbsim --duration-s 2 --step-ms 20 --attack none \
  --output artifacts/virtual_hardware_lab_results.json
```

The model names and digital parameters are explicit: `V429-LAB-2T4R` has two
TX channels, four RX channels, 100 kbps, 32-bit words, a four-bit interword
gap, a 64-word TX FIFO, and loopback enabled; `SYNTHETIC_AIR_DATA_LRU`
consumes the project's four synthetic labels; `PX4-FMU-VIRTUAL` accepts HIL
sensor/GPS payloads, has sensor/GPS watchdogs, rejects arming, and returns
zero actuator controls. The virtual card can exercise parity faults, drops,
FIFO overflow, rate mismatch, and digital wire timing. The lab runner also
supports `injection`, `replay`, `tamper`, `flood`, and `rate_mismatch` scenarios.

The successful 2-second JSBSim run emitted and admitted 404 words, delivered
all 404 to the virtual LRU, fed the virtual board, and produced 101 actuator
feedback samples while remaining disarmed. The attack evidence is stored in
`artifacts/virtual_hardware_lab_*.json` and `.md`. These results prove the
software boundary and digital state machine only; they do not model voltage,
impedance, termination, EMI, MCU interrupts, PWM, vendor SDK behavior, a real
LRU, or aircraft airworthiness.

Project sources used for the composition are [JSBSim](https://github.com/JSBSim-Team/jsbsim),
[BlueSky](https://github.com/TUDelft-CNS-ATM/bluesky),
[FlightGear](https://github.com/FlightGear/flightgear),
[OpenAP](https://github.com/junzis/openap), and
[PX4 Autopilot](https://github.com/PX4/PX4-Autopilot). Their roles are
different; no claim is made that any one of them is a certified civil-aircraft
avionics or airworthiness platform.

## Integration boundary

- JSBSim is the optional flight-dynamics engine.
- FlightGear can be added as a visualization and cockpit process through a
  separate adapter.
- BlueSky/OpenAP can provide air-traffic and civil-aircraft performance
  scenarios through a separate co-simulation adapter.
- ARINC 429 physical HIL requires an approved interface card or FPGA/electrical
  transceiver. The repository currently models words and security decisions,
  not ARINC 429 voltage waveforms.
- PX4 HIL consumes MAVLink HIL messages. It has no native ARINC 429 driver, so
  a controlled ARINC 429-to-semantic-data-to-MAVLink gateway is required.

The synthetic labels in this testbed are not claims about any aircraft's ICD.
For a report tied to a real aircraft, replace them with values from the
approved ICD, label allocation, timing budget, and system safety assessment.

## RS-232/RS-422 serial security lab

The independent serial lab is implemented in `sim/serial_lab.py` and run with
`scripts/run_serial_experiments.py`:

```bash
python3 scripts/run_serial_experiments.py
```

It models a synthetic `RS422-AVIONICS-LRU` profile (`TIA-422-B`, 115,200
bit/s, 8N1, two independent differential pairs) and a synthetic
`RS232-GSE-MAINT` profile (`TIA-232-F`, 115,200 bit/s, 8N1, one TX/RX pair).
The application frame is explicitly synthetic:
`SYNC|VERSION|TYPE|SEQUENCE|LENGTH|JSON_PAYLOAD|CRC16`. The native receiver
checks framing and CRC only. The secure gateway adds source, role, channel,
message-type binding, HMAC, timestamp, sequence, semantic policy, ground
state, maintenance-session, and rate controls before allowing native output.

The experiment records normal full-duplex traffic, passive capture, direct
injection, recomputed-CRC tampering, replay, airborne maintenance commands,
9,600/115,200 baud mismatch, and a 64-frame serial flood. It is a digital
model: no `/dev/tty`, voltage, common-mode, termination, EMI/ESD, cable fault,
USB-UART hardware, real LRU, or aircraft interface is opened.

## 5G-ATG security-state lab

The 5G-ATG model is implemented in `sim/atg5g_lab.py` and run with
`scripts/run_atg5g_experiments.py`:

```bash
python3 scripts/run_atg5g_experiments.py
```

It represents a civil-aviation 5GS deployment profile rather than a separate
universal ATG protocol:

```text
aircraft 5G UE -> ATG NR gNB -> AMF/SMF/UPF dedicated core -> N6 ground app
                                      |                       |
                         N3 GTP-U / DNN / S-NSSAI       cabin/avionics boundary
                                      |
                                  OAM/OSS boundary
```

The scenarios cover trusted registration, digital Uu/RRC/NAS interference,
pre-security broadcast visibility and rogue-cell rejection, registration
signaling pressure, N3/N6 payload tampering and replay, TEID/QFI session
binding, cabin-to-avionics injection, passive metadata/payload observation,
and OAM route-policy changes. All identifiers, PLMN, DNN, slice, QFI, TEID,
UE and application messages are synthetic project data.

The lab is local and deterministic. It does not transmit RF, create a real
cell, operate a gNB/5GC, use a SIM/eSIM, capture IMSI/SUPI, open sockets, or
connect to an aircraft or operator network. The results prove digital
security-state and boundary behavior only. Real RF, gNB/5GC interoperability,
aircraft ICD, LRU, flight-phase mobility, and airworthiness evidence require a
separately authorized test bench.

Traceability and reverse verification are part of the 5G-ATG deliverable:
`report/5g_atg_traceability.json` registers external sources, quantitative
claims, scenario conclusions, local evidence and audit steps;
`report/5g_atg_traceability_matrix.md` is the reviewer-facing matrix. Run
`scripts/verify_atg5g_evidence.py` after regenerating the experiment output. It
re-runs the model, compares the fresh result with the archived JSON, resolves
JSON paths and source anchors, runs the focused tests, and writes a SHA-256
provenance manifest to `artifacts/5g_atg_provenance.json`.
