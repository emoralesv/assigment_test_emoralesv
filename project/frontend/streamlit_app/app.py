import json
from datetime import datetime
from urllib.parse import quote
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from assigment_test_emoralesv.project.frontend.streamlit_app.client import APIClient, APIError, PreviewWorkflow

st.set_page_config(page_title='Asignación comercial',page_icon='◈',layout='wide')
st.title('Asignación comercial')
st.caption('Revisa los datos, compara propuestas y conserva la trazabilidad de cada decisión.')
client=APIClient()
workflow=PreviewWorkflow(st.session_state)
if st.session_state.get('navigation')=='Dashboard':st.session_state['navigation']='Resumen'
if '_goto' in st.session_state:st.session_state['navigation']=st.session_state.pop('_goto')
view=st.sidebar.radio('Navegación',['Resumen','Registros','Vendedores','Asignación','Auditoría','Simulación de lote','Comparación histórica'],key='navigation')
st.sidebar.caption('')
if st.sidebar.button('Actualizar datos'):st.rerun()

with st.sidebar.expander('Administración de datos'):
    st.caption('Restaura los registros normalizados de ejemplo. Elimina asignaciones, propuestas y cambios operativos actuales.')
    reset_confirmed=st.checkbox('Entiendo que se eliminarán los datos operativos actuales.',key='database_reset_confirm')
    if st.button('Inicializar y reiniciar base de datos',type='primary',disabled=not reset_confirmed,key='database_reset'):
        try:
            with st.spinner('Reiniciando e importando datos normalizados…'):
                result=client.request('POST','/admin/database-reset',{'confirm_reset':True})
            workflow.clear()
            for key in ('assignment_proposals','assignment_proposal_errors','explanation'):
                st.session_state.pop(key,None)
            st.success(result.get('message','Base de datos reiniciada.'))
            st.rerun()
        except APIError as exc:
            st.error('No se pudo reiniciar la base de datos: '+str(exc))

with st.sidebar.container(border=True):
    st.markdown('**Estado de los servicios**')
    try:
        health=client.request('GET','/services')
        database=health.get('database',{});llm=health.get('llm_service',{})
        st.caption('🟢 API · '+('🟢' if database.get('status')=='live' else '🔴')+' Base de datos · '+('🟢' if llm.get('status')=='live' and llm.get('model_available') else '🟠')+' IA')
        with st.expander('Detalles de servicios'):
            database=health.get('database',{})
            if database.get('status')=='live':
                st.markdown('🟢 **Base de datos · Disponible**')
                if not database.get('schema_initialized'):
                    st.caption('Conectada; falta inicializar los datos.')
            else:st.markdown('🔴 **Base de datos · No disponible**')
            llm=health.get('llm_service',{})
            if llm.get('status')=='live':
                st.markdown('🟢 **Servicio de IA · Disponible**')
                processor=llm.get('processor','unknown')
                processor={'not_loaded':'Modelo sin cargar','unknown':'Procesador sin confirmar','cpu':'Procesador (CPU)','gpu':'Tarjeta gráfica (GPU)','mixed':'Procesador y tarjeta gráfica'}.get(processor,processor)
                st.caption(f"{llm.get('model','')} · {processor}")
                preparation=llm.get('preparation',{})
                if preparation.get('status') in ('checking','downloading'):
                    st.info('Preparando modelo: '+preparation.get('downloading_model',preparation.get('requested_model','')))
                    if 'layer_progress' in preparation:st.caption(f"Descarga de la capa actual: {preparation['layer_progress']}%")
                elif preparation.get('status')=='failed':st.warning('No se pudo preparar el modelo: '+preparation.get('error','Error desconocido')+'. Se reintentará automáticamente.')
                elif not llm.get('model_available'):st.warning('El modelo está pendiente de descarga.')
                if preparation.get('fallback_reason'):st.caption('Modelo configurado inexistente; usando el predeterminado: '+preparation.get('selected_model',preparation.get('default_model','')))
            else:st.markdown('🔴 **Servicio de IA · No disponible**')
    except APIError as exc:
        st.markdown('🟠 **API · Responde con error**' if exc.status else '🔴 **API · No disponible**')
        st.markdown('🟠 **Base de datos · Sin confirmar**')
        st.markdown('🟠 **Servicio de IA · Sin confirmar**')
        st.caption('No se pudo verificar la conexión con la base de datos.')
    st.caption('Última consulta: '+datetime.now().astimezone().strftime('%H:%M:%S %Z'))
    st.button('Comprobar estado',key='refresh_services')



def table(items,columns=None):
    if not items:st.info('No hay datos para mostrar.');return
    rows=[{k:v for k,v in row.items() if columns is None or k in columns} for row in items]
    rows=[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in rows]
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)


def dashboard():
    from assigment_test_emoralesv.project.frontend.streamlit_app.dashboard_view import render
    render(client)


def records():
    from assigment_test_emoralesv.project.frontend.streamlit_app.review_worklist import render
    render(client,workflow)


def sellers():
    from assigment_test_emoralesv.project.frontend.streamlit_app.sellers_view import render
    render(client,workflow)


def assignment():
    from assigment_test_emoralesv.project.frontend.streamlit_app.assignment_view import render
    render(client,workflow)


def audit():
    st.subheader('Auditoría de decisiones')
    st.caption('Consulta una decisión por empresa y vendedor. Los identificadores técnicos se conservan en la traza, sin ser necesarios para revisarla.')
    page=st.number_input('Página de historial',min_value=1,step=1)
    with st.spinner('Consultando auditoría…'):data=client.request('GET','/audit',params={'offset':(page-1)*50,'limit':50})
    assignments=data['assignments']
    if not assignments:
        st.info('Aún no hay decisiones ejecutadas para auditar. Genera y ejecuta una propuesta aprobada para ver su trazabilidad aquí.')
        return
    frame=pd.DataFrame(assignments);frame['created_at']=pd.to_datetime(frame['created_at'],utc=True,errors='coerce')
    current=int(frame['is_current'].sum());historical=len(frame)-current
    a,b,c,d=st.columns(4)
    a.metric('Asignaciones registradas',data['total'])
    b.metric('Vigentes en esta página',current)
    c.metric('Históricas en esta página',historical)
    d.metric('Eventos registrados',data['event_total'])
    st.caption('Las gráficas reflejan la página consultada; cambia la página para recorrer el historial sin cargar tablas extensas.')
    chart_left,chart_right=st.columns(2)
    by_seller=frame.groupby('seller_name',dropna=False).size().sort_values()
    chart_left.plotly_chart(go.Figure(go.Bar(x=by_seller.values,y=by_seller.index,orientation='h',marker_color='#0d9488',text=by_seller.values,textposition='auto',hovertemplate='%{y}<br>%{x} decisiones<extra></extra>')).update_layout(height=310,margin={'l':10,'r':10,'t':30,'b':20},title='Decisiones por vendedor'),width='stretch',key='audit_seller_chart')
    by_event=pd.DataFrame(data['events']).groupby('event_type',dropna=False).size().sort_values(ascending=False) if data['events'] else pd.Series(dtype=int)
    if not by_event.empty:
        chart_right.plotly_chart(go.Figure(go.Pie(labels=by_event.index,values=by_event.values,hole=.62,textinfo='label+percent')).update_layout(height=310,margin={'l':10,'r':10,'t':30,'b':20},title='Eventos de auditoría'),width='stretch',key='audit_event_chart')
    else:chart_right.info('Aún no hay eventos registrados.')
    if frame['created_at'].notna().any():
        timeline=frame.assign(fecha=frame['created_at'].dt.strftime('%d %b')).groupby(['fecha','seller_name'],dropna=False).size().reset_index(name='decisiones')
        figure=go.Figure()
        for seller,rows in timeline.groupby('seller_name',dropna=False):
            figure.add_trace(go.Bar(name=str(seller),x=rows['fecha'],y=rows['decisiones']))
        figure.update_layout(barmode='stack',height=280,margin={'l':10,'r':10,'t':30,'b':30},title='Evolución de decisiones por fecha',yaxis_title='Decisiones')
        st.plotly_chart(figure,width='stretch',key='audit_timeline_chart')
    def record_label(row):
        place=' · '.join(part for part in (row.get('city'),row.get('zone')) if part)
        return f"{row['company_name']} · {row['seller_name']}"+(f" · {place}" if place else '')
    selected=st.selectbox('Decisión que quieres revisar',assignments,format_func=record_label,key='audit_record')
    rid=str(selected['record_id'])
    context_a,context_b,context_c=st.columns(3)
    context_a.metric('Empresa',selected['company_name'])
    context_b.metric('Vendedor',selected['seller_name'])
    context_c.metric('Estado del registro',selected.get('status','—'))
    if st.button('Ver explicación de la decisión'):
        with st.spinner('Consultando decisión…'):
            st.session_state['explanation']=client.request('GET',f'/records/{quote(rid,safe="")}/assignment-explanation')
    explanation=st.session_state.get('explanation')
    if explanation and explanation['record_id']==rid:
        st.subheader('Explicación determinista');st.write(explanation['explanation'])
        with st.expander('Traza oficial'):st.json(explanation['decision_trace'])
        if st.button('Generar explicación IA (opcional)'):
            with st.spinner('Generando explicación complementaria…'):
                result=client.request('POST',f'/records/{quote(rid.strip(),safe="")}/generate-ai-explanation')
            if result.get('error'):
                reason={'ModelNotReadyError':'El modelo se está preparando o no está disponible; revisa el panel de servicios.','TimeoutError':'Ollama superó el tiempo de espera.','URLError':'No se pudo conectar con Ollama.','ValidationError':'La respuesta IA no cumple el esquema.','JSONDecodeError':'La respuesta IA no contiene JSON válido.'}.get(result['error'],result['error'])
                st.warning(reason+' Se conserva la explicación determinista.')
            st.caption('Fuente: '+result['source']);st.write(result['explanation'])
    with st.expander('Ver detalle tabular de asignaciones'):
        table(assignments,['company_name','seller_name','city','zone','status','created_at','is_current'])
    with st.expander(f"Eventos y reasignaciones ({data['event_total']})"):
        table(data['events'],['company_name','seller_name','event_type','created_at','payload'])

def load_analysis():
    from assigment_test_emoralesv.project.frontend.streamlit_app.load_view import render
    render(client)

def historical_analysis():
    from assigment_test_emoralesv.project.frontend.streamlit_app.load_view import render
    render(client,historical=True)

try:
    {'Comparación histórica':historical_analysis,'Simulación de lote':load_analysis,'Resumen':dashboard,'Registros':records,'Vendedores':sellers,'Asignación':assignment,'Auditoría':audit}[view]()
except APIError as exc:
    if exc.status==409:st.warning('Los datos cambiaron. Actualiza y vuelve a intentarlo.')
    st.error(str(exc))
