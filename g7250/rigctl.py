"""Hamlib NET rigctl server, so WSJT-X, JTDX, LOG4OM etc. can share the radio.

In WSJT-X: Rig = "Hamlib NET rigctl", Network Server = 127.0.0.1:4532, PTT = CAT.
In LOG4OM 2: Hamlib, tick "Connect to active HAMLIB instance", 127.0.0.1 port 4532.

Reads are answered from the station's cached state (no radio round trip); sets go to the
radio. Lessons carried over from real-world use with Hamlib 4.7:
  * \\get_lock_mode must answer "0" + "RPRT 0", or netrigctl stalls ~20 s on mode changes.
  * LOG4OM opens with ";V ?" and polls "+\\get_vfo_info VFOA"; it needs the labelled form.
  * vfo-mode clients put a VFO token before values ("F VFOA 7074000", "T VFOA 3").
  * \\dump_state must be protocol version 1 with ptt_type=1 so WSJT-X keys over CAT
    instead of trying to open a serial PTT port.
"""
import asyncio
import itertools

HAMLIB_MODES = {"USB": "USB", "LSB": "LSB", "CW": "CW", "CW-R": "CWR", "AM": "AM", "FM": "FM",
                "RTTY": "RTTY", "RTTY-R": "RTTYR", "DIG": "PKTUSB"}
FROM_HAMLIB = {"USB": "USB", "LSB": "LSB", "CW": "CW", "CWR": "CW-R", "AM": "AM", "FM": "FM",
               "RTTY": "RTTY", "RTTYR": "RTTY-R", "PKTUSB": "DIG", "PKTLSB": "LSB", "DIG": "DIG",
               "DATA-U": "DIG", "PKTFM": "FM"}
VFO_TOKENS = {"VFOA", "VFOB", "CURRVFO", "MAIN", "SUB", "TX", "RX"}
CURRENT_VFO_TOKENS = {"VFOA", "CURRVFO", "MAIN"}

DUMP_STATE = (
    "1\n2\n1\n"
    "30000 3000000000 0xffffffffffffffff -1 -1 0x3 0xffffffff\n"
    "0 0 0 0 0 0 0\n"
    "30000 3000000000 0xffffffffffffffff 5 100000 0x3 0xffffffff\n"
    "0 0 0 0 0 0 0\n"
    "0 0\n0 0\n"
    "0\n0\n0\n0\n"
    "0 0 0 0 0 0 0\n0 0 0 0 0 0 0\n"
    "0\n0\n0\n0\n0\n0\n"
    "ptt_type=1\n"
    "done\n"
)


class RigctlServer:
    def __init__(self, station, port=4532, host="127.0.0.1"):
        self.station = station
        self.port, self.host = port, host
        self._server = None
        self._ids = itertools.count(1)
        self.clients = 0

    async def start(self):
        self._server = await asyncio.start_server(self._client, self.host, self.port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _client(self, reader, writer):
        owner = f"rigctl:{next(self._ids)}"
        self.clients += 1
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("ascii", "replace").strip()
                if len(text) > 1024:
                    break
                if text in ("q", "\\quit"):
                    break
                writer.write((await self.execute(text, owner)).encode("ascii"))
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.clients -= 1
            await self.station.guard.release(owner, "Hamlib client disconnected")
            writer.close()

    async def execute(self, line, owner):
        st = self.station
        parts = line.split()
        if not parts:
            return "RPRT -1\n"
        cmd, extended = parts[0], None
        if cmd[0] in "+;|,":
            extended, cmd = cmd[0], cmd[1:]
        if cmd in ("F", "\\set_freq", "M", "\\set_mode") and len(parts) >= 3 and parts[1].upper() in CURRENT_VFO_TOKENS:
            parts = [parts[0]] + parts[2:]
        try:
            if cmd in ("f", "\\get_freq"):
                return f"{st.frequency or 0}\n"
            if cmd in ("F", "\\set_freq"):
                hz = int(float(parts[1]))
                if hz <= 0:
                    return "RPRT -1\n"
                await st.set_frequency(hz)
                return "RPRT 0\n"
            if cmd in ("m", "\\get_mode"):
                return f"{HAMLIB_MODES.get(st.mode, 'USB')}\n{st.passband_width}\n"
            if cmd in ("M", "\\set_mode"):
                mode = FROM_HAMLIB.get(parts[1].upper())
                if not mode:
                    return "RPRT -1\n"
                await st.set_mode(mode)
                width = int(float(parts[2])) if len(parts) >= 3 else 0
                if width > 0 and mode in ("USB", "LSB", "DIG", "RTTY"):      # 0 / -1 = leave as is
                    await st.rig_set("passband", [st.demod.low, st.demod.low + width])
                return "RPRT 0\n"
            if cmd in ("t", "\\get_ptt"):
                return f"{int(st.ptt)}\n"
            if cmd in ("T", "\\set_ptt"):
                value = parts[1] if len(parts) == 2 else parts[2] if len(parts) == 3 and parts[1].upper() in VFO_TOKENS else None
                if value not in ("0", "1", "2", "3"):
                    return "RPRT -1\n"
                if value == "0":
                    await st.guard.release(owner, "Hamlib PTT off")
                    return "RPRT 0\n"
                return "RPRT 0\n" if await st.guard.key(owner, heartbeat=False) else "RPRT -9\n"
            if cmd in ("v", "\\get_vfo"):
                return "VFOA\n"
            if cmd in ("V", "\\set_vfo"):
                if len(parts) == 2 and parts[1] == "?":
                    if extended is None:
                        return "VFOA VFOB \nRPRT 0\n"
                    sep = "\n" if extended == "+" else extended
                    return f"set_vfo: currVFO:?{sep}VFOA VFOB \nRPRT 0\n"
                return "RPRT 0\n" if len(parts) >= 2 and parts[1].upper() == "VFOA" else "RPRT -11\n"
            if cmd == "\\get_vfo_info":
                vfo = parts[1] if len(parts) >= 2 else "currVFO"
                b = vfo.upper() in ("VFOB", "SUB")
                hz = (st.freq_other if b else st.frequency) or 0
                values = [str(hz), HAMLIB_MODES.get(st.mode, "USB"), str(st.passband_width), str(int(st.split)), "0"]
                if extended is None:
                    return "\n".join(values) + "\n"
                sep = "\n" if extended == "+" else extended
                labels = ["Freq", "Mode", "Width", "Split", "SatMode"]
                return f"get_vfo_info: currVFO {vfo}{sep}" + sep.join(f"{l}: {v}" for l, v in zip(labels, values)) + f"{sep}RPRT 0\n"
            if cmd in ("s", "\\get_split_vfo"):
                return f"{int(st.split)}\nVFOB\n"
            if cmd in ("S", "\\set_split_vfo"):
                await st.set_split(parts[1] == "1")
                return "RPRT 0\n"
            if cmd in ("i", "\\get_split_freq"):
                return f"{st.freq_other or 0}\n"
            if cmd in ("I", "\\set_split_freq"):
                await st.set_other_frequency(int(float(parts[-1])))
                return "RPRT 0\n"
            if cmd in ("l", "\\get_level") and len(parts) >= 2 and parts[-1].upper() == "STRENGTH":
                return f"{round(st.s_over_s9)}\n"
            if cmd == "\\chk_vfo":
                return "CHKVFO 0\n"
            if cmd == "\\get_powerstat":
                return "1\n"
            if cmd == "\\get_lock_mode":
                return "0\nRPRT 0\n"
            if cmd == "\\set_lock_mode":
                return "RPRT 0\n"
            if cmd == "\\dump_state":
                return DUMP_STATE
        except (IndexError, ValueError):
            return "RPRT -1\n"
        except Exception:
            return "RPRT -5\n"
        return "RPRT -11\n"
