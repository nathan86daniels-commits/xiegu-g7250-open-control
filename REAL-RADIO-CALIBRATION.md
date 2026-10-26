# Real-radio calibration and validation

> **Important:** This project has not yet been tested on a real G7250. Treat the first hardware session as an experimental validation session. Do not transmit into an antenna while testing. Use an appropriate dummy load, follow the radio manual and local regulations, and start at low power.

This guide covers two separate jobs:

1. Confirm that the LAN IQ stream, CAT link, scope and controls behave correctly.
2. Calibrate the displayed meters against known readings from the radio and external test equipment.

## 1. Receive-only setup

1. Put the radio and computer on the same LAN and note the radio's IP address.
2. Start the application with `G7250_RADIO_IP` set if the default address is not correct.
3. Connect CAT in the app and confirm that frequency, mode and receive status update.
4. Confirm that the scope is receiving IQ before attempting any transmit feature.
5. Tune to a known signal or a calibrated RF signal source.

Check the following:

- A signal above the tuned frequency appears on the correct side of the scope. If the spectrum is mirrored, try `G7250_SWAP_IQ=1`.
- The frequency readout, CAT frequency and scope ruler agree.
- Changing frequency within the IQ window moves the passband without losing the IQ window.
- If CAT tuning also moves the radio's IQ centre, set `"centred": true` in `data/settings.json` and repeat the check.
- Mode, filter width, shift, audio and squelch behave as expected.

If the scope's carrier or passband is consistently offset, open **DevTools** and adjust **Carrier line / passband, Hz**. This is a display offset, not a radio frequency correction.

## 2. Meter calibration

Open **DevTools** and use the live raw reading shown beside each group. Read the same value from the radio or an external calibrated instrument, then move the corresponding slider until the app agrees.

| Group | Reference points |
| --- | --- |
| S meter | Radio reading at S9 and S9+60; use the IQ S offset only when operating without CAT. |
| PO | Known forward power, commonly 50 W and 100 W if those points are supported by the radio and test equipment. Never force a radio beyond its rated output. |
| SWR | Known 1.5:1, 2:1 and 3:1 loads or a calibrated reference meter. Do not create a high-SWR test by deliberately mismatching an antenna. |
| ALC | A known 100% ALC indication using a controlled audio input. |
| COMP | Known 15 dB and 30 dB compressor indications, where available. |
| VDD | Known supply-voltage readings, such as 10 V and 16 V, only when those points are available safely. Do not overvoltage the radio to create a calibration point. |
| IDD | Known current readings, such as 10 A, 15 A and 25 A, from a suitable external current measurement. Do not force excessive current. |

The values are stored in this browser's local storage, not in the repository. **COPY VALUES** exports them as JSON and **LOAD VALUES** imports them on another browser. Do not include station credentials, callsigns, grids, logbooks or recordings in an issue or pull request.

## 3. Transmit validation

Only continue after receive-only checks pass.

1. Connect a suitable dummy load and an external power/SWR meter.
2. Start at the lowest practical power and use a short test transmission.
3. Compare the radio's forward power, SWR, ALC, compressor, supply voltage and current readings with the app.
4. Test PTT release, browser disconnect, CAT loss and the maximum-TX-time limit. The radio must unkey in every case.
5. Test the SWR sweep only into a suitable load. It transmits a low-power CW carrier across the selected range and must never be started on an unknown antenna.
6. Stop immediately if power, SWR, frequency, mode or unkey behaviour is unexpected.

Record the radio model, firmware version, test equipment, frequencies, power levels and raw readings used. Keep hardware-specific calibration JSON separate from the public source unless it contains no private station information.

## 4. Reporting a hardware result

When reporting results, state whether the test used the simulator or a real radio, include the firmware version, and describe the equipment and safe load used. Report observed behaviour separately from assumptions about the LAN or CI-V protocol.
