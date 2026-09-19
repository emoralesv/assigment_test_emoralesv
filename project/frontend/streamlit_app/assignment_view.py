"""Preview, compare, and manually validate assignment proposals."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from .client import APIError

LABELS={'capacity_aware':'Carga','fuzzy_optimal':'Equilibrio','ai_assisted':'IA asistida'}
MODEL_DESCRIPTIONS={
    'capacity_aware':'Regla local: siempre entrega el siguiente registro a quien tenga menor carga relativa.',
    'fuzzy_optimal':'Optimización de lote: combina equilibrio, montos y afinidad del vendedor.',
    'ai_assisted':'Optimización de lote con requisitos extraídos de las notas antes de calcular afinidad.'}

def charts(projection):
    people=[p for p in projection['people'] if p['capacity']]
    names=[p['name'] if p['eligible'] else '⚠ '+p['name'] for p in people]
    unavailable=['; '.join(p['availability_reasons']) or 'No disponible' for p in people]
    tabs=st.tabs(['Cartera previa y nuevas asignaciones','Montos conocidos'])
    with tabs[0]:
        fig=go.Figure()
        for name,key,color in [('Cartera previa','before_workload','#94a3b8'),('Nuevas asignaciones','additions','#0d9488')]:
            fig.add_trace(go.Bar(name=name,x=names,y=[p[key] for p in people],marker_color=['#dc2626' if not p['eligible'] else color for p in people],
                text=[f"{p['before_workload']} + {p['additions']} = {p['after_workload']} de {p['capacity']} ({p['after_workload']/p['capacity']:.0%})" if key=='additions' else '' for p in people],textposition='auto'))
        fig.add_trace(go.Scatter(name='Capacidad',x=names,y=[p['capacity'] for p in people],mode='markers',marker={'symbol':'line-ew','size':18,'color':'#dc2626'},customdata=unavailable,hovertemplate='%{x}<br>%{customdata}<extra></extra>'))
        fig.update_layout(barmode='stack',height=400,yaxis_title='Registros activos',legend={'orientation':'h'},margin={'b':100})
        st.plotly_chart(fig,width='stretch')
    with tabs[1]:
        fig=go.Figure()
        for name,key,color in [('Monto previo','before_amount','#94a3b8'),('Monto de nuevas asignaciones','new_amount','#7c3aed')]:
            values=[p['after_amount']-p['before_amount'] if key=='new_amount' else p[key] for p in people]
            fig.add_trace(go.Bar(name=name,x=names,y=values,marker_color=['#dc2626' if not p['eligible'] else color for p in people]))
        fig.update_layout(barmode='stack',height=400,yaxis_title='Monto estimado conocido',legend={'orientation':'h'},margin={'b':100})
        st.plotly_chart(fig,width='stretch')

def proposal_cards(proposals,errors=None,render_key='final'):
    """Compact, progressively rendered comparison while the batch is running."""
    errors=errors or {}
    methods=('capacity_aware','fuzzy_optimal','ai_assisted')
    for column,method in zip(st.columns(3),methods):
        column.markdown('**'+LABELS.get(method,method)+'**')
        column.caption(MODEL_DESCRIPTIONS.get(method,''))
        if method in errors:
            column.error('No se pudo generar: '+errors[method])
            continue
        proposal=proposals.get(method)
        if not proposal:
            column.info('Calculando…')
            continue
        metrics=proposal.get('projection',{}).get('metrics',{})
        column.metric('Asignados',len(proposal.get('assignments',[])))
        column.metric('Sin asignar',len(proposal.get('unassigned',[])))
        dispersion=metrics.get('after_workload_dispersion')
        equilibrium=max(0,1-dispersion) if isinstance(dispersion,float) else None
        compatibility=metrics.get('compatibility_rate')
        column.metric('Equilibrio de carga',f'{equilibrium:.0%}' if equilibrium is not None else 'sin datos')
        column.metric('Compatibilidad observada',f'{compatibility:.0%}' if isinstance(compatibility,float) else 'sin datos')
        column.caption('Equilibrio = 1 − dispersión de utilización. Compatibilidad = coincidencias verificables de zona y segmento.')
        people=[person for person in proposal.get('projection',{}).get('people',[]) if person.get('capacity')]
        if people:
            before=[person['before_workload']/person['capacity'] if person['capacity'] else 0 for person in people]
            additions=[person['additions']/person['capacity'] if person['capacity'] else 0 for person in people]
            figure=go.Figure()
            names=[person['name'] if person['eligible'] else '⚠ '+person['name'] for person in people]
            colors=['#dc2626' if not person['eligible'] else '#7c3aed' if method=='ai_assisted' else '#0d9488' for person in people]
            figure.add_trace(go.Bar(name='Antes',x=names,y=before,marker_color=['#fecaca' if not person['eligible'] else '#94a3b8' for person in people]))
            figure.add_trace(go.Bar(name='Nuevas',x=names,y=additions,marker_color=colors,text=[f"{(base+new):.0%}" for base,new in zip(before,additions)],textposition='auto',customdata=['; '.join(person['availability_reasons']) for person in people],hovertemplate='%{x}<br>%{y:.0%}<br>%{customdata}<extra></extra>'))
            figure.update_layout(barmode='stack',height=210,margin={'l':8,'r':8,'t':8,'b':55},yaxis={'tickformat':'.0%','title':'Utilización'},legend={'orientation':'h'})
            column.plotly_chart(figure,width='stretch',key='proposal-chart-'+render_key+'-'+method+'-'+proposal['preview_id'])

def tradeoff_chart(proposals):
    points=[]
    for method,proposal in proposals.items():
        metrics=proposal.get('projection',{}).get('metrics',{})
        dispersion=metrics.get('after_workload_dispersion')
        compatibility=metrics.get('compatibility_rate')
        if dispersion is not None and compatibility is not None:
            points.append((method,max(0,1-dispersion),compatibility,len(proposal.get('assignments',[]))))
    if not points:return
    bars=go.Figure()
    bars.add_trace(go.Bar(name='Equilibrio de carga',x=[LABELS.get(point[0],point[0]) for point in points],y=[point[1] for point in points],marker_color='#0d9488'))
    bars.add_trace(go.Bar(name='Compatibilidad observada',x=[LABELS.get(point[0],point[0]) for point in points],y=[point[2] for point in points],marker_color='#7c3aed'))
    bars.update_layout(barmode='group',height=310,yaxis={'title':'Puntuación (%)','tickformat':'.0%','range':[0,1]},margin={'l':20,'r':20,'t':25,'b':35},title='Equilibrio frente a compatibilidad')
    st.plotly_chart(bars,width='stretch',key='assignment_scoreboard')
    figure=go.Figure(go.Scatter(
        x=[point[2] for point in points],y=[point[1] for point in points],mode='markers+text',
        text=[LABELS.get(point[0],point[0]) for point in points],textposition='top center',
        marker={'size':[max(14,8+point[3]) for point in points],'color':['#0d9488','#7c3aed','#2563eb'][:len(points)]},
        hovertemplate='%{text}<br>Compatibilidad: %{x:.1%}<br>Equilibrio de carga: %{y:.1%}<extra></extra>'))
    figure.update_layout(height=330,xaxis={'title':'Compatibilidad observada → mayor es mejor','tickformat':'.0%','range':[0,1]},yaxis={'title':'Equilibrio de carga → mayor es mejor','tickformat':'.0%','range':[0,1]},margin={'l':20,'r':20,'t':25,'b':40})
    st.plotly_chart(figure,width='stretch',key='assignment_tradeoff')
    st.caption('El mapa deja visible el intercambio: un método puede subir a la derecha por compatibilidad y bajar en equilibrio. La esquina superior derecha es mejor en ambas métricas. La compatibilidad se calcula con coincidencias observables de zona y segmento; si faltan esos datos, no se infiere afinidad.')
def render(client,workflow):
    with st.spinner('Cargando registros y modelos…'):
        models=client.request('GET','/assignment-models')['models'];rows=[];offset=0
        while True:
            page=client.request('GET','/records',params={'status':'nuevo','offset':offset,'limit':100});rows.extend(page['items']);offset+=len(page['items'])
            if offset>=page['total'] or not page['items']:break
    records={r['id']:r for r in rows}
    st.subheader('Generar propuesta')
    def record_label(rid):
        record=records[rid]
        context=' · '.join(str(value) for value in (record.get('city'),record.get('zone'),record.get('sector')) if value)
        return record['company_name']+(f' · {context}' if context else '')
    ids=st.multiselect('Registros sin asignar',list(records),format_func=record_label,max_selections=100,key='selected_records')
    st.subheader('Criterios de equilibrio')
    base=st.session_state.get('assignment_configuration',{})
    balance=st.slider('Equilibrio frente a compatibilidad',0.0,1.0,float(base.get('balance_weight',.60)),.05,key='assignment_balance')
    amount=st.slider('Peso de montos dentro del equilibrio',0.0,1.0,float(base.get('amount_weight',.5)),.05,key='assignment_amount')
    st.caption('Equilibrio: 0 favorece afinidad; 1 reparte carga relativa. El valor inicial 0.60 hace visible la diferencia con Carga. Montos: 0 prioriza registros por capacidad; 1 incorpora montos conocidos por capacidad. Carga nunca usa estos controles.')
    configuration={'balance_weight':balance,'amount_weight':amount}
    if st.button('Generar las tres propuestas',disabled=not ids,key='generate'):
        workflow.clear()
        st.session_state.pop('proposal_choice',None)
        st.session_state['assignment_proposals']={};st.session_state['assignment_proposal_errors']={}
        live=st.empty();progress=st.progress(0,text='Iniciando las tres propuestas…')
        with st.status('Generando propuestas',expanded=True) as status:
            for position,model in enumerate(models,1):
                method=model['id'];status.write('Calculando '+LABELS.get(method,method)+'…')
                try:
                    st.session_state['assignment_proposals'][method]=client.request('POST','/assignment-previews',{
                        'record_ids':sorted(ids),'method':method,'configuration':configuration})
                except APIError as exc:
                    st.session_state['assignment_proposal_errors'][method]=str(exc)
                with live.container():
                    st.caption('Resultados disponibles hasta ahora')
                    proposal_cards(st.session_state['assignment_proposals'],st.session_state['assignment_proposal_errors'],'live-'+str(position))
                progress.progress(position/len(models),text=f'{position} de {len(models)} propuestas listas o reportadas')
            status.update(label='Generación de propuestas terminada',state='complete',expanded=False)
    proposals=st.session_state.get('assignment_proposals',{})
    if proposals:
        st.divider();st.subheader('Compara las tres propuestas')
        st.caption('Se evaluaron con el mismo lote y los mismos criterios. Elige una para revisarla, corregirla o aprobarla.')
        proposal_cards(proposals,st.session_state.get('assignment_proposal_errors',{}),'comparison')
        tradeoff_chart(proposals)
        choice=st.selectbox('Propuesta para revisar', [None]+list(proposals),format_func=lambda value:'Selecciona una propuesta' if value is None else LABELS.get(value,value),key='proposal_choice')
        if choice:
            request={'record_ids':sorted(ids),'method':choice,'configuration':configuration}
            if st.session_state.get('preview',{}).get('preview_id')!=proposals[choice]['preview_id']:
                workflow.store(proposals[choice],request)
    preview=st.session_state.get('preview')
    if not preview:
        if proposals:st.info('Selecciona una propuesta para verla en detalle y hacer ajustes manuales.')
        return
    request={'record_ids':sorted(ids),'method':preview['method'],'configuration':configuration}
    projection=preview.get('projection')
    st.divider();st.subheader('Impacto de la propuesta')
    if projection:
        metric=projection['metrics']
        for col,label,value in zip(st.columns(5),['Dispersión de carga antes','Dispersión de carga después','Compatibilidad observada','Dispersión de montos después','Registros sin asignar'],
            [metric['before_workload_dispersion'],metric['after_workload_dispersion'],metric.get('compatibility_rate'),metric['after_amount_relative_cv'],len(preview['unassigned'])]):
            col.metric(label,f'{value:.1%}' if isinstance(value,float) else value)
        st.caption('Menor dispersión indica un reparto más uniforme dentro del equipo elegible. Los montos solo consideran valores conocidos.')
        charts(projection)
    st.subheader('Revisión manual de la propuesta')
    st.caption('Cambia el vendedor en una fila y valida los ajustes. El servidor rechaza capacidad, ausencias, zona, equipo y requisitos incumplidos. Los cambios no ejecutan la asignación.')
    names={p['id']:p['name'] for p in (projection or {}).get('people',[])}
    reverse={name:sid for sid,name in names.items()}
    manual=pd.DataFrame([{'_record_id':item['record_id'],
        'Empresa':records.get(item['record_id'],{}).get('company_name','Registro sin nombre'),
        'Ciudad':records.get(item['record_id'],{}).get('city') or '—',
        'Zona':records.get(item['record_id'],{}).get('zone') or '—',
        'Sector':records.get(item['record_id'],{}).get('sector') or '—',
        'Vendedor propuesto':names.get(item['seller_id'],item['seller_id'])} for item in preview['assignments']])
    edited=st.data_editor(manual,hide_index=True,use_container_width=True,key='manual_assignments',disabled=['Empresa','Ciudad','Zona','Sector'],
        column_config={'_record_id':None,'Vendedor propuesto':st.column_config.SelectboxColumn(options=list(reverse),required=True)})
    if st.button('Validar cambios manuales',disabled=manual.empty):
        updates=[{'record_id':row['_record_id'],'seller_id':reverse[row['Vendedor propuesto']]} for _,row in edited.iterrows()]
        try:
            updated=client.request('PATCH',f"/assignment-previews/{preview['preview_id']}/manual-assignments",{'assignments':updates})
            workflow.store(updated,request);st.success('Cambios manuales validados. Revisa de nuevo las gráficas antes de aprobar.');st.rerun()
        except APIError as exc:st.error('No se validaron los cambios: '+str(exc))
    with st.expander('Registros sin asignar y restricciones'):
        unassigned=pd.DataFrame([{
            'Empresa':records.get(item['record_id'],{}).get('company_name','Registro sin nombre'),
            'Ciudad':records.get(item['record_id'],{}).get('city') or '—',
            'Zona':records.get(item['record_id'],{}).get('zone') or '—',
            'Sector':records.get(item['record_id'],{}).get('sector') or '—',
            'Restricciones':', '.join(item.get('reasons',[])) or 'Sin detalle disponible'
        } for item in preview['unassigned']])
        st.dataframe(unassigned,hide_index=True,width='stretch')
    workflow.displayed()
    changed=request!=st.session_state.get('preview_request')
    if changed:st.warning('Los criterios o el lote cambiaron; genera otro preview antes de ejecutar.')
    execution_blocked=changed or preview['status'] not in {'draft','approved'} or not preview['assignments']
    approval_key='approval:'+preview['preview_id']
    def remember_approval():
        if st.session_state.get(approval_key):st.session_state['preview_approved']=preview['preview_id']
        else:st.session_state.pop('preview_approved',None)
    st.checkbox('He revisado las gráficas y apruebo este preview',key=approval_key,disabled=execution_blocked,on_change=remember_approval)
    approved=st.session_state.get('preview_approved')==preview['preview_id']
    if st.button('Ejecutar preview aprobado',disabled=execution_blocked or not approved,key='execute'):
        try:
            result=workflow.execute(client,st.session_state['preview_request'],approved)
            st.success(f"Asignación confirmada: {len(result['assignments'])} registros.")
        except APIError as exc:st.error(str(exc))
