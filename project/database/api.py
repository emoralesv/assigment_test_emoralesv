"""Only this FastAPI service connects to the application's PostgreSQL instance."""
from contextlib import asynccontextmanager, contextmanager
from enum import Enum
import os
import secrets
import threading
import uuid

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from assigment_test_emoralesv.normalization.common import ROOT
from assigment_test_emoralesv.project.assignment_engine.contracts import StateRequest, PreviewRequest, ExecuteRequest
from . import frontend
from .safety import from_environment, validate
from .importer import reset, TABLES
from . import workflows
from datetime import datetime

TableName=Enum('TableName',{name:name for name in TABLES},type=str)


class ResetRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    confirm_reset: bool


class StorePreviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request: PreviewRequest
    state_hash: str=Field(pattern='^[a-f0-9]{64}$')
    preview: dict


class ExplanationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assignment_id: uuid.UUID | None
    preview_item_id: uuid.UUID
    model: str=Field(min_length=1)
    prompt: str
    response: str | None=None
    error: str | None=None


class SignalEditRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    signals: dict
    expected_updated_at: datetime
    reason: str=Field(min_length=3,max_length=1000)
    assignment_note: str | None=Field(default=None,max_length=8000)

class TechnicalSkillsRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    skills: list[dict]=Field(max_length=30)
    reason: str=Field(min_length=3,max_length=1000)

class SellerEligibilityRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    active: bool
    team_id: str | None=None
    zone: str | None=Field(default=None,max_length=120)
    maximum_capacity: int | None=Field(default=None,ge=0,le=10000)
    availability_override: bool=False
    reason: str=Field(min_length=3,max_length=1000)

class SuggestionQueueRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    record_ids: list[str]=Field(min_length=1,max_length=200)

class SuggestionUpdateRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: str
    signals: dict | None=None
    model: str | None=None
    error: str | None=None

class SuggestionAcceptRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_updated_at: datetime
    reason: str=Field(min_length=3,max_length=1000)
    signals: dict | None=None
    assignment_note: str | None=Field(default=None,max_length=8000)

class ManualAssignmentsRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assignments: list[dict]=Field(min_length=1,max_length=100)


def create_app(config=None,api_token=None,admin_token=None,input_directory=None):
    reset_lock=threading.Lock()
    api_auth=HTTPBearer(auto_error=False,scheme_name='DatabaseServiceToken')
    admin_auth=HTTPBearer(auto_error=False,scheme_name='DatabaseAdminToken')

    @asynccontextmanager
    async def lifespan(app):
        cfg=config or from_environment();validate(cfg,True,allow_compose=True)
        # A fresh PostgreSQL volume contains the database but not this application's
        # schema. Import the normalized seed once, before opening the request pool.
        args=cfg.connect_args();args.update(row_factory=dict_row,prepare_threshold=None,options='-c statement_timeout=15000')
        with psycopg.connect(**args) as bootstrap_connection:
            schema_ready=bootstrap_connection.execute("SELECT to_regclass('sales_assignment.records') IS NOT NULL AS initialized").fetchone()['initialized']
        if not schema_ready:
            reset(cfg,True,input_directory or ROOT/'normalized',allow_compose=True)
        app.state.config=cfg
        app.state.api_token=api_token or os.environ.get('DATABASE_API_TOKEN','')
        app.state.admin_token=admin_token or os.environ.get('DATABASE_API_ADMIN_TOKEN','')
        if not app.state.api_token or not app.state.admin_token:raise RuntimeError('Database API tokens must be configured')
        pool=ConnectionPool(kwargs=args,min_size=1,max_size=5,open=False,timeout=15)
        pool.open();pool.wait(timeout=30);app.state.pool=pool
        # Compatible additive migration for databases already initialized by the demo.
        with pool.connection() as conn:
            conn.execute('ALTER TABLE sales_assignment.record_signals ADD COLUMN IF NOT EXISTS assignment_note text')
            conn.execute('''CREATE TABLE IF NOT EXISTS sales_assignment.user_technical_skills (
                user_id text NOT NULL REFERENCES sales_assignment.users(id), skill text NOT NULL,
                verification_evidence text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, skill), CHECK (length(trim(skill)) > 0),
                CHECK (length(trim(verification_evidence)) > 0))''')
            conn.execute("""CREATE TABLE IF NOT EXISTS sales_assignment.record_ai_suggestions (
                record_id text PRIMARY KEY REFERENCES sales_assignment.records(id), status text NOT NULL,
                heuristic_reasons jsonb NOT NULL DEFAULT '[]'::jsonb, proposed_signals jsonb, model text,
                error text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
                CHECK (status IN ('pending','running','proposed','accepted','rejected','error')))""")
            conn.execute('''CREATE TABLE IF NOT EXISTS sales_assignment.seller_operational_overrides (
                user_id text PRIMARY KEY REFERENCES sales_assignment.users(id), active boolean,
                team_id text REFERENCES sales_assignment.teams(id), zone text, maximum_capacity integer,
                availability_override boolean NOT NULL DEFAULT false, reason text NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT now(), CHECK (maximum_capacity >= 0))''')
        try:yield
        finally:pool.close()

    app=FastAPI(title='Sales Database API',version='1.0.0',lifespan=lifespan)

    def authenticate(credentials,expected):
        if credentials is None or not secrets.compare_digest(credentials.credentials,expected):
            raise HTTPException(401,'Invalid API token',headers={'WWW-Authenticate':'Bearer'})

    def service_access(credentials:HTTPAuthorizationCredentials|None=Depends(api_auth)):
        authenticate(credentials,app.state.api_token)

    def admin_access(credentials:HTTPAuthorizationCredentials|None=Depends(admin_auth)):
        authenticate(credentials,app.state.admin_token)

    @contextmanager
    def connection():
        try:
            with app.state.pool.connection() as conn:yield conn
        except workflows.NotFound as exc:raise HTTPException(404,str(exc)) from None
        except workflows.Conflict as exc:raise HTTPException(409,str(exc)) from None
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        except (psycopg.Error,PoolTimeout):raise HTTPException(503,'Database operation unavailable; transaction rolled back') from None

    @app.get('/health')
    def health():
        with connection() as conn:
            initialized=conn.execute("SELECT to_regclass('sales_assignment.records') IS NOT NULL AS initialized").fetchone()['initialized']
        return {'status':'ok','database':'connected','schema_initialized':initialized}

    @app.get('/data/{table}',dependencies=[Depends(service_access)])
    def list_rows(table:TableName,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0)):
        with connection() as conn:
            rows=conn.execute(sql.SQL('SELECT * FROM {} ORDER BY 1 LIMIT %s OFFSET %s').format(sql.Identifier('sales_assignment',table.value)),(limit,offset)).fetchall()
            total=conn.execute(sql.SQL('SELECT count(*) AS total FROM {}').format(sql.Identifier('sales_assignment',table.value))).fetchone()['total']
        return {'items':rows,'total':total,'limit':limit,'offset':offset}

    @app.get('/summary',dependencies=[Depends(service_access)])
    def summary():
        with connection() as conn:
            counts={name:conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier('sales_assignment',name))).fetchone()['n'] for name in ('records','users','teams','assignments','assignment_events')}
            statuses=conn.execute('SELECT status,count(*) AS count FROM sales_assignment.records GROUP BY status ORDER BY status').fetchall()
        return {'counts':counts,'record_statuses':statuses}

    @app.get('/ui/historical-simulation-state',dependencies=[Depends(service_access)])
    def historical_simulation_state():
        with connection() as conn:return frontend.load_snapshot(conn)

    @app.get('/ui/load',dependencies=[Depends(service_access)])
    def ui_load():
        with connection() as conn:return frontend.load_snapshot(conn,[])

    @app.post('/ui/simulation-state',dependencies=[Depends(service_access)])
    def ui_simulation_state(body:StateRequest):
        with connection() as conn:return frontend.load_snapshot(conn,body.record_ids)

    @app.get('/ui/dashboard',dependencies=[Depends(service_access)])
    def ui_dashboard():
        with connection() as conn:return frontend.dashboard(conn)

    @app.get('/ui/sellers',dependencies=[Depends(service_access)])
    def ui_sellers():
        with connection() as conn:return frontend.sellers(conn)

    @app.get('/ui/review-worklist',dependencies=[Depends(service_access)])
    def ui_review_worklist():
        with connection() as conn:return frontend.review_worklist(conn)

    @app.post('/ui/review-suggestions/queue',dependencies=[Depends(service_access)])
    def ui_queue_suggestions(body:SuggestionQueueRequest):
        with connection() as conn:return frontend.queue_suggestions(conn,body.record_ids)

    @app.put('/ui/review-suggestions/{record_id}',dependencies=[Depends(service_access)])
    def ui_update_suggestion(record_id:str,body:SuggestionUpdateRequest):
        with connection() as conn:return frontend.update_suggestion(conn,record_id,body.model_dump())

    @app.post('/ui/review-suggestions/{record_id}/accept',dependencies=[Depends(service_access)])
    def ui_accept_suggestion(record_id:str,body:SuggestionAcceptRequest):
        with connection() as conn:return frontend.accept_suggestion(conn,record_id,body.model_dump())

    @app.put('/ui/sellers/{seller_id}/technical-skills',dependencies=[Depends(service_access)])
    def ui_technical_skills(seller_id:str,body:TechnicalSkillsRequest):
        with connection() as conn:return frontend.edit_technical_skills(conn,seller_id,body.model_dump())

    @app.put('/ui/sellers/{seller_id}/eligibility',dependencies=[Depends(service_access)])
    def ui_seller_eligibility(seller_id:str,body:SellerEligibilityRequest):
        with connection() as conn:return frontend.edit_seller_eligibility(conn,seller_id,body.model_dump())

    @app.get('/ui/records',dependencies=[Depends(service_access)])
    def ui_records(q:str='',status:str='',offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),problem_field:str=''):
        with connection() as conn:return frontend.records(conn,q,status,offset,limit,problem_field)

    @app.get('/ui/records/{record_id}',dependencies=[Depends(service_access)])
    def ui_record(record_id:str):
        with connection() as conn:return frontend.record_detail(conn,record_id)

    @app.patch('/ui/records/{record_id}/signals',dependencies=[Depends(service_access)])
    def ui_edit(record_id:str,body:SignalEditRequest):
        with connection() as conn:return frontend.edit_signals(conn,record_id,body.model_dump())

    @app.get('/ui/audit',dependencies=[Depends(service_access)])
    def ui_audit(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100)):
        with connection() as conn:return frontend.audit(conn,offset,limit)

    @app.post('/state',dependencies=[Depends(service_access)])
    def state(request:StateRequest):
        with connection() as conn:return workflows.get_state(conn,request.record_ids)

    @app.post('/previews',status_code=201,dependencies=[Depends(service_access)])
    def store_preview(body:StorePreviewRequest):
        with connection() as conn:return workflows.save_preview(conn,body.request.model_dump(),body.state_hash,body.preview)

    @app.get('/previews/{preview_id}',dependencies=[Depends(service_access)])
    def get_preview(preview_id:uuid.UUID):
        with connection() as conn:return workflows.get_preview(conn,preview_id)

    @app.patch('/previews/{preview_id}/manual-assignments',dependencies=[Depends(service_access)])
    def manual_assignments(preview_id:uuid.UUID,body:ManualAssignmentsRequest):
        with connection() as conn:return workflows.manual_assignments(conn,preview_id,body.assignments)

    @app.post('/previews/{preview_id}/execute',dependencies=[Depends(service_access)])
    def execute(preview_id:uuid.UUID,body:ExecuteRequest):
        with connection() as conn:return workflows.execute_preview(conn,preview_id)

    @app.get('/records/{record_id}/assignment-explanation',dependencies=[Depends(service_access)])
    def get_explanation(record_id:str):
        with connection() as conn:return workflows.explanation(conn,record_id)

    @app.post('/records/{record_id}/llm-explanations',dependencies=[Depends(service_access)])
    def add_explanation(record_id:str,body:ExplanationRequest):
        with connection() as conn:return workflows.store_explanation(conn,record_id,body.model_dump(mode='json'))

    @app.post('/admin/reset',dependencies=[Depends(admin_access)])
    def admin_reset(body:ResetRequest):
        if not body.confirm_reset:raise HTTPException(422,'confirm_reset must be true')
        if not reset_lock.acquire(blocking=False):raise HTTPException(409,'Reset already running')
        try:
            report=reset(app.state.config,True,input_directory or ROOT/'normalized',allow_compose=True)
            if report['transaction_status']!='committed':raise HTTPException(409,report)
            return report
        except HTTPException:raise
        except (ValueError,FileNotFoundError) as exc:raise HTTPException(422,str(exc)) from None
        finally:reset_lock.release()

    return app

app=create_app()
