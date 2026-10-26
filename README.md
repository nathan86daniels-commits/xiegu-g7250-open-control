# G7250 Open Control

> **Experimental hardware warning:** This project has not yet been tested on a real G7250. It currently runs end to end against the built-in simulator. Do not transmit with a real radio until LAN IQ, CAT, PTT safety, meter scaling, and fail-safe behavior have been verified on actual hardware. Use appropriate RF safety precautions and a suitable dummy load when testing transmission.

A browser control panel for the Xiegu G7250. It takes the radio's LAN IQ stream for the scope and receive audio, and
uses the USB CI-V port for CAT and transmit. The server is Python and runs on Linux, Windows and macOS (Python 3.10+),
including a Raspberry Pi next to the radio. You use it from a browser on a big screen with a mouse.

> **Status: not yet tested on a real G7250.** Everything runs end to end against the built-in simulated radio,
> including simulated CI-V. The LAN side matches what has been seen working on a real radio (see *Protocol*).
> The CI-V side follows Xiegu's Icom-style command set, so expect some commands to need adjusting on real hardware.
> Commands the radio refuses (FA) are recorded and their controls greyed out, not retried.

This project is **AI-assisted**: the code was written with Claude (Anthropic) under human direction.

## What's in it

- **Scope**: 48 kS/s IQ shown as a ±24 kHz spectrum plus waterfall (WebGL2). It has the full-height "Ice glass"
  passband with an opacity slider, a LEVEL control, palettes, and DX cluster spots drawn on the scope. **Drag** the
  passband along the band, click to jump to a signal, or use the mouse wheel. The scope stays still while the passband
  moves. **REC IQ** saves the raw radio packets (`.iq16`).
- **VFO and controls**: mouse wheel on any digit, band buttons, memories, mode, filter, AGC, preamp, attenuator,
  NR, NB, notch, RF gain, squelch, power, mic, compressor, VOX, split and A/B. Most of these need USB CAT.
- **Meters**: S-meter (analog dial and bars), PO, SWR, ALC, COMP and Vdd from CI-V 15h.
- **PTT is fail-safe**: the browser sends a heartbeat while PTT is held, and the radio is unkeyed if the heartbeat stops,
  the CAT link drops, or the maximum TX time is reached.
- **Audio**: LAN receive audio demodulated from the IQ, or the radio's USB audio played on this PC. The mic can be sent
  to the radio's USB audio while PTT is held.
- **CW** (rail button with the dots and dashes):
  - the **Neural** decoder (the default): our own trained CW model (e4), which copies every
    keyed station in the passband, slows very fast CW down for the model, and prints about 1 s behind the audio
    (**DELAY** button: 1 s, 2 s or 10 s, where 10 s is the most accurate). Needs `onnxruntime` (in requirements);
  - a **Classic** DSP decoder as the alternative, also used for the WPM, signal and lock readouts;
  - six memories (F1–F6; right-click a memory to edit it) and free text;
  - sending through the radio's own keyer (CI-V 17h); Esc stops sending;
  - a keyer speed control;
  - `{CALL}` is your callsign and `{DX}` is the callsign in the log entry box.
- **WSPR** (rail button with the radio waves):
  - its own WSPR-2 decoder works on every even-minute slot, straight from the IQ.
  - Transmit plays the WSPR tones into the radio's USB audio, then sets random 1-in-N slots, power and band dial.
  - Transmit stops at once on Stop, PTT release, CAT loss, a mode change on the radio, no power output, or SWR above 3.
  - The radio is always unkeyed before power, mode and frequency are restored.
  - WSPRnet upload is **off** unless you turn it on.
- **Logbook** (SQLite):
  - ADIF import and export; delete with the Delete key; right-click a QSO to edit it.
  - Country and distance work offline (AD1C cty.dat).
  - The **station's local time** includes daylight saving, using the state or province where one is known.
  - **QRZ lookup** fills in the name, QTH and grid; HamQTH is the free fallback, and OpenStreetMap places a QTH that has
    no grid.
  - Optional **QRZ Logbook upload** (off by default).
  - **Contest mode** with serial numbers sent and received, and **Cabrillo 3.0** export.
- **Globe** (one 3D globe for everything):
  - **LOG** shows your QSOs, **DX** shows cluster spots, and **WSPR** shows wspr.live reports plus your own decodes.
  - **MUF** shows the KC2G MUF map with ionosondes and the greyline. Expand it to full size.
- **Hamlib NET rigctl** server on port 4532, for WSJT-X, LOG4OM and fldigi.
  - WSJT-X: Rig "Hamlib NET rigctl", PTT "CAT".
- **Built-in simulated G7250**: IQ with FT8, CW, carriers and two WSPR beacons on 20 m, plus a simulated CI-V radio.
  You can try everything without a radio.

## Run it

```bash
pip install -r requirements.txt

# with the radio (LAN at its default 192.168.3.199, USB CAT picked in the app)
python -m uvicorn server:app --host 0.0.0.0 --port 8000

# without a radio
G7250_SIMULATOR=1 G7250_DATA=data-sim python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>. Then:

1. Click **Connect** in the top right and pick the radio's CAT serial port (on Linux usually `/dev/ttyUSB0` or
   `/dev/ttyACM0`). On Linux, add yourself to the `dialout` group if the port can't be opened.
2. Open **Settings** (the gear icon) and enter your callsign and grid. QRZ, HamQTH and the QRZ Logbook key also go here
   if you want them.

On Windows (cmd), use `set G7250_SIMULATOR=1` first.

### Run the simulator

The built-in simulator needs no G7250 hardware. From the project directory, install the requirements first, then use one of these:

**Linux or macOS**

```bash
G7250_SIMULATOR=1 G7250_DATA=data-sim python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

**Windows Command Prompt**

```bat
set G7250_SIMULATOR=1
set G7250_DATA=data-sim
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

**Windows PowerShell**

```powershell
$env:G7250_SIMULATOR = "1"
$env:G7250_DATA = "data-sim"
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000>. The simulator creates its local settings and logbook folder automatically; no radio, CAT cable, or credentials are required.

| Setting | Default | Meaning |
|---|---|---|
| `G7250_RADIO_IP` | `192.168.3.199` | Radio address |
| `G7250_BIND_IP` | `0.0.0.0` | Local address that receives IQ (UDP 50001) |
| `G7250_SWAP_IQ` | off | Set to `1` if the spectrum looks mirrored |
| `G7250_DATA` | `data` | Folder for settings, logbook, recordings and caches |
| `G7250_SIMULATOR` | off | Set to `1` to use the built-in pretend radio |

Firewall: allow inbound **UDP 50001** from the radio. **Don't expose port 8000 to the internet.** It controls a
transmitter, and Settings holds your QRZ login.

### WSPR transmit checklist

- The radio must take its transmit audio from **USB** (radio menu), and **Audio out to radio** in the WSPR panel must
  be the radio's USB sound card. On Linux this comes from PortAudio via `sounddevice` (`sudo apt install libportaudio2`).
- Your callsign and grid must be set. A compound callsign (with a `/`) or a 6-character grid is sent as the hashed
  two-message pair.
- The PC clock must be accurate to within about 1 second (use NTP).
- Watch the first transmission. On real hardware, check power, SWR and that the radio unkeys at the end.

## Things to check on a real G7250

Before connecting hardware, read the [real-radio calibration and validation guide](REAL-RADIO-CALIBRATION.md).

- **I/Q orientation**: a signal above the dial should appear to the right. If it doesn't, set `G7250_SWAP_IQ=1`.
- **CW tuning**: CW is received with the dial on the signal's carrier and the tone at the pitch. Compare with the
  radio's own display.
- **Scope tuning**: the IQ centre stays still while you tune, drag or wheel inside the ±24 kHz window, and the
  passband moves across the scope. It re-centres about 19 kHz from the middle. This assumes the radio only moves its
  IQ centre when it gets the LAN tune command. If changing the VFO by CAT also moves the IQ centre, the passband will
  sit off its signals; set `"scope": {"centred": true}` in `data/settings.json` to always keep the VFO centred.
- **CI-V address**: Auto tries A4h, 88h and 70h. Any command refused with FA appears in the CAT dialog.
- **Keyer speed (14h 0Ch)**: the 0–255 to 6–48 wpm mapping is the Icom convention. Check it against the radio's menu.
- **Meter scaling** (S, PO, SWR, ALC): the values follow Icom's tables and may need calibrating against the radio.

## Protocol (LAN)

Xiegu has not published the LAN protocol. Sources are marked:
**[HW]** seen working on a real G7250;
**[DLL]** read-only static analysis of Xiegu's `ExtIO_G7250.dll`.

- UDP only. The radio listens on **50002**, the PC receives IQ on **50001**, and the radio's default IP is
  **192.168.3.199**. [HW]
- **Tune** (14 bytes, no reply): `AA 55 55 AA | 00 01 | frequency in Hz, uint64 big-endian`.
  14.074 MHz = `AA 55 55 AA 00 01 00 00 00 00 00 D6 C0 90`. [HW] Clamped to 100 kHz – 54 MHz. [DLL]
- **IQ**: 768-byte payloads, about 250 per second, each 192 × (int16 I, int16 Q) = **48 kS/s**. [HW]
  Little-endian is assumed.
- The LAN link carries no PTT, mode or CAT; all of that goes over USB CI-V.

## Layout

```
server.py               FastAPI app: REST, WebSocket, static UI (API docs at /docs)
g7250/protocol.py       LAN frames and IQ decoding
g7250/radio.py          UDP link: tune, receive, rate stats, IQ recording
g7250/dsp.py            spectrum FFT
g7250/demod.py          IQ -> audio (SSB/CW/AM/FM)
g7250/civ.py, cat.py    CI-V framing, CAT link, PTT guard, meter conversions
g7250/rigctl.py         Hamlib NET rigctl server
g7250/station.py        ties radio, CAT, audio, CW, WSPR and logbook together
g7250/neural_cw.py      Neural CW decoder (model in g7250/cw_model/);  cw_decoder.py: Classic decoder
g7250/cw_service.py     decoder feed + keyer send
g7250/wspr_codec.py     WSPR-2 encoder and decoder;  wspr_service.py: slots, transmit safety, WSPRnet
g7250/logbook.py        SQLite logbook, ADIF
g7250/callbook.py       QRZ / HamQTH lookup, QRZ Logbook upload, Cabrillo
g7250/timezone.py       the other station's local time
g7250/geo.py            Maidenhead, distance/bearing, cty.dat
g7250/cluster.py, propagation.py, muf.py, wspr.py   DX cluster, solar data, KC2G MUF, wspr.live
g7250/simulator.py, sim_civ.py                      pretend G7250 (IQ and CI-V)
web/                    browser UI (plain JS, no build step)
```

## Data and credits

- Country data: **AD1C** cty.dat.
- Neural CW model: our own (e4), included under the same terms as this project.
- MUF map, foF2 and ionosondes: **KC2G** (prop.kc2g.com), using GIRO data.
- Solar data: **N0NBH** (hamqsl.com).
- WSPR reports: **wspr.live**.
- Callbook lookups: **QRZ.com** and **HamQTH**.
- Geocoding: **OpenStreetMap Nominatim**.
- Map outlines: **Natural Earth** via world-atlas.
