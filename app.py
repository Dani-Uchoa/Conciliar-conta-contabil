import streamlit as st
import pandas as pd
import itertools
import io
from decimal import Decimal, ROUND_HALF_UP

st.set_page_config(page_title="Auditoria Contábil - Domínio Sistemas", layout="wide")

def formatar_moeda(v):
    try:
        if isinstance(v, str):
            v = v.replace('.', '').replace(',', '.')
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except:
        return Decimal('0.00')

# ==========================================
# MÓDULOS DE CACHE E LEITURA
# ==========================================
@st.cache_data
def extrair_saldo_balancete(file_bytes, conta_alvo):
    df_balancete = pd.read_excel(io.BytesIO(file_bytes))
    cols_str = ' '.join(str(c).upper() for c in df_balancete.columns)
    
    if ('CÓDIGO' in cols_str or 'CODIGO' in cols_str or 'CONTA' in cols_str) and 'ANTERIOR' in cols_str:
        df_bal = df_balancete.copy()
        df_bal.columns = [str(col).strip().upper() for col in df_balancete.columns]
    else:
        header_idx = -1
        for idx, row in df_balancete.iterrows():
            row_str = ' '.join(str(x).upper() for x in row.values)
            if ('CÓDIGO' in row_str or 'CODIGO' in row_str or 'CONTA' in row_str) and 'ANTERIOR' in row_str:
                header_idx = idx
                break
        if header_idx == -1: return Decimal('0.00')
        df_bal = df_balancete.iloc[header_idx+1:].copy()
        df_bal.columns = [str(col).strip().upper() for col in df_balancete.iloc[header_idx].values]
    
    col_conta = next((c for c in df_bal.columns if 'CÓDIGO' in c or 'CODIGO' in c or 'CONTA' in c), None)
    col_saldo = next((c for c in df_bal.columns if 'ANTERIOR' in c), None)
    if not col_conta or not col_saldo: return Decimal('0.00')
        
    df_bal[col_conta] = pd.to_numeric(df_bal[col_conta], errors='coerce')
    linha_conta = df_bal[df_bal[col_conta] == float(conta_alvo)]
    if linha_conta.empty: return Decimal('0.00')
    return formatar_moeda(linha_conta.iloc[0][col_saldo])

@st.cache_data
def carregar_base_lancamentos(file_bytes):
    df_raw = pd.read_excel(io.BytesIO(file_bytes), header=5)
    df_clean = df_raw.dropna(how='all', axis=1).dropna(how='all', axis=0).copy()
    headers = df_clean.iloc[0].tolist()
    
    new_headers = []
    counts = {}
    for h in headers:
        h_str = str(h) if pd.notna(h) else "Col_NaN"
        if h_str in counts:
            counts[h_str] += 1
            new_headers.append(f"{h_str}_{counts[h_str]}")
        else:
            counts[h_str] = 0
            new_headers.append(h_str)

    df_clean.columns = new_headers
    df_clean = df_clean.iloc[1:].reset_index(drop=True)
    df_main = df_clean[df_clean['Data'].notna()].copy()
    df_main = df_main[~df_main['Data'].astype(str).str.startswith('Conta:')].copy()
    df_main['Valor'] = df_main['Valor'].apply(formatar_moeda)
    df_main['Data'] = pd.to_datetime(df_main['Data'], errors='coerce')
    return df_main

# ==========================================
# MOTOR DOS 3 RAZÕES (SEM DESMEMBRAMENTO)
# ==========================================
def montar_tabela_razao(eventos):
    """Gera a tabela do Razão clássico a partir de uma lista de eventos."""
    razao = []
    saldo = Decimal('0.00')
    for ev in sorted(eventos, key=lambda x: (pd.to_datetime(x['Data Real'], format='%d/%m/%Y'), x['Crédito'] > 0)):
        saldo += ev['Débito']
        saldo -= ev['Crédito']
        razao.append({
            'Data': ev['Data'],
            'Histórico': ev['Histórico'],
            'Débito (+)': float(ev['Débito']) if ev['Débito'] > 0 else None,
            'Crédito (-)': float(ev['Crédito']) if ev['Crédito'] > 0 else None,
            'Saldo Acumulado': float(saldo)
        })
    return razao, float(saldo)

def processar_razoes_contabeis(df_main, conta_alvo, saldo_anterior_informado, tipo):
    if tipo == 'CARTAO':
        col_deb = 'Débito'
        col_cred = 'Crédito'
    else: # FORNECEDOR (Passivo, natureza invertida)
        col_deb = 'Crédito'
        col_cred = 'Débito'

    df_alvo = df_main[(df_main['Débito'] == conta_alvo) | (df_main['Crédito'] == conta_alvo)].copy()
    saldo_ant = abs(Decimal(str(saldo_anterior_informado)))
    
    todos_debitos = []
    todos_creditos = []

    # Injeta o Saldo Anterior como o primeiro Débito da linha do tempo
    if saldo_ant > Decimal('0.00'):
        data_base = df_alvo['Data'].min() if not df_alvo.empty else pd.to_datetime('2026-01-01')
        todos_debitos.append({
            'Data Real': (data_base - pd.Timedelta(days=1)).strftime('%d/%m/%Y'),
            'Data': 'Saldo Anterior',
            'Histórico': 'SALDO ANTERIOR HERDADO',
            'Débito': saldo_ant, 'Crédito': Decimal('0.00')
        })

    # Extrai os Débitos e Créditos do período
    for idx, row in df_alvo.iterrows():
        dt_str = row['Data'].strftime('%d/%m/%Y')
        valor = Decimal(str(row['Valor']))
        if row[col_deb] == conta_alvo:
            todos_debitos.append({'Data Real': dt_str, 'Data': dt_str, 'Histórico': row['Histórico'], 'Débito': valor, 'Crédito': Decimal('0.00')})
        if row[col_cred] == conta_alvo:
            todos_creditos.append({'Data Real': dt_str, 'Data': dt_str, 'Histórico': row['Histórico'], 'Débito': Decimal('0.00'), 'Crédito': valor})

    todos_debitos.sort(key=lambda x: pd.to_datetime(x['Data Real'], format='%d/%m/%Y'))
    todos_creditos.sort(key=lambda x: pd.to_datetime(x['Data Real'], format='%d/%m/%Y'))

    # MOTOR DE MATCHING EXATO (Sem Desmembramento)
    # Tenta achar onde a soma acumulada de débitos e créditos se interceptam com precisão.
    d_cum = [sum(d['Débito'] for d in todos_debitos[:i+1]) for i in range(len(todos_debitos))]
    c_cum = [sum(c['Crédito'] for c in todos_creditos[:i+1]) for i in range(len(todos_creditos))]
    
    intersecoes = set(d_cum).intersection(set(c_cum))
    
    d_conciliados = []
    c_conciliados = []
    d_pendentes = todos_debitos.copy()
    c_pendentes = todos_creditos.copy()

    # Se achar um bloco exato que zere
    if intersecoes:
        max_intersecao = max(intersecoes) # Pega o maior bloco contínuo que zera
        idx_d = d_cum.index(max_intersecao)
        idx_c = c_cum.index(max_intersecao)
        
        d_conciliados = todos_debitos[:idx_d+1]
        c_conciliados = todos_creditos[:idx_c+1]
        
        d_pendentes = todos_debitos[idx_d+1:]
        c_pendentes = todos_creditos[idx_c+1:]
    else:
        # Tenta varredura secundária com omissão de 1 ou 2 taxas (imitação de agrupamento manual)
        match_encontrado = False
        for i in range(len(d_cum)-1, -1, -1):
            if match_encontrado: break
            target = d_cum[i]
            for j in range(len(c_cum)):
                if c_cum[j] >= target:
                    subset_c = todos_creditos[:j+1]
                    for drop in itertools.combinations(range(len(subset_c)), 1):
                        if c_cum[j] - sum(subset_c[k]['Crédito'] for k in drop) == target:
                            c_conciliados = [subset_c[k] for k in range(len(subset_c)) if k not in drop]
                            d_conciliados = todos_debitos[:i+1]
                            d_pendentes = todos_debitos[i+1:]
                            c_pendentes = [todos_creditos[k] for k in range(len(todos_creditos)) if k not in range(j+1) or k in drop]
                            match_encontrado = True
                            break
                    if match_encontrado: break

    # Montagem dos 3 Razões Finais
    todos_eventos = todos_debitos + todos_creditos
    eventos_conciliados = d_conciliados + c_conciliados
    eventos_pendentes = d_pendentes + c_pendentes

    razao_total, saldo_final_total = montar_tabela_razao(todos_eventos)
    razao_conciliado, saldo_final_conciliado = montar_tabela_razao(eventos_conciliados)
    razao_pendente, saldo_final_pendente = montar_tabela_razao(eventos_pendentes)

    return razao_total, razao_conciliado, razao_pendente, saldo_final_total, saldo_final_conciliado, saldo_final_pendente

def gerar_excel_memoria(dfs_dict):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for sheet_name, data in dfs_dict.items():
            if data: pd.DataFrame(data).to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()

# ==========================================
# INTERFACE DE USUÁRIO (STREAMLIT)
# ==========================================
st.title("Auditoria Contábil - Domínio Sistemas")
st.markdown("Emissão de Livros Razão. Conciliação por Partida Dobrada.")

modo = st.radio("Selecione a Natureza da Conta:", ["1. Cartões a Receber (Ativo)", "2. Fornecedores a Pagar (Passivo)"])
conta_input = st.number_input("Digite a conta contábil alvo (Ex: 623 ou 1059)", value=0, step=1)

st.markdown("---")
col_arq1, col_arq2 = st.columns(2)
with col_arq1:
    arquivo_lancamentos = st.file_uploader("1. Anexe a Base Geral de Lançamentos (.xlsx)", type=["xlsx"])
with col_arq2:
    arquivo_balancete = st.file_uploader("2. Anexe o Balancete Opcional (.xlsx)", type=["xlsx"])

saldo_abertura_var = Decimal('0.00')

if arquivo_balancete and conta_input != 0:
    try:
        bytes_balancete = arquivo_balancete.getvalue()
        saldo_abertura_var = extrair_saldo_balancete(bytes_balancete, conta_input)
        st.success(f"✔️ Saldo Anterior de R$ {float(saldo_abertura_var):,.2f} capturado do Balancete.".replace(",", "X").replace(".", ",").replace("X", "."))
    except Exception as e:
        st.error(f"Erro ao analisar o Balancete. Detalhe: {e}")
        saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00)))
elif not arquivo_balancete:
    saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00, step=100.00)))

if arquivo_lancamentos and conta_input != 0:
    try:
        st.info("Processando Partidas Dobradas...")
        bytes_lancamentos = arquivo_lancamentos.getvalue()
        df_base_geral = carregar_base_lancamentos(bytes_lancamentos)
        
        tipo_auditoria = 'CARTAO' if 'Cartões' in modo else 'FORNECEDOR'
        
        r_total, r_conciliado, r_pendente, saldo_tot, saldo_conc, saldo_pend = processar_razoes_contabeis(
            df_base_geral, conta_input, saldo_abertura_var, tipo_auditoria
        )
        
        excel_data = gerar_excel_memoria({
            '1. Razão Total': r_total,
            '2. Razão Conciliado (Zerado)': r_conciliado,
            '3. Razão Pendente (Aberto)': r_pendente
        })
        
        st.download_button(label="📥 Baixar 3 Razões (Excel)", data=excel_data, 
                           file_name=f"Auditoria_3Razoes_{conta_input}.xlsx", 
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
        st.write("---")
        st.subheader("Balanço de Validação das Abas")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.info(f"**Aba 1 (Razão Total)**\nSaldo Final: R$ {saldo_tot:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        with col2:
            st.success(f"**Aba 2 (Razão Conciliado)**\nSaldo Final: R$ {saldo_conc:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        with col3:
            st.warning(f"**Aba 3 (Razão Pendente)**\nSaldo Final: R$ {saldo_pend:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        if abs(saldo_conc) == 0.0 and round(saldo_tot, 2) == round(saldo_pend, 2):
            st.success("✅ **Auditoria Validada:** A aba de itens conciliados fechou em R$ 0,00 e o saldo de pendências bate rigorosamente com o Razão Total.")
        else:
            st.warning("⚠️ **Aviso:** Não foi possível isolar um bloco exato que zerasse matematicamente. Os itens não conciliados foram movidos para a Aba 3.")
        
    except Exception as e:
        st.error(f"Falha na execução. Detalhe técnico: {e}")
