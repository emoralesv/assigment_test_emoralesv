"""Optional local Docker bootstrap for an unavailable Ollama container.

Compose remains the normal production orchestrator. This module is only active
when the deployer deliberately mounts the Docker socket and enables it.
"""
import os


class DockerBootstrap:
    def __init__(self):
        self.enabled=os.environ.get('DOCKER_BOOTSTRAP_ENABLED','false').lower()=='true'
        self.status='disabled' if not self.enabled else 'pending'
        self.error=None

    def ensure_ollama(self):
        if not self.enabled:
            return False
        try:
            import docker
            client=docker.from_env()
            name=os.environ.get('OLLAMA_BOOTSTRAP_CONTAINER','sales-assignment-ollama')
            try:
                container=client.containers.get(name)
                if container.status!='running':container.start()
                self.status='running'
                return True
            except docker.errors.NotFound:
                pass
            image=os.environ.get('OLLAMA_IMAGE','ollama/ollama:latest')
            self.status='pulling'
            client.images.pull(image)
            volume_name=os.environ.get('OLLAMA_BOOTSTRAP_VOLUME','sales-assignment-ollama-data')
            try:client.volumes.get(volume_name)
            except docker.errors.NotFound:client.volumes.create(volume_name)
            network=os.environ.get('DOCKER_BOOTSTRAP_NETWORK','project_default')
            client.networks.get(network)  # Do not silently put the model on the wrong network.
            client.containers.run(image,name=name,detach=True,network=network,
                environment={'OLLAMA_HOST':'0.0.0.0:11434','OLLAMA_KEEP_ALIVE':'10m'},
                volumes={volume_name:{'bind':'/root/.ollama','mode':'rw'}},
                labels={'com.sales-assignment.managed':'true','com.sales-assignment.service':'ollama'},
                restart_policy={'Name':'unless-stopped'})
            self.status='started'
            return True
        except Exception as exc:
            self.status='failed';self.error=type(exc).__name__
            return False
