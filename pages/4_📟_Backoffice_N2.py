import streamlit as st 
import pandas as pd
import requests
import time
import plotly.express as px
from datetime import datetime, timedelta
from io import BytesIO

# Importação do utils (reaproveitando sua lógica atual)
from utils import check_password, logout_button

# Configurações da página
st.set_page_config(page_title="Monitoramento N2", page_icon="📟", layout="wide")

# Bloqueio de acesso
usuario = check_password()
if not usuario:
    st.stop()

# Configuração Intercom
WORKSPACE_ID = "xwvpdtlu"
try:
    INTERCOM_ACCESS_TOKEN = st.secrets["INTERCOM_TOKEN"]
except:
    INTERCOM_ACCESS_TOKEN = st.sidebar.text_input("Intercom Token", type="password")

if not INTERCOM_ACCESS_TOKEN:
    st.warning("⚠️ Configure o Token para continuar.")
    st.stop()

HEADERS = {
    "Authorization": f"Bearer {INTERCOM_ACCESS_TOKEN}",
    "Accept": "application/json",
    "Intercom-Version": "2.10"
}

# Funções de busca

@st.cache_data(ttl=3600)
def get_all_admins():
    url = "https://api.intercom.io/admins"
    try:
        r = requests.get(url, headers=HEADERS)
        return {str(a['id']): a['name'] for a in r.json().get('admins', [])}
    except:
        return {}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_n2_tickets(start_date, end_date):
    url = "https://api.intercom.io/tickets/search"
    ts_start = int(datetime.combine(start_date, datetime.min.time()).timestamp())
    ts_end = int(datetime.combine(end_date, datetime.max.time()).timestamp())
    
    # Filtro fixo para o ID 14 (Tecnologia - N2)
    payload = {
        "query": {
            "operator": "AND",
            "value": [
                {"field": "created_at", "operator": ">", "value": ts_start},
                {"field": "created_at", "operator": "<", "value": ts_end},
                {"field": "ticket_type_id", "operator": "=", "value": "14"}
            ]
        },
        "pagination": {"per_page": 50}
    }
    
    tickets = []
    has_more = True
    status_text = st.empty()
    
    while has_more:
        try:
            resp = requests.post(url, headers=HEADERS, json=payload)
            data = resp.json()
            batch = data.get('tickets', [])
            tickets.extend(batch)
            status_text.caption(f"📥 Baixando tickets... {len(tickets)} encontrados.")
            
            if data.get('pages', {}).get('next'):
                payload['pagination']['starting_after'] = data['pages']['next']['starting_after']
            else:
                has_more = False
        except Exception as e:
            st.error(f"Erro na API: {e}")
            break
            
    status_text.empty()
    return tickets

def process_tickets(tickets, admin_map):
    rows = []
    for t in tickets:
        attrs = t.get('ticket_attributes', {})
        admin_id = t.get('admin_assignee_id')
        
        # Puxa a conversa vinculada, se houver
        linked = t.get('linked_objects', {}).get('data', [])
        conversa_id = linked[0]['id'] if linked else None
        link_conversa = f"https://app.intercom.com/a/inbox/{WORKSPACE_ID}/inbox/conversation/{conversa_id}" if conversa_id else "Sem vínculo"

        row = {
            "ID Ticket": t.get('ticket_id'), # Usa o ID curto (92657184)
            "Assunto": attrs.get('_default_title_', 'Sem Assunto'),
            "Data Criação": (datetime.fromtimestamp(t['created_at']) - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M"),
            "Status": t.get('ticket_state_internal_label', t.get('ticket_state')), # Usa o label traduzido
            "Analista N2": admin_map.get(str(admin_id), "Não atribuído"),
            "Criado por": attrs.get('Criado por', 'N/A'),
            "Plataforma": attrs.get('Plataforma', '-'),
            "Severidade": attrs.get('Severidade', '-'),
            "Empresa": attrs.get('Nome da Empresa', '-'),
            "Jira": attrs.get('Chamado no Jira', '-'),
            "Link Ticket": f"https://app.intercom.com/a/inbox/{WORKSPACE_ID}/tickets/{t.get('id')}",
            "Link Conversa Original": link_conversa
        }
        rows.append(row)
    return pd.DataFrame(rows)

# Interface Principal

st.title("📟 Painel Back-office: Tecnologia N2")

with st.sidebar:
    st.header("Filtros")
    data_hoje = datetime.now()
    periodo = st.date_input("Período de abertura", (data_hoje - timedelta(days=15), data_hoje), format="DD/MM/YYYY")
    btn_run = st.button("🚀 Atualizar Dados", type="primary")
    logout_button()

if btn_run:
    start, end = periodo
    with st.spinner("Buscando tickets no Intercom..."):
        admins = get_all_admins()
        raw_data = fetch_n2_tickets(start, end)
        
        if raw_data:
            df = process_tickets(raw_data, admins)
            st.session_state['df_n2'] = df
        else:
            st.warning("Nenhum ticket encontrado para este período.")

if 'df_n2' in st.session_state:
    df = st.session_state['df_n2']
    
    # KPIs Rápidos
    k1, k2, k3, k4 = st.columns(4)
    total = len(df)
    abertos = len(df[df['Status'].isin(['Aberto', 'Em Andamento'])])
    finalizados = len(df[df['Status'] == 'Finalizado'])
    
    k1.metric("Total de Tickets", total)
    k1.caption("No período selecionado")
    k2.metric("Ativos (Aberto/Work)", abertos)
    k3.metric("Finalizados", finalizados)
    k4.metric("Taxa de Conclusão", f"{(finalizados/total*100):.1f}%" if total > 0 else "0%")

    st.divider()

    col_graf1, col_graf2 = st.columns(2)

    with col_graf1:
        st.subheader("Situação dos Tickets")
        fig_status = px.pie(df, names='Status', hole=0.4, color='Status',
                            color_discrete_map={'Aberto': '#ef553b', 'Em Andamento': '#636efa', 'Finalizado': '#00cc96'})
        st.plotly_chart(fig_status, use_container_width=True)

    with col_graf2:
        st.subheader("Carga por Analista")
        df_adm = df['Analista N2'].value_counts().reset_index()
        fig_adm = px.bar(df_adm, x='count', y='Analista N2', orientation='h', text='count')

    st.subheader("📋 Lista Detalhada")
    st.dataframe(
        df, 
        use_container_width=True, 
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="🔗 Abrir Ticket")
        }
    )
