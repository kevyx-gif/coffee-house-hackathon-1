"""Preview privado de Gradio/FastAPI. No publica ni modifica un proxy."""
import argparse
import asyncio
import contextlib
import signal
import subprocess
import threading
from pathlib import Path

import httpx
import uvicorn

from coffee_house.api.app import create_app
from coffee_house.api.service import AdminService
from coffee_house.auth import AuthService
from coffee_house.conversation import ConversationFlow, TurnResult
from coffee_house.domain.catalog import load_catalog
from coffee_house.inference.client import LlamaClient
from coffee_house.responses import CustomerReply
from coffee_house.storage.sqlite import AvailabilityStore


class PreviewServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        previous={sig:signal.signal(sig,self.handle_exit) for sig in (signal.SIGINT,signal.SIGTERM)}
        try:
            yield
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)
        # No volver a emitir SIGTERM antes del finally que cierra nuestro motor.


class OfflineClient:
    def __init__(self):
        self.free = {0,1,2}
        self.cleanups = set()
    async def close(self):
        pass


class OfflineFlow:
    def __init__(self):
        self.client = OfflineClient()
    async def answer(self, question, **kwargs):
        return TurnResult(CustomerReply("El asistente no está disponible por ahora. Puedes seguir consultando el menú.","model_unavailable"),
                          "unresolved","stock",0,failure="inference_unavailable")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth",type=Path,required=True)
    parser.add_argument("--db",type=Path,default=Path("state/web.sqlite3"))
    parser.add_argument("--port",type=int,default=18090)
    parser.add_argument("--menu-only",action="store_true")
    for key in ("server","model","adapter","cache"):
        parser.add_argument("--"+key)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    catalog = load_catalog(root/"data/catalog.json")
    store = AvailabilityStore(args.db,catalog)
    store.initialize()
    auth = AuthService.load(args.auth)
    process = None
    if args.menu_only:
        flow = OfflineFlow()
    else:
        if not all((args.server,args.model,args.cache)):
            raise SystemExit("Configura motor/base/adaptador/caché; o usa --menu-only para probar menú/panel sin inferencia")
        import torch

        from coffee_house.retrieval.queries import GroundedQueries
        from coffee_house.retrieval.search import (
            HybridIndex,
            MiniLMEncoder,
            build_documents,
        )
        torch.set_num_threads(2)
        index = HybridIndex(build_documents(catalog,root/"data/knowledge/limites.md"),MiniLMEncoder(args.cache))
        command = [args.server,"-m",args.model,"--host","127.0.0.1","--port","18089",
            "-t","2","-tb","2","-np","3","-c","12288","--kv-unified-per-slot","4096","--no-context-shift","--slots","-ngl","0"]
        if args.adapter:
            command.extend(["--lora",args.adapter])
        import socket
        with socket.socket() as port_check:
            port_check.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            port_check.bind(("127.0.0.1",18089))
        args.db.parent.mkdir(parents=True,exist_ok=True)
        log = (args.db.parent/"web-engine.log").open("w")
        process = await asyncio.to_thread(subprocess.Popen,command,stdout=log,stderr=log)
        try:
            async with httpx.AsyncClient(trust_env=False,timeout=1) as http:
                async with asyncio.timeout(60):
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError("No inició el motor privado")
                        try:
                            if (await http.get("http://127.0.0.1:18089/health")).status_code==200:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(.2)
            client = LlamaClient("http://127.0.0.1:18089",cleanup_seconds=15)
            await client.verify_server()
            flow = ConversationFlow(GroundedQueries(catalog,index,store),client)
        except BaseException:
            process.terminate()
            await asyncio.to_thread(process.wait,timeout=15)
            log.close()
            raise
    flow.private_paths = [str(args.auth.parent.absolute()),str(Path(args.model).absolute().parent.parent)] if args.model else [str(args.auth.parent.absolute())]
    origins=(f"http://localhost:{args.port}",f"http://127.0.0.1:{args.port}")
    try:
        app = create_app(AdminService(store,auth),flow,catalog,cookie_secure=False,origins=origins,preview=True)
        server = PreviewServer(uvicorn.Config(app,host="127.0.0.1",port=args.port,log_level="warning",access_log=False,workers=1,timeout_graceful_shutdown=5))
        await server.serve()
    finally:
        await flow.client.close()
        if process:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait,timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait,timeout=5)
            log.close()


if __name__ == "__main__":
    asyncio.run(main())
