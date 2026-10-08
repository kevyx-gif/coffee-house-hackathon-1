"""Web VPS: inferencia privada vía túnel loopback, cookie HTTPS y subruta."""
import argparse
import asyncio
from pathlib import Path

import torch
import uvicorn

from coffee_house.api.app import create_app
from coffee_house.api.service import AdminService
from coffee_house.auth import AuthService
from coffee_house.conversation import ConversationFlow
from coffee_house.domain.catalog import load_catalog
from coffee_house.inference.client import LlamaClient
from coffee_house.retrieval.queries import GroundedQueries
from coffee_house.retrieval.search import HybridIndex, MiniLMEncoder, build_documents
from coffee_house.storage.sqlite import AvailabilityStore


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--engine", default="http://127.0.0.1:18189")
    parser.add_argument("--root-path", default="/soodo/CoffeeHouse")
    parser.add_argument("--port", type=int, default=18790)
    parser.add_argument("--semantic", action="store_true", help="Conversación con preferencias explícitas y clasificación Llama")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    torch.set_num_threads(1)
    catalog = load_catalog(root/"data/catalog.json")
    store = AvailabilityStore(args.db,catalog)
    store.initialize()
    auth = AuthService.load(args.auth)
    index = HybridIndex(build_documents(catalog,root/"data/knowledge/limites.md"),MiniLMEncoder(args.cache))
    client = LlamaClient(args.engine,cleanup_seconds=15)
    # Servir menú/panel aunque la torre esté apagada; consultas fallan sin inventar datos.
    if args.semantic:
        from coffee_house.conversation.semantic import SemanticConversationFlow
        flow = SemanticConversationFlow(GroundedQueries(catalog,index,store),client)
    else:
        flow = ConversationFlow(GroundedQueries(catalog,index,store),client)
    flow.private_paths = [str(args.auth.parent.absolute()),str(Path(args.cache).absolute())]
    origins = ("https://www.xn--k-9gaa.com","https://xn--k-9gaa.com")
    app = create_app(AdminService(store,auth),flow,catalog,origins=origins,root_path=args.root_path)
    await uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=args.port,root_path=args.root_path,
        proxy_headers=True,forwarded_allow_ips="127.0.0.1",access_log=False,log_level="warning",timeout_graceful_shutdown=20)).serve()


if __name__ == "__main__":
    asyncio.run(main())
