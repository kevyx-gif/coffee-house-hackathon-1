const ports=new Set();let source=null,last={type:'offline'};
function broadcast(message){last=message;for(const port of ports)port.postMessage(message);}
onconnect=e=>{const port=e.ports[0];ports.add(port);port.start();port.postMessage(last);port.onmessage=event=>{if(event.data==='close'){ports.delete(port);if(!ports.size&&source){source.close();source=null;}}};
if(!source){source=new EventSource(new URL('events',self.location.href));source.addEventListener('stock',e=>broadcast({type:'stock',revision:Number(e.data)}));source.addEventListener('heartbeat',e=>broadcast({type:'heartbeat',revision:Number(e.data)}));source.onerror=()=>broadcast({type:'offline'});}};
