"""Presentation only: every projection and metric comes from FastAPI."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from .client import APIError, PreviewWorkflow

LABELS={'actual':'Actual','capacity_aware':'Capacity-Aware','fuzzy_optimal':'Fuzzy Optimal','ai_assisted':'AI-Assisted'}
COLORS={'actual':'#64748b','capacity_aware':'#2563eb','fuzzy_optimal':'#0d9488','ai_assisted':'#9333ea'}


def charts(scenarios,people_ids,baseline_label='Actual'):
    tabs=st.tabs(['Carga de registros','Utilización de capacidad','Montos estimados'])
    for tab,field,title in zip(tabs,['open_workload','utilization','estimated_amount'],['Registros activos','Utilización de capacidad','Subtotal conocido de ingresos estimados · unidad monetaria de origen']):
        with tab:
            fig=go.Figure()
            for method,result in scenarios.items():
                people=[p for p in result['people'] if p['id'] in people_ids]
                fig.add_trace(go.Bar(name=baseline_label if method=='actual' else LABELS[method],x=[f"{p['name']} ({p['id']})" for p in people],y=[p[field] for p in people],
                    marker_color=COLORS[method],customdata=[[p['missing_amounts'],p['inferred_ownership']] for p in people],
                    hovertemplate='%{x}<br>%{y}<br>Montos faltantes: %{customdata[0]}<br>Propiedad histórica inferida: %{customdata[1]}<extra>%{fullData.name}</extra>'))
            if field=='open_workload':
                base=[p for p in scenarios['actual']['people'] if p['id'] in people_ids]
                fig.add_trace(go.Scatter(name='Capacidad máxima',x=[f"{p['name']} ({p['id']})" for p in base],y=[p['maximum_capacity'] for p in base],mode='markers',marker={'symbol':'line-ew','size':18,'color':'#ef4444'}))
            if field=='utilization':fig.update_yaxes(tickformat='.0%');fig.add_hline(y=1,line_dash='dash',line_color='#ef4444')
            fig.update_layout(barmode='group',yaxis_title=title,height=450,legend={'orientation':'h'},margin={'t':40,'b':90},yaxis={'rangemode':'tozero'})
            st.plotly_chart(fig,use_container_width=True)


@st.fragment
def inspection_question(client, data, chosen, after, state_key):
    st.subheader('Preguntar sobre esta asignación')
    st.caption('Se enviará a Ollama la inspección resumida del modelo seleccionado, con cargas y motivos de exclusión. No cambia asignaciones.')
    question_key=state_key+'_question_'+data['id']+'_'+chosen
    with st.form(question_key):
        compared=st.multiselect('Personas a comparar (opcional, máximo 3)',[p['id'] for p in after],
            format_func=lambda sid:next(p['name'] for p in after if p['id']==sid),max_selections=3)
        question=st.text_area('Tu pregunta',placeholder='¿Por qué Santiago tiene 8 registros y la otra persona 0?')
        ask=st.form_submit_button('Explicar con la inspección')
    if ask:
        if len(question.strip())<3:st.warning('Escribe una pregunta antes de enviarla.')
        else:
            try:
                with st.spinner('Analizando la inspección con Ollama…'):
                    answer=client.request('POST',f"/simulations/{data['id']}/{chosen}/explanation",{'question':question.strip(),'person_ids':compared})
                st.session_state[question_key+'_answer']=answer
            except APIError as exc:st.error(str(exc))
    answer=st.session_state.get(question_key+'_answer')
    if answer:
        st.caption('Pregunta: '+answer['question'])
        if answer['error']:st.warning('La IA no pudo responder ('+answer['error']+'). Se muestran los datos verificados de la inspección.')
        else:st.caption('Explicación guiada por IA con hechos verificados · '+str(answer['model']))
        st.write(answer['explanation'])
        with st.expander('Datos verificados y contexto enviado'):
            st.write(answer['deterministic_explanation'])
            st.json(answer['evidence'])


def render(client,historical=False):
    st.subheader('Comparación de métodos con datos históricos' if historical else 'Simulación de lote pendiente')
    state_key='historical_simulation' if historical else 'simulation'
    if historical:
        st.info('Compara métodos usando registros históricos en memoria. Cada persona parte de carga cero y se mantienen capacidad, disponibilidad y restricciones de notas. No permite ejecutar asignaciones ni modifica el historial real.')
        st.caption('Sirve para observar el comportamiento de los métodos sobre ejemplos anteriores; los perfiles conservan experiencia observada en esos mismos datos.')
    st.caption('Las barras de montos muestran subtotales conocidos: revisa los faltantes antes de comparar. Cartera activa: asignados + en gestión. Los montos son ingresos estimados de las empresas, no ventas cerradas. No se asume moneda ni periodicidad.')
    if not historical:
        first,second,third=st.columns(3)
        first.markdown('**Carga actual**\n\nDescribe la cartera real: registros activos, capacidad y disponibilidad. No cambia datos.')
        second.markdown('**Simulación de lote**\n\nProyecta cómo repartir los registros pendientes que selecciones entre los tres métodos. No ejecuta asignaciones.')
        third.markdown('**Para qué sirve**\n\nCompara el antes y después del mismo equipo para elegir una propuesta que luego puedes llevar a revisión manual.')
    with st.spinner('Leyendo cartera y capacidad…'):current=client.request('GET','/historical-load-analysis' if historical else '/load-analysis')
    people=current['people']
    if not people:st.info('No hay personas para analizar.');return
    a,b,c=st.columns(3)
    team=a.selectbox('Equipo',['Todos']+sorted({str(p['team_id']) for p in people if p['team_id'] is not None}))
    zone=b.selectbox('Zona',['Todas']+sorted({p['zone'] for p in people if p['zone']}))
    availability=c.selectbox('Disponibilidad',['Todas','Disponibles','No disponibles'])
    def visible(rows):
        return {p['id'] for p in rows if (team=='Todos' or str(p['team_id'])==team) and (zone=='Todas' or p['zone']==zone) and (availability=='Todas' or p['available']==(availability=='Disponibles'))}
    ids=visible(people)
    with st.expander('Antes de asignar · carga inicial cero' if historical else 'Antes de asignar · cartera actual',expanded=not st.session_state.get(state_key+'_id')):
        if not ids:st.info('No hay personas que coincidan con los filtros.')
        else:charts({'actual':current},ids,'Antes: carga cero' if historical else 'Antes: carga actual')
        st.dataframe(pd.DataFrame([{k:p[k] for k in ('name','role','assigned_count','in_management_count','maximum_capacity','remaining_capacity','missing_amounts','availability_reasons')} for p in people if p['id'] in ids]),hide_index=True,use_container_width=True)
        u=current['unattributed']
        st.caption(f"Registros activos sin propietario: {u['records']} · monto conocido: {u['estimated_amount']:,.2f} · montos faltantes: {u['missing_amounts']}.")
    st.divider();st.subheader('Comparar los tres modelos')
    selected=[]
    if not historical:
        rows=[];offset=0
        while True:
            page=client.request('GET','/records',params={'status':'nuevo','limit':100,'offset':offset})
            rows.extend(page['items']);offset+=len(page['items'])
            if offset>=page['total'] or not page['items']:break
        names={r['id']:r for r in rows}
        selected=st.multiselect('Lote de prueba (máximo 100)',list(names),format_func=lambda i:f"{i} · {names[i]['company_name']}",max_selections=100,key='simulation_records')
        st.caption('Puedes incluir registros bloqueados: aparecerán sin asignar. Simular no modifica propietarios ni guarda previews ejecutables.')
    st.subheader('Criterios del experimento')
    balance=st.slider('Prioridad del equilibrio frente a compatibilidad',0.0,1.0,0.60,0.05,key=state_key+'_balance')
    amount_weight=st.slider('Peso de los montos dentro del equilibrio',0.0,1.0,0.5,0.05,key=state_key+'_amount')
    st.caption('Equilibrio: 0 favorece afinidad; 1 reparte carga relativa. El valor inicial 0.60 permite distinguirlo de Carga, que nunca usa estos controles. Montos: 0 prioriza registros por capacidad; 1 incorpora montos conocidos por capacidad.')
    if st.button('Probar los tres métodos con todos los datos' if historical else 'Simular los tres modelos',disabled=not historical and not selected):
        response=client.request('POST','/historical-simulations',{'balance_weight':balance,'amount_weight':amount_weight}) if historical else client.request('POST','/simulations',{'record_ids':sorted(selected),'configuration':{'balance_weight':balance,'amount_weight':amount_weight}})
        st.session_state[state_key+'_id']=response['id']
        st.session_state.pop(state_key+'_result',None)
        st.session_state.pop(state_key+'_automatic_summary',None)
    if not st.session_state.get(state_key+'_id'):return

    def results():
        if not st.session_state.get(state_key+'_id'):
            st.info('Genera una nueva simulación para ver resultados.');return
        data=st.session_state.get(state_key+'_result')
        try:
            if not data or not data['complete']:
                data=client.request('GET','/simulations/'+st.session_state[state_key+'_id'])
                st.session_state[state_key+'_result']=data
        except APIError as exc:
            if exc.status==404:
                st.session_state.pop(state_key+'_id',None)
                st.session_state.pop(state_key+'_result',None)
                st.warning('La simulación ya no está disponible: pudo caducar o reiniciarse la API. Genera una nueva.');return
            st.error(str(exc));return
        if not data['complete']:
            st.caption('Los modelos siguen calculando en segundo plano. Actualiza para consultar su avance.')
            if st.button('Actualizar resultados',key=state_key+'_refresh'):
                st.rerun()
        st.caption('Lote comparado: '+', '.join(data['request']['record_ids'])+' · Fecha: '+data['effective_date'])
        if (not historical and sorted(selected)!=data['request']['record_ids']) or balance!=data['request']['configuration']['balance_weight'] or amount_weight!=data['request']['configuration'].get('amount_weight',.5):
            st.warning('La selección cambió. Los resultados corresponden al lote anterior; vuelve a simular para compararlo.')
        done=sum(r['status'] in ('completed','failed') for r in data['results'].values())
        st.progress(done/3,text=f'{done}/3 modelos terminados')
        scenarios={'actual':data['baseline']};summary=[]
        for name,result in data['results'].items():
            if result['status']=='queued':st.info(LABELS[name]+': en cola; los otros modelos pueden seguir avanzando.');continue
            if result['status']=='running':st.info(LABELS[name]+': calculando…');continue
            if result['status']=='failed':st.error(LABELS[name]+': '+result['error']);continue
            scenarios[name]=result['projection'];m=result['projection']['metrics']
            summary.append({'Modelo':LABELS[name],'Asignados':m['assigned'],'Sin asignar':m['unassigned'],
                'Dispersión de utilización (pp)':m['workload_dispersion']*100 if m['workload_dispersion'] is not None else None,
                'Utilización (%)':m['capacity_utilization']*100 if m['capacity_utilization'] is not None else None,
                'Dispersión montos conocidos/capacidad (%)':m.get('known_amount_relative_cv')*100 if m.get('known_amount_relative_cv') is not None else None,
                'Montos faltantes':m.get('missing_amounts'),
                'Dispersión de montos completos':m['amount_dispersion'],'Personas con monto completo':m['amount_comparable_people'],
                'Personas comparadas':m['balance_cohort'],'Compatibilidad zona (%)':m['zone_compatibility']*100 if m['zone_compatibility'] is not None else None,
                'Compatibilidad segmento (%)':m['segment_compatibility']*100 if m['segment_compatibility'] is not None else None,
                'Restricciones cumplidas':m['mandatory_constraints_respected'],'Registros bloqueados':m['blocked_records'],
                'Avisos de calidad':m['quality_warnings']})
            warnings=result['plan']['trace'].get('warnings',[])
            if warnings:st.warning(f'{LABELS[name]}: advertencias o fallback; revisa la traza antes de comparar.')
        if len(scenarios)>1:
            st.subheader('Después de asignar · resultados simulados')
            chosen=st.selectbox('Modelo para la tabla posterior',[m for m in scenarios if m!='actual'],format_func=lambda m:LABELS[m],key=state_key+'_after_model_'+data['id'])
            after=scenarios[chosen]['people']
            st.dataframe(pd.DataFrame([{
                'Persona':p['name'],'Rol':p['role'],'Elegible al inicio':p['available'],
                'Carga antes':p['initial_workload'],'Nuevas asignaciones':p['proposed_assignments'],
                'Asignados después':p['assigned_count'],'En gestión después':p['in_management_count'],
                'Carga después':p['open_workload'],'Capacidad máxima':p['maximum_capacity'],
                'Capacidad restante':p['remaining_capacity'],'Utilización después (%)':p['utilization']*100 if p['utilization'] is not None else None,
                'Monto conocido después':p['estimated_amount'],'Montos faltantes':p['missing_amounts'],
                'Motivos de exclusión':', '.join(p['availability_reasons'])
            } for p in after if p['id'] in visible(after)]),hide_index=True,use_container_width=True)
            st.caption('Este es el resultado proyectado del modelo seleccionado. El equilibrio se evalúa entre vendedores elegibles; personas ausentes, inactivas o con otro rol pueden permanecer en cero.')
            charts(scenarios,visible(data['baseline']['people']),'Inicio ficticio (carga cero)' if historical else 'Actual al simular')
            st.dataframe(pd.DataFrame(summary),hide_index=True,use_container_width=True)
            st.caption('Menor dispersión = mayor equilibrio. Se compara el grupo disponible al inicio, sin cambiarlo entre modelos. Dispersión monetaria: solo carteras con montos completos; requiere al menos dos personas; revisa «Personas con monto completo». Las métricas corresponden al lote completo, aunque filtres las gráficas.')
            if done==3:
                summary_key=state_key+'_automatic_summary'
                automatic=st.session_state.get(summary_key)
                if not automatic or automatic.get('simulation_id')!=data['id']:
                    with st.spinner('Preparando conclusión automática…'):
                        try:
                            automatic=client.request('POST',f"/simulations/{data['id']}/summary")
                            st.session_state[summary_key]=automatic
                        except APIError as exc:
                            automatic={'simulation_id':data['id'],'explanation':'No se pudo generar la conclusión automática: '+str(exc),'source':'error'}
                            st.session_state[summary_key]=automatic
                st.subheader('Conclusión automática')
                if automatic['source']=='ai':st.caption('Explicación de IA basada en los resultados verificados · '+str(automatic.get('model','')))
                elif automatic['source']=='deterministic':st.caption('La IA no estuvo disponible; se muestran los resultados verificados.')
                st.write(automatic['explanation'])
            inspection_question(client,data,chosen,after,state_key)
        for name,result in data['results'].items():
            if result['status']!='completed':continue
            with st.expander('Inspeccionar '+LABELS[name]):
                st.json(result['plan'])
                if st.button('Llevar a revisión',key='review:'+data['id']+name,disabled=historical or not result['plan']['assignments']):
                    try:
                        preview=client.request('POST',f"/simulations/{data['id']}/{name}/preview")
                        request=data['request']|{'method':name}
                        PreviewWorkflow(st.session_state).store(preview,request)
                        st.session_state['selected_records']=request['record_ids']
                        st.session_state['method']=name
                        st.session_state['assignment_configuration']=request['configuration']
                        st.session_state['_goto']='Asignación'
                        st.rerun()
                    except APIError as exc:st.error('No se pudo preparar el preview: '+str(exc))
    results()
