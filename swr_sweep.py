"""SWR band sweep.

Measures SWR across a frequency range by transmitting a low-power CW carrier and reading the radio's
PO and SWR meters at each step. It transmits, so every part of it is bounded:

- it keys only through the station's PTT guard (owner "swr"), so it cannot start while anything else
  transmits, and the guard's maximum TX time still applies;
- one key-down holds at most MAX_CARRIER_S, then the carrier drops for CARRIER_BREAK_S;
- it stops at once on an SWR of ABORT_SWR or more (no antenna, a fault);
- a step with no measurable power is skipped, never plotted as a perfect 1:1;
- the radio's frequency, mode, power and tuner are put back afterwards, each restore guarded so one
  failure can never leave the radio keyed or on the wrong frequency, also when stopped or on error.

Readings are sent raw (0-255) as well as converted, so the browser can apply the DevTools calibration.
"""
import asyncio
import statistics

from g7250.cat import PRI_USER, po_watts, swr_value

ABORT_SWR = 5.0
MIN_POWER_RAW = 3            # below this the radio is not really producing a carrier
SETTLE_S = 0.12              # after each frequency change
SAMPLES, SAMPLE_GAP_S = 5, 0.09
MAX_CARRIER_S = 25.0
CARRIER_BREAK_S = 0.5
MAX_POINTS = 2000
LOWEST_HZ, HIGHEST_HZ = 1_000_000, 54_000_000


class SwrSweep:
    def __init__(self, station):
        self.st = station
        self.task = None
        self.points = []
        self.state = {"running": False, "band": "", "start_hz": 0, "stop_hz": 0, "message": "Ready. The sweep transmits a low-power carrier."}

    def status(self):
        return {**self.state, "points": self.points}

    async def start(self, band, start_hz, stop_hz, step_hz, watts):
        if self.task and not self.task.done():
            raise RuntimeError("A sweep is already running.")
        start_hz, stop_hz, step_hz, watts = int(start_hz), int(stop_hz), int(step_hz), int(watts)
        if step_hz <= 0 or stop_hz <= start_hz:
            raise ValueError("The sweep range is not valid.")
        if start_hz < LOWEST_HZ or stop_hz > HIGHEST_HZ:
            raise ValueError("The range is 1 to 54 MHz.")
        if (stop_hz - start_hz) // step_hz + 1 > MAX_POINTS:
            raise ValueError(f"That is more than {MAX_POINTS} steps. Widen the step or narrow the range.")
        if not 1 <= watts <= 30:
            raise ValueError("Sweep power is 1 to 30 W.")
        if not self.st.cat_connected:
            raise RuntimeError("Connect the radio with USB CAT first.")
        if self.st.guard.owner or self.st.ptt:
            raise RuntimeError("The radio is already transmitting.")
        self.points = []
        self.state = {"running": True, "band": band, "start_hz": start_hz, "stop_hz": stop_hz, "message": "Sweeping…"}
        self.task = asyncio.create_task(self._run(start_hz, stop_hz, step_hz, watts))

    async def stop(self):
        if self.task and not self.task.done():
            self.state["message"] = "Stopping…"
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass

    async def _meters(self, cat):
        await cat.read_meter("po_raw", PRI_USER)
        await cat.read_meter("swr_raw", PRI_USER)
        return cat.state.get("po_raw") or 0, cat.state.get("swr_raw") or 0

    async def _run(self, start_hz, stop_hz, step_hz, watts):
        st, cat = self.st, self.st.cat
        orig = {"freq": st.frequency, "mode": st.mode, "power": cat.state.get("power"), "atu": cat.state.get("atu")}
        error, skipped, keyed = None, 0, False

        async def key():
            nonlocal keyed
            if not await st.guard.key("swr", heartbeat=False):
                raise RuntimeError("The radio would not key.")
            keyed = True

        async def unkey():
            nonlocal keyed
            if keyed:
                await st.guard.release("swr", "SWR sweep")
                keyed = False

        try:
            # CW gives a steady carrier; the ATU is bypassed so the reading is the antenna itself.
            await st.set_mode("CW")
            await cat.set_atu(False)
            await cat.set_level("power", round(watts / 100 * 255))
            await cat.set_freq(start_hz)
            await asyncio.sleep(SETTLE_S)
            point_s = SETTLE_S + SAMPLES * SAMPLE_GAP_S
            per_carrier = max(1, int(MAX_CARRIER_S / point_s))
            in_carrier = 0
            await key()
            hz = start_hz
            while hz <= stop_hz:
                if hz != start_hz:
                    await cat.set_freq(hz)
                    await asyncio.sleep(SETTLE_S)
                if not st.ptt:
                    raise RuntimeError("Transmit was released.")
                # a single early read often lands on zero; sample across the key-down, keep the median
                power_raw, highest, samples = 0, 0, []
                for _ in range(SAMPLES):
                    await asyncio.sleep(SAMPLE_GAP_S)
                    po, swr = await self._meters(cat)
                    if po <= MIN_POWER_RAW:
                        continue
                    power_raw, highest = max(power_raw, po), max(highest, swr)
                    samples.append(swr)
                if power_raw <= MIN_POWER_RAW:
                    skipped += 1                 # no carrier measured: skip rather than invent a 1:1
                else:
                    raw = round(statistics.median(samples))
                    point = {"hz": hz, "swr": round(swr_value(raw), 3), "raw": raw, "po_raw": power_raw, "watts": round(po_watts(power_raw), 1)}
                    self.points.append(point)
                    self.state["message"] = f"{hz / 1e6:.3f} MHz   SWR {point['swr']:.2f}:1"
                    if swr_value(highest) >= ABORT_SWR:
                        error = f"Stopped at {hz / 1e6:.3f} MHz: SWR {point['swr']:.1f}:1 or higher. Check the antenna."
                        break
                hz += step_hz
                in_carrier += 1
                if in_carrier >= per_carrier and hz <= stop_hz:
                    await unkey()                # never one long carrier: a short break every 25 s
                    in_carrier = 0
                    await asyncio.sleep(CARRIER_BREAK_S)
                    await cat.set_freq(hz)
                    await asyncio.sleep(SETTLE_S)
                    await key()
            await unkey()
            if error is None and not self.points:
                error = "No readings: the radio produced no measurable power. Raise the sweep power and check the antenna is connected."
            elif error is None and skipped:
                error = f"{skipped} of {skipped + len(self.points)} steps produced no power and were skipped."
        except asyncio.CancelledError:
            error = "Sweep stopped."
        except Exception as exc:
            error = f"Sweep stopped: {exc}"
        finally:
            # unkey first, then restore in a fixed order, each step guarded
            for step in (lambda: st.guard.release("swr", "SWR sweep ended") if st.guard.owner == "swr" else None,
                         lambda: st.set_mode(orig["mode"]) if orig["mode"] else None,
                         lambda: cat.set_freq(orig["freq"]) if orig["freq"] else None,
                         lambda: cat.set_level("power", orig["power"]) if orig["power"] is not None else None,
                         lambda: cat.set_atu(True) if orig["atu"] else None):
                try:
                    coro = step()
                    if coro:
                        await coro
                except Exception:
                    pass
            self.state["running"] = False
            self.state["message"] = self._describe(error)

    def _describe(self, error):
        if not self.points:
            return error or "No readings."
        best = min(self.points, key=lambda p: p["swr"])
        text = f"Best {best['swr']:.2f}:1 at {best['hz'] / 1e6:.3f} MHz"
        return f"{error}   {text}" if error else text
