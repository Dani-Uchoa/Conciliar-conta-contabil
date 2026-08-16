import streamlit as st
import pandas as pd
import io
import re
import itertools
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
# MÓDULOS DE EXTRAÇÃO E LEITURA 
# ==========================================
@st.cache_data
def extrair_metadados_dominio(raw_data, conta_alvo):
    try:
        df_raw = pd.read_excel(io.BytesIO(raw_data), header=None)
    except:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data).read())
            df_raw = dfs[0]
        except:
            return "NÃO IDENTIFICADA", "NÃO IDENTIFICADO", "NÃO IDENTIFICADA"

    empresa = "NÃO IDENTIFICADA"
    periodo = "NÃO IDENTIFICADO"
    conta_nome = "NÃO IDENTIFICADA"
    
    for idx, row in df_raw.head(20).iterrows():
        row_str = ' '.join(str(x).upper() for x in row.values if pd.notna(x))
        if 'EMPRESA:' in row_str and empresa == "NÃO IDENTIFICADA":
            parts = str(row.values[0]).split('Empresa:')
            if len(parts) > 1:
                sub_parts = parts[1].split('-')
                empresa = sub_parts[1].strip() if len(sub_parts) > 1 else parts[1].strip()
        if ('PERÍODO:' in row_str or 'PERIODO:' in row_str) and periodo == "NÃO IDENTIFICADO":
            for val in row.values:
                val_str = str(val).upper()
                if 'PERÍODO:' in val_str:
                    periodo = val_str.split('PERÍODO:')[1].strip()
                elif 'PERIODO:' in val_str:
                    periodo = val_str.split('PERIODO:')[1].strip()

    if conta_alvo != 0:
        for col in df_raw.columns:
            matches = df_raw[col].astype(str).str.extract(fr'Conta:\s*{conta_alvo}\s*-\s*([^0-9]+)', flags=re.IGNORECASE)
            if not matches.isna().all().values[0]:
                conta_nome = matches.dropna().iloc[0, 0].strip()
                break

    return empresa, periodo, conta_nome

@st.cache_data
def extrair_saldo_balancete(raw_data, conta_alvo):
    try:
        df_balancete = pd.read_excel(io.BytesIO(raw_data))
    except:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data).read())
            df_balancete = dfs[0]
        except:
            raise ValueError("O formato do Balancete não é suportado ou está corrompido.")

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
def carregar_base_lancamentos(raw_data):
    try:
        df_raw = pd.read_excel(io.BytesIO(raw_data), header=None)
    except:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data))
            df_raw = dfs[0]
        except:
            raise ValueError("Erro de leitura. Certifique-se de que é um arquivo suportado.")

    header_idx = 5
    for idx, row in df_raw.head(20).iterrows():
        row_str = ' '.join(str(x).upper() for x in row.values)
        if 'DATA' in row_str and 'HIST' in row_str:
            header_idx = idx
            break

    df_clean = df_raw.iloc[header_idx:].copy()
    df_clean.columns = [str(h).strip() for h in df_clean.iloc[0].values]
    df_clean = df_clean.iloc[1:].dropna(how='all', axis=1).dropna(how='all', axis=0).reset_index(drop=True)
    
    cols = pd.Series(df_clean.columns)
    for dup in cols[cols.duplicated()].unique(): 
        cols[cols[cols == dup].index.values.tolist()] = [dup + '_' + str(i) if i != 0 else dup for i in range(sum(cols == dup))]
    df_clean.columns = cols
    
    df_main = df_clean[df_clean['Data'].notna()].copy()
    df_main = df_main[~df_main['Data'].astype(str).str.startswith('Conta:')].copy()
    df_main['Valor'] = df_main['Valor'].apply(formatar_moeda)
    df_main['Data'] = pd.to_datetime(df_main['Data'], errors='coerce')
    
    if 'Débito' in df_main.columns: df_main['Débito'] = pd.to_numeric(df_main['Débito'], errors='coerce')
    if 'Crédito' in df_main.columns: df_main['Crédito'] = pd.to_numeric(df_main['Crédito'], errors='coerce')
    
    return df_main

# ==========================================
# MOTOR COMBINATÓRIO N-PARA-N (EXATO)
# ==========================================
def achar_combinacao_exata(lista_candidatos, valor_alvo, max_itens=5):
    # Procura subconjuntos de tamanho 2 até max_itens que somem o valor alvo exato
    for r in range(2, max_itens + 1):
        for combo in itertools.combinations(lista_candidatos, r):
            soma_combo = sum(item[1]['Valor'] for item in combo)
            if soma_combo == valor_alvo:
                return combo
    return None

def montar_tabela_razao(eventos):
    razao = []
    saldo = Decimal('0.00')
    for ev in eventos:
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
    col_prov, col_baixa = ('Débito', 'Crédito') if tipo == 'CARTAO' else ('Crédito', 'Débito')

    df_alvo = df_main[(df_main['Débito'] == conta_alvo) | (df_main['Crédito'] == conta_alvo)].copy()
    saldo_ant = abs(Decimal(str(saldo_anterior_informado)))
    
    provisoes = []
    baixas = []

    if saldo_ant > Decimal('0.00'):
        data_base = df_alvo['Data'].min() if not df_alvo.empty else pd.to_datetime('2026-01-01')
        provisoes.append({
            'Data Real': data_base - pd.Timedelta(days=1),
            'Tipo': 'Anterior',
            'Valor': saldo_ant,
            'Original': {
                'Data': 'Saldo Anterior', 
                'Histórico': 'SALDO ANTERIOR HERDADO', 
                'Débito': saldo_ant if tipo == 'CARTAO' else Decimal('0.00'), 
                'Crédito': saldo_ant if tipo == 'FORNECEDOR' else Decimal('0.00')
            }
        })

    for idx, row in df_alvo.iterrows():
        dt = row['Data']
        valor = Decimal(str(row['Valor']))
        
        evento = {
            'Data Real': dt,
            'Tipo': 'Atual',
            'Valor': valor,
            'Original': {
                'Data': dt.strftime('%d/%m/%Y'), 
                'Histórico': row['Histórico'], 
                'Débito': Decimal('0.00'), 
                'Crédito': Decimal('0.00')
            }
        }
        
        if row[col_prov] == conta_alvo:
            evento['Original'][col_prov] = valor
            provisoes.append(evento)
        if row[col_baixa] == conta_alvo:
            evento['Original'][col_baixa] = valor
            baixas.append(evento)

    provisoes.sort(key=lambda x: x['Data Real'])
    baixas.sort(key=lambda x: x['Data Real'])

    matched_provisoes = set()
    matched_baixas = set()
    
    eventos_ant = []
    eventos_atual = []

    def registrar_match(lista_p_idx, lista_b_idx):
        for p_idx in lista_p_idx:
            matched_provisoes.add(p_idx)
            p = provisoes[p_idx]
            if p['Tipo'] == 'Anterior': eventos_ant.append(p['Original'])
            else: eventos_atual.append(p['Original'])
            
        for b_idx in lista_b_idx:
            matched_baixas.add(b_idx)
            b = baixas[b_idx]
            if b['Tipo'] == 'Anterior': eventos_ant.append(b['Original'])
            else: eventos_atual.append(b['Original'])

    # FASE 1: Pareamento 1-para-1 exato
    for i, b in enumerate(baixas):
        for j, p in enumerate(provisoes):
            if j in matched_provisoes: continue
            if p['Valor'] == b['Valor'] and 0 <= (b['Data Real'] - p['Data Real']).days <= 60:
                registrar_match([j], [i])
                break

    # FASE 2: Agrupamento N-para-1 (Várias Notas para 1 Pagamento)
    for i, b in enumerate(baixas):
        if i in matched_baixas: continue
        
        candidatos_p = [(j, p) for j, p in enumerate(provisoes) 
                        if j not in matched_provisoes 
                        and 0 <= (b['Data Real'] - p['Data Real']).days <= 60
                        and p['Valor'] < b['Valor']]
        
        combo = achar_combinacao_exata(candidatos_p, b['Valor'])
        if combo:
            indices_p = [item[0] for item in combo]
            registrar_match(indices_p, [i])

    # FASE 3: Agrupamento 1-para-N (1 Nota para Vários Pagamentos/Parcelas)
    for j, p in enumerate(provisoes):
        if j in matched_provisoes: continue
        
        candidatos_b = [(i, b) for i, b in enumerate(baixas) 
                        if i not in matched_baixas 
                        and 0 <= (b['Data Real'] - p['Data Real']).days <= 60
                        and b['Valor'] < p['Valor']]
        
        combo = achar_combinacao_exata(candidatos_b, p['Valor'])
        if combo:
            indices_b = [item[0] for item in combo]
            registrar_match([j], indices_b)

    # O que não combinou vai para PENDENTE, o laço continua normalmente
    eventos_pend = []
    for j, p in enumerate(provisoes):
        if j not in matched_provisoes: eventos_pend.append(p['Original'])
            
    for i, b in enumerate(baixas):
        if i not in matched_baixas: eventos_pend.append(b['Original'])

    todos_eventos = [p['Original'] for p in provisoes] + [b['Original'] for b in baixas]

    def sort_event(e):
        dt = pd.to_datetime(e['Data'], format='%d/%m/%Y', errors='coerce')
        if pd.isna(dt): dt = pd.to_datetime('1900-01-01')
        return (dt, e['Crédito'] > 0)

    todos_eventos.sort(key=sort_event)
    eventos_ant.sort(key=sort_event)
    eventos_atual.sort(key=sort_event)
    eventos_pend.sort(key=sort_event)

    razao_tot, saldo_tot = montar_tabela_razao(todos_eventos)
    razao_ant, saldo_ant_aba = montar_tabela_razao(eventos_ant)
    razao_atual, saldo_atual_aba = montar_tabela_razao(eventos_atual)
    razao_pend, saldo_pend_aba = montar_tabela_razao(eventos_pend)

    return razao_tot, razao_ant, razao_atual, razao_pend, saldo_tot, saldo_ant_aba, saldo_atual_aba, saldo_pend_aba

def gerar_excel_memoria(dfs_dict):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for sheet_name, data in dfs_dict.items():
            if data: pd.DataFrame(data).to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()

# ==========================================
# INTERFACE DE USUÁRIO (STREAMLIT)
# ==========================================
st.title("📊 Auditoria Contábil - Domínio Sistemas")
st.markdown("Emissão Analítica de Livros Razão com Motor Combinatório de Soma Exata.")

col_nat, col_conta = st.columns(2)
with col_nat:
    modo = st.radio("Selecione a Natureza da Conta:", ["🏢 1. Cartões a Receber (Ativo)", "🏦 2. Fornecedores a Pagar (Passivo)"])
with col_conta:
    conta_input = st.number_input("Digite a conta contábil alvo (Ex: 623 ou 1059):", value=0, step=1)

st.markdown("---")
col_arq1, col_arq2 = st.columns(2)
with col_arq1:
    arquivo_lancamentos = st.file_uploader("📂 1. Anexe a Base Geral de Lançamentos (.xls/.xlsx)", type=["xls", "xlsx"])
with col_arq2:
    arquivo_balancete = st.file_uploader("📑 2. Anexe o Balancete Opcional (.xls/.xlsx)", type=["xls", "xlsx"])

saldo_abertura_var = Decimal('0.00')

if arquivo_balancete and conta_input != 0:
    try:
        bytes_balancete = arquivo_balancete.getvalue()
        saldo_abertura_var = extrair_saldo_balancete(bytes_balancete, conta_input)
        st.success(f"✔️ Saldo Anterior de R$ {float(saldo_abertura_var):,.2f} capturado do Balancete.".replace(",", "X").replace(".", ",").replace("X", "."))
    except Exception as e:
        st.error("Erro ao analisar o Balancete. Verifique o formato.")
        saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$):", value=0.00)))
elif not arquivo_balancete:
    saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$):", value=0.00, step=100.00)))

if arquivo_lancamentos and conta_input != 0:
    try:
        bytes_lancamentos = arquivo_lancamentos.getvalue()
        
        empresa_ext, periodo_ext, conta_nome_ext = extrair_metadados_dominio(bytes_lancamentos, conta_input)
        
        st.markdown(f"### 🏢 Empresa: **{empresa_ext}**")
        st.markdown(f"**📅 Competência:** {periodo_ext} | **🏷️ Conta:** {conta_input} - {conta_nome_ext}")
        st.markdown("---")
        
        df_base_geral = carregar_base_lancamentos(bytes_lancamentos)
        
        tem_na_base = (df_base_geral['Débito'] == conta_input).any() or (df_base_geral['Crédito'] == conta_input).any()
        if not tem_na_base:
            st.error(f"❌ EXECUÇÃO BLOQUEADA: A conta {conta_input} não possui lançamentos no arquivo anexado.")
            st.stop()
            
        tipo_auditoria = 'CARTAO' if 'Cartões' in modo else 'FORNECEDOR'
        
        r_tot, r_ant, r_atual, r_pend, s_tot, s_ant, s_atual, s_pend = processar_razoes_contabeis(
            df_base_geral, conta_input, saldo_abertura_var, tipo_auditoria
        )
        
        if len(r_ant) == 0 and len(r_atual) == 0:
            st.warning("⚠️ Atenção: Nenhum pareamento exato (simples ou agrupado) foi encontrado nas regras definidas.")
        
        excel_data = gerar_excel_memoria({
            '1. Razão Total': r_tot,
            '2. Conciliado (Ano Anterior)': r_ant,
            '3. Conciliado (Atual)': r_atual,
            '4. Não Conciliado (Pendente)': r_pend
        })
        
        nome_arquivo = f"Conciliacao_{empresa_ext.replace(' ', '_')}_{conta_input}.xlsx"
        st.download_button(label="📥 Baixar Detalhamento do Razão (Excel)", data=excel_data, 
                           file_name=nome_arquivo, 
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
        st.write("---")
        st.subheader("Balanço de Validação")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.info(f"**Total**\nR$ {s_tot:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        with col2:
            st.success(f"**Anterior**\nR$ {s_ant:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        with col3:
            st.success(f"**Atual**\nR$ {s_atual:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        with col4:
            st.warning(f"**Pendente**\nR$ {s_pend:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        if round(s_tot, 2) == round(s_pend, 2):
            st.success("✅ **Auditoria Concluída:** A estruturação matemática das pendências está correta (Total e Pendente coincidem).")
        
    except Exception as e:
        st.error(f"Falha na execução. Detalhe técnico: {e}")
