"""Compact review queue; suggestions remain proposals until accepted."""
import pandas as pd
import json
import streamlit as st
from .client import APIError

STATUS={'not_applicable':'No aplica','pending':'Pendiente','running':'Analizando','proposed':'Propuesta lista',
        'accepted':'Aceptada','rejected':'Rechazada','error':'Error'}

def _requirements(labels):
    return ' · '.join(labels) if labels else '—'

def render(client, workflow):
    st.subheader('Registros sin asignar')
    st.caption('Revisa los registros nuevos, analiza candidatos con IA y acepta únicamente las propuestas que correspondan.')
    data=client.request('GET','/review-worklist');rows=data['items']
    a,b,c=st.columns(3)
    search=a.text_input('Buscar empresa, ciudad o sector')
    sectors=sorted({row['sector'] for row in rows if row['sector']})
    sector=b.selectbox('Sector',['Todos']+sectors)
    only_review=c.checkbox('Solo pendientes de revisión')
    def include(row):
        text=' '.join(str(row.get(k) or '') for k in ('company_name','city','zone','sector')).casefold()
        return (not search or search.casefold() in text) and (sector=='Todos' or row['sector']==sector) and (not only_review or row['ai_status'] in ('pending','running','proposed','error'))
    visible=[row for row in rows if include(row)]
    counts={status:sum(row['ai_status']==status for row in rows) for status in STATUS}
    for col,label,value in zip(st.columns(4),['Sin asignar','Pendientes IA','Analizando','Propuestas listas'],
                               [len(rows),counts['pending'],counts['running'],counts['proposed']]):col.metric(label,value)
    proposals=[row for row in rows if row['ai_status']=='proposed']
    if proposals:
        st.subheader('Propuestas pendientes de tu aprobación')
        proposed_id=st.selectbox('Selecciona una propuesta para revisarla', [row['id'] for row in proposals],
            format_func=lambda rid:next(row['company_name']+' · '+row['note_preview'][:55] for row in proposals if row['id']==rid))
        st.session_state['review_selected_id']=proposed_id
        st.caption('La IA no cambia registros por sí sola. Puedes aceptar, rechazar o ajustar cada propuesta.')
    if st.button('Analizar candidatos',disabled=counts['pending']+counts['error']==0):
        try:
            result=client.request('POST','/review-analysis-batches')
            st.success(f"{result['queued']} registros entraron a la cola de análisis ({result['workers']} trabajador(es)).")
            st.rerun()
        except APIError as exc:st.error(str(exc))
    if counts['running'] or counts['pending']:
        st.caption('Hay análisis en curso. Usa «Actualizar resultados» para consultar el avance sin interrumpir la tabla.')
        if st.button('Actualizar resultados'):st.rerun()
    display=pd.DataFrame([{'Empresa':row['company_name'],'Estado':row['status'].replace('_',' '),'Ciudad / zona':' / '.join(x for x in (row['city'],row['zone']) if x) or '—',
        'Sector':row['sector'] or '—','Nota':row['note_preview'],'Requerimientos':_requirements(row['requirements']),
        'IA':STATUS[row['ai_status']]} for row in visible])
    if display.empty:st.info('No hay registros que coincidan con los filtros.');return
    event=st.dataframe(display,hide_index=True,use_container_width=True,on_select='rerun',selection_mode='single-row',key='review_table')
    selected=getattr(getattr(event,'selection',None),'rows',[])
    if selected:st.session_state['review_selected_id']=visible[selected[0]]['id']
    selected_id=st.session_state.get('review_selected_id')
    row=next((item for item in rows if item['id']==selected_id),None)
    if not row:return
    st.divider();st.subheader('Revisar · '+row['company_name'])
    detail=client.request('GET','/records/'+row['id'])
    st.caption('Criterios de la heurística: '+(' · '.join(row['heuristic_reasons']) or 'No aplica'))
    st.text_area('Nota original',detail.get('original_notes') or 'Sin nota.',disabled=True,key='review_note:'+row['id'])
    suggestion=row.get('suggestion') or {}
    proposed=suggestion.get('proposed_signals') if row['ai_status']=='proposed' else None
    labels=proposed or detail['signals']
    if proposed:
        st.write('**Propuesta de IA**')
        st.caption('Es un borrador. Ajusta cualquier campo antes de aceptarlo.')
    elif row['ai_status']=='error':st.warning('La IA no pudo generar una propuesta: '+str(suggestion.get('error') or 'Error desconocido')+'. Puedes revisar el registro manualmente.')
    else:st.info('Puedes completar este registro manualmente aunque la IA no tenga una propuesta.')
    details=labels['details']
    assignment_note=st.text_area('Nota para asignación (editable)',detail.get('assignment_note') or detail.get('notes') or '',
        help='La nota original se conserva arriba. Esta versión se utiliza en futuras simulaciones.',key='assignment_note:'+row['id'])
    left,right=st.columns(2)
    sector=left.checkbox('Requiere experiencia en el sector',value=bool(details.get('sector_expertise_requested')),key='proposal_sector:'+row['id'])
    senior=right.checkbox('Requiere vendedor senior',value=bool(details.get('seniority_requested')),key='proposal_senior:'+row['id'])
    manual=left.checkbox('Requiere revisión manual',value=bool(details.get('requires_manual_review') or labels.get('needs_review')),key='proposal_manual:'+row['id'])
    technical=right.text_input('Especialidad técnica',value=details.get('technical_expertise') or '',key='proposal_technical:'+row['id'],
        help='Escribe la habilidad requerida; debe coincidir con una habilidad verificada en Vendedores para que el motor la use.')
    reason=st.text_input('Motivo del cambio',value='Propuesta de IA revisada y aceptada.' if proposed else '',key='review_reason:'+row['id'])
    def revised():
        result=json.loads(json.dumps(labels));d=result['details']
        d.update(sector_expertise_requested=sector,seniority_requested=senior,requires_manual_review=manual,technical_expertise=technical.strip() or None)
        result['needs_review']=manual
        result['requirements']=sorted(name for name,on in [('sector_expertise_requested',sector),('seniority_requested',senior),('technical_expertise',bool(technical.strip()))] if on)
        return result
    if proposed:
        accept,reject=st.columns(2)
        if accept.button('Aceptar cambios revisados',type='primary'):
            if not reason.strip():st.error('Indica el motivo del cambio.')
            else:
                try:
                    client.request('POST','/review-suggestions/'+row['id']+'/accept',{'expected_updated_at':row['signal_metadata']['updated_at'],
                        'reason':reason,'signals':revised(),'assignment_note':assignment_note})
                    workflow.clear();st.success('Cambios aceptados. La siguiente simulación los tendrá en cuenta.');st.rerun()
                except APIError as exc:st.error(str(exc))
        if reject.button('Rechazar propuesta'):
            try:client.request('POST','/review-suggestions/'+row['id']+'/reject');st.rerun()
            except APIError as exc:st.error(str(exc))
    elif st.button('Guardar cambios manuales',type='primary',key='save_manual:'+row['id']):
        if not reason.strip():st.error('Indica el motivo del cambio.')
        else:
            try:
                client.request('PATCH','/records/'+row['id']+'/signals',{'signals':revised(),'assignment_note':assignment_note,
                    'expected_updated_at':row['signal_metadata']['updated_at'],'reason':reason})
                workflow.clear();st.success('Cambios manuales guardados.');st.rerun()
            except APIError as exc:st.error(str(exc))
