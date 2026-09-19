"""HTTP-only frontend client; no persistence or assignment rules."""
import os
import httpx

class APIError(Exception):
    def __init__(self,message,status=None):
        super().__init__(message);self.status=status

class APIClient:
    def __init__(self,base_url=None,transport=None):
        self.base_url=(base_url or os.environ.get('API_BASE_URL','http://localhost:8000')).rstrip('/')
        self.transport=transport

    def request(self,method,path,body=None,params=None):
        try:
            with httpx.Client(base_url=self.base_url,transport=self.transport,
                              timeout=httpx.Timeout(10 if path in ('/health','/services') else 600,connect=5)) as client:
                response=client.request(method,path,json=body,params=params)
        except httpx.TimeoutException:raise APIError('La solicitud agotó el tiempo de espera. Consulta el preview antes de reintentar la ejecución.') from None
        except httpx.HTTPError:raise APIError('No se pudo conectar con la API. Comprueba que esté disponible.') from None
        try:data=response.json()
        except ValueError:raise APIError('La API devolvió una respuesta no válida.',response.status_code) from None
        if response.is_error:
            detail=data.get('detail','Error de API') if isinstance(data,dict) else data
            if isinstance(detail,list):detail='; '.join(f"{'.'.join(map(str,x.get('loc',[])))}: {x.get('msg','Error')}" for x in detail)
            raise APIError(str(detail),response.status_code)
        return data

class PreviewWorkflow:
    """UI approval state: only the displayed, current preview may be executed."""
    def __init__(self,state):self.state=state
    def clear(self):
        for key in ('preview','preview_request','preview_displayed','preview_approved','preview_conflict'):
            self.state.pop(key,None)
    def store(self,preview,request):
        self.clear()
        # A regenerated or manually revised preview requires a fresh approval,
        # even when a widget key from an older display is still in the session.
        self.state.pop('approval:'+preview['preview_id'],None)
        self.state['preview']=preview;self.state['preview_request']=request
    def displayed(self):self.state['preview_displayed']=self.state['preview']['preview_id']
    def execute(self,client,request,approved):
        p=self.state.get('preview')
        if not p or request!=self.state.get('preview_request') or self.state.get('preview_displayed')!=p['preview_id'] or not approved or p['status'] not in ('draft','approved') or not p['assignments'] or self.state.get('preview_conflict'):
            raise APIError('Muestra y aprueba un preview vigente antes de ejecutar.')
        try:result=client.request('POST',f"/assignment-previews/{p['preview_id']}/execute",{'approved':True})
        except APIError as exc:
            if exc.status==409:self.state['preview_conflict']=True
            raise
        self.state['preview']['status']='executed'
        return result
