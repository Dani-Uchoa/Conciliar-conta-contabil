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
# MÓDULOS DE CACHE E LEITURA (PARSERS)
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
        if header_idx == -1:
            raise ValueError("Não foi possível localizar as colunas de 'Código/Conta' e 'Saldo Anterior' no balancete.")
        df_bal = df_balancete.iloc[header_idx+1:].copy()
        df_bal.columns = [str(col).strip().upper() for col in df_balancete.iloc[header_idx].values]
    
    col_conta = next((c for c in df_bal.columns if 'CÓDIGO' in c or 'CODIGO' in c or 'CONTA' in c), None)
    col_saldo = next((c for c in df_bal.columns if 'ANTERIOR' in c), None)
    
    if not col_conta or not col_saldo:
        raise ValueError("O layout do Balancete não contém colunas claras de 'Código' e 'Saldo Anterior'.")
        
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
# MOTORES MATEMÁTICOS DE CONCILIAÇÃO
# ==========================================
def processar_fornecedores(df_main, conta_alvo, saldo_anterior_informado):
    pagamentos = df_main[df_main['Débito'] == conta_alvo].to_dict('records')
    notas_fiscais = df_main[df_main['Crédito'] == conta_alvo].to_dict('records')
    ids_conciliados_pag = set()
    ids_conciliados_nf = set()
    relacoes_validadas = []

    for i, pag in enumerate(pagamentos):
        for j, nf in enumerate(notas_fiscais):
            if j not in ids_conciliados_nf and pag['Valor'] == nf['Valor']:
                ids_conciliados_pag.add(i)
                ids_conciliados_nf.add(j)
                relacoes_validadas.append({'pags': [pag], 'nfs': [nf]})
                break

    for i, pag in enumerate(pagamentos):
        if i in ids_conciliados_pag: continue
        encontrado = False
        nfs_disp = [(idx, nf) for idx, nf in enumerate(notas_fiscais) if idx not in ids_conciliados_nf]
        for tamanho in range(1, 5):
            if encontrado: break
            for combinacao in itertools.combinations(nfs_disp, tamanho):
                if sum(nf['Valor'] for idx, nf in combinacao) == pag['Valor']:
                    ids_conciliados_pag.add(i)
                    nfs_match = []
                    for idx, nf in combinacao:
                        ids_conciliados_nf.add(idx)
                        nfs_match.append(nf)
                    relacoes_validadas.append({'pags': [pag], 'nfs': nfs_match})
                    encontrado = True
                    break

    pag_sobra = [pag for i, pag in enumerate(pagamentos) if i not in ids_conciliados_pag]
    nf_sobra = [nf for i, nf in enumerate(notas_fiscais) if i not in ids_conciliados_nf]

    pagos_antecipados = []
    for rel in relacoes_validadas:
        data_nf_min = min([nf['Data'] for nf in rel['nfs']])
        data_pag_min = min([pag['Data'] for pag in rel['pags']])
        if data_pag_min < data_nf_min:
            pagos_antecipados.append({
                'Data Pagamento': data_pag_min.strftime('%d/%m/%Y'), 'Valor Pagamento': float(rel['pags'][0]['Valor']),
                'Data NF': data_nf_min.strftime('%d/%m/%Y'), 'Doc NF': rel['nfs'][0]['Documento']
            })

    nfs_abertas_reais = [{
        'Doc': nf['Documento'], 'Emissão': nf['Data'].strftime('%d/%m/%Y'), 
        'Valor': float(nf['Valor']), 'Histórico': nf['Histórico']
    } for j, nf in enumerate(nf_sobra)]
    
    pags_brutos = [pag for i, pag in enumerate(pag_sobra)]
    pags_brutos.sort(key=lambda x: x['Data'])
    
    saldo_ant = abs(Decimal(str(saldo_anterior_informado)))
    pags_orfaos_finais = []
    
    for pag in pags_brutos:
        valor_pag = pag['Valor']
        if saldo_ant >= valor_pag:
            saldo_ant -= valor_pag
            pags_orfaos_finais.append({
                'Data': pag['Data'].strftime('%d/%m/%Y'), 'Valor': float(valor_pag), 'Histórico': pag['Histórico'],
                'Motivo': 'Liquidação de Saldo Anterior'
            })
        elif saldo_ant > Decimal('0.00'):
            pags_orfaos_finais.append({
                'Data': pag['Data'].strftime('%d/%m/%Y'), 'Valor': float(saldo_ant), 'Histórico': pag['Histórico'],
                'Motivo': 'Liquidação de Saldo Anterior (Parcial)'
            })
            pags_orfaos_finais.append({
                'Data': pag['Data'].strftime('%d/%m/%Y'), 'Valor': float(valor_pag - saldo_ant), 'Histórico': pag['Histórico'],
                'Motivo': 'Pagamento Órfão'
            })
            saldo_ant = Decimal('0.00')
        else:
            pags_orfaos_finais.append({
                'Data': pag['Data'].strftime('%d/%m/%Y'), 'Valor': float(valor_pag), 'Histórico': pag['Histórico'],
                'Motivo': 'Pagamento Órfão (Sem NF)'
            })

    conciliados_exp = []
    for idx_grupo, rel in enumerate(relacoes_validadas, 1):
        pag = rel['pags'][0]
        for i_nf, nf in enumerate(rel['nfs']):
            conciliados_exp.append({
                'ID Grupo': f"G-{idx_grupo}",
                'Data Pagamento': pag['Data'].strftime('%d/%m/%Y') if i_nf == 0 else None,
                'Valor Pagamento': float(pag['Valor']) if i_nf == 0 else None,
                'Data NF': nf['Data'].strftime('%d/%m/%Y'), 'Doc NF': nf['Documento'], 'Valor NF': float(nf['Valor'])
            })
    return nfs_abertas_reais, pags_orfaos_finais, pagos_antecipados, conciliados_exp, float(saldo_ant)

def processar_cartoes_extrato(df_main, conta_alvo, saldo_anterior_informado):
    df_alvo = df_main[(df_main['Débito'] == conta_alvo) | (df_main['Crédito'] == conta_alvo)].copy()
    
    vendas = df_alvo[df_alvo['Débito'] == conta_alvo].to_dict('records')
    recebimentos = df_alvo[df_alvo['Crédito'] == conta_alvo].to_dict('records')
    
    movimentacao_completa = vendas + recebimentos
    movimentacao_completa.sort(key=lambda x: x['Data'])
    
    saldo_anterior = abs(Decimal(str(saldo_anterior_informado)))
    
    extrato_razao = []
    extrato_razao.append({
        'Data': '01/01/2026', 
        'Histórico': 'SALDO ANTERIOR (HERDADO)', 
        'Vendas Lançadas (Débito)': None, 
        'Recebimentos/Taxas (Crédito)': None, 
        'Classificação do Lançamento': 'Origem da Dívida',
        'Saldo Acumulado a Receber': float(saldo_anterior)
    })
    
    saldo_atual = saldo_anterior
    pendencia_ano_anterior = saldo_anterior
    
    for mov in movimentacao_completa:
        valor_str = mov['Valor']
        
        if mov['Débito'] == conta_alvo: # Nova Venda
            saldo_atual += valor_str
            extrato_razao.append({
                'Data': mov['Data'].strftime('%d/%m/%Y'),
                'Histórico': mov['Histórico'],
                'Vendas Lançadas (Débito)': float(valor_str),
                'Recebimentos/Taxas (Crédito)': None,
                'Classificação do Lançamento': 'Nova Venda',
                'Saldo Acumulado a Receber': float(saldo_atual)
            })
        else: # Recebimento/Taxa
            saldo_atual -= valor_str
            classificacao = ""
            
            # Rastreador de Destino do Crédito (Identifica o Saldo Anterior)
            if pendencia_ano_anterior > Decimal('0.00'):
                if valor_str <= pendencia_ano_anterior:
                    classificacao = "Liquida Saldo Anterior (Não Conciliar na Domínio)"
                    pendencia_ano_anterior -= valor_str
                else:
                    classificacao = f"Misto (R$ {pendencia_ano_anterior} pro Anterior / Resto p/ Venda Nova)"
                    pendencia_ano_anterior = Decimal('0.00')
            else:
                classificacao = "Liquida Vendas Atuais"
                
            extrato_razao.append({
                'Data': mov['Data'].strftime('%d/%m/%Y'),
                'Histórico': mov['Histórico'],
                'Vendas Lançadas (Débito)': None,
                'Recebimentos/Taxas (Crédito)': float(valor_str),
                'Classificação do Lançamento': classificacao,
                'Saldo Acumulado a Receber': float(saldo_atual)
            })

    # Motor FIFO Atômico
    idx_venda = 0
    pool_creditos = Decimal('0.00')
    saldo_fifo = saldo_anterior
    
    for v in vendas: v['Status'] = 'Pendente'
        
    for rec in recebimentos:
        pool_creditos += rec['Valor']
        if saldo_fifo > Decimal('0.00'):
            if pool_creditos >= saldo_fifo:
                pool_creditos -= saldo_fifo
                saldo_fifo = Decimal('0.00')
            else:
                saldo_fifo -= pool_creditos
                pool_creditos = Decimal('0.00')
                
        while idx_venda < len(vendas):
            if pool_creditos >= vendas[idx_venda]['Valor']:
                pool_creditos -= vendas[idx_venda]['Valor']
                vendas[idx_venda]['Status'] = 'Conciliado (Pago)'
                idx_venda += 1
            else: break
            
    vendas_listagem = [{
        'Data Venda': v['Data'].strftime('%d/%m/%Y'), 'Histórico': v['Histórico'],
        'Valor': float(v['Valor']), 'Status FIFO': v['Status']
    } for v in vendas]
    
    # Adiciona a linha de dedução dos créditos que sobraram
    if pool_creditos > Decimal('0.00'):
        vendas_listagem.append({
            'Data Venda': '---',
            'Histórico': '(-) CRÉDITOS LIVRES NA CONTA (Aguardando próxima venda)',
            'Valor': float(-pool_creditos),
            'Status FIFO': 'Abate o valor a receber'
        })

    return extrato_razao, vendas_listagem, float(saldo_atual), float(pool_creditos)

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
st.markdown("Identificação analítica em base geral. Arquivos mantidos em cache para processamento instantâneo.")

modo = st.radio("Selecione o Modelo de Regra de Negócio:", 
                ["1. Fornecedores", "2. Cartões / Contas (Razão e Extrato)"])

conta_input = st.number_input("Digite a conta contábil alvo (Ex: 1059 ou 808)", value=0, step=1)

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
        saldo_capturado = extrair_saldo_balancete(bytes_balancete, conta_input)
        saldo_abertura_var = saldo_capturado
        st.success(f"✔️ Saldo Anterior de R$ {float(saldo_abertura_var):,.2f} capturado do Balancete.".replace(",", "X").replace(".", ",").replace("X", "."))
    except Exception as e:
        st.error(f"Erro ao analisar o Balancete. Detalhe: {e}")
        saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00)))
elif not arquivo_balancete:
    saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00, step=100.00)))

if arquivo_lancamentos and conta_input != 0:
    try:
        st.info("Processando base de dados em cache...")
        bytes_lancamentos = arquivo_lancamentos.getvalue()
        df_base_geral = carregar_base_lancamentos(bytes_lancamentos)
        
        if "1. Fornecedores" in modo:
            nfs, pags, antecipados, conciliados, saldo_ant_restante = processar_fornecedores(df_base_geral, conta_input, saldo_abertura_var)
            excel_data = gerar_excel_memoria({'Conciliados': conciliados, 'NFs Abertas': nfs, 'Pagamentos (Sobra)': pags})
            st.download_button(label="📥 Baixar Relatório (Fornecedores)", data=excel_data, 
                               file_name=f"Auditoria_Fornecedor_{conta_input}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            extrato, vendas_list, saldo_final, creditos_livres = processar_cartoes_extrato(df_base_geral, conta_input, saldo_abertura_var)
            
            excel_data = gerar_excel_memoria({
                'Extrato Conta Corrente (Razão)': extrato, 
                'Status Lançamento Vendas': vendas_list
            })
            
            st.download_button(label="📥 Baixar Relatório (Cartões)", data=excel_data, 
                               file_name=f"Auditoria_Cartoes_{conta_input}.xlsx", 
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            
            st.write("---")
            st.subheader("Balanço Sintético do Período")
            st.warning(f"**Saldo Residual Final a Receber:** R$ {saldo_final:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            if creditos_livres > 0:
                st.info(f"*(Inclui R$ {creditos_livres:,.2f} de créditos retidos na conta)*".replace(",", "X").replace(".", ",").replace("X", "."))
            
    except Exception as e:
        st.error(f"Falha na execução. Detalhe técnico: {e}")
