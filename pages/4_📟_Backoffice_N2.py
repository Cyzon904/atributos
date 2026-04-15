import streamlit as st 
import pandas as pd
import requests
import time
import plotly.express as px
from datetime import datetime, timedelta
from io import BytesIO
import re

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
    hoje = datetime.now()
    
    for t in tickets:
        attrs = t.get('ticket_attributes', {})
        admin_id = t.get('admin_assignee_id')
        
        # CORREÇÃO DE STATUS FANTASMA
        # Se o sistema diz que está encerrado (open: false), forçamos para "Fechado"
        if t.get('open') is False:
            status_atual = 'Fechado'
        else:
            status_atual = t.get('ticket_state_internal_label', t.get('ticket_state'))
        
        # Datas ajustadas para o fuso (-3h)
        dt_criacao_raw = datetime.fromtimestamp(t['created_at']) - timedelta(hours=3)
        dt_update_raw = datetime.fromtimestamp(t['updated_at']) - timedelta(hours=3)
        
        # Lógica da Bolinha de SLA
        dias_aberto = (hoje - dt_criacao_raw).days
        indicador_sla = ""
        status_abertos = ['Aberto', 'Em andamento', 'Em Andamento', 'Em Análise N2']
        
        if status_atual in status_abertos:
            indicador_sla = "🔴" if dias_aberto >= 5 else "🟢"
        
        # Data de Finalização
        data_finalizacao = "-"
        status_conclusao = ['Resolvido', 'Fechado', 'Concluído', 'Concluído N2']
        
        if status_atual in status_conclusao or t.get('open') is False:
            data_finalizacao = dt_update_raw.strftime("%d/%m/%Y %H:%M")

        # Buscar o Status do Jira
        status_jira = "-"
        parts = t.get('ticket_parts', {}).get('ticket_parts', [])
        
        for part in reversed(parts):
            if part.get('part_type') == 'comment':
                body = part.get('body', '')
                if "O status do chamado foi atualizado para:" in body:
                    texto_limpo = re.sub('<[^<]+>', '', body)
                    status_jira = texto_limpo.split("O status do chamado foi atualizado para:")[1].strip()
                    break 

        # Puxa a conversa vinculada, se houver
        linked = t.get('linked_objects', {}).get('data', [])
        conversa_id = linked[0]['id'] if linked else None
        link_conversa = f"https://app.intercom.com/a/inbox/{WORKSPACE_ID}/inbox/conversation/{conversa_id}?view=List" if conversa_id else "Sem vínculo"

        # Montagem de todas as colunas
        row = {
            "SLA": indicador_sla,
            "ID Ticket": t.get('ticket_id'),
            "Assunto": attrs.get('_default_title_', 'Sem Assunto'),
            "Data Criação": dt_criacao_raw.strftime("%d/%m/%Y %H:%M"),
            "Data Resolução": data_finalizacao,
            "Status Intercom": status_atual,
            "Status Jira": status_jira, 
            "Analista N2": admin_map.get(str(admin_id), "Não atribuído"),
            "Criado por": attrs.get('Criado por', 'N/A'),
            "Plataforma": attrs.get('Plataforma', '-'),
            "Severidade": attrs.get('Severidade', '-'),
            "Empresa": attrs.get('Nome da Empresa', '-'),
            "Jira": attrs.get('Chamado no Jira', '-'),
            "Link Ticket": f"https://app.intercom.com/a/inbox/{WORKSPACE_ID}/inbox/conversation/{t.get('id')}?view=TableFullscreen",
            "Link Conversa Original": link_conversa
        }
        rows.append(row)
    return pd.DataFrame(rows)
    
def converter_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Tickets N2')
        
        # Ajustando o tamanho da primeira coluna para ficar mais bonito
        worksheet = writer.sheets['Tickets N2']
        worksheet.set_column('A:O', 20) 
    return output.getvalue()
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
            
            # --- NOVA LÓGICA DE ORDENAÇÃO FIXA ---
            # Vermelho vale 0, Verde vale 1 e o restante vale 2
            prioridade = {'🔴': 0, '🟢': 1, '': 2}
            df['ordem_prioridade'] = df['SLA'].map(prioridade)
            
            # Ordenação pela prioridade e depois pela Data de Criação (mais antigos primeiro)
            df = df.sort_values(by=['ordem_prioridade', 'Data Criação'], ascending=[True, True])
            
            df = df.drop(columns=['ordem_prioridade'])
            
            st.session_state['df_n2'] = df
        else:
            st.warning("Nenhum ticket encontrado para este período.")

if 'df_n2' in st.session_state:
    df_completo = st.session_state['df_n2']
    
    # --- NOVO FILTRO GLOBAL ---
    with st.sidebar:
        st.markdown("### ⚙️ Filtros Globais")
        
        # Lista fixa do time de atendimento
        time_atendimento = [
            'rhayslla.junca@produttivo.com.br',
            'douglas.david@produttivo.com.br',
            'aline.souza@produttivo.com.br',
            'danielle.ghesini@produttivo.com.br',
            'jenyffer.souza@produttivo.com.br',
            'marcelo.misugi@produttivo.com.br',
            'heloisa.atm.slv@produttivo.com.br',
            'bruno.braga@produttivo.com.br'
        ]
        
        criadores_unicos = sorted(df_completo['Criado por'].dropna().astype(str).unique())
        
        # Garante que o painel só marque como padrão os e-mails que realmente estão na busca atual
        padrao_selecionado = [email for email in time_atendimento if email in criadores_unicos]
        
        # Cria o filtro já com o time preenchido
        sel_criadores = st.multiselect(
            "👤 Aberto por (Time de Atendimento):", 
            options=criadores_unicos,
            default=padrao_selecionado
        )

    # Aplica o filtro no dataframe que será usado no resto do painel
    df = df_completo.copy()
    if sel_criadores:
        df = df[df['Criado por'].isin(sel_criadores)]
    
    # KPIs Rápidos
    k1, k2, k3, k4 = st.columns(4)
    total = len(df)
    
    # Atualizado para ler a coluna "Status Intercom" e incluir novos status de resolução
    abertos = len(df[df['Status Intercom'].isin(['Aberto', 'Em andamento', 'Em Andamento', 'Em Análise N2'])])
    resolvidos = len(df[df['Status Intercom'].isin(['Resolvido', 'Fechado', 'Concluído', 'Concluído N2'])])
    
    k1.metric("Total de Tickets", total)
    k1.caption("Após filtros")
    k2.metric("Ativos (Aberto/Work)", abertos)
    k3.metric("Resolvidos", resolvidos) 
    k4.metric("Taxa de Conclusão", f"{(resolvidos/total*100):.1f}%" if total > 0 else "0%")

    st.divider()

    col_graf1, col_graf2 = st.columns(2)

    with col_graf1:
        st.subheader("Situação dos Tickets")
        # Mapa de cores
        cores_status = {
            'Aberto': '#ef553b', 
            'Em andamento': '#636efa', 
            'Em Andamento': '#636efa',
            'Em Análise N2': '#feca57',
            'Resolvido': '#00cc96',
            'Fechado': '#00cc96',
            'Concluído': '#00cc96',
            'Concluído N2': '#00cc96'
        }
        if not df.empty:
            fig_status = px.pie(df, names='Status Intercom', hole=0.4, color='Status Intercom', color_discrete_map=cores_status)
            st.plotly_chart(fig_status, use_container_width=True)
        else:
            st.info("Nenhum ticket encontrado com este filtro.")

    with col_graf2:
        st.subheader("Carga por Analista")
        if not df.empty:
            df_adm = df['Analista N2'].value_counts().reset_index()
            fig_adm = px.bar(df_adm, x='count', y='Analista N2', orientation='h', text='count')
            st.plotly_chart(fig_adm, use_container_width=True)

    st.divider()

    st.divider()

    # --- FILTROS ESPECÍFICOS DA TABELA ---
    st.markdown("#### 🔍 Filtros da Lista Detalhada")
    
    # Criamos 4 colunas para deixar os filtros organizados lado a lado
    cf1, cf2, cf3, cf4 = st.columns(4)
    
    with cf1:
        opcoes_analista = sorted(df['Analista N2'].astype(str).unique())
        filtro_analista = st.multiselect("Analista N2", options=opcoes_analista)
        
    with cf2:
        opcoes_jira = sorted(df['Status Jira'].astype(str).unique())
        filtro_jira = st.multiselect("Status Jira", options=opcoes_jira)
        
    with cf3:
        opcoes_plat = sorted(df['Plataforma'].astype(str).unique())
        filtro_plat = st.multiselect("Plataforma", options=opcoes_plat)
        
    with cf4:
        opcoes_sev = sorted(df['Severidade'].astype(str).unique())
        filtro_sev = st.multiselect("Severidade", options=opcoes_sev)

    # Cria uma cópia do dataframe apenas para exibição e exportação
    df_exibicao = df.copy()

    # Aplica os filtros se algo for selecionado
    if filtro_analista:
        df_exibicao = df_exibicao[df_exibicao['Analista N2'].isin(filtro_analista)]
    if filtro_jira:
        df_exibicao = df_exibicao[df_exibicao['Status Jira'].isin(filtro_jira)]
    if filtro_plat:
        df_exibicao = df_exibicao[df_exibicao['Plataforma'].isin(filtro_plat)]
    if filtro_sev:
        df_exibicao = df_exibicao[df_exibicao['Severidade'].isin(filtro_sev)]

    # --- LISTA DETALHADA E BOTÃO DE EXPORTAR ---
    c_titulo, c_botao = st.columns([4, 1])
    
    with c_titulo:
        # Mostra a quantidade de chamados filtrados no título
        st.subheader(f"📋 Lista Detalhada ({len(df_exibicao)} chamados)")
        
    with c_botao:
        if not df_exibicao.empty:
            excel_file = converter_excel(df_exibicao)
            st.download_button(
                label="📥 Baixar Excel",
                data=excel_file,
                file_name=f"Relatorio_Backoffice_N2_{datetime.now().strftime('%d_%m_%Y')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary"
            )

    st.dataframe(
        df_exibicao, 
        use_container_width=True, 
        hide_index=True,
        column_config={
            "SLA": st.column_config.Column(width="small"),
            "Link Ticket": st.column_config.LinkColumn(
                "Link Ticket", display_text="🔗 Abrir Ticket"
            ),
            "Link Conversa Original": st.column_config.LinkColumn(
                "Link Conversa Original", display_text="💬 Abrir Conversa"
            )
        }
    )
