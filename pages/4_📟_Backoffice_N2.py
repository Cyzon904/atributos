import streamlit as st 
import pandas as pd
import requests
import time
import plotly.express as px
from datetime import datetime, timedelta
from io import BytesIO
import re

# Puxando as funções de segurança e botão de sair que já usamos em outros lugares
from utils import check_password, logout_button

# Configurando a carinha da página e o título que aparece na aba do navegador
st.set_page_config(page_title="Monitoramento N2", page_icon="📟", layout="wide")

# Bloqueio de segurança para ninguém de fora acessar o painel
usuario = check_password()
if not usuario:
    st.stop()

# Configuração para acessar o Intercom
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

# Funções que buscam os dados lá no Intercom

@st.cache_data(ttl=3600)
def get_all_admins():
    # Pego a lista de todos os analistas para poder trocar o ID pelo nome deles depois
    url = "https://api.intercom.io/admins"
    try:
        r = requests.get(url, headers=HEADERS)
        return {str(a['id']): a['name'] for a in r.json().get('admins', [])}
    except:
        return {}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_n2_tickets(start_date, end_date):
    url = "https://api.intercom.io/tickets/search"
    # Transformo as datas que escolhi na tela no formato que o Intercom entende
    ts_start = int(datetime.combine(start_date, datetime.min.time()).timestamp())
    ts_end = int(datetime.combine(end_date, datetime.max.time()).timestamp())
    
    # Criei essa função menor para fazer a busca e ler todas as páginas de resultados sem repetir código
    def executar_busca(payload):
        resultados = []
        has_more = True
        while has_more:
            try:
                resp = requests.post(url, headers=HEADERS, json=payload)
                data = resp.json()
                batch = data.get('tickets', [])
                resultados.extend(batch)
                
                # Se tiver mais páginas, pego a indicação da próxima e continuo buscando
                if data.get('pages', {}).get('next'):
                    payload['pagination']['starting_after'] = data['pages']['next']['starting_after']
                else:
                    has_more = False
            except Exception as e:
                st.error(f"Erro na API: {e}")
                break
        return resultados

    # 1. Primeiro eu peço apenas os tickets criados na data que selecionei na tela
    payload_periodo = {
        "query": {
            "operator": "AND",
            "value": [
                {"field": "created_at", "operator": ">", "value": ts_start},
                {"field": "created_at", "operator": "<", "value": ts_end},
                {"field": "ticket_type_id", "operator": "=", "value": "14"} # ID 14 é a fila de Tecnologia N2
            ]
        },
        "pagination": {"per_page": 50}
    }
    
    # 2. Depois eu peço os tickets antigos que ainda estão abertos, para montar nosso backlog
    payload_abertos = {
        "query": {
            "operator": "AND",
            "value": [
                {"field": "open", "operator": "=", "value": True},
                {"field": "ticket_type_id", "operator": "=", "value": "14"}
            ]
        },
        "pagination": {"per_page": 50}
    }
    
    status_text = st.empty()
    
    status_text.caption("📥 Baixando tickets do período selecionado...")
    tickets_periodo = executar_busca(payload_periodo)
    
    status_text.caption("📥 Resgatando backlog antigo em aberto...")
    tickets_abertos = executar_busca(payload_abertos)
    
    status_text.empty()
    
    # Faço uma marcação invisível para eu saber em qual aba o ticket vai aparecer depois
    for t in tickets_abertos:
        t['_origem_fila'] = 'Backlog'
        
    for t in tickets_periodo:
        t['_origem_fila'] = 'Período'
    
    # Junto tudo colocando o backlog primeiro. Assim, se um ticket estiver nas duas listas,
    # a marcação de 'Período' vai sobrescrever a de 'Backlog' da forma certa.
    todos_tickets = tickets_abertos + tickets_periodo
    # Removo os repetidos usando o ID do ticket para não contar o mesmo chamado duas vezes
    tickets_unicos = {t['id']: t for t in todos_tickets}
    
    return list(tickets_unicos.values())

def process_tickets(tickets, admin_map):
    # Aqui eu limpo e organizo os dados brutos que vieram da API
    rows = []
    hoje = datetime.now()
    
    for t in tickets:
        attrs = t.get('ticket_attributes', {})
        admin_id = t.get('admin_assignee_id')
        
        # CORREÇÃO DE STATUS FANTASMA
        # Às vezes a automação fecha o ticket mas a etiqueta trava em 'Em andamento'
        # Então se o sistema diz que a janela está encerrada, eu forço o status para "Fechado"
        if t.get('open') is False:
            status_atual = 'Fechado'
        else:
            status_atual = t.get('ticket_state_internal_label', t.get('ticket_state'))
        
        # Ajusto o fuso horário subtraindo 3 horas para bater com o horário do Brasil
        dt_criacao_raw = datetime.fromtimestamp(t['created_at']) - timedelta(hours=3)
        dt_update_raw = datetime.fromtimestamp(t['updated_at']) - timedelta(hours=3)
        
        # Regra do SLA: se o ticket está aberto há 5 dias ou mais, ganha bolinha vermelha
        dias_aberto = (hoje - dt_criacao_raw).days
        indicador_sla = ""
        status_abertos = ['Aberto', 'Em andamento', 'Em Andamento', 'Em Análise N2', 'Esperando por você']
        
        if status_atual in status_abertos:
            indicador_sla = "🔴" if dias_aberto >= 5 else "🟢"
        
        # Garanto que pego a data certa quando o ticket recebe um status de finalizado
        data_finalizacao = "-"
        status_conclusao = ['Resolvido', 'Fechado', 'Concluído', 'Concluído N2']
        
        if status_atual in status_conclusao or t.get('open') is False:
            data_finalizacao = dt_update_raw.strftime("%d/%m/%Y %H:%M")

        # Em vez de ter que abrir o chamado, leio os comentários de trás para frente
        # e pego a última atualização que o N2 fez no Jira
        status_jira = "-"
        parts = t.get('ticket_parts', {}).get('ticket_parts', [])
        
        for part in reversed(parts):
            if part.get('part_type') == 'comment':
                body = part.get('body', '')
                if "O status do chamado foi atualizado para:" in body:
                    texto_limpo = re.sub('<[^<]+>', '', body)
                    status_jira = texto_limpo.split("O status do chamado foi atualizado para:")[1].strip()
                    break 

        # Se o ticket veio de um chat, eu puxo o link da conversa original
        linked = t.get('linked_objects', {}).get('data', [])
        conversa_id = linked[0]['id'] if linked else None
        link_conversa = f"https://app.intercom.com/a/inbox/{WORKSPACE_ID}/inbox/conversation/{conversa_id}?view=List" if conversa_id else "Sem vínculo"

        # Pegamos o e-mail, removemos espaços nas pontas e deixamos minúsculo
        criador_bruto = attrs.get('Criado por', 'N/A')
        criador_limpo = str(criador_bruto).strip().lower() if criador_bruto and criador_bruto != 'N/A' else 'N/A'

        # Montagem de todas as colunas
        row = {
            "SLA": indicador_sla,
            "ID Ticket": t.get('ticket_id'),
            "Origem": t.get('_origem_fila', 'Período'),
            "Assunto": attrs.get('_default_title_', 'Sem Assunto'),
            "Data Criação": dt_criacao_raw.strftime("%d/%m/%Y %H:%M"),
            "Data Resolução": data_finalizacao,
            "Status Intercom": status_atual,
            "Status Jira": status_jira, 
            "Analista N2": admin_map.get(str(admin_id), "Não atribuído"),
            "Criado por": criador_limpo, # Aplicamos a nova variável aqui
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
    # Função rápida para transformar a nossa tabela em um arquivo Excel bonitinho
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Tickets N2')
        
        # Alargo as colunas para o texto não ficar cortado quando a pessoa baixar
        worksheet = writer.sheets['Tickets N2']
        worksheet.set_column('A:O', 20) 
    return output.getvalue()

# A partir daqui é a interface que aparece na tela do painel

st.title("📟 Painel Back-office: Tecnologia N2")

with st.sidebar:
    st.header("Filtros")
    data_hoje = datetime.now()
    periodo = st.date_input("Período de abertura", (data_hoje - timedelta(days=15), data_hoje), format="DD/MM/YYYY")
    btn_run = st.button("🚀 Atualizar Dados", type="primary")
    logout_button()

# Quando eu clico em atualizar, o painel roda as funções que criei lá em cima
if btn_run:
    start, end = periodo
    with st.spinner("Buscando tickets no Intercom..."):
        admins = get_all_admins()
        raw_data = fetch_n2_tickets(start, end)
        
        if raw_data:
            df = process_tickets(raw_data, admins)
            
            # Criei essa regra para organizar a fila: vermelhos no topo, verdes no meio, resolvidos embaixo
            prioridade = {'🔴': 0, '🟢': 1, '': 2}
            df['ordem_prioridade'] = df['SLA'].map(prioridade)
            
            # Ordeno pela cor e depois deixo os mais antigos sempre em cima para darmos prioridade
            df = df.sort_values(by=['ordem_prioridade', 'Data Criação'], ascending=[True, True])
            
            # Apago a coluna de prioridade para ela não aparecer na tela
            df = df.drop(columns=['ordem_prioridade'])
            
            st.session_state['df_n2'] = df
        else:
            st.warning("Nenhum ticket encontrado para este período.")

if 'df_n2' in st.session_state:
    df_completo = st.session_state['df_n2']
    
    # --- FILTRO GLOBAL DE EQUIPE ---
    with st.sidebar:
        st.markdown("### ⚙️ Filtros Globais")
        
        # Deixo nossa equipe já pré-configurada para não precisar digitar toda vez
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
        
        # Limpo os valores vazios para não dar erro na hora de colocar em ordem alfabética
        criadores_unicos = sorted(df_completo['Criado por'].dropna().astype(str).unique())
        
        # Garanto que só vai marcar como padrão os e-mails que realmente estão na busca atual
        padrao_selecionado = [email for email in time_atendimento if email in criadores_unicos]
        
        sel_criadores = st.multiselect(
            "👤 Aberto por (Time de Atendimento):", 
            options=criadores_unicos,
            default=padrao_selecionado
        )

    # Aplico o filtro de e-mail na nossa base
    df = df_completo.copy()
    if sel_criadores:
        df = df[df['Criado por'].isin(sel_criadores)]
        
    # Limpeza da base para garantir que o backlog só tenha tickets realmente ativos
    # Adicionamos o 'Esperando por você' aqui também
    status_ativos = ['Aberto', 'Em andamento', 'Em Andamento', 'Em Análise N2', 'Esperando por você']
    df = df[(df['Origem'] == 'Período') | ((df['Origem'] == 'Backlog') & (df['Status Intercom'].isin(status_ativos)))]
    
    # Crio as caixinhas com os números rápidos para bater o olho e ver como estamos
    k1, k2, k3, k4, k5 = st.columns(5)
    total = len(df)
    
    abertos_periodo = len(df[(df['Status Intercom'].isin(status_ativos)) & (df['Origem'] == 'Período')])
    abertos_backlog = len(df[(df['Status Intercom'].isin(status_ativos)) & (df['Origem'] == 'Backlog')])
    
    resolvidos = len(df[df['Status Intercom'].isin(['Resolvido', 'Fechado', 'Concluído', 'Concluído N2'])])
    k1.metric("Total de Tickets", total)
    k1.caption("Após filtros")
    
    # Separo os ativos em duas métricas diferentes
    k2.metric("Ativos (Período)", abertos_periodo)
    k3.metric("Ativos (Backlog)", abertos_backlog)
    
    k4.metric("Resolvidos", resolvidos) 
    k5.metric("Taxa de Conclusão", f"{(resolvidos/total*100):.1f}%" if total > 0 else "0%")

    st.divider()

    # Ajustei para 3 colunas para caber o novo gráfico
    col_graf1, col_graf2, col_graf3 = st.columns(3)

    with col_graf1:
        st.subheader("Situação dos Tickets")
        # Defino cores fixas para os gráficos não mudarem de cor sozinhos
        cores_status = {
            'Aberto': '#ef553b', 
            'Em andamento': '#636efa', 
            'Em Andamento': '#636efa',
            'Em Análise N2': '#feca57',
            'Esperando por você': '#feca57',
            'Resolvido': '#00cc96',
            'Fechado': '#00cc96',
            'Concluído': '#00cc96',
            'Concluído N2': '#00cc96'
        }
        if not df.empty:
            fig_status = px.pie(df, names='Status Intercom', hole=0.4, color='Status Intercom', color_discrete_map=cores_status)
            
            # ADICIONE ESTA LINHA: Mostra o valor absoluto e a porcentagem dentro do gráfico
            fig_status.update_traces(textinfo='value+percent')
            
            st.plotly_chart(fig_status, use_container_width=True)
        else:
            st.info("Nenhum ticket encontrado com este filtro.")

    with col_graf2:
        st.subheader("Carga por Analista")
        if not df.empty:
            df_adm = df['Analista N2'].value_counts().reset_index()
            fig_adm = px.bar(df_adm, x='count', y='Analista N2', orientation='h', text='count')
            st.plotly_chart(fig_adm, use_container_width=True)

    with col_graf3:
        st.subheader("Plataforma")
        if not df.empty:
            # Gráfico de rosca simples puxando a coluna Plataforma
            fig_plat = px.pie(df, names='Plataforma', hole=0.4)
            
            # ADICIONE ESTA LINHA: Mostra o valor absoluto e a porcentagem dentro do gráfico
            fig_plat.update_traces(textinfo='value+percent')
            
            st.plotly_chart(fig_plat, use_container_width=True)
        else:
            st.info("Nenhum ticket encontrado com este filtro.")

    st.divider()

    # --- FILTROS DA TABELA ---
    with st.form("form_filtros_n2"):
        st.markdown("#### 🔍 Filtros da Lista Detalhada")
        
        cf1, cf2, cf3, cf4 = st.columns(4)
        
        with cf1:
            # Adicionei o .dropna() em todos para garantir que valores nulos não quebrem o código
            opcoes_analista = sorted(df['Analista N2'].dropna().astype(str).unique())
            filtro_analista = st.multiselect("Analista N2", options=opcoes_analista)
            
        with cf2:
            opcoes_jira = sorted(df['Status Jira'].dropna().astype(str).unique())
            filtro_jira = st.multiselect("Status Jira", options=opcoes_jira)
            
        with cf3:
            opcoes_plat = sorted(df['Plataforma'].dropna().astype(str).unique())
            filtro_plat = st.multiselect("Plataforma", options=opcoes_plat)
            
        with cf4:
            opcoes_sev = sorted(df['Severidade'].dropna().astype(str).unique())
            filtro_sev = st.multiselect("Severidade", options=opcoes_sev)

        # O botão DEVE ficar alinhado aqui dentro do bloco "with st.form"
        btn_aplicar = st.form_submit_button("✅ Aplicar Filtros")

    # Faço uma cópia só para a tabela, assim não estrago os gráficos lá de cima
    df_exibicao = df.copy()

    if filtro_analista:
        df_exibicao = df_exibicao[df_exibicao['Analista N2'].isin(filtro_analista)]
    if filtro_jira:
        df_exibicao = df_exibicao[df_exibicao['Status Jira'].isin(filtro_jira)]
    if filtro_plat:
        df_exibicao = df_exibicao[df_exibicao['Plataforma'].isin(filtro_plat)]
    if filtro_sev:
        df_exibicao = df_exibicao[df_exibicao['Severidade'].isin(filtro_sev)]

    # --- ABAS E BOTÃO DE EXCEL ---
    # Divido o espaço para alinhar o botão do Excel bonitinho à direita
    c_titulo, c_botao = st.columns([4, 1])
    
    with c_titulo:
        st.subheader("📋 Lista Detalhada")
        
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

    # Separo os chamados nas duas abas usando aquela marcação que fiz lá em cima
    df_periodo = df_exibicao[df_exibicao['Origem'] == 'Período']

    # Para o Backlog, filtramos para mostrar apenas os chamados que realmente estão ativos
    status_ativos = ['Aberto', 'Em andamento', 'Em Andamento', 'Esperando por você', 'Em Análise N2']
    df_backlog = df_exibicao[(df_exibicao['Origem'] == 'Backlog') & (df_exibicao['Status Intercom'].isin(status_ativos))]

    # Crio as abas já mostrando o número de chamados em cada uma
    aba_periodo, aba_backlog = st.tabs([
        f"📅 Período Selecionado ({len(df_periodo)})", 
        f"🗄️ Backlog Pendente ({len(df_backlog)})"
    ])

    # Transformo as URLs em links clicáveis e escondo a coluna Origem
    config_colunas = {
        "SLA": st.column_config.Column(width="small"),
        "Origem": None, 
        "Link Ticket": st.column_config.LinkColumn("Link Ticket", display_text="🔗 Abrir Ticket"),
        "Link Conversa Original": st.column_config.LinkColumn("Link Conversa Original", display_text="💬 Abrir Conversa")
    }

    with aba_periodo:
        st.dataframe(df_periodo, use_container_width=True, hide_index=True, column_config=config_colunas)

    with aba_backlog:
        if not df_backlog.empty:
            st.dataframe(df_backlog, use_container_width=True, hide_index=True, column_config=config_colunas)
        else:
            st.success("🎉 Nenhum chamado antigo pendente no Backlog!")
