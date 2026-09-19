"""Actionable overview backed by complete server aggregates."""
import plotly.graph_objects as go
import streamlit as st

FIELDS={'notas':'Notas por revisar','zona':'Zona','nit':'Identificación','ingresos_estimados':'Montos por revisar',
        'empleados':'Número de empleados','sector':'Sector','fecha_creacion':'Fecha de creación','fecha_ingreso':'Fecha de ingreso','capacidad_maxima':'Capacidad máxima','lider_id':'Líder','desde':'Inicio de ausencia','hasta':'Fin de ausencia','equipo_id':'Equipo'}
REASONS={'SELLER_ABSENT':'Ausencia','SELLER_INACTIVE':'Inactivo','ROLE_NOT_SELLER':'Otro rol',
         'INVALID_TEAM':'Sin equipo válido','MISSING_SELLER_ZONE':'Sin zona','CAPACITY_UNDEFINED':'Sin capacidad definida',
         'ZERO_CAPACITY':'Capacidad cero','CAPACITY_EXHAUSTED':'Capacidad agotada','ABSENCE_REQUIRES_REVIEW':'Ausencia por revisar'}


def open_records(field):
    st.session_state['record_problem_field']=field
    st.session_state['records_page']=1
    st.session_state['_goto']='Registros'
    st.rerun()


def render(client):
    data=client.request('GET','/dashboard');m=data['metrics']
    st.subheader('Resumen operativo')
    for col,label,key in zip(st.columns(4),['Pendientes con candidato','Pendientes sin candidato','Vendedores elegibles','Cupos libres elegibles'],
                             ['ready_records','blocked_records','available_sellers','free_capacity']):
        col.metric(label,m.get(key,0))
    st.caption('Con candidato: al menos un vendedor cumple las restricciones actuales para ese registro. No garantiza que todos puedan asignarse juntos; comparten cupos.')
    st.subheader('Disponibilidad del equipo')
    excluded=data.get('exclusions',[])
    if excluded:
        fig=go.Figure(go.Bar(x=[r['people'] for r in excluded],y=[REASONS.get(r['reason'],r['reason']) for r in excluded],orientation='h',text=[r['people'] for r in excluded],textposition='auto',marker_color='#64748b',hovertemplate='%{y}: %{x} personas<extra></extra>'))
        fig.update_layout(xaxis_title='Personas excluidas',yaxis={'autorange':'reversed'},height=max(280,len(excluded)*38))
        st.plotly_chart(fig,width='stretch',key='availability_chart',config={'displayModeBar':False})
        st.caption('Incluye todos los roles. Una persona puede tener varios motivos; ausencia u otro rol no implican un error en sus datos.')
    else:st.info('No hay exclusiones registradas.')
    st.subheader('Carga del equipo elegible')
    eligible=[p for p in data.get('people',[]) if p['available']]
    eligible.sort(key=lambda p:p['open_workload']/p['maximum_capacity'],reverse=True)
    if eligible:
        fig=go.Figure(go.Bar(y=[p['name'] for p in eligible],x=[100*p['open_workload']/p['maximum_capacity'] for p in eligible],orientation='h',
            text=[f"{p['open_workload']} de {p['maximum_capacity']}" for p in eligible],textposition='auto',marker_color='#0d9488',hovertemplate='%{y}<br>%{x:.1f}% · %{text}<extra></extra>'))
        fig.update_layout(xaxis={'title':'Utilización (%)','range':[0,100]},yaxis={'autorange':'reversed'},height=max(300,len(eligible)*45))
        st.plotly_chart(fig,width='stretch',key='workload_chart',config={'displayModeBar':False})
    else:st.info('No hay vendedores elegibles en este momento.')
    with st.expander('Personas fuera del reparto'):
        for p in data.get('people',[]):
            if not p['available']:st.write(f"**{p['name']}** · "+', '.join(REASONS.get(r,r) for r in p['availability_reasons'])+f" · Carga actual: {p['open_workload']}")
