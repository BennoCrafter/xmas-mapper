"""Web UI for the light and the LED detector: python main.py ui [--reload] [--host 0.0.0.0] [--port 8000]

Config via env: XMAS_DATA (job folder, default ./data), XMAS_LIGHT_NAME / XMAS_LIGHT_ADDRESS.
To add a page (e.g. the 3D point cloud): a <section data-view> + nav link in web.html, API routes below.
"""

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from bleak import BleakScanner
from bleak.exc import BleakError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from govee_strip import OFF, TOTAL_LEDS, GoveeStrip
from led_detector import EXTS, decode_ids, save_frame, track, viewer_frame

SRC = Path(__file__).parent
JOBS = Path(os.environ.get("XMAS_DATA", SRC.parent / "data")) / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
BOOT = str(time.time())
running: set[str] = set()  # job ids with a live thread in this process


def error(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status)


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")  # write + rename: the UI polls while jobs write
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


# ---------- detector jobs ----------
def job_dir(job_id: str) -> Path | None:
    folder = JOBS / job_id
    return folder if folder.resolve().parent == JOBS.resolve() and (folder / "job.json").is_file() else None


def job_info(folder: Path) -> dict:
    info = json.loads((folder / "job.json").read_text())
    if info["state"] == "running" and folder.name not in running:
        info["state"], info["error"] = "failed", "interrupted by a server restart"
    return info | {"id": folder.name}


def label(folder: Path, shown: list[dict]) -> int:
    """Decodes LED ids into the viewer frames and saves them; returns how many tracks decoded."""
    seen = [{led["id"]: (led["x"], led["y"], tuple(led["rgb"])) for led in f["leds"]} for f in shown]
    decoded = len(decode_ids(seen, shown))
    write_json(folder / "frames.json", shown)
    return decoded


def run_job(folder: Path, source: Path, info: dict) -> None:
    shown, ids = [], set()
    try:
        for name, image, stages, detections, leds in track(str(source), keep_stages=True, **info["opts"]):
            shown.append(viewer_frame(name, image, save_frame(folder, len(shown), stages), detections, leds))
            ids |= leds.keys()
            info.update(frames=len(shown), leds=len(ids))
            write_json(folder / "job.json", info)
        info["decoded"] = label(folder, shown)
        info["state"] = "done"
    except (Exception, SystemExit) as e:  # frames() exits on unreadable input
        info.update(state="failed", error=str(e))
    finally:
        write_json(folder / "job.json", info)
        running.discard(folder.name)


async def list_jobs(request: Request) -> JSONResponse:
    folders = sorted((f for f in JOBS.iterdir() if (f / "job.json").is_file()), reverse=True)
    return JSONResponse([job_info(f) for f in folders])


async def create_job(request: Request) -> JSONResponse:
    form = await request.form(max_files=5000)
    try:
        opts = dict(
            min_area=int(form.get("min_area") or 50),
            max_distance=float(form.get("max_distance") or 30),
            min_hits=int(form.get("min_hits") or 3),
            fps=float(form.get("fps") or 10),
        )
    except ValueError:
        return error("options must be numbers")
    uploads = [f for f in form.getlist("files") if getattr(f, "filename", None)]
    names = [Path(f.filename).name for f in uploads]  # drop any client-supplied directories
    if not uploads or any(n in ("", ".", "..") for n in names):
        return error("upload a video or some images")
    videos = [n for n in names if Path(n).suffix.lower() not in EXTS]
    if videos and len(names) > 1:
        return error("upload one video, or images only")

    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    folder = JOBS / job_id
    (folder / "input").mkdir(parents=True)
    for f, name in zip(uploads, names):
        with (folder / "input" / name).open("wb") as out:
            shutil.copyfileobj(f.file, out)
    source = folder / "input" / videos[0] if videos else folder / "input"
    info = dict(state="running", frames=0, leds=0, opts=opts, source=videos[0] if videos else f"{len(names)} images")
    write_json(folder / "job.json", info)
    running.add(job_id)
    threading.Thread(target=run_job, args=(folder, source, dict(info)), daemon=True).start()
    return JSONResponse(info | {"id": job_id}, 201)


async def delete_job(request: Request) -> JSONResponse:
    folder = job_dir(request.path_params["id"])
    if not folder:
        return error("no such job", 404)
    if folder.name in running:
        return error("job is still running", 409)
    shutil.rmtree(folder)
    return JSONResponse({})


async def relabel_job(request: Request) -> JSONResponse:
    folder = job_dir(request.path_params["id"])
    if not folder:
        return error("no such job", 404)
    info = job_info(folder)
    if info["state"] != "done":
        return error("job isn't done", 409)
    shown = json.loads((folder / "frames.json").read_text())
    info["decoded"] = await asyncio.to_thread(label, folder, shown)  # decoding is CPU work, keep the server responsive
    write_json(folder / "job.json", {k: v for k, v in info.items() if k != "id"})
    return JSONResponse(info)


async def viewer(request: Request):
    # served live (not copied into the job) so viewer edits show up in old jobs too
    return FileResponse(SRC / "led_viewer.html") if job_dir(request.path_params["id"]) else error("no such job", 404)


# ---------- light ----------
strip: GoveeStrip | None = None
strip_lock = asyncio.Lock()


def light_state() -> dict:
    on = bool(strip and strip._client and strip._client.is_connected)
    return dict(
        connected=on,
        total=TOTAL_LEDS,
        brightness=strip.brightness if strip else 100,
        leds={i: rgb.hex() for i, rgb in strip.leds.items() if rgb != OFF} if strip else {},
    )


async def connected_strip() -> GoveeStrip:
    global strip
    async with strip_lock:
        if not light_state()["connected"]:
            s = GoveeStrip(os.environ.get("XMAS_LIGHT_NAME", "6871"), os.environ.get("XMAS_LIGHT_ADDRESS") or None)
            await s.connect()
            if strip:
                s.leds, s.brightness = strip.leds, strip.brightness  # keep the drawing across reconnects
            strip = s
        return strip


async def get_light(request: Request) -> JSONResponse:
    return JSONResponse(light_state())


async def light_action(request: Request) -> JSONResponse:
    action, body = request.path_params["action"], await request.json() if await request.body() else {}
    try:
        if action == "scan":
            found = (await BleakScanner.discover(timeout=6, return_adv=True)).values()
            return JSONResponse([dict(name=ad.local_name, address=d.address, rssi=ad.rssi) for d, ad in found if ad.local_name])
        if action == "disconnect":
            if strip:
                await strip.disconnect()
            return JSONResponse(light_state())
        if action not in ("connect", "power", "fill", "draw", "brightness"):
            return error("unknown action", 404)
        s = await connected_strip()
        if action == "power":
            await s.power(bool(body.get("on", True)))
        elif action == "fill":
            await s.fill(body["color"])
            s.clear().set(range(TOTAL_LEDS), body["color"])  # so the next drawing keeps the fill
        elif action == "draw":  # leds: {"0-9,100": "ff0000", ...}, clear: start from all off
            if body.get("clear"):
                s.clear()
            for spec, color in body.get("leds", {}).items():
                s.set(spec, color)
            for spec in body.get("off", []):
                s.off(spec)
            await s.show()
        elif action == "brightness":
            s.set_brightness(body["percent"])
            await s.show()
    except (ValueError, KeyError, TypeError) as e:
        return error(f"bad request: {e}")
    except (ConnectionError, TimeoutError, BleakError, OSError) as e:
        return error(f"light: {e or type(e).__name__}", 503)
    return JSONResponse(light_state())


# ---------- app ----------
async def dev_version(request: Request) -> JSONResponse:
    """Changes on every server restart and html edit; the page reloads itself when it does (--reload only)."""
    return JSONResponse(BOOT + "".join(str(p.stat().st_mtime) for p in sorted(SRC.glob("*.html"))))


@contextlib.asynccontextmanager
async def lifespan(app):
    yield
    if strip and light_state()["connected"]:
        await strip.disconnect()  # free the light for the next process (or the Govee app)


routes = [
    Route("/", lambda r: FileResponse(SRC / "web.html")),
    Route("/api/jobs", list_jobs),
    Route("/api/jobs", create_job, methods=["POST"]),
    Route("/api/jobs/{id}", delete_job, methods=["DELETE"]),
    Route("/api/jobs/{id}/label", relabel_job, methods=["POST"]),
    Route("/api/light", get_light),
    Route("/api/light/{action}", light_action, methods=["POST"]),
    Route("/jobs/{id}/", viewer),
    Mount("/jobs", StaticFiles(directory=JOBS)),
]
if os.environ.get("XMAS_DEV") == "1":
    routes.append(Route("/api/dev", dev_version))
app = Starlette(routes=routes, lifespan=lifespan)


def main() -> None:
    ap = argparse.ArgumentParser(description="xmas-mapper web UI")
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from other devices")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="dev: restart on .py edits, reload the page on any edit")
    args = ap.parse_args()
    print(f"UI at http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/")
    if args.reload:
        os.environ["XMAS_DEV"] = "1"  # inherited by uvicorn's reloader subprocess
        uvicorn.run("web:app", host=args.host, port=args.port, reload=True, reload_dirs=[str(SRC)], app_dir=str(SRC))
    else:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
