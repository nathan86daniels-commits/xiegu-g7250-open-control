"""G7250 Open Control web server.

    python -m uvicorn server:app --host 127.0.0.1 --port 8000

Settings (environment variables):
    G7250_RADIO_IP      radio address (default 192.168.3.199)
    G7250_BIND_IP       local address to receive IQ on (default 0.0.0.0)
    G7250_SWAP_IQ       1 to swap I and Q if the spectrum is mirrored
    G7250_SIMULATOR     1 to run the built-in pretend radio (LAN IQ + CAT, no G7250 needed)
    G7250_DATA          folder for settings, logbook and recordings (default ./data)
Everything else (callsign, grid, CAT port, cluster, Hamlib) is set in the app.
"""
import asyncio
import itertools
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from g7250 import protocol
from g7250.cat import list_serial_ports
from g7250.station import Station
from swr_sweep import SwrSweep

ROOT = Path(__file__).parent
DATA = Path(os.environ.get("G7250_DATA", ROOT / "data"))

station = Station(
    DATA,
    simulator=os.environ.get("G7250_SIMULATOR") == "1",
    radio_ip=os.environ.get("G7250_RADIO_IP", protocol.DEFAULT_RADIO_IP),
    bind_ip=os.environ.get("G7250_BIND_IP", "0.0.0.0"),
    swap_iq=os.environ.get("G7250_SWAP_IQ") == "1",
)
sweep = SwrSweep(station)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await station.start()
    yield
    await sweep.stop()
    await station.stop()


app = FastAPI(title="G7250 Open Control", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """The UI files change with every update; make browsers revalidate instead of running an old copy."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class FrequencyRequest(BaseModel):
    hz: int = Field(..., ge=protocol.FREQ_MIN_HZ, le=protocol.FREQ_MAX_HZ)


class ControlRequest(BaseModel):
    name: str
    value: object = None


class RecordRequest(BaseModel):
    seconds: float = Field(15, gt=0, le=600)


class CatConnectRequest(BaseModel):
    port: str
    baud: int = 115200
    address: str = "auto"


class QsoRequest(BaseModel):
    call: str
    freq_hz: int = 0
    mode: str = ""
    rst_s: str = ""
    rst_r: str = ""
    name: str = ""
    qth: str = ""
    grid: str = ""
    notes: str = ""
    utc: str | None = None
    stx: str = ""
    srx: str = ""
    contest: str = ""
    region: str = ""


# ---------------------------------------------------------------- radio

@app.get("/api/status")
def get_status():
    return station.status()


@app.post("/api/frequency")
async def set_frequency(req: FrequencyRequest):
    await station.set_frequency(req.hz)
    return {"frequency_hz": station.frequency, "frame": protocol.build_tune_frame(req.hz).hex(" ")}


@app.post("/api/scope/centre")
def scope_centre(req: FrequencyRequest):
    station.pan_scope(req.hz)
    return {"lo_hz": station.link.frequency_hz}


@app.post("/api/control")
async def control(req: ControlRequest):
    try:
        await station.rig_set(req.name, req.value)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:                       # CatTimeout, Unsupported
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    return {"ok": True}


@app.get("/api/spectrum")
def get_spectrum():
    db = station.link.spectrum.latest_db
    if db is None:
        raise HTTPException(503, "No IQ received yet")
    return {"lo_hz": station.link.frequency_hz, "span_hz": protocol.NOMINAL_SAMPLE_RATE,
            "dbfs": [round(float(v), 1) for v in db]}


@app.get("/api/cat/ports")
def cat_ports():
    return list_serial_ports()


@app.post("/api/cat/connect")
async def cat_connect(req: CatConnectRequest):
    ok = await station.connect_cat(req.port, req.baud, req.address)
    if not ok:
        raise HTTPException(502, station.cat_error)
    return station.status()["cat"]


@app.post("/api/cat/disconnect")
async def cat_disconnect():
    await station.disconnect_cat()
    return {"ok": True}


# ---------------------------------------------------------------- IQ recording

RECORDINGS = DATA / "recordings"


@app.post("/api/record/start")
def record_start(req: RecordRequest):
    return {"recording": station.link.start_recording(RECORDINGS, req.seconds).name}


@app.post("/api/record/stop")
def record_stop():
    path = station.link.stop_recording()
    return {"stopped": path.name if path else None}


@app.get("/api/record/download")
def record_download():
    p = station.link.last_recording
    if not p or not p.exists():
        raise HTTPException(404, "No finished recording yet")
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


# ---------------------------------------------------------------- settings, memories

@app.get("/api/settings")
def get_settings():
    return station.settings


@app.post("/api/settings")
def save_settings(changes: dict = Body(...)):
    station.save_settings(changes)
    return station.settings


@app.get("/api/memories")
def get_memories():
    return station.memories


@app.post("/api/memories/{slot}")
def save_memory(slot: int, value: dict | None = Body(None)):
    if not 0 <= slot < len(station.memories):
        raise HTTPException(404, "No such memory")
    station.save_memory(slot, value)
    return station.memories


# ---------------------------------------------------------------- logbook

@app.get("/api/log")
def log_list(search: str = "", limit: int = 200):
    return station.logbook.list(search, limit)


@app.post("/api/log")
async def log_add(q: QsoRequest):
    try:
        row = station.logbook.add(q.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if station.settings["qrz"].get("auto_upload") and station.settings["qrz"].get("logbook_key"):
        asyncio.get_running_loop().create_task(_qrz_upload(row["id"]))
    return row


async def _qrz_upload(qso_id):
    adif = station.logbook.adif_record(qso_id, station.settings["callsign"], station.settings["grid"])
    res = await asyncio.get_running_loop().run_in_executor(None, station.callbook.qrz_upload, adif, station.settings["callsign"])
    station.logbook.set_upload(qso_id, res["outcome"], res.get("logid"))
    if res["outcome"] == "needs_subscription":
        station.save_settings({"qrz": {"auto_upload": False}})
    return res


@app.put("/api/log/{qso_id}")
def log_update(qso_id: int, changes: dict = Body(...)):
    row = station.logbook.update(qso_id, changes)
    if not row:
        raise HTTPException(404, "No such QSO")
    return row


@app.post("/api/log/{qso_id}/qrz")
async def log_qrz(qso_id: int):
    return await _qrz_upload(qso_id)


@app.get("/api/callbook")
async def callbook(call: str, fresh: bool = False):
    from g7250.timezone import station_time
    res = await asyncio.get_running_loop().run_in_executor(None, station.callbook.lookup, call, not fresh)
    rec = res.get("record")
    res["local_time"] = station_time(call, (rec or {}).get("state") or (rec or {}).get("region"), (rec or {}).get("lon"))
    return res


@app.get("/api/log/contests")
def log_contests(contest: str = ""):
    return {"contests": station.logbook.contests(), "next_serial": station.logbook.next_serial(contest) if contest else "001"}


@app.post("/api/log/cabrillo")
def log_cabrillo(body: dict = Body(...)):
    from g7250.callbook import cabrillo
    details = {"callsign": station.settings["callsign"], "grid": station.settings["grid"], **body}
    text = cabrillo(details, station.logbook.all_rows(body.get("contest") or None))
    name = (body.get("contest_id") or "log").replace(" ", "-")
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{name}.cbr"'})


@app.delete("/api/log/{qso_id}")
def log_delete(qso_id: int):
    station.logbook.delete(qso_id)
    return {"ok": True}


@app.get("/api/log/stats")
def log_stats():
    return {**station.logbook.stats(), "points": station.logbook.points()}


@app.get("/api/log/lookup")
def log_lookup(call: str, grid: str = "", lat: float | None = None, lon: float | None = None, region: str | None = None):
    return station.logbook.lookup(call, grid, lat, lon, region)


@app.get("/api/log/export.adi")
def log_export():
    text = station.logbook.export_adif(station.settings["callsign"], station.settings["grid"])
    return PlainTextResponse(text, headers={"Content-Disposition": 'attachment; filename="g7250-log.adi"'})


@app.post("/api/log/import")
async def log_import(request: Request):
    text = (await request.body()).decode("utf-8", "replace")
    return {"imported": station.logbook.import_adif(text)}


# ---------------------------------------------------------------- cluster, propagation

@app.get("/api/cluster")
def cluster_spots():
    return {"status": station.cluster.status, "spots": station.cluster.recent()[:100]}


@app.get("/api/propagation")
def propagation():
    return station.prop.snapshot()


@app.get("/api/muf")
def muf():
    return station.muf.payload()


@app.get("/api/wspr")
async def wspr(hours: int = 24, force: bool = False):
    try:
        return await station.wspr.get(station.settings["callsign"], hours, force)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"wspr.live: {type(exc).__name__}")


@app.get("/api/wspr/live")
def wspr_live():
    return {**station.wspr_service.status(), "decodes": station.wspr_service.decodes[:200],
            "tx_log": station.wspr_service.tx_log[:50]}


@app.post("/api/wspr/start")
async def wspr_start(body: dict = Body({})):
    await station.wspr_service.start(bool(body.get("transmit")))
    return station.wspr_service.status()


@app.post("/api/wspr/stop")
async def wspr_stop():
    await station.wspr_service.stop()
    return station.wspr_service.status()


@app.get("/api/swr/sweep")
def swr_sweep_status():
    return sweep.status()


@app.post("/api/swr/sweep")
async def swr_sweep_start(body: dict = Body(...)):
    """Transmits. The browser asks the operator first and sends confirm: true."""
    if body.get("confirm") is not True:
        raise HTTPException(400, "The sweep transmits: confirm it first.")
    try:
        await sweep.start(str(body.get("band", "")), body["start_hz"], body["stop_hz"], body["step_hz"], body["watts"])
    except (KeyError, TypeError):
        raise HTTPException(400, "The sweep range is not valid.")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return sweep.status()


@app.post("/api/swr/stop")
async def swr_sweep_stop():
    await sweep.stop()
    return sweep.status()


@app.get("/api/audio/outputs")
def audio_outputs():
    from g7250.wspr_service import list_output_devices
    return list_output_devices()


@app.get("/api/cw")
def cw_read(since: int = 0):
    return station.cw.read(since)


@app.post("/api/cw/send")
async def cw_send(body: dict = Body(...)):
    try:
        await station.cw.send(str(body.get("text", "")))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    return {"ok": True}


@app.post("/api/cw/stop")
async def cw_stop():
    try:
        await station.cw.stop_sending()
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}")
    return {"ok": True}


@app.post("/api/cw/clear")
def cw_clear():
    station.cw.clear()
    return {"ok": True}


@app.post("/api/cw/decoder")
def cw_decoder(body: dict = Body(...)):
    if "enabled" in body:
        station.cw.enabled = bool(body["enabled"])
    if "decoder" in body or "delay" in body:
        station.cw.configure(body.get("decoder"), float(body["delay"]) if "delay" in body else None)
    return {"enabled": station.cw.enabled, "decoder": station.cw.decoder_name, "delay": station.cw.delay}


@app.get("/api/log/globe")
def log_globe(period: str = "all", band: str = ""):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    start = {"hour": now - dt.timedelta(hours=1),
             "today": now.replace(hour=0, minute=0, second=0, microsecond=0),
             "week": (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)}.get(period)
    return station.logbook.globe(start.strftime("%Y-%m-%dT%H:%M:%SZ") if start else None, band or None)


# ---------------------------------------------------------------- websockets

_ws_ids = itertools.count(1)


@app.websocket("/ws")
async def main_socket(ws: WebSocket):
    """Server -> browser: JSON status whenever anything changes (and at least once a
    second), binary Float32Array spectrum frames (dBFS, lowest frequency first).
    Browser -> server: {"type":"ptt","on":true|false} and {"type":"beat"} while keyed."""
    await ws.accept()
    owner = f"ui:{next(_ws_ids)}"

    async def receive():
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "ptt":
                if msg.get("on"):
                    await station.guard.key(owner)
                else:
                    await station.guard.release(owner, "PTT released")
            elif msg.get("type") == "beat":
                station.guard.beat(owner)

    async def send():
        last_seq, last_spec, last_sent = -1, -1, 0.0
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            seq = station.state_seq
            if seq != last_seq or now - last_sent > 1.0:
                last_seq, last_sent = seq, now
                await ws.send_text(json.dumps(station.status()))
            spec = station.link.spectrum
            if spec.seq != last_spec and spec.latest_db is not None:
                last_spec = spec.seq
                await ws.send_bytes(spec.latest_db.tobytes())
            await asyncio.sleep(0.04)

    tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await station.guard.release(owner, "browser disconnected")


@app.websocket("/ws/audio")
async def audio_socket(ws: WebSocket):
    """Binary Int16 PCM, 12 kHz mono, demodulated from the IQ stream."""
    await ws.accept()
    q = station.add_audio_listener()
    try:
        while True:
            await ws.send_bytes(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        station.remove_audio_listener(q)


app.mount("/lib", StaticFiles(directory=ROOT / "web" / "lib"), name="lib")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
