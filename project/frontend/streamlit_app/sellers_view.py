"""Availability-first seller operations view."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from .client import APIError

REASONS={'SELLER_ABSENT':'Ausencia activa','SELLER_INACTIVE':'Vendedor inactivo','INVALID_TEAM':'Equipo inválido o faltante',
         'MISSING_SELLER_ZONE':'Zona faltante','CAPACITY_UNDEFINED':'Capacidad sin definir','ZERO_CAPACITY':'Capacidad cero',
         'CAPACITY_EXHAUSTED':'Capacidad agotada','ABSENCE_REQUIRES_REVIEW':'Ausencia por revisar'}

def render(client,workflow):
    data=client.request('GET','/sellers');people=data['items'];eligible=[p for p in people if p['available']];blocked=[p for p in people if not p['available']]
    st.subheader('Disponibilidad del equipo')
    st.caption('Corrige las condiciones operativas que excluyen vendedores antes de generar una asignación.')
    free=sum(max(0,(p['maximum_capacity'] or 0)-p['open_workload']) for p in eligible)
    for col,label,value in zip(st.columns(4),['Vendedores elegibles','Fuera del reparto','Cupos libres','Utilización elegible'],
        [len(eligible),len(blocked),free,f"{sum(p['open_workload'] for p in eligible)/sum((p['maximum_capacity'] or 0) for p in eligible):.0%}" if sum((p['maximum_capacity'] or 0) for p in eligible) else 'Sin datos']):col.metric(label,value)
    reasons={reason:sum(reason in p['availability_reasons'] for p in blocked) for p in people for reason in p['availability_reasons']}
    if reasons:
        ordered=sorted(reasons,key=reasons.get,reverse=True)
        fig=go.Figure(go.Bar(y=[REASONS.get(reason,reason) for reason in ordered],x=[reasons[reason] for reason in ordered],orientation='h',
            marker_color='#dc2626',text=[reasons[reason] for reason in ordered],textposition='auto',hovertemplate='%{y}: %{x} vendedor(es)<extra></extra>'))
        fig.update_layout(title='Motivos de no elegibilidad',height=max(280,45*len(ordered)),xaxis_title='Vendedores',yaxis={'autorange':'reversed'})
        st.plotly_chart(fig,width='stretch',config={'displayModeBar':False})
    if blocked:
        st.subheader('Personas que requieren atención')
        selected_id=st.selectbox('Selecciona una persona para revisar o subsanar',[p['id'] for p in blocked],
            format_func=lambda sid:next(p['name']+' · '+', '.join(REASONS.get(r,r) for r in p['availability_reasons']) for p in blocked if p['id']==sid))
    else:
        st.success('Todos los vendedores cumplen las condiciones de elegibilidad.');selected_id=eligible[0]['id'] if eligible else None
    with st.expander('Carga de personas elegibles'):
        ordered=sorted(eligible,key=lambda p:p['open_workload']/(p['maximum_capacity'] or 1),reverse=True)
        if ordered:
            fig=go.Figure(go.Bar(y=[p['name'] for p in ordered],x=[100*p['open_workload']/(p['maximum_capacity'] or 1) for p in ordered],orientation='h',
                text=[f"{p['open_workload']} de {p['maximum_capacity']}" for p in ordered],textposition='auto',marker_color='#0d9488'))
            fig.update_layout(xaxis={'title':'Utilización (%)','range':[0,100]},yaxis={'autorange':'reversed'},height=max(260,42*len(ordered)))
            st.plotly_chart(fig,width='stretch',config={'displayModeBar':False})
    if not selected_id:return
    seller=next(p for p in people if p['id']==selected_id)
    st.divider();st.subheader('Resolver condiciones · '+seller['name'])
    if seller['availability_reasons']:
        st.warning('Motivos actuales: '+', '.join(REASONS.get(r,r) for r in seller['availability_reasons']))
    st.caption('Los cambios operativos quedan auditados y se usan en la siguiente simulación; no reemplazan los datos recibidos.')
    teams=data.get('teams',[]);team_ids=['']+[t['id'] for t in teams]
    labels={'': 'Sin equipo'}|{t['id']:t['name']+' ('+t['id']+')' for t in teams}
    with st.form('eligibility:'+seller['id']):
        active=st.checkbox('Vendedor activo',value=bool(seller.get('active')))
        team=st.selectbox('Equipo operativo',team_ids,index=team_ids.index(seller.get('team_id') or '') if (seller.get('team_id') or '') in team_ids else 0,format_func=lambda x:labels[x])
        zones=['']+data.get('zones',[])
        current_zone=seller.get('zone') or ''
        if current_zone not in zones:zones.append(current_zone)
        zone=st.selectbox('Zona operativa',zones,index=zones.index(current_zone),format_func=lambda value:value or 'Sin zona')
        capacity=st.number_input('Capacidad máxima',min_value=0,max_value=10000,value=int(seller['maximum_capacity'] or 0),step=1)
        override=st.checkbox('Autorizar disponibilidad pese a una ausencia activa',value=bool(seller.get('availability_override')),
            help='Úsalo solo tras confirmar que la ausencia ya no aplica. No elimina el dato fuente de la ausencia.')
        reason=st.text_input('Motivo de la corrección')
        save=st.form_submit_button('Guardar condiciones operativas')
    if save:
        if not reason.strip():st.error('Indica el motivo de la corrección.')
        else:
            try:
                client.request('PUT','/sellers/'+seller['id']+'/eligibility',{'active':active,'team_id':team or None,'zone':zone or None,
                    'maximum_capacity':capacity,'availability_override':override,'reason':reason.strip()})
                workflow.clear();st.success('Condiciones operativas actualizadas.');st.rerun()
            except APIError as exc:st.error(str(exc))
    st.subheader('Habilidades técnicas verificadas')
    evidence={entry['skill']:entry['evidence'] for entry in seller.get('technical_skill_evidence',[])}
    catalog_map=data.get('technical_skill_catalog',{})
    catalog=list(dict.fromkeys(list(catalog_map)+list(evidence)))
    selected_skills=st.multiselect('Habilidades verificadas',catalog,default=list(evidence),
        format_func=lambda skill:catalog_map.get(skill,skill),key='skills:'+seller['id'])
    with st.form('save_skills:'+seller['id']):
        edited={skill:st.text_input('Evidencia para '+catalog_map.get(skill,skill),value=evidence.get(skill,''),key='evidence:'+seller['id']+':'+skill) for skill in selected_skills}
        skill_reason=st.text_input('Motivo de la actualización de habilidades')
        save_skills=st.form_submit_button('Guardar habilidades verificadas')
    if save_skills:
        skills=[{'skill':skill,'evidence':value} for skill,value in edited.items()]
        if not skill_reason.strip():st.error('Indica el motivo de la actualización.')
        else:
            try:
                client.request('PUT','/sellers/'+seller['id']+'/technical-skills',{'skills':skills,'reason':skill_reason.strip()})
                workflow.clear();st.success('Habilidades verificadas guardadas.');st.rerun()
            except APIError as exc:st.error(str(exc))
