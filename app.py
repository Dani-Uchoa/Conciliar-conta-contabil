import streamlit as st
import pandas as pd
import io
import re
import numpy as np
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict

st.set_page_config(page_title="Auditoria Contábil - Domínio Sistemas", layout="wide")

def formatar_moeda(v):
    try:
        if isinstance(v, str):
            v = v.replace('.', '').replace(',', '.')
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (ValueError, TypeError):
        return Decimal('0.00')

def formatar_brl(valor):
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# ==========================================
# MÓDULOS DE EXTRAÇÃO E LEITURA 
# ==========================================
@st.cache_data
def extrair_saldo_balancete(raw_data, conta_alvo):
    try:
        df_balancete = pd.read_excel(io.BytesIO(raw_data))
    except Exception:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data).read())
            df_balancete = dfs[0]
        except Exception:
            raise ValueError("O formato do Balancete não é suportado.")

    cols_str = ' '.join(str(c).upper() for c in df_balancete.columns)

    if ('CÓDIGO' in cols_str or 'CODIGO' in cols_str or 'CONTA' in cols_str) and 'ANTERIOR' in cols_str:
        df_bal = df_balancete.copy()
        df_bal.columns = [str(col).strip().upper() for col in df_balancete.columns]
    else:
        header_idx = -1
        for idx, row in df_balancete.head(20).iterrows():
            row_str = ' '.join(str(x).upper() for x in row.values)
            if ('CÓDIGO' in row_str or 'CODIGO' in row_str or 'CONTA' in row_str) and 'ANTERIOR' in row_str:
                header_idx = idx
                break
        if header_idx == -1:
            return Decimal('0.00')
        df_bal = df_balancete.iloc[header_idx+1:].copy()
        df_bal.columns = [str(col).strip().upper() for col in df_balancete.iloc[header_idx].values]

    col_conta = next((c for c in df_bal.columns if 'CÓDIGO' in c or 'CODIGO' in c or 'CONTA' in c), None)
    col_saldo = next((c for c in df_bal.columns if 'ANTERIOR' in c), None)
    if not col_conta or not col_saldo:
        return Decimal('0.00')

    df_bal[col_conta] = pd.to_numeric(df_bal[col_conta], errors='coerce')
    linha_conta = df_bal[df_bal[col_conta] == float(conta_alvo)]
    if linha_conta.empty:
        return Decimal('0.00')
    return formatar_moeda(linha_conta.iloc[0][col_saldo])

@st.cache_data
def extrair_cabecalho(raw_data):
    try:
        df_raw = pd.read_excel(io.BytesIO(raw_data), header=None)
    except Exception:
        return None, None, {}

    empresa = None
    if len(df_raw) > 0 and not pd.isna(df_raw.iloc[0, 0]):
        primeira = str(df_raw.iloc[0, 0]).strip()
        if primeira and not primeira.upper().startswith(('CONTA:', 'DATA', 'C.N.P.J')):
            empresa = primeira

    periodo = None
    contas_nomes = {}
    for val in df_raw[0]:
        if pd.isna(val):
            continue
        v = str(val).strip()
        vu = v.upper()
        if periodo is None and (vu.startswith('PERÍODO') or vu.startswith('PERIODO')):
            periodo = v.split(':', 1)[1].strip() if ':' in v else v
        if vu.startswith('CONTA:'):
            resto = v.split(':', 1)[1].strip() if ':' in v else ''
            if '-' in resto:
                num_str, nome = resto.split('-', 1)
                num_str = num_str.strip()
                if num_str.isdigit():
                    contas_nomes[int(num_str)] = nome.strip()

    return empresa, periodo, contas_nomes

@st.cache_data
def carregar_base_lancamentos(raw_data):
    try:
        df_raw = pd.read_excel(io.BytesIO(raw_data), header=None)
    except Exception:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data))
            df_raw = dfs[0]
        except Exception:
            raise ValueError("Erro de leitura.")

    header_idx = 5
    for idx, row in df_raw.head(20).iterrows():
        row_str = ' '.join(str(x).upper() for x in row.values)
        if 'DATA' in row_str and 'HIST' in row_str:
            header_idx = idx
            break

    df_clean = df_raw.iloc[header_idx:].copy()
    df_clean.columns = [str(h).strip() for h in df_clean.iloc[0].values]
    
    # Tratamento para colunas duplicadas (ex: NaN)
    cols = pd.Series(df_clean.columns)
    for dup in cols[cols.duplicated()].unique():
        cols[cols[cols == dup].index.values.tolist()] = [dup + '_' + str(i) if i != 0 else dup for i in range(sum(cols == dup))]
    df_clean.columns = cols

    df_clean = df_clean.iloc[1:].dropna(subset=['Data', 'Valor']).reset_index(drop=True)
    df_clean = df_clean[~df_clean['Data'].astype(str).str.startswith('Conta:')].copy()
    
    df_clean['Valor'] = df_clean['Valor'].apply(formatar_moeda)
    df_clean['Data'] = pd.to_datetime(df_clean['Data'], errors='coerce')
    
    df_clean['Débito'] = pd.to_numeric(df_clean['Débito'], errors='coerce')
    df_clean['Crédito'] = pd.to_numeric(df_clean['Crédito'], errors='coerce')

    return df_clean

# ==========================================
# MOTOR DE CONCILIAÇÃO (OTIMIZADO - DP)
# ==========================================
def montar_tabela_razao(eventos):
    razao = []
    saldo = Decimal('0.00')
    for ev in eventos:
        saldo += ev['Débito']
        saldo -= ev['Crédito']
        razao.append({
            'Data': ev['Data_str'],
            'Histórico': ev['Histórico'],
            'Débito (+)': float(ev['Débito']) if ev['Débito'] > 0 else None,
            'Crédito (-)': float(ev['Crédito']) if ev['Crédito'] > 0 else None,
            'Saldo Acumulado': float(saldo)
        })
    return razao, float(saldo)

def _subset_sum_dp(alvo, valores, max_itens=6):
    """
    Encontra um subconjunto de `valores` cuja soma seja exatamente `alvo`.
    Usa Programação Dinâmica em array 1D para extrema performance.
    Trabalha com inteiros (valor * 100).
    Retorna os índices dos valores que compõem a soma, ou None.
    """
    if alvo <= 0 or not valores: return None
    
    # Multiplica por 100 e converte pra inteiro pra evitar flutuações decimais
    alvo_int = int(float(alvo) * 100)
    vals_int = [int(float(v) * 100) for v in valores]
    
    # O array `dp` armazena o caminho. Se dp[S] != -1, significa que
    # é possível atingir a soma S, e dp[S] guarda o índice do último item usado.
    dp = np.full(alvo_int + 1, -1, dtype=int)
    dp[0] = -2 # Marcador inicial
    
    # Rastreio de quantos itens foram usados para chegar naquela soma
    contagem_itens = np.zeros(alvo_int + 1, dtype=int)
    
    for idx, v in enumerate(vals_int):
        if v <= 0 or v > alvo_int: continue
        # Itera de trás pra frente pra usar cada item apenas uma vez
        for j in range(alvo_int, v - 1, -1):
            if dp[j - v] != -1 and dp[j] == -1 and contagem_itens[j - v] < max_itens:
                dp[j] = idx
                contagem_itens[j] = contagem_itens[j - v] + 1
                if j == alvo_int:
                    break
        if dp[alvo_int] != -1:
            break

    if dp[alvo_int] != -1:
        indices_usados = []
        soma_atual = alvo_int
        while soma_atual > 0:
            idx = dp[soma_atual]
            indices_usados.append(idx)
            soma_atual -= vals_int[idx]
        return indices_usados
    return None

def processar_razoes_contabeis(df_main, conta_alvo, saldo_anterior_informado, tipo, janela_dias=60):
    
    col_deb, col_cred = ('Débito', 'Crédito') if tipo == 'CARTAO' else ('Crédito', 'Débito')
    
    df_alvo = df_main[(df_main['Débito'] == conta_alvo) | (df_main['Crédito'] == conta_alvo)].copy()
    
    todos_debitos = []
    todos_creditos = []
    
    saldo_ant = abs(Decimal(str(saldo_anterior_informado)))
    tem_saldo_anterior = saldo_ant > Decimal('0.00')
    
    if tem_saldo_anterior:
        data_base = df_alvo['Data'].min() if not df_alvo.empty else pd.to_datetime('2026-01-01')
        data_ant = data_base - pd.Timedelta(days=1)
        todos_debitos.append({
            'Data_str': data_ant.strftime('%d/%m/%Y'), 'Data_dt': data_ant,
            'Histórico': 'SALDO ANTERIOR HERDADO', 'Débito': saldo_ant, 'Crédito': Decimal('0.00')
        })

    for idx, row in df_alvo.iterrows():
        dt = row['Data']
        if pd.isna(dt): continue
        dt_str = dt.strftime('%d/%m/%Y')
        valor = row['Valor']
        
        if row[col_deb] == conta_alvo:
            todos_debitos.append({
                'Data_str': dt_str, 'Data_dt': dt, 'Histórico': str(row.get('Histórico', '')), 
                'Débito': valor, 'Crédito': Decimal('0.00')
            })
        if row[col_cred] == conta_alvo:
            todos_creditos.append({
                'Data_str': dt_str, 'Data_dt': dt, 'Histórico': str(row.get('Histórico', '')), 
                'Débito': Decimal('0.00'), 'Crédito': valor
            })

    todos_debitos.sort(key=lambda x: x['Data_dt'])
    todos_creditos.sort(key=lambda x: x['Data_dt'])

    debitos_usados = set()
    creditos_usados = set()
    
    # 1. Tenta fechar o Saldo Anterior
    indices_credito_ant = []
    if tem_saldo_anterior:
        # Pega créditos do início do ano pra tentar fechar o saldo anterior
        candidatos_cred = [todos_creditos[i]['Crédito'] for i in range(len(todos_creditos)) if i < 150] 
        res_ant = _subset_sum_dp(saldo_ant, candidatos_cred, max_itens=10)
        if res_ant:
            indices_credito_ant = res_ant
            debitos_usados.add(0)
            creditos_usados.update(res_ant)

    # 2. Matching 1-para-1 exato na janela de dias
    for i, deb in enumerate(todos_debitos):
        if i in debitos_usados: continue
        valor_deb = deb['Débito']
        data_deb = deb['Data_dt']
        
        for j, cred in enumerate(todos_creditos):
            if j in creditos_usados: continue
            diff_dias = (cred['Data_dt'] - data_deb).days
            if 0 <= diff_dias <= janela_dias and cred['Crédito'] == valor_deb:
                debitos_usados.add(i)
                creditos_usados.add(j)
                break
                
    # 3. Matching Dinâmico (1 Débito para N Créditos) - DP
    for i, deb in enumerate(todos_debitos):
        if i in debitos_usados: continue
        valor_deb = deb['Débito']
        data_deb = deb['Data_dt']
        
        # Filtra candidatos no futuro dentro da janela
        candidatos_idx = [j for j, cred in enumerate(todos_creditos) 
                          if j not in creditos_usados and 0 <= (cred['Data_dt'] - data_deb).days <= janela_dias]
        
        candidatos_val = [todos_creditos[j]['Crédito'] for j in candidatos_idx]
        
        # Usa o DP super-rápido limitando a 6 itens por grupo pra evitar lixo
        res_dp = _subset_sum_dp(valor_deb, candidatos_val, max_itens=6)
        if res_dp:
            debitos_usados.add(i)
            # Mapeia os índices do array filtrado de volta pros originais
            indices_reais = [candidatos_idx[idx] for idx in res_dp]
            creditos_usados.update(indices_reais)

    eventos_ant = []
    if tem_saldo_anterior and len(indices_credito_ant) > 0:
        eventos_ant = [todos_debitos[0]] + [todos_creditos[j] for j in indices_credito_ant]

    eventos_atual = [todos_debitos[i] for i in debitos_usados if i != 0] + [todos_creditos[j] for j in creditos_usados if j not in indices_credito_ant]
    
    eventos_pend = [todos_debitos[i] for i in range(len(todos_debitos)) if i not in debitos_usados] + \
                   [todos_creditos[j] for j in range(len(todos_creditos)) if j not in creditos_usados]

    chave_ordem = lambda x: (x['Data_dt'], x['Crédito'] > 0)
    eventos_ant.sort(key=chave_ordem)
    eventos_atual.sort(key=chave_ordem)
    eventos_pend.sort(key=chave_ordem)
    todos_eventos = sorted(todos_debitos + todos_creditos, key=chave_ordem)

    razao_tot, saldo_tot = montar_tabela_razao(todos_eventos)
    razao_ant, saldo_ant_aba = montar_tabela_razao(eventos_ant)
    razao_atual, saldo_atual_aba = montar_tabela_razao(eventos_atual)
    razao_pend, saldo_pend_aba = montar_tabela_razao(eventos_pend)

    return razao_tot, razao_ant, razao_atual, razao_pend, saldo_tot, saldo_ant_aba, saldo_atual_aba, saldo_pend_aba

def gerar_excel_memoria(dfs_dict):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for sheet_name, data in dfs_dict.items():
            if data:
                pd.DataFrame(data).to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()


# ==========================================
# INTERFACE STREAMLIT
# ==========================================
st.title("📊 Auditoria Contábil - Motor Dinâmico")
arquivo_lancamentos = st.file_uploader("📁 Base Geral de Lançamentos (.xls/.xlsx)", type=["xls", "xlsx"])

empresa, periodo, contas_nomes = None, None, {}
if arquivo_lancamentos:
    empresa, periodo, contas_nomes = extrair_cabecalho(arquivo_lancamentos.getvalue())

with st.sidebar:
    janela_dias = st.number_input("📅 Janela máxima (dias)", value=60, step=10, min_value=1)

col_nat, col_conta = st.columns([1.3, 1])
with col_nat:
    modo = st.radio("Natureza da conta:", ["1. Cartões a Receber (Ativo)", "2. Fornecedores a Pagar (Passivo)"])
with col_conta:
    conta_input = st.number_input("🔢 Conta contábil alvo", value=0, step=1)
    if conta_input != 0 and conta_input in contas_nomes:
        st.caption(f"📄 {contas_nomes[conta_input]}")

arquivo_balancete = st.file_uploader("📁 Balancete Opcional (.xls/.xlsx)", type=["xls", "xlsx"])

saldo_abertura_var = Decimal('0.00')
if arquivo_balancete and conta_input != 0:
    try:
        bytes_balancete = arquivo_balancete.getvalue()
        saldo_abertura_var = extrair_saldo_balancete(bytes_balancete, conta_input)
        st.success(f"✔️ Saldo Anterior: {formatar_brl(saldo_abertura_var)}")
    except Exception:
        saldo_abertura_var = Decimal(str(st.number_input("Saldo Manual (R$)", value=0.00)))
elif not arquivo_balancete:
    saldo_abertura_var = Decimal(str(st.number_input("Saldo Manual (R$)", value=0.00, step=100.00)))

if arquivo_lancamentos and conta_input != 0:
    try:
        bytes_lancamentos = arquivo_lancamentos.getvalue()
        df_base_geral = carregar_base_lancamentos(bytes_lancamentos)

        if not ((df_base_geral['Débito'] == conta_input).any() or (df_base_geral['Crédito'] == conta_input).any()):
            st.error(f"❌ A conta {conta_input} não possui lançamentos.")
            st.stop()

        tipo_auditoria = 'CARTAO' if 'Cartões' in modo else 'FORNECEDOR'

        r_tot, r_ant, r_atual, r_pend, s_tot, s_ant, s_atual, s_pend = processar_razoes_contabeis(
            df_base_geral, conta_input, saldo_abertura_var, tipo_auditoria, janela_dias=janela_dias
        )

        excel_data = gerar_excel_memoria({
            '1. Razão Total': r_tot,
            '2. Conciliado (Ano Anterior)': r_ant,
            '3. Conciliado (Atual)': r_atual,
            '4. Não Conciliado (Pendente)': r_pend
        })

        st.download_button(label="📥 Baixar 4 Razões", data=excel_data,
                           file_name=f"Auditoria_{conta_input}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        st.write("---")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("📘 Total", formatar_brl(s_tot))
        col2.metric("✅ Ano Anterior", formatar_brl(s_ant))
        col3.metric("✅ Atual", formatar_brl(s_atual))
        col4.metric("⚠️ Pendente", formatar_brl(s_pend))

        if abs(s_ant) == 0.0 and abs(s_atual) == 0.0 and round(s_tot, 2) == round(s_pend, 2):
            st.success("✅ Auditoria Validada perfeitamente.")

    except Exception as e:
        st.error(f"Falha na execução: {e}")
