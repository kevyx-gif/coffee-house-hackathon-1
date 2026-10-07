import html
import secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gradio as gr

from coffee_house.api.service import COOKIE
from coffee_house.auth import AuthError
from coffee_house.storage.sqlite import Change, RevisionConflict, StorageUnavailable

from .menu import render_menu

CSS = """
.gradio-container .table-wrap,.gradio-container [role=gridcell],.gradio-container [role=columnheader],.gradio-container [role=row]{background:#fff!important;color:#302319!important}
#admin-table [role=row] [role=gridcell]:nth-child(-n+2),#admin-table th [role=button],#admin-table th input[type=checkbox],#admin-table th:nth-child(-n+2),#admin-table td:nth-child(-n+2),#admin-table col:nth-child(-n+2),#admin-table .drop-hint,#admin-table button[aria-label="Add row"],#admin-table th button{display:none!important}
.chat-log{min-height:240px;max-height:400px;overflow:auto;background:#fff;border:1px solid #e0d4c3;border-radius:18px;padding:20px;margin:14px 0}.chat-message{white-space:pre-wrap;border-radius:14px;padding:12px;margin:12px 0;max-width:90%}.chat-message.user{background:#efe2cb;margin-left:auto}.chat-message.assistant{background:#f7f0e4}.greeting{color:#675748!important}#coffee-chat label{display:block;margin-top:12px;color:#302319!important}#coffee-chat textarea,#coffee-chat select,#coffee-chat input{display:block;width:100%;box-sizing:border-box;padding:12px;border:1px solid #d3c5b3;border-radius:12px;background:#fff;color:#302319}#coffee-chat button{padding:11px 16px;border-radius:12px;border:1px solid #d3c5b3;background:#fff;color:#302319;cursor:pointer}#chat-submit,#chat-manual{background:#684632!important;color:#fff!important}.chat-actions{display:flex;gap:10px;flex-wrap:wrap}#coffee-chat details{margin:18px 0;padding:16px;background:#f3ebdc;border-radius:14px}#chat-extras label{display:flex;gap:8px}#chat-extras input{width:auto}#coffee-chat button:disabled,#coffee-chat textarea:disabled{opacity:.6;cursor:wait}
body,.gradio-container{background:#faf6ef!important;color:#302319!important;font-family:system-ui,sans-serif!important}
.gradio-container{max-width:1180px!important;margin:auto!important} h1{font-size:2.4rem!important;letter-spacing:-.04em}
.gradio-container h1,.gradio-container h2,.gradio-container h3,.menu-card .variant,.menu-card strong,.demo-note{color:#302319!important}
#brand{padding:24px 4px 10px}#brand p{color:#675748}.demo-note{padding:10px 14px;border:1px solid #e2ceab;border-radius:14px;background:#fff9e9}
#connection-status{padding:8px 2px;font-size:.95rem;color:#795c47}.menu-filters{display:flex;gap:12px;margin:20px 0;flex-wrap:wrap}
.menu-filters input,.menu-filters select{padding:12px;border:1px solid #d3c5b3;border-radius:12px;background:white;flex:1;min-width:180px;color:#302319}
.menu-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.menu-category{margin:22px 0}.menu-category h2{font-size:1.4rem;margin-bottom:12px}
.menu-card{background:white;border:1px solid #e5dacb;border-radius:18px;padding:17px;box-shadow:0 3px 15px #49341906}
.card-top{display:flex;align-items:start;justify-content:space-between;gap:8px}.card-top h3{font-size:1.05rem;font-weight:650;margin:0 0 14px}
.badge{white-space:nowrap;font-size:.73rem;padding:4px 8px;border-radius:20px}.available{background:#efe2cb;color:#775231}.out{background:#f8e7db;color:#874221}.unknown{background:#ece9e3;color:#635748}
.variant{display:flex;justify-content:space-between;gap:8px;margin:8px 0;font-size:.85rem}.variant strong{white-space:nowrap}.menu-card small{display:block;font-size:.72rem;line-height:1.4;color:#766958}.source{margin-top:14px}
.extras-section,.bread{border-radius:18px;border:1px solid #e0d4c3;background:#f3ebdc;padding:20px;margin:20px 0}.extra-item{display:flex;justify-content:space-between;gap:10px;margin:10px 0;font-size:.9rem}
#tabs>.tab-nav{position:sticky;top:0;z-index:5;background:#faf6ef!important}#chat-send,#admin-save{background:#684632!important;color:white!important}
@media(max-width:800px){.menu-grid{grid-template-columns:repeat(2,minmax(0,1fr))}h1{font-size:1.9rem!important}}
@media(max-width:500px){.menu-grid{grid-template-columns:1fr}.gradio-container{padding:10px!important}.variant{font-size:.9rem}.card-top h3{font-size:1.15rem}}
/* Asistente primero: tarjetas, jerarquía y panel lateral de estilo offcanvas. */
body{background:radial-gradient(ellipse at 10% 0%,#f4e7d6,transparent 55%),#f8f7f4!important}
.gradio-container{max-width:1100px!important;padding:24px 88px 32px 24px!important;background:transparent!important}
#brand{display:flex;align-items:center;gap:20px;padding:10px 0 24px}#brand h1{font-size:2.6rem!important;margin:6px 0;line-height:1.12}#brand p{margin:4px 0}.brand-symbol{display:grid;place-items:center;position:relative;flex:0 0 64px;height:64px;background:#513426;color:#fff;border-radius:21px;font-size:38px;font-family:Georgia,serif;box-shadow:0 8px 20px #51342630}.brand-symbol span{position:absolute;right:7px;top:7px;font-size:13px;color:#e8c28c}#brand div>p:first-child{font-size:11px;letter-spacing:2px;font-weight:700}
.demo-note{font-size:12px;background:#fff7e7;border:none}#connection-status{font-size:12px;margin:7px 0 14px}
#tabs>.tab-nav{background:transparent!important;border:0!important;position:static;padding-bottom:10px!important;gap:8px}#tabs>.tab-nav button{border-radius:12px!important;padding:9px 15px!important}#tabs>.tab-nav button.selected{background:#513426!important;color:white!important;border:none!important}
#chat-interface{background:#fff!important;border:1px solid #e8e5dc!important;border-radius:24px!important;box-shadow:0 12px 44px #4d342610;overflow:hidden}
.assistant-heading{display:flex;gap:13px;align-items:center;border-bottom:1px solid #ebece7;padding:20px 24px;background:linear-gradient(110deg,#f5eee4,#fff)}.assistant-heading h2{font-size:18px;margin:0}.assistant-heading p{font-size:12px;color:#827262;margin:4px 0 0}.assistant-avatar{display:grid;place-items:center;background:#513426;color:#e8c28c;border-radius:15px;width:42px;height:42px;flex-shrink:0;font-size:24px}.assistant-tag{margin-left:auto;background:#f0e3cd;color:#775231;padding:5px 10px;border-radius:20px;font-size:11px}
#coffee-chat{padding:0 24px 22px}.chat-log{height:220px;min-height:180px;max-height:32vh;border:none;border-radius:0;margin:0 0 10px;padding:22px 0;background:transparent}.greeting{background:#f5eee5;border:1px solid #e6d9c8;border-radius:0 18px 18px;padding:16px;max-width:85%;font-size:15px;line-height:1.6}.chat-message{font-size:15px;line-height:1.55;max-width:85%;padding:15px 17px}.chat-message.assistant{background:#f5eee5;border-radius:0 18px 18px}.chat-message.user{background:#513426;color:#fff;border-radius:18px 0 18px 18px}
#coffee-chat textarea{background:#fffbf6;border:1px solid #decab4;resize:vertical;min-height:78px;box-shadow:inset 0 1px 3px #51342605}#coffee-chat textarea:focus{outline:3px solid #51342620;border-color:#513426}#coffee-chat label[for=chat-text]{font-size:12px;font-weight:600}#chat-status{font-size:12px;color:#827262;min-height:18px}.chat-actions button{font-size:13px!important;font-weight:600;transition:background .15s,box-shadow .15s}#chat-submit,#chat-manual{background:#513426!important;box-shadow:0 4px 10px #51342620}#coffee-chat button:hover:not(:disabled){box-shadow:0 4px 14px #51342625}#coffee-chat details{font-size:13px;background:#faf3e9;border:1px solid #e7dacb;margin-bottom:0}#coffee-chat summary{cursor:pointer;font-weight:600}
#menu-display{height:0!important;min-height:0!important;padding:0!important;border:0!important;overflow:visible!important}
#menu-drawer{position:fixed;z-index:40;right:18px;top:170px;width:66px;max-height:calc(100dvh - 190px);border:1px solid #dfcbb7;border-radius:18px;background:#fff;box-shadow:0 8px 30px #51342620;color:#302319}
#menu-drawer>summary{list-style:none;cursor:pointer;display:flex;flex-direction:column;gap:6px;align-items:center;padding:17px 10px;font-size:13px;font-weight:700;color:#513426}#menu-drawer>summary::-webkit-details-marker{display:none}#menu-drawer>summary span{font-size:22px}
#menu-drawer[open]{width:min(440px,calc(100vw - 28px));top:18px;max-height:calc(100dvh - 36px);border-radius:22px;box-shadow:-14px 0 60px #3a251b25}
#menu-drawer[open]>summary{flex-direction:row;padding:12px 22px;border-bottom:1px solid #e7dacb;justify-content:space-between}
.drawer-body{overflow-y:auto;max-height:calc(100dvh - 98px);padding:18px 20px;overscroll-behavior:contain}.drawer-heading{display:flex;align-items:center;justify-content:space-between}.drawer-heading small{font-size:10px;letter-spacing:1.6px;color:#8b715a}.drawer-heading h2{margin:5px 0;font-size:25px}.drawer-heading button{border:1px solid #e7dacb;background:#faf2e7;border-radius:50%;height:34px;width:34px;cursor:pointer;color:#513426}.drawer-body>p{font-size:13px;color:#827262}.drawer-body .menu-grid{grid-template-columns:1fr;gap:10px}.drawer-body .menu-category h2{font-size:16px}.drawer-body .menu-card{box-shadow:none;border-color:#e6d8c8;padding:15px}.drawer-body .menu-filters{gap:8px;margin:15px 0}.drawer-body .menu-filters input,.drawer-body .menu-filters select{min-width:0;width:100%;flex:1 1 100%;font-size:13px}.drawer-body .card-top h3{font-size:15px}.drawer-body .extras-section,.drawer-body .bread{padding:15px;font-size:13px}
button:focus-visible,summary:focus-visible{outline:3px solid #c7a17d;outline-offset:3px}
@media(max-width:600px){.gradio-container{padding:16px 65px 20px 12px!important}#brand{gap:10px;align-items:flex-start}#brand h1{font-size:1.8rem!important}.brand-symbol{flex-basis:42px;height:42px;font-size:26px;border-radius:14px}#brand div>p:last-child{font-size:12px}.assistant-heading{padding:15px 12px;gap:8px}.assistant-heading p{font-size:11px}.assistant-heading h2{font-size:16px}.assistant-tag{display:none}#coffee-chat{padding:0 12px 15px}.chat-message,.greeting{max-width:94%;font-size:14px}.chat-log{height:210px}.chat-actions{gap:6px}.chat-actions button{padding:10px!important;font-size:12px!important}#menu-drawer{right:7px;width:48px;top:190px;border-radius:13px}#menu-drawer>summary{padding:13px 6px;font-size:11px}#menu-drawer[open]{right:7px;top:7px;width:calc(100vw - 14px);max-height:calc(100dvh - 14px)}.drawer-body{max-height:calc(100dvh - 76px)}}

/* Carta amplia: el acceso permanece discreto a la derecha. */
#brand h1,.drawer-heading h2{font-family:Georgia,"Times New Roman",serif!important;font-weight:500!important;letter-spacing:-1.2px!important}
#brand h1{font-size:2.8rem!important}.brand-symbol{border:1px solid #b99364;background:linear-gradient(145deg,#674732,#382219)}
#chat-interface{box-shadow:0 16px 50px #593d2410;border-color:#e6d8c8!important}.assistant-heading{background:linear-gradient(110deg,#f4e9da,#fffcf8)}.assistant-tag{color:#865f38;background:#f0e3ce}.assistant-avatar{background:linear-gradient(145deg,#674732,#382219)}
.gradio-container .tab-nav button.selected,.gradio-container [role=tab][aria-selected=true]{color:#684632!important;border-color:#9c724b!important}#connection-status{color:#795c47!important}
#menu-drawer[open]{left:50%;right:auto;transform:translateX(-50%);width:min(1120px,calc(100vw - 48px));top:24px;max-height:calc(100dvh - 48px);border:1px solid #c9ac8a;background:#fffcf7;box-shadow:0 22px 100px #35221750}
#menu-drawer[open]>summary{padding:12px 26px;background:#513426;color:#fff;border-radius:20px 20px 0 0;border-bottom:1px solid #a27d56;font-size:14px}
.drawer-body{padding:24px 28px;background:linear-gradient(140deg,#fffcf7,#f7f0e5);max-height:calc(100dvh - 111px)}.drawer-heading h2{font-size:36px}.drawer-heading small{color:#937048}.drawer-body .menu-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.drawer-body .menu-card{padding:20px;border-radius:17px;background:#fffdf9;box-shadow:0 5px 18px #65412109;border:1px solid #e2d4c3;min-width:0}.drawer-body .menu-card:hover{border-color:#be9871;box-shadow:0 9px 25px #65412116}.drawer-body .card-top{flex-wrap:wrap;gap:6px}.drawer-body .card-top h3{font-family:Georgia,serif;font-size:19px;line-height:1.25;margin-bottom:8px}.drawer-body .badge{font-size:10px}.drawer-body .variant{font-size:12px;align-items:start}.drawer-body .variant strong{color:#724924!important}.drawer-body .menu-category{margin:26px 0}.drawer-body .menu-category h2{font-family:Georgia,serif;font-size:23px;padding-bottom:10px;border-bottom:1px solid #dcc5aa}.drawer-body .source{font-size:10px;color:#a18b76}.drawer-body .menu-filters{position:sticky;top:-24px;padding:12px 0;background:#faf4e9;z-index:2;display:flex;flex-wrap:wrap;gap:10px}.drawer-body .menu-filters input{flex:2 1 240px;width:auto}.drawer-body .menu-filters select{flex:1 1 180px;width:auto}.menu-reset{border:1px solid #cdb394;border-radius:12px;padding:10px 14px;background:#fffaf2;color:#684632;cursor:pointer}.menu-count{font-size:12px;color:#826950;margin:8px 0}.menu-empty{padding:20px;background:#f3e6d3;border-radius:14px;color:#684632}
#menu-drawer .menu-card[hidden],#menu-drawer .menu-category[hidden],.menu-empty[hidden]{display:none!important}
@media(max-width:900px){.drawer-body .menu-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){#brand h1{font-size:1.9rem!important}#menu-drawer[open]{left:50%;right:auto;top:7px;width:calc(100vw - 14px);max-height:calc(100dvh - 14px)}.drawer-body{padding:16px;max-height:calc(100dvh - 80px)}.drawer-body .menu-grid{grid-template-columns:1fr}.drawer-heading h2{font-size:29px}.drawer-body .menu-filters{top:-16px}.drawer-body .menu-card{padding:16px}.drawer-body .menu-filters input,.drawer-body .menu-filters select{flex:1 1 100%}}

.chat-message.user .message-text{color:#fff!important}
.message-source{display:block;margin:12px 0 0 auto;font-size:10px;line-height:1.45;text-align:right;color:#927f6c;white-space:normal;max-width:100%;font-weight:400}
.chat-message.pending{width:fit-content;color:#806b57;font-size:14px;background:#f7f0e5;border:1px solid #eadfce}
.chat-message.pending .message-text::after{content:"…";display:inline-block;margin-left:3px;animation:coffee-wait 1.5s ease-in-out infinite}
@keyframes coffee-wait{0%,100%{opacity:.35}50%{opacity:1}}
@media(prefers-reduced-motion:reduce){.chat-message.pending .message-text::after{animation:none}}

"""


def state_new():
    return {"id": secrets.token_urlsafe(24), "ticket": None, "messages": [], "delivered": False, "confirmed": None}


def table_for(catalog, snapshot):
    return [["product", item.id, item.name, snapshot.products[item.id]] for item in catalog.products.values()] + [
        ["extra", item.id, item.name, snapshot.extras[item.id]] for item in catalog.extras.values()]


def delta(rows, base, catalog):
    expected = {(row[0], row[1]): row[3] for row in base}
    identifiers = {("product",key) for key in catalog.products} | {("extra",key) for key in catalog.extras}
    if set(expected) != identifiers or len(base) != len(identifiers):
        raise ValueError("Actualiza el estado y conserva solo cambios de disponibilidad")
    if len(rows) != len(base):
        raise ValueError("No añadas ni retires productos")
    changes, seen = [], set()
    for row in rows:
        if len(row) != 4 or (row[0], row[1]) not in expected or (row[0], row[1]) in seen or type(row[3]) is not bool:
            raise ValueError("Revisa la tabla; solo cambia disponibilidad")
        seen.add((row[0], row[1]))
        if row[3] != expected[(row[0], row[1])]:
            changes.append(Change(row[0], row[1], row[3]))
    return changes


def chat_markup(catalog):
    return '<div id="coffee-chat"><div id="chat-log" role="log" aria-label="Conversación" class="chat-log"><p class="greeting">¡Hola! Puedo orientarte con el menú y con información de la cafetería. ¿Qué te gustaría consultar?</p></div><label for="chat-text">Tu consulta</label><textarea id="chat-text" placeholder="¿Todavía tienen latte caliente?" rows="3"></textarea><p id="chat-status" role="status">Consulta el menú desde la pestaña del lateral derecho.</p><div class="chat-actions"><button id="chat-submit">Enviar consulta</button><button id="chat-stop">Cancelar consulta</button><button id="chat-reset">Nueva conversación</button></div></div>'



def mount(app, admin, flow, queue, catalog, *, root_path=""):
    with gr.Blocks(title="Coffee & Matcha House", analytics_enabled=False) as demo:
        gr.HTML('<header id="brand"><span class="brand-symbol" aria-hidden="true">C<span>✦</span></span><div><p>COFFEE & MATCHA HOUSE</p><h1>Tu antojo empieza aquí.</h1><p>Pregúntame por una bebida, sus extras o qué tenemos hoy.</p></div></header>'
                '<div class="demo-note">Demo del asistente · La disponibilidad es una simulación. No se realizan pedidos ni compras.</div>'
                '<div id="connection-status" role="status">Conectando para consultar disponibilidad…</div>')
        gr.HTML('<details id="menu-drawer"><summary aria-label="Abrir o cerrar Menú"><span aria-hidden="true">☰</span> Menú</summary><div class="drawer-body"><header class="drawer-heading"><div><small>EXPLORA A TU RITMO</small><h2>Nuestro menú</h2></div><button id="menu-close" aria-label="Cerrar Menú">✕</button></header><p>Encuentra tu próxima bebida favorita.</p><div id="coffee-menu">' + render_menu(catalog, admin.store.snapshot()) + '</div></div></details>',elem_id="menu-display")
        with gr.Tabs(elem_id="tabs", selected="chat"):
            with gr.Tab("Asistente", id="chat"):
                gr.HTML('<div class="assistant-heading"><span class="assistant-avatar" aria-hidden="true">✦</span><div><h2>Tu asistente de café</h2><p>Algo caliente, frío o un nuevo favorito. Lo encontramos juntos.</p></div><span class="assistant-tag">Hablemos</span></div>' + chat_markup(catalog),elem_id="chat-interface")
            with gr.Tab("Administración", id="admin"), gr.Column(elem_id="admin-panel"):
                gr.Markdown("### Acceso privado\nSolo el administrador puede modificar la disponibilidad de la demo.")
                username = gr.Textbox(label="Usuario")
                password = gr.Textbox(label="Contraseña", type="password")
                csrf = gr.Textbox(value="",visible=False)
                with gr.Row():
                    login = gr.Button("Iniciar sesión")
                    logout = gr.Button("Cerrar sesión")
                gr.HTML('<p id="admin-conflict-note" role="status"></p>')
                admin_status = gr.Markdown("Inicia sesión para revisar la disponibilidad y el historial.", elem_id="admin-status")
                baseline = gr.JSON(value=[],visible=False)
                revision = gr.Number(value=0,precision=0,visible=False)
                reviewed = gr.State(None)
                table = gr.Dataframe(value=[], headers=["Tipo","Identificador","Producto o extra","En stock"],
                    datatype=["str","str","str","bool"], type="array", static_columns=[0,1,2],
                    column_count=4,
                    show_search="search", buttons=[],interactive=True, max_height=460, label="Borrador de disponibilidad",elem_id="admin-table")
                with gr.Row():
                    review = gr.Button("Revisar cambios", elem_id="admin-review")
                    save = gr.Button("Guardar cambios", variant="primary", elem_id="admin-save")
                    refresh = gr.Button("Actualizar estado")
                history = gr.Dataframe(value=[], headers=["Fecha","Administrador","Producto o extra","Anterior","Nuevo"],
                    type="array", buttons=[],interactive=False, label="Historial privado", max_height=300)
                history_button = gr.Button("Consultar historial")
        table.input(fn=None,inputs=[table,baseline,revision],outputs=None,queue=False,api_visibility="private",
            js="(rows,base,rev)=>{sessionStorage.setItem('coffee-admin-draft',JSON.stringify({rows,base,rev}));window.coffeeReviewed=false;const b=document.querySelector('button#admin-save,#admin-save button');if(b)b.disabled=true;return [];}")
        def cookie(request):
            return request.cookies.get(COOKIE)
        def merge_draft(rows, base, snapshot):
            changes = delta(rows, base, catalog) if base else []
            fresh = table_for(catalog, snapshot)
            values = {(c.kind,c.identifier): c.available for c in changes}
            result = [row[:3] + [values.get((row[0],row[1]),row[3])] for row in fresh]
            return result, fresh
        def load_admin(user, pwd, token, rows, base, bundle, request: gr.Request):
            if not token:
                return "","",token,rows,base,gr.skip(),None,"Inicia sesión para revisar la disponibilidad y el historial."
            try:
                admin.auth.require(cookie(request), token, write=True)
                snapshot = admin.store.snapshot()
                draft, fresh = merge_draft(rows, base, snapshot)
                previous = admin.store.operation(bundle["operation"],admin.auth.username) if bundle else None
                message = "El guardado anterior ya estaba confirmado. Revisa cualquier cambio adicional." if previous else "Sesión iniciada. Revisa tu borrador antes de guardar."
                return "", "", token, draft, fresh, snapshot.revision, None, message
            except (AuthError, StorageUnavailable, ValueError) as error:
                return "", "", token, rows, base, gr.skip(), None, str(error)
        login_js = """async (u,p,t,rows,base,bundle) => {
          try { const r=await fetch(new URL('api/login',window.coffeeBase),{method:'POST',headers:{'Content-Type':'application/json','X-Coffee-Client':'1'},body:JSON.stringify({username:u,password:p})});
          const d=await r.json();window.coffeeCsrf=r.ok?d.csrf:'';window.coffeeReviewed=false;window.coffeeLoginError=d.message;return ['', '',window.coffeeCsrf,rows,base,bundle];
          }catch(e){window.coffeeLoginError='No se pudo conectar. Conserva tu borrador';return ['','','',rows,base,bundle];} }"""
        login.click(load_admin, [username,password,csrf,table,baseline,reviewed], [username,password,csrf,table,baseline,revision,reviewed,admin_status],
                    js=login_js, api_visibility="private", queue=False)
        demo.load(load_admin,[username,password,csrf,table,baseline,reviewed],[username,password,csrf,table,baseline,revision,reviewed,admin_status],
            js="async(u,p,t,rows,base,bundle)=>{try{const r=await fetch(new URL('api/me',window.coffeeBase||location.href));const d=await r.json();window.coffeeCsrf=r.ok?d.csrf:'';const saved=JSON.parse(sessionStorage.getItem('coffee-admin-draft')||'null');return ['','',window.coffeeCsrf,saved?.rows||rows,saved?.base||base,bundle];}catch(e){return ['','','',rows,base,bundle];}}",
            api_visibility="private",queue=False,show_progress="hidden")
        def refresh_admin(token, rows, base, bundle, request: gr.Request):
            loaded = load_admin("","",token,rows,base,bundle,request)
            return loaded[3:]
        refresh.click(refresh_admin,[csrf,table,baseline,reviewed],[table,baseline,revision,reviewed,admin_status],api_visibility="private",queue=False)
        def review_admin(rows, base, rev, token, request: gr.Request):
            try:
                admin.auth.require(cookie(request),token,write=True)
                changes = delta(rows, base, catalog)
                if not changes:
                    return rows, base, rev, None, "No hay cambios en el borrador."
                ticket = admin.review(cookie(request), token, rev, changes)
                descriptions = [f"{catalog.products[c.identifier].name if c.kind=='product' else catalog.extras[c.identifier].name}: {'en stock' if c.available else 'agotado'}" for c in changes]
                bundle = {"token":ticket,"changes":changes,"operation":secrets.token_urlsafe(24),"revision":rev}
                return rows,base,rev,bundle,"**Cambios revisados:**\n\n" + "\n".join("- " + html.escape(d) for d in descriptions) + "\n\nPulsa Guardar para confirmarlos."
            except RevisionConflict:
                snapshot = admin.store.snapshot()
                merged,fresh = merge_draft(rows,base,snapshot)
                return merged,fresh,snapshot.revision,None,"La disponibilidad cambió. Conservé tus cambios; revísalos nuevamente antes de guardar."
            except (AuthError,StorageUnavailable,ValueError) as error:
                return rows,base,rev,None,"Borrador: estos cambios aún no se han guardado. " + str(error)
        review_event = review.click(review_admin,[table,baseline,revision,csrf],[table,baseline,revision,reviewed,admin_status],api_visibility="private",queue=False)
        review_event.then(fn=None,inputs=[revision],outputs=None,queue=False,api_visibility="private",
            js="(rev)=>{const ok=document.getElementById('admin-status')?.textContent.includes('Pulsa Guardar para confirmarlos')&&Number(rev)===window.coffeeRevision;window.coffeeReviewed=!!ok;const b=document.querySelector('button#admin-save,#admin-save button');if(b)b.disabled=!ok||!window.coffeeConnected;return [];}" )
        async def save_admin(rows, base, rev, token, bundle, request: gr.Request):
            try:
                admin.auth.require(cookie(request),token,write=True)
                if not bundle or delta(rows,base,catalog) != bundle["changes"]:
                    raise ValueError("Revisa los cambios antes de guardar")
                result = await admin.save(cookie(request),token,bundle["token"],bundle["revision"],bundle["changes"],bundle["operation"])
                snapshot = admin.store.snapshot()
                fresh = table_for(catalog,snapshot)
                return fresh,fresh,snapshot.revision,None,f"Cambios guardados. Se actualizaron {result.changed} elementos."
            except RevisionConflict:
                snapshot = admin.store.snapshot()
                merged,fresh = merge_draft(rows,base,snapshot)
                return merged,fresh,snapshot.revision,None,"Otro guardado cambió la disponibilidad. Tu borrador sigue aquí; revísalo nuevamente."
            except (AuthError,StorageUnavailable,ValueError) as error:
                return rows,base,rev,bundle,"Borrador: estos cambios aún no se han guardado. " + str(error)
        saved_event = save.click(save_admin,[table,baseline,revision,csrf,reviewed],[table,baseline,revision,reviewed,admin_status],api_visibility="private",queue=False)
        saved_event.then(fn=None,outputs=None,queue=False,api_visibility="private",
            js="()=>{if(document.getElementById('admin-status')?.textContent.includes('Cambios guardados')){sessionStorage.removeItem('coffee-admin-draft');window.coffeeReviewed=false;}return [];}")
        def get_history(request: gr.Request):
            try:
                entries = []
                cursor = 0
                while True:
                    batch = admin.history(cookie(request), after_sequence=cursor, limit=1000)
                    entries.extend(batch)
                    if len(batch) < 1000:
                        break
                    cursor = batch[-1].sequence
                return [[datetime.fromisoformat(e.saved_at).astimezone(ZoneInfo("America/Mexico_City")).strftime("%d/%m/%Y %H:%M"),e.administrator,catalog.products[e.identifier].name if e.kind=="product" else catalog.extras[e.identifier].name,
                         "en stock" if e.before else "agotado","en stock" if e.after else "agotado"] for e in entries],"Historial consultado."
            except (AuthError,StorageUnavailable) as error:
                return [],str(error)
        history_button.click(get_history,None,[history,admin_status],api_visibility="private",queue=False)
        logout_js = """async(t)=>{try{await fetch(new URL('api/logout',window.coffeeBase),{method:'POST',headers:{'X-Coffee-CSRF':t}});}finally{window.coffeeCsrf='';window.coffeeReviewed=false;const b=document.querySelector('button#admin-save,#admin-save button');if(b)b.disabled=true;}return [''];}"""
        logout.click(lambda t:(t,None,[],"Sesión cerrada. Tu borrador no se ha guardado."),[csrf],[csrf,reviewed,history,admin_status],js=logout_js,api_visibility="private",queue=False)
    app.state.ui_callbacks = {"load_admin":load_admin,"review_admin":review_admin,"save_admin":save_admin}
    app.state.gradio_demo = demo
    script = Path(__file__).with_name("web.js").read_text(encoding="utf-8")
    root = Path(__file__).resolve().parents[3]
    return gr.mount_gradio_app(app,demo,path="/",root_path=root_path or None,css=CSS,js=script,theme=gr.themes.Soft(primary_hue="orange",neutral_hue="stone").set(body_text_color_dark="#302319",body_background_fill_dark="#faf6ef",background_fill_primary_dark="#ffffff",background_fill_secondary_dark="#f3ebdc",block_background_fill_dark="#ffffff",block_label_text_color_dark="#302319",block_title_text_color_dark="#302319",table_text_color_dark="#302319",accordion_text_color_dark="#302319",input_background_fill_dark="#ffffff"),
        footer_links=[],run_history=False,enable_monitoring=False,show_error=False,
        blocked_paths=[str(root/"state"),str(root/"models"),str(root/".env"),str(root/"tmp"),str(admin.store.path.parent),str(getattr(admin.auth,"credential_path",root/"state/admin.json").parent),*getattr(flow,"private_paths",[])])
