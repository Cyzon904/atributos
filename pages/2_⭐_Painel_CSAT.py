import streamlit as st
import pandas as pd
import time
from datetime import datetime, timezone, timedelta, time as dt_time
from utils import check_password, make_api_request

# --- Configurações Iniciais ---
st.set_page_config(page_title="Meu CSAT", page_icon="⭐", layout="wide")

# Bloqueio de segurança
if not check_password():
    st.stop()

# Recuperação de segredos
try:
    APP_ID = st.secrets["INTERCOM_APP_ID"]
except KeyError:
    st.error("Erro: Configure 'INTERCOM_APP_ID' no arquivo .streamlit/secrets.toml")
    st.stop()

FUSO_BR = timezone(timedelta(hours=-3))

# Lista dos times que podem aparecer no painel
TIMES_PERMITIDOS_IDS = [2975006, 1972225]

# --- Funções de Busca ---

@st.cache_data(ttl=60, show_spinner=False)
def get_admin_names(): 
    """Busca os nomes dos admins e filtra apenas os que pertencem aos times permitidos."""
    url = "https://api.intercom.io/admins"
    data = make_api_request("GET", url)
    
    admins_filtrados = {}
    if data:
        for a in data.get('admins', []):
            # Pega a lista de times do atendente, se não tiver, cria uma lista vazia
            admin_teams = a.get('team_ids', [])
            
            # Verifica se o atendente está em pelo menos um dos times da nossa lista
            if any(team_id in TIMES_PERMITIDOS_IDS for team_id in admin_teams):
                admins_filtrados[a['id']] = a['name']
                
    return admins_filtrados

@st.cache_data(ttl=60, show_spinner=False)
def fetch_individual_csat_data(start_ts, end_ts, admin_id):
    """Busca as conversas filtrando direto pelo ID do analista."""
    url = "https://api.intercom.io/conversations/search"
    payload = {
        "query": {
            "operator": "AND",
            "value": [
                {"field": "updated_at", "operator": ">", "value": start_ts},
                {"field": "updated_at", "operator": "<", "value": end_ts},
                {"field": "admin_assignee_id", "operator": "=", "value": admin_id}
            ]
        },
        "pagination": {"per_page": 150}
    }
    
    conversas = []
    data = make_api_request("POST", url, json=payload)
    if not data: return []
    
    total = data.get('total_count', 0)
    conversas.extend(data.get('conversations', []))
    
    if total > 0:
        while data.get('pages', {}).get('next'):
            time.sleep(0.2)
            payload['pagination']['starting_after'] = data['pages']['next']['starting_after']
            data = make_api_request("POST", url, json=payload)
            if data:
                conversas.extend(data.get('conversations', []))
            else:
                break
            
    return conversas

def process_individual_stats(conversas, start_ts, end_ts):
    """Gera as estatísticas de um único analista."""
    stats = {'pos': 0, 'neu': 0, 'neg': 0, 'total': 0}
    details_list = []
    
    for c in conversas:
        if not c.get('conversation_rating'): continue
        
        rating_obj = c['conversation_rating']
        nota = rating_obj.get('rating')
        if nota is None: continue
        
        data_nota = rating_obj.get('created_at')
        if not data_nota: continue
        
        if not (start_ts <= data_nota <= end_ts): continue
        
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

# Interface Visual

st.title("⭐ Meu Painel de Qualidade (CSAT)")
st.caption("Acompanhe os seus indicadores de qualidade.")

# Carrega a lista de analistas antes de montar o formulário
admins = get_admin_names()

if not admins:
    st.warning("Não foi possível carregar a lista de analistas.")
    st.stop()

with st.form("filtro_csat_individual"):
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        admin_ids = list(admins.keys())
        # O usuário vê o nome na tela, mas o código usa o ID para a busca
        admin_selecionado_id = st.selectbox(
            "👤 Seu Nome:", 
            options=admin_ids, 
            format_func=lambda x: admins[x]
        )
        
    with col2:
        periodo = st.date_input(
            "📅 Período:",
            value=(datetime.now().replace(day=1), datetime.now()),
            format="DD/MM/YYYY"
        )
        
    with col3:
        st.write("") 
        st.write("")
        submit_btn = st.form_submit_button("Buscar", type="primary", use_container_width=True)

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
        
    with st.spinner("Buscando suas avaliações..."):
        raw_data = fetch_individual_csat_data(ts_start, ts_end, admin_selecionado_id)
        stats, lista_detalhada = process_individual_stats(raw_data, ts_start, ts_end)
    
    st.session_state['dados_csat_ind'] = {
        'stats': stats,
        'lista_detalhada': lista_detalhada
    }

if 'dados_csat_ind' in st.session_state:
    dados = st.session_state['dados_csat_ind']
    stats = dados['stats']
    lista_detalhada = dados['lista_detalhada']
    
    total = stats['total']
    csat_real = (stats['pos'] / total * 100) if total > 0 else 0
    
    total_valid = stats['pos'] + stats['neg']
    csat_adjusted = (stats['pos'] / total_valid * 100) if total_valid > 0 else 0

    st.divider()
    
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("CSAT Geral", f"{csat_real:.1f}%", f"{total} avaliações")
    c2.metric("CSAT Ajustado", f"{csat_adjusted:.1f}%", "Sem neutras") 
    c3.metric("😍 Positivas", stats['pos'])
    c4.metric("😐 Neutras", stats['neu'])
    c5.metric("😡 Negativas", stats['neg'])
    
    st.divider()

    st.subheader("🔎 Detalhamento das Suas Avaliações")

    if lista_detalhada:
        df_detalhe = pd.DataFrame(lista_detalhada)
        
        st.data_editor(
            df_detalhe,
            column_config={
                "Link": st.column_config.LinkColumn("Ticket", display_text="Abrir"),
                "Nota": st.column_config.NumberColumn("Nota", format="%d ⭐"),
                "Comentário": st.column_config.TextColumn("Obs. Cliente", width="medium")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Nenhuma avaliação encontrada neste período.")
else:
    st.info("Escolha seu nome e o período acima para começar.")
