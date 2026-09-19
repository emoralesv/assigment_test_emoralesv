"""Run the full test suite against a new disposable, loopback-only PostgreSQL."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]

def main():
    image=os.environ.get('POSTGRES_TEST_IMAGE','postgres:17')
    name='sales-assignment-tests-'+uuid.uuid4().hex[:10]
    password=secrets.token_hex(24)
    with tempfile.TemporaryDirectory(prefix='sales-db-test-') as temp:
        envfile=Path(temp)/'postgres.env'
        envfile.write_text('POSTGRES_DB=sales_assignment_test\nPOSTGRES_USER=sales_assignment_test\nPOSTGRES_PASSWORD='+password+'\n')
        envfile.chmod(0o600)
        created=False
        try:
            subprocess.run(['docker','run','--rm','-d','--name',name,'--publish','127.0.0.1::5432','--env-file',str(envfile),image],check=True,stdout=subprocess.DEVNULL)
            created=True
            port=subprocess.check_output(['docker','port',name,'5432/tcp'],text=True).strip().rsplit(':',1)[1]
            env=dict(os.environ,APP_ENV='test',DATABASE_HOST='127.0.0.1',DATABASE_PORT=port,DATABASE_NAME='sales_assignment_test',DATABASE_USER='sales_assignment_test',DATABASE_PASSWORD=password,RUN_DATABASE_INTEGRATION_TESTS='1')
            env.pop('DATABASE_URL',None)
            result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT,env=env)
            return result.returncode
        finally:
            if created:subprocess.run(['docker','rm','-f',name],check=False,stdout=subprocess.DEVNULL)

if __name__=='__main__':raise SystemExit(main())
