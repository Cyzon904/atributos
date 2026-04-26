import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime, timezone, timedelta, time as dt_time
import sys
import os

# Ajuste de caminho para garantir que utils.py seja encontrado
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from utils import check_password, logout_button
except ImportError:
    st.error("Erro: utils.py não encontrado. Verifique se o arquivo está na pasta raiz.")
    st.stop()

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Painel de Performance", page_icon="🎯", layout="wide")

# --- LOGIN ---
if not check_password():
    st.stop()

# --- CONFIGURAÇÕES GERAIS ---
WORKSPACE_ID = "xwvpdtlu"
FUSO_BR = timezone(timedelta(hours=-3))
TIMES_PERMITIDOS_IDS = [2975006, 1972225]

try:
    APP_ID = st.secrets["INTERCOM_APP_ID"]
    INTERCOM_ACCESS_TOKEN = st.secrets["INTERCOM_TOKEN"]
except KeyError:
    st.error("Erro: Configure 'INTERCOM_APP_ID' e 'INTERCOM_TOKEN' no arquivo .streamlit/secrets.toml")
    st.stop()

HEADERS = {"Authorization": f"Bearer {INTERCOM_ACCESS_TOKEN}", "Accept": "application/json"}

logout_button()

# --- FUNÇÕES ---

@st.cache_data(ttl=3600)
def get_admin_list():
    """Busca lista de analistas, filtra pelos times permitidos e ordena por nome."""
    url = "https://api.intercom.io/admins"
    try:
        r = requests.get(url, headers=HEADERS)
        admins = r.json().get('admins', [])
        
        analistas_validos = {}
        for a in admins:
            if a.get('id') and a.get('name'):
                admin_teams = [int(tid) for tid in a.get('team_ids', [])]
                if any(team_id in TIMES_PERMITIDOS_IDS for team_id in admin_teams):
                    analistas_validos[a['name']] = a['id']
                    
        # Retorna o dicionário ordenado alfabeticamente
        return dict(sorted(analistas_validos.items()))
    except:
        return {}

@st.cache_data(ttl=3600)
def get_attribute_definitions():
    """Busca os nomes bonitos dos atributos do Intercom."""
    url = "https://api.intercom.io/data_attributes"
    params = {"model": "conversation"}
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        return {item['name']: item['label'] for item in r.json().get('data', [])}
    except:
        return {}

def fetch_my_conversations(ts_start, ts_end, admin_id):
    """Busca conversas fechadas para calcular a classificação."""
    url = "https://api.intercom.io/conversations/search"
    
    query_rules = [
        {"field": "created_at", "operator": ">", "value": ts_start},
        {"field": "created_at", "operator": "<", "value": ts_end},
        {"field": "admin_assignee_id", "operator": "=", "value": admin_id},
        {"field": "state", "operator": "=", "value": "closed"},
        {"field": "team_assignee_id", "operator": "IN", "value": TIMES_PERMITIDOS_IDS}
    ]
    
    payload = {
        "query": {"operator": "AND", "value": query_rules},
        "pagination": {"per_page": 150}
    }
    
    conversas_validas = []
    has_more = True
    
    while has_more:
        try:
            resp = requests.post(url, headers=HEADERS, json=payload)
            data = resp.json()
            batch = data.get('conversations', [])
            
            for c in batch:
                attrs = c.get('custom_attributes', {})
                categoria = attrs.get('Ticket category')
                if categoria == "Back-office ticket":
                    continue 
                conversas_validas.append(c)
            
            if data.get('pages', {}).get('next'):
                payload['pagination']['starting_after'] = data['pages']['next']['starting_after']
                time.sleep(0.1)
            else:
                has_more = False
        except:
            break
            
    return conversas_validas

def fetch_individual_csat_data(ts_start, ts_end, admin_id):
    """Busca as conversas para calcular o CSAT."""
    url = "https://api.intercom.io/conversations/search"
    payload = {
        "query": {
            "operator": "AND",
            "value": [
                {"field": "updated_at", "operator": ">", "value": ts_start},
                {"field": "updated_at", "operator": "<", "value": ts_end},
                {"field": "admin_assignee_id", "operator": "=", "value": admin_id}
            ]
        },
        "pagination": {"per_page": 150}
    }
    
    conversas = []
    has_more = True
    
    while has_more:
        try:
            resp = requests.post(url, headers=HEADERS, json=payload)
            data = resp.json()
            conversas.extend(data.get('conversations', []))
            
            if data.get('pages', {}).get('next'):
                payload['pagination']['starting_after'] = data['pages']['next']['starting_after']
                time.sleep(0.1)
            else:
                has_more = False
        except:
            break
            
    return conversas

def process_individual_stats(conversas, ts_start, ts_end):
    """Gera as estatísticas de CSAT."""
    stats = {'pos': 0, 'neu': 0, 'neg': 0, 'total': 0}
    details_list = []
    
    for c in conversas:
        if not c.get('conversation_rating'): continue
        
        rating_obj = c['conversation_rating']
        nota = rating_obj.get('rating')
        if nota is None: continue
        
        data_nota = rating_obj.get('created_at')
        if not data_nota: continue
        
        if not (ts_start <= data_nota <= ts_end): continue
        
        stats['total'] += 1
        
        label_nota = ""
        if nota >= 4:
            stats['pos'] += 1; label_nota = "😍 Positiva" 
        elif nota == 3:
            stats['neu'] += 1; label_nota = "😐 Neutra" 
        else:
            stats['neg'] += 1; label_nota = "😡 Negativa"

        dt_evento = datetime.fromtimestamp(data_nota, tz=FUSO_BR).strftime("%d/%m %H:%M")
        comentario = rating_obj.get('remark', '-')
        link_url = f"https://app.intercom.com/a/inbox/{APP_ID}/inbox/conversation/{c['id']}"
        
        details_list.append({
            "Data": dt_evento,
            "Nota": nota,
            "Tipo": label_nota,
            "Comentário": comentario,
            "Link": link_url
        })
            
    return stats, details_list

# --- INTERFACE DO USUÁRIO ---

st.title("🎯 Meu Painel de Performance")
st.markdown("Acompanhe suas metas de classificação e sua nota de qualidade (CSAT).")

# Carrega a lista de analistas
admins = get_admin_list()

if not admins:
    st.info("Carregando lista de analistas...")
    st.stop()

# --- FORMULÁRIO UNIFICADO ---
with st.form("form_filtros_unificado"):
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        # A lista de chaves já está em ordem alfabética graças à função get_admin_list
        usuario_selecionado = st.selectbox("👤 Seu Nome:", list(admins.keys()))
        
    with col2:
        data_hoje = datetime.now()
        periodo = st.date_input("📅 Período:", (data_hoje - timedelta(days=7), data_hoje), format="DD/MM/YYYY")
        
    with col3:
        st.write("") 
        st.write("")
        submit_btn = st.form_submit_button("🔄 Buscar Dados", type="primary", use_container_width=True)

# --- LÓGICA DE BUSCA ---
if submit_btn:
    ts_start, ts_end = 0, 0
    if isinstance(periodo, tuple):
        d_im = periodo[0]
        d_fm = periodo[1] if len(periodo) > 1 else periodo[0]
        ts_start = int(datetime.combine(d_im, dt_time.min).timestamp())
        ts_end = int(datetime.combine(d_fm, dt_time.max).timestamp())
    else:
        ts_start = int(datetime.combine(periodo, dt_time.min).timestamp())
        ts_end = int(datetime.combine(periodo, dt_time.max).timestamp())
        
    admin_id_alvo = admins[usuario_selecionado]

    with st.spinner("Buscando dados de Classificação e CSAT..."):
        # Busca Classificação
        raw_classificacao = fetch_my_conversations(ts_start, ts_end, admin_id_alvo)
        mapa_attrs = get_attribute_definitions()
        
        # Processa Classificação
        rows_classificacao = []
        if raw_classificacao:
            for c in raw_classificacao:
                attrs = c.get('custom_attributes', {})
                motivo = None
                for k, v in attrs.items():
                    label = mapa_attrs.get(k, k)
                    if label == "Motivo de Contato":
                        motivo = v
                        break
                
                link = f"https://app.intercom.com/a/inbox/{WORKSPACE_ID}/inbox/conversation/{c['id']}"
                rows_classificacao.append({
                    "ID": c['id'],
                    "Data": datetime.fromtimestamp(c['created_at']).strftime("%d/%m/%Y %H:%M"),
                    "Motivo": motivo,
                    "Link": link,
                    "Status": "✅ Classificado" if motivo else "🚨 Pendente"
                })
        
        # Busca e Processa CSAT
        raw_csat = fetch_individual_csat_data(ts_start, ts_end, admin_id_alvo)
        stats_csat, lista_detalhada_csat = process_individual_stats(raw_csat, ts_start, ts_end)

        # Salva tudo na memória
        st.session_state['dados_performance'] = {
            'nome': usuario_selecionado,
            'df_classificacao': pd.DataFrame(rows_classificacao),
            'stats_csat': stats_csat,
            'lista_csat': lista_detalhada_csat
        }
        st.success("Dados atualizados com sucesso!")

# --- EXIBIÇÃO DOS RESULTADOS ---
if 'dados_performance' in st.session_state:
    dados = st.session_state['dados_performance']
    nome_atual = dados['nome']
    df_class = dados['df_classificacao']
    stats = dados['stats_csat']
    lista_csat = dados['lista_csat']

    # --- SEÇÃO 1: CLASSIFICAÇÃO ---
    st.subheader("📊 Metas de Classificação")
    
    if not df_class.empty:
        total_class = len(df_class)
        classificados = len(df_class[df_class["Motivo"].notna()])
        pendentes = total_class - classificados
        taxa = (classificados / total_class * 100) if total_class > 0 else 0
        
        k1, k2, k3 = st.columns(3)
        k1.metric("Conversas de Suporte", total_class)
        k2.metric("Pendentes de Classificação", pendentes, delta="-Zerado!" if pendentes == 0 else f"{pendentes} para fazer", delta_color="inverse")
        k3.metric("Minha Taxa", f"{taxa:.1f}%", delta="Meta: 90%", delta_color="normal" if taxa >= 90 else "inverse")

        st.progress(min(taxa / 100, 1.0))
        
        if taxa < 90:
            st.warning(f"Atenção, {nome_atual}! Faltam **{int(((0.9 * total_class) - classificados)) + 1}** conversas para bater 90%.")
        
        with st.expander("Ver lista de classificações"):
            tab_pendentes, tab_todos = st.tabs(["🚨 Pendências", "📋 Histórico"])
            with tab_pendentes:
                df_pendentes = df_class[df_class["Status"] == "🚨 Pendente"]
                if not df_pendentes.empty:
                    st.dataframe(df_pendentes[["Data", "ID", "Link"]], use_container_width=True, column_config={"Link": st.column_config.LinkColumn("Link", display_text="🔗 Abrir no Intercom")}, hide_index=True)
                else:
                    st.success("Tudo limpo! Nenhuma pendência encontrada.")
            with tab_todos:
                st.dataframe(df_class[["Data", "ID", "Motivo", "Status", "Link"]], use_container_width=True, column_config={"Link": st.column_config.LinkColumn("Link", display_text="Abrir")}, hide_index=True)
    else:
        st.info("Nenhuma conversa de suporte fechada encontrada neste período.")

    st.divider()

    # --- SEÇÃO 2: CSAT ---
    st.subheader("⭐ Qualidade (CSAT)")
    
    total_csat = stats['total']
    csat_real = (stats['pos'] / total_csat * 100) if total_csat > 0 else 0
    total_valid = stats['pos'] + stats['neg']
    csat_adjusted = (stats['pos'] / total_valid * 100) if total_valid > 0 else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("CSAT Geral", f"{csat_real:.1f}%", f"{total_csat} avaliações")
    c2.metric("CSAT Ajustado", f"{csat_adjusted:.1f}%", "Sem neutras") 
    c3.metric("😍 Positivas", stats['pos'])
    c4.metric("😐 Neutras", stats['neu'])
    c5.metric("😡 Negativas", stats['neg'])

    if lista_csat:
        with st.expander("Ver detalhamento das avaliações"):
            st.data_editor(
                pd.DataFrame(lista_csat),
                column_config={
                    "Link": st.column_config.LinkColumn("Ticket", display_text="Abrir"),
                    "Nota": st.column_config.NumberColumn("Nota", format="%d ⭐"),
                    "Comentário": st.column_config.TextColumn("Obs. Cliente", width="medium")
                },
                use_container_width=True,
                hide_index=True
            )
    else:
        st.info("Nenhuma avaliação recebida neste período.")
