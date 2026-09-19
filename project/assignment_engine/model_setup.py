"""Prepare an Ollama model asynchronously, preserving the server's model cache."""
import json
import os
from threading import Lock,Thread
import time
import urllib.error
import urllib.request


def canonical(name):return name if ':' in name or '@' in name else name+':latest'


class ModelNotFoundError(RuntimeError):pass


class ModelSetup:
    def __init__(self,client):
        self.client=client;self.lock=Lock();self.last_attempt=0
        self.requested=client.model
        self.default=os.environ.get('OLLAMA_DEFAULT_MODEL','llama3.2:1b')
        self.state={'status':'not_checked','requested_model':self.requested,'default_model':self.default}

    def models(self):
        with urllib.request.urlopen(self.client.base_url.rstrip('/')+'/api/tags',timeout=5) as response:
            return {canonical(m['name']) for m in json.load(response).get('models',[])}

    def pull(self,name):
        self.state.update(status='downloading',downloading_model=name)
        request=urllib.request.Request(self.client.base_url.rstrip('/')+'/api/pull',
            json.dumps({'model':name,'stream':True}).encode(),{'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=300) as response:
            for line in response:
                if not line.strip():continue
                event=json.loads(line)
                if event.get('error'):
                    message=event['error']
                    if 'pull model manifest' in message.lower() and ('file does not exist' in message.lower() or 'model not found' in message.lower()):
                        raise ModelNotFoundError(message)
                    raise RuntimeError(message)
                self.state.update(download_status=event.get('status','downloading'))
                if event.get('total'):
                    self.state['layer_progress']=round(100*event.get('completed',0)/event['total'],1)
        if canonical(name) not in self.models():raise RuntimeError('Model absent after pull')

    def run(self):
        try:
            available=self.models();selected=self.requested
            if canonical(selected) not in available:
                try:self.pull(selected)
                except (urllib.error.HTTPError,ModelNotFoundError) as exc:
                    # Do not replace a model on connectivity, auth, or server failures.
                    if (isinstance(exc,urllib.error.HTTPError) and exc.code!=404) or canonical(selected)==canonical(self.default):raise
                    selected=self.default
                    self.state['fallback_reason']='configured_model_not_found'
                    if canonical(selected) not in available:self.pull(selected)
            self.client.model=selected
            self.state.update(status='ready',selected_model=selected,error=None)
        except Exception as exc:
            self.state.update(status='failed',error=('HTTP_'+str(exc.code)) if isinstance(exc,urllib.error.HTTPError) else type(exc).__name__)
        finally:self.lock.release()

    def start(self):
        if self.state['status']=='ready' or time.monotonic()-self.last_attempt<60:return
        if not self.client.base_url:return
        if not self.lock.acquire(blocking=False):return
        self.last_attempt=time.monotonic();self.state.update(status='checking',error=None)
        Thread(target=self.run,name='ollama-model-setup',daemon=True).start()
