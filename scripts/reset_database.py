"""Explicit local/test reset through the database FastAPI admin endpoint."""
import argparse
import json
import os
from pathlib import Path
import urllib.request
from dotenv import dotenv_values

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-reset',action='store_true',required=True)
    args=parser.parse_args()
    values={**dotenv_values(Path(__file__).resolve().parents[1]/'.env',interpolate=False),**os.environ}
    token=values.get('DATABASE_API_ADMIN_TOKEN')
    if not token:parser.error('DATABASE_API_ADMIN_TOKEN is required')
    url=values.get('DATABASE_API_URL','http://127.0.0.1:8001').rstrip('/')+'/admin/reset'
    request=urllib.request.Request(url,json.dumps({'confirm_reset':args.confirm_reset}).encode(),
        {'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=180) as response:print(json.dumps(json.load(response),indent=2))
if __name__=='__main__':main()
