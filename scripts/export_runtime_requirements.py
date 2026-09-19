"""Select image dependencies from the one authoritative requirements.txt."""
from pathlib import Path
import re
import sys
common={'fastapi','uvicorn','pydantic','python-dotenv','jsonschema'}
profiles={'frontend':{'streamlit','pandas','httpx','plotly'},'database':common|{'psycopg'},'assignment':common|{'httpx','docker','numpy','scipy','scikit-fuzzy','networkx','packaging'}}
for line in Path('requirements.txt').read_text().splitlines():
    name=re.split(r'[\[<>=!~]',line.strip())[0]
    if name in profiles[sys.argv[1]]:print(line)
