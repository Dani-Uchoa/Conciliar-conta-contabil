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
def extrair_saldo_balancete(raw_data, conta_alvo):
    try:
        df_balancete = pd.read_excel(io.BytesIO(raw_data))
    except:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data).read())
            df_balancete = dfs[0]
        except:
            raise ValueError("O formato do Balancete não é suportado ou o arquivo está corrompido.")

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
            raise ValueError("Erro de leitura. Certifique-se de que é um Excel válido.")

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
# MOTOR ORIGINAL DE INTERSEÇÃO (O QUE DEU CERTO) + 4 RAZÕES
# ==========================================
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
    if tipo == 'CARTAO':
        col_deb, col_cred = 'Débito', 'Crédito'
    else: 
        col_deb, col_cred = 'Crédito', 'Débito'

    df_alvo = df_main[(df_main['Débito'] == conta_alvo) | (df_main['Crédito'] == conta_alvo)].copy()
    saldo_ant = abs(Decimal(str(saldo_anterior_informado)))
    
    todos_debitos = []
    todos_creditos = []

    if saldo_ant > Decimal('0.00'):
        data_base = df_alvo['Data'].min() if not df_alvo.empty else pd.to_datetime('2026-01-01')
        todos_debitos.append({
            'Data Real': data_base - pd.Timedelta(days=1), 'Data': 'Saldo Anterior',
            'Histórico': 'SALDO ANTERIOR HERDADO', 'Débito': saldo_ant, 'Crédito': Decimal('0.00')
        })

    for idx, row in df_alvo.iterrows():
        dt_real = row['Data']
        dt_str = dt_real.strftime('%d/%m/%Y')
        valor = Decimal(str(row['Valor']))
        if row[col_deb] == conta_alvo:
            todos_debitos.append({'Data Real': dt_real, 'Data': dt_str, 'Histórico': row['Histórico'], 'Débito': valor, 'Crédito': Decimal('0.00')})
        if row[col_cred] == conta_alvo:
            todos_creditos.append({'Data Real': dt_real, 'Data': dt_str, 'Histórico': row['Histórico'], 'Débito': Decimal('0.00'), 'Crédito': valor})

    todos_debitos.sort(key=lambda x: x['Data Real'])
    todos_creditos.sort(key=lambda x: x['Data Real'])

    # O MOTOR QUE FUNCIONOU: ACUMULADORES INDEPENDENTES
    d_cum = [sum(d['Débito'] for d in todos_debitos[:i+1]) for i in range(len(todos_debitos))]
    c_cum = [sum(c['Crédito'] for c in todos_creditos[:i+1]) for i in range(len(todos_creditos))]
    
    intersecoes = sorted(list(set(d_cum).intersection(set(c_cum))))
    
    d_ant, c_ant = [], []
    d_atual, c_atual = [], []
    d_pend = todos_debitos.copy()
    c_pend = todos_creditos.copy()

    if intersecoes:
        # Pega a primeira interseção (Corte do Ano Anterior) e a Última (Corte do Atual)
        primeira_intersecao = intersecoes[0]
        max_intersecao = intersecoes[-1]
        
        idx_d_primeira = d_cum.index(primeira_intersecao)
        idx_c_primeira = c_cum.index(primeira_intersecao)
        
        idx_d_max = d_cum.index(max_intersecao)
        idx_c_max = c_cum.index(max_intersecao)
        
        if saldo_ant > Decimal('0.00'):
            # Até a primeira interseção vai para o Razão Ano Anterior
            d_ant = todos_debitos[:idx_d_primeira+1]
            c_ant = todos_creditos[:idx_c_primeira+1]
            
            # O restante do que bateu vai para o Razão Atual
            d_atual = todos_debitos[idx_d_primeira+1 : idx_d_max+1]
            c_atual = todos_creditos[idx_c_primeira+1 : idx_c_max+1]
        else:
            # Sem saldo velho, tudo que concilia vai para o Razão Atual
            d_atual = todos_debitos[:idx_d_max+1]
            c_atual = todos_creditos[:idx_c_max+1]
            
        # O que ficou de fora das interseções vai para o Razão Pendências
        d_pend = todos_debitos[idx_d_max+1:]
        c_pend = todos_creditos[idx_c_max+1:]
        
    else:
        # Fallback caso haja taxas não mapeadas (Omissão) - idêntico à versão de 3 abas
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
                            
                            if saldo_ant > Decimal('0.00'):
                                d_ant = d_conciliados
                                c_ant = c_conciliados
                            else:
                                d_atual = d_conciliados
                                c_atual = c_conciliados
                                
                            d_pend = todos_debitos[i+1:]
                            c_pend = [todos_creditos[k] for k in range(len(todos_creditos)) if k not in range(j+1) or k in drop]
                            match_encontrado = True
                            break
                    if match_encontrado: break

    # Ordenação e Montagem das Tabelas
    todos_eventos = sorted(todos_debitos + todos_creditos, key=lambda x: (x['Data Real'], x['Crédito'] > 0))
    eventos_ant = sorted(d_ant + c_ant, key=lambda x: (x['Data Real'], x['Crédito'] > 0))
    eventos_atual = sorted(d_atual + c_atual, key=lambda x: (x['Data Real'], x['Crédito'] > 0))
    eventos_pend = sorted(d_pend + c_pend, key=lambda x: (x['Data Real'], x['Crédito'] > 0))

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
st.title("Auditoria Contábil - Domínio Sistemas")
st.markdown("Emissão Analítica de Livros Razão. Separação de Exercícios.")

modo = st.radio("Selecione a Natureza da Conta:", ["1. Cartões a Receber (Ativo)", "2. Fornecedores a Pagar (Passivo)"])
conta_input = st.number_input("Digite a conta contábil alvo (Ex: 623 ou 1059)", value=0, step=1)

st.markdown("---")
col_arq1, col_arq2 = st.columns(2)
with col_arq1:
    arquivo_lancamentos = st.file_uploader("1. Anexe a Base Geral de Lançamentos (.xls ou .xlsx)", type=["xls", "xlsx"])
with col_arq2:
    arquivo_balancete = st.file_uploader("2. Anexe o Balancete Opcional (.xls ou .xlsx)", type=["xls", "xlsx"])

saldo_abertura_var = Decimal('0.00')

if arquivo_balancete and conta_input != 0:
    try:
        bytes_balancete = arquivo_balancete.getvalue()
        saldo_abertura_var = extrair_saldo_balancete(bytes_balancete, conta_input)
        st.success(f"✔️ Saldo Anterior de R$ {float(saldo_abertura_var):,.2f} capturado do Balancete.".replace(",", "X").replace(".", ",").replace("X", "."))
    except Exception as e:
        st.error(f"Erro ao analisar o Balancete. Verifique o formato do arquivo.")
        saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00)))
elif not arquivo_balancete:
    saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00, step=100.00)))

if arquivo_lancamentos and conta_input != 0:
    try:
        bytes_lancamentos = arquivo_lancamentos.getvalue()
        df_base_geral = carregar_base_lancamentos(bytes_lancamentos)
        
        # VALIDAÇÃO BLOQUEANTE DA CONTA
        tem_na_base = (df_base_geral['Débito'] == conta_input).any() or (df_base_geral['Crédito'] == conta_input).any()
        if not tem_na_base:
            st.error(f"❌ EXECUÇÃO BLOQUEADA: A conta {conta_input} não possui lançamentos no arquivo anexado.")
            st.stop()
            
        tipo_auditoria = 'CARTAO' if 'Cartões' in modo else 'FORNECEDOR'
        
        r_tot, r_ant, r_atual, r_pend, s_tot, s_ant, s_atual, s_pend = processar_razoes_contabeis(
            df_base_geral, conta_input, saldo_abertura_var, tipo_auditoria
        )
        
        # VALIDAÇÃO DE CONCILIAÇÃO (ALERTA VERMELHO)
        if len(r_ant) == 0 and len(r_atual) == 0:
            st.error("🚨 ATENÇÃO: NENHUMA CONCILIAÇÃO OCORREU. Não foram encontrados blocos exatos.")
        
        excel_data = gerar_excel_memoria({
            '1. Razão Total': r_tot,
            '2. Conciliado (Ano Anterior)': r_ant,
            '3. Conciliado (Atual)': r_atual,
            '4. Não Conciliado (Pendente)': r_pend
        })
        
        st.download_button(label="📥 Baixar 4 Razões (Excel)", data=excel_data, 
                           file_name=f"Auditoria_4Razoes_{conta_input}.xlsx", 
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
        st.write("---")
        st.subheader("Balanço de Validação das Abas")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.info(f"**Aba 1 (Total)**\nR$ {s_tot:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        with col2:
            st.success(f"**Aba 2 (Ant)**\nR$ {s_ant:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        with col3:
            st.success(f"**Aba 3 (Atual)**\nR$ {s_atual:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        with col4:
            st.warning(f"**Aba 4 (Pend)**\nR$ {s_pend:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        if abs(s_ant) == 0.0 and abs(s_atual) == 0.0 and round(s_tot, 2) == round(s_pend, 2):
            st.success("✅ **Auditoria Validada:** As abas de itens conciliados fecharam em R$ 0,00 perfeitamente.")
        
    except Exception as e:
        st.error(f"Falha na execução. Detalhe técnico: {e}")
