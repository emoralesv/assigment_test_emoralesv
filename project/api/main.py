"""Assignment HTTP API. Persistence is accessed only through Database API."""
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from uuid import UUID
import httpx
from fastapi import FastAPI, HTTPException, Query
from urllib.parse import urlencode, quote
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from statistics import pstdev
from assigment_test_emoralesv.project.assignment_engine.contracts import PreviewRequest, ExecuteRequest, StateRequest, Configuration
from assigment_test_emoralesv.project.assignment_engine.simulation import Simulations, project, historical_examples
from assigment_test_emoralesv.project.assignment_engine.models import CapacityAware, FuzzyOptimal, AIAssisted
from assigment_test_emoralesv.project.assignment_engine.domain import candidate_exclusions
from assigment_test_emoralesv.project.assignment_engine.llm import OllamaClient
from assigment_test_emoralesv.project.assignment_engine.model_setup import canonical
from assigment_test_emoralesv.project.api.docker_bootstrap import DockerBootstrap

class DatabaseClient:
    def request(self, method, path, body=None, admin=False):
        try:
            token=os.environ.get('DATABASE_API_ADMIN_TOKEN' if admin else 'DATABASE_API_TOKEN','')
            response=httpx.request(method,os.environ.get('DATABASE_API_URL','http://database_api:8000').rstrip('/')+path,
                headers={'Authorization':'Bearer '+token},json=body,timeout=3 if path=='/health' else 60)
        except httpx.HTTPError:
            raise HTTPException(503,'Database API unavailable') from None
        if response.is_error:
            raise HTTPException(response.status_code,response.json().get('detail','Database operation failed'))
        return response.json()

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

class ManualAssignmentsRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assignments: list[dict]=Field(min_length=1,max_length=100)

class SuggestionAcceptRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_updated_at: datetime
    reason: str=Field(min_length=3,max_length=1000)
    signals: dict | None=None
    assignment_note: str | None=Field(default=None,max_length=8000)

class SimulationRequest(StateRequest):
    configuration: Configuration=Field(default_factory=Configuration)

class InspectionQuestion(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question: str=Field(min_length=3,max_length=1500)
    person_ids: list[str]=Field(default_factory=list,max_length=3)

class DatabaseResetRequest(BaseModel):
    """Deliberate UI confirmation for restoring the normalized demo dataset."""
    model_config=ConfigDict(extra='forbid')
    confirm_reset: bool

def create_app(database=None,llm=None):
    database=database or DatabaseClient();llm=llm or OllamaClient()
    docker_bootstrap=DockerBootstrap()
    models={model.method:model for model in (CapacityAware(),FuzzyOptimal(),AIAssisted(llm))}
    def with_projection(preview):
        state=preview.pop('snapshot',None)
        if not state:return preview
        records={r['id']:r for r in state['records']};people=[];sellers={seller['id']:seller for seller in state['sellers']}
        additions={};amounts={}
        for item in preview['assignments']:
            additions[item['seller_id']]=additions.get(item['seller_id'],0)+1
            value=records[item['record_id']].get('estimated_revenue')
            if value is not None:amounts[item['seller_id']]=amounts.get(item['seller_id'],0)+float(value)
        availability_record={'id':'availability','status':'nuevo','signals':{},'notes':''}
        for seller in state['sellers']:
            cap=seller.get('maximum_capacity');before=seller.get('open_workload',0);after=before+additions.get(seller['id'],0)
            availability_reasons=candidate_exclusions(availability_record,seller,{'effective_date':state['effective_date']})
            people.append({'id':seller['id'],'name':seller['name'],'eligible':not availability_reasons,
                'capacity':cap,'before_workload':before,'after_workload':after,'before_amount':float(seller.get('known_estimated_amount',0)),
                'after_amount':float(seller.get('known_estimated_amount',0))+amounts.get(seller['id'],0),'additions':additions.get(seller['id'],0),
                'availability_reasons':availability_reasons})
        cohort=[p for p in people if p['eligible']];before=[p['before_workload']/p['capacity'] for p in cohort];after=[p['after_workload']/p['capacity'] for p in cohort]
        money=[p['after_amount']/p['capacity'] for p in cohort];mean=sum(money)/len(money) if money else 0
        compatible=observed=0
        for item in preview['assignments']:
            record=records[item['record_id']];seller=sellers[item['seller_id']]
            for record_field,seller_field in (('zone','zone'),('segment','expert_segment')):
                if record.get(record_field) and seller.get(seller_field):
                    observed+=1;compatible+=record[record_field]==seller[seller_field]
        preview['projection']={'people':people,'metrics':{'before_workload_dispersion':pstdev(before) if before else None,
            'after_workload_dispersion':pstdev(after) if after else None,'after_amount_relative_cv':pstdev(money)/mean if len(money)>1 and mean else None}}
        preview['projection']['metrics'].update(compatibility_rate=compatible/observed if observed else None,compatibility_observed=observed)
        return preview
    analysis_workers=max(1,min(2,int(os.environ.get('OLLAMA_ANALYSIS_WORKERS','1'))))
    analyzer=ThreadPoolExecutor(max_workers=analysis_workers,thread_name_prefix='note-analysis')
    def analyze_record(record_id):
        try:
            database.request('PUT','/ui/review-suggestions/'+quote(record_id,safe=''),{'status':'running'})
            record=database.request('GET','/ui/records/'+quote(record_id,safe=''))
            result=llm.extract_review_suggestion(record)
            body={'status':'proposed','signals':result['output'],'model':result['model']} if result.get('schema_valid') else {
                'status':'error','model':result.get('model'),'error':result.get('error') or 'No se pudo validar la respuesta'}
            database.request('PUT','/ui/review-suggestions/'+quote(record_id,safe=''),body)
        except Exception as exc:
            try:database.request('PUT','/ui/review-suggestions/'+quote(record_id,safe=''),{'status':'error','error':type(exc).__name__})
            except Exception:pass
    @asynccontextmanager
    async def lifespan(app):
        docker_bootstrap.ensure_ollama()
        if hasattr(llm,'prepare_model'):llm.prepare_model()
        try:yield
        finally:analyzer.shutdown(wait=False,cancel_futures=True)
    app=FastAPI(title='Sales Assignment API',version='1.0.0',lifespan=lifespan)
    simulations=Simulations(models)

    @app.get('/health')
    def health():
        status=database.request('GET','/health')
        return {'status':'ok','database_api':status}

    @app.get('/services')
    def services():
        result={'api':{'status':'live'},'docker_bootstrap':{'status':docker_bootstrap.status,'error':docker_bootstrap.error}}
        try:
            db=database.request('GET','/health')
            result['database']={'status':'live' if db.get('database')=='connected' else 'unknown',
                                'schema_initialized':db.get('schema_initialized',False)}
        except HTTPException:
            result['database']={'status':'unavailable'}
        base=getattr(llm,'base_url','')
        model=getattr(llm,'model','')
        result['llm_service']={'status':'unavailable','model':model,'processor':'unknown'}
        try:
            with httpx.Client(timeout=2) as http:
                tags=http.get(base.rstrip('/')+'/api/tags');tags.raise_for_status()
                names=[m.get('name') for m in tags.json().get('models',[])]
                available=canonical(model) in {canonical(name) for name in names if name}
                result['llm_service'].update(status='live',model_available=available)
                if not available and hasattr(llm,'model_setup') and llm.model_setup.state['status']=='ready':
                    llm.model_setup.state['status']='not_checked'
                    llm.model_setup.last_attempt=0
                loaded=http.get(base.rstrip('/')+'/api/ps');loaded.raise_for_status()
                running=next((m for m in loaded.json().get('models',[]) if m.get('name')==model or m.get('model')==model),None)
                if running:
                    vram=running.get('size_vram',0)
                    processor='CPU' if not vram else 'GPU' if vram>=running.get('size',0) else 'CPU + GPU'
                    result['llm_service'].update(processor=processor,vram_bytes=vram)
                else:result['llm_service']['processor']='not_loaded'
        except (httpx.HTTPError,ValueError,KeyError,TypeError):pass
        if hasattr(llm,'model_setup'):
            llm.prepare_model()
            result['llm_service']['preparation']=dict(llm.model_setup.state)
        return result

    @app.post('/admin/database-reset')
    def database_reset(body:DatabaseResetRequest):
        if not body.confirm_reset:
            raise HTTPException(422,'Debes confirmar el reinicio de la base de datos.')
        report=database.request('POST','/admin/reset',{'confirm_reset':True},admin=True)
        return {'message':'Base de datos inicializada con los datos normalizados.','report':report}

    @app.get('/load-analysis')
    def load_analysis():
        state=database.request('GET','/ui/load')
        return project(state)|{'effective_date':state['effective_date']}

    @app.get('/historical-load-analysis')
    def historical_load_analysis():
        state=historical_examples(database.request('GET','/ui/historical-simulation-state'))
        return project(state)|{'effective_date':state['effective_date']}

    @app.post('/simulations',status_code=202)
    def simulate(body:SimulationRequest):
        state=database.request('POST','/ui/simulation-state',{'record_ids':body.record_ids})
        if any(r['status']!='nuevo' for r in state['records']):raise HTTPException(422,'Selecciona únicamente registros pendientes.')
        try:jid=simulations.create(state,body.model_dump())
        except ValueError as exc:raise HTTPException(429,str(exc)) from None
        return {'id':jid}

    @app.post('/historical-simulations',status_code=202)
    def historical_simulate(configuration:Configuration):
        state=historical_examples(database.request('GET','/ui/historical-simulation-state'))
        request={'record_ids':[r['id'] for r in state['records']],'configuration':configuration.model_dump()}
        try:jid=simulations.create(state,request)
        except ValueError as exc:raise HTTPException(429,str(exc)) from None
        return {'id':jid}

    @app.get('/simulations/{simulation_id}')
    def simulation(simulation_id:UUID):
        try:return simulations.get(str(simulation_id))
        except KeyError:raise HTTPException(404,'Simulación no disponible; genera una nueva.') from None

    @app.post('/simulations/{simulation_id}/{method}/explanation')
    def simulation_explanation(simulation_id:UUID,method:str,body:InspectionQuestion):
        from assigment_test_emoralesv.project.assignment_engine.inspection import context,render_selection
        try:job=simulations.get(str(simulation_id),private=True)
        except KeyError:raise HTTPException(404,'Simulación no disponible; genera una nueva.') from None
        if method not in models:raise HTTPException(422,'Modelo no válido.')
        if job['results'][method]['status']!='completed':raise HTTPException(409,'El modelo todavía no terminó.')
        try:evidence,deterministic=context(job,method,body.question,body.person_ids)
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        try:
            answer=render_selection(evidence,llm.explain_inspection(evidence));source='ai';error=None
        except Exception as exc:
            answer=deterministic;source='deterministic';error=type(exc).__name__
        return {'simulation_id':str(simulation_id),'method':method,'question':body.question,
                'explanation':answer,'source':source,'error':error,'model':getattr(llm,'model',None),
                'deterministic_explanation':deterministic,'evidence':evidence}

    @app.post('/simulations/{simulation_id}/summary')
    def simulation_summary(simulation_id:UUID):
        from assigment_test_emoralesv.project.assignment_engine.inspection import simulation_summary as build_summary
        try:job=simulations.get(str(simulation_id),private=True)
        except KeyError:raise HTTPException(404,'Simulación no disponible; genera una nueva.') from None
        try:evidence,deterministic=build_summary(job)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        try:
            generated=llm.explain_simulation_summary(evidence)
            answer=generated['response'];source='ai';error=None;model=generated['model']
        except Exception as exc:
            answer=deterministic;source='deterministic';error=type(exc).__name__;model=getattr(llm,'model',None)
        return {'simulation_id':str(simulation_id),'best_method':evidence['best_method'],'best_label':evidence['best_label'],
                'explanation':answer,'source':source,'error':error,'model':model,'deterministic_explanation':deterministic,'evidence':evidence}

    @app.post('/simulations/{simulation_id}/{method}/preview',status_code=201)
    def simulation_preview(simulation_id:UUID,method:str):
        try:job=simulations.get(str(simulation_id),private=True)
        except KeyError:raise HTTPException(404,'Simulación no disponible; genera una nueva.') from None
        if job['state'].get('scenario')=='historical_examples':
            raise HTTPException(409,'Las pruebas históricas son solo ejemplos y no se pueden ejecutar.')
        result=job['results'].get(method)
        if not result or result['status']!='completed':raise HTTPException(409,'El resultado todavía no está disponible.')
        request=job['request']|{'method':method}
        return database.request('POST','/previews',{'request':request,'state_hash':job['state']['state_hash'],'preview':result['plan']})

    @app.get('/dashboard')
    def dashboard():return database.request('GET','/ui/dashboard')

    @app.get('/sellers')
    def sellers():return database.request('GET','/ui/sellers')

    @app.put('/sellers/{seller_id}/technical-skills')
    def technical_skills(seller_id:str,body:TechnicalSkillsRequest):
        return database.request('PUT','/ui/sellers/'+quote(seller_id,safe='')+'/technical-skills',body.model_dump())

    @app.put('/sellers/{seller_id}/eligibility')
    def seller_eligibility(seller_id:str,body:SellerEligibilityRequest):
        return database.request('PUT','/ui/sellers/'+quote(seller_id,safe='')+'/eligibility',body.model_dump())

    @app.get('/records')
    def records(q:str='',status:str='',offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),problem_field:str=''):
        return database.request('GET','/ui/records?'+urlencode(dict(q=q,status=status,offset=offset,limit=limit,problem_field=problem_field)))

    @app.get('/records/{record_id}')
    def record(record_id:str):return database.request('GET','/ui/records/'+quote(record_id,safe=''))

    @app.get('/review-worklist')
    def review_worklist():return database.request('GET','/ui/review-worklist')

    @app.post('/review-analysis-batches')
    def start_review_analysis():
        rows=database.request('GET','/ui/review-worklist')['items']
        candidates=[row['id'] for row in rows if row['ai_status'] in ('pending','running','error')]
        if not candidates:return {'queued':0,'message':'No hay registros pendientes para analizar.'}
        queued=database.request('POST','/ui/review-suggestions/queue',{'record_ids':candidates})['record_ids']
        for record_id in queued:analyzer.submit(analyze_record,record_id)
        return {'queued':len(queued),'workers':analysis_workers}

    @app.post('/review-suggestions/{record_id}/accept')
    def accept_review_suggestion(record_id:str,body:SuggestionAcceptRequest):
        return database.request('POST','/ui/review-suggestions/'+quote(record_id,safe='')+'/accept',body.model_dump(mode='json'))

    @app.post('/review-suggestions/{record_id}/reject')
    def reject_review_suggestion(record_id:str):
        return database.request('PUT','/ui/review-suggestions/'+quote(record_id,safe=''),{'status':'rejected'})

    @app.patch('/records/{record_id}/signals')
    def edit(record_id:str,body:SignalEditRequest):
        return database.request('PATCH','/ui/records/'+quote(record_id,safe='')+'/signals',body.model_dump(mode='json',exclude_unset=True))

    @app.post('/records/{record_id}/analyze-note')
    def analyze_note(record_id:str):
        record=database.request('GET','/ui/records/'+quote(record_id,safe=''))
        extraction=llm.extract(record)
        if not extraction.get('schema_valid'):
            raise HTTPException(503,'No se pudo analizar la nota. Comprueba que el servicio de IA y el modelo estén disponibles.')
        return {'signals':extraction['output'],'model':extraction['model'],'latency':extraction['latency']}

    @app.get('/audit')
    def audit(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100)):
        return database.request('GET','/ui/audit?'+urlencode(dict(offset=offset,limit=limit)))

    @app.get('/assignment-models')
    def list_models():
        return {'models':[{'id':name,'requires_llm':name=='ai_assisted'} for name in models]}

    @app.post('/assignment-previews',status_code=201)
    def preview(request:PreviewRequest):
        state=database.request('POST','/state',{'record_ids':request.record_ids})
        configuration=request.configuration.model_dump(exclude_none=True)
        configuration['effective_date']=state['effective_date']
        result=models[request.method].preview(state['records'],state['sellers'],configuration)
        return with_projection(database.request('POST','/previews',{'request':request.model_dump(),'state_hash':state['state_hash'],'preview':result}))

    @app.get('/assignment-previews/{preview_id}')
    def get_preview(preview_id:UUID):
        return with_projection(database.request('GET',f'/previews/{preview_id}'))

    @app.patch('/assignment-previews/{preview_id}/manual-assignments')
    def manual_assignment_preview(preview_id:UUID,body:ManualAssignmentsRequest):
        return with_projection(database.request('PATCH',f'/previews/{preview_id}/manual-assignments',body.model_dump()))

    @app.post('/assignment-previews/{preview_id}/execute')
    def execute(preview_id:UUID,body:ExecuteRequest):
        return database.request('POST',f'/previews/{preview_id}/execute',body.model_dump())

    @app.get('/records/{record_id}/assignment-explanation')
    def explanation(record_id:str):
        return database.request('GET',f'/records/{record_id}/assignment-explanation')

    @app.post('/records/{record_id}/generate-ai-explanation')
    def ai_explanation(record_id:str):
        current=explanation(record_id)
        try:generated=llm.explain(current['decision_trace'])
        except Exception as exc:
            generated={'model':llm.model or 'unconfigured','prompt':'Explain stored decision trace','response':None,'error':type(exc).__name__}
        generated.update(assignment_id=current['assignment_id'],preview_item_id=current['preview_item_id'])
        return database.request('POST',f'/records/{record_id}/llm-explanations',generated)
    return app

app=create_app()
