"""Read-only local trial checks. No DDL, model calls, secrets or private document reads."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.request import urlopen
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
os.environ.update({k:v for k,v in dotenv_values(ROOT/'.env').items() if v is not None})
from app.core.config import settings
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
url=make_url(settings.DATABASE_URL)
if url.host not in {'localhost','127.0.0.1','::1'}:raise SystemExit('Not a loopback database; stopped')
report={'date':datetime.now(ZoneInfo('Australia/Sydney')).date().isoformat(),'configuration':'.env','python':sys.version.split()[0],
        'llm_backend':settings.LLM_BACKEND,'citation_policy':'A (conservative)',
        'citation_audit_enabled':settings.CITATION_AUDIT_ENABLED,'runtime_metrics_enabled':settings.RUNTIME_METRICS_ENABLED,
        'storage_exists':Path(settings.LOCAL_DATA_DIR).exists(),'storage_directory':str(Path(settings.LOCAL_DATA_DIR).resolve()),'checks':{}}
for name,endpoint in [('api','http://127.0.0.1:18001/api/health'),('frontend_proxy','http://localhost:3001/api/health'),('frontend','http://localhost:3001/auth/login')]:
    try:
        with urlopen(endpoint,timeout=4) as response:report['checks'][name]=response.status==200
    except Exception:report['checks'][name]=False
try:
    engine=create_engine(settings.DATABASE_URL,connect_args={'connect_timeout':4})
    with engine.connect() as connection:report['checks']['database']=connection.execute(text('SELECT 1')).scalar()==1
    engine.dispose()
except Exception:report['checks']['database']=False
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if all(report['checks'].values()) else 1)
