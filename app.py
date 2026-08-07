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
    
    if linha_conta.empty:
        return Decimal('0.00')
        
    valor_bruto = linha_conta.iloc[0][col_saldo]
    return formatar_moeda(valor_bruto)

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
                'Data NF': data_nf_min.strftime('%d/%m/%Y'), 'Doc NF': rel['nfs'][0]['Documento'],
                'Motivo': 'Data do pagamento anterior à emissão da NF'
            })

    divergencia_juros = []
    ids_pag_juros = set()
    ids_nf_juros = set()
    for i, pag in enumerate(pag_sobra):
        if i in ids_pag_juros: continue
        for j, nf in enumerate(nf_sobra):
            if j in ids_nf_juros: continue
            if pag['Data'] >= nf['Data'] and pag['Valor'] > nf['Valor']:
                diferenca = pag['Valor'] - nf['Valor']
                if diferenca <= (nf['Valor'] * Decimal('0.15')):
                    divergencia_juros.append({
                        'Doc NF': nf['Documento'], 'Valor NF': float(nf['Valor']), 
                        'Valor Pagamento': float(pag['Valor']), 'Juros Calculado': float(diferenca),
                        'Motivo': 'Diferença absorvida como Juros/Multa'
                    })
                    ids_pag_juros.add(i)
                    ids_nf_juros.add(j)
                    break

    nfs_abertas_reais = [{
        'Doc': nf['Documento'], 'Emissão': nf['Data'].strftime('%d/%m/%Y'), 
        'Valor': float(nf['Valor']), 'Histórico': nf['Histórico'],
        'Motivo': 'Nota Fiscal sem pagamento correspondente'
    } for j, nf in enumerate(nf_sobra) if j not in ids_nf_juros]
    
    pags_brutos = [pag for i, pag in enumerate(pag_sobra) if i not in ids_pag_juros]
    pags_brutos.sort(key=lambda x: x['Data'])
    
    saldo_ant = abs(Decimal(str(saldo_anterior_informado)))
    pags_orfaos_finais = []
    
    for pag in pags_brutos:
        valor_pag = pag['Valor']
        if saldo_ant >= valor_pag:
            saldo_ant -= valor_pag
            pags_orfaos_finais.append({
                'Data': pag['Data'].strftime('%d/%m/%Y'), 'Valor': float(valor_pag), 'Histórico': pag['Histórico'],
                'Motivo': 'Liquidação de Saldo Anterior (Ano/Mês Passado)'
            })
        elif saldo_ant > Decimal('0.00'):
            pags_orfaos_finais.append({
                'Data': pag['Data'].strftime('%d/%m/%Y'), 'Valor': float(saldo_ant), 'Histórico': pag['Histórico'],
                'Motivo': 'Liquidação de Saldo Anterior (Parcial)'
            })
            pags_orfaos_finais.append({
                'Data': pag['Data'].strftime('%d/%m/%Y'), 'Valor': float(valor_pag - saldo_ant), 'Histórico': pag['Histórico'],
                'Motivo': 'Pagamento Órfão (Sem NF e Saldo Anterior Esgotado)'
            })
            saldo_ant = Decimal('0.00')
        else:
            pags_orfaos_finais.append({
                'Data': pag['Data'].strftime('%d/%m/%Y'), 'Valor': float(valor_pag), 'Histórico': pag['Histórico'],
                'Motivo': 'Pagamento Órfão (Débito sem obrigação correspondente)'
            })

    conciliados_exp = []
    for idx_grupo, rel in enumerate(relacoes_validadas, 1):
        pag = rel['pags'][0]
        qtd_nfs = len(rel['nfs'])
        for i_nf, nf in enumerate(rel['nfs']):
            conciliados_exp.append({
                'ID Grupo': f"G-{idx_grupo}",
                'Data Pagamento': pag['Data'].strftime('%d/%m/%Y') if i_nf == 0 else None,
                'Valor Pagamento': float(pag['Valor']) if i_nf == 0 else None,
                'Data NF': nf['Data'].strftime('%d/%m/%Y'), 
                'Doc NF': nf['Documento'], 
                'Valor NF': float(nf['Valor']),
                'Motivo': 'Conciliação Agrupada' if qtd_nfs > 1 else 'Conciliação Exata'
            })

    return nfs_abertas_reais, pags_orfaos_finais, pagos_antecipados, divergencia_juros, conciliados_exp, float(saldo_ant)

def processar_cartoes_fifo(df_main, conta_alvo, saldo_anterior_informado):
    """Novo Motor: FIFO Atômico. Não desmembra vendas. Ou liquida 100% ou deixa 100% pendente."""
    df_alvo = df_main[(df_main['Débito'] == conta_alvo) | (df_main['Crédito'] == conta_alvo)].copy()
    
    vendas = df_alvo[df_alvo['Débito'] == conta_alvo].sort_values('Data').to_dict('records')
    recebimentos = df_alvo[df_alvo['Crédito'] == conta_alvo].sort_values('Data').to_dict('records')
    
    saldo_anterior = abs(Decimal(str(saldo_anterior_informado)))
    idx_venda = 0
    total_vendas = len(vendas)
    
    baixas_saldo_anterior = []
    pool_creditos = Decimal('0.00')

    for rec in recebimentos:
        credito_disponivel = rec['Valor']
        
        # 1. Barreira do Saldo Anterior (Prioridade)
        if saldo_anterior > Decimal('0.00'):
            if credito_disponivel >= saldo_anterior:
                baixas_saldo_anterior.append({
                    'Data Recebimento': rec['Data'].strftime('%d/%m/%Y'), 'Histórico Bancário': rec['Histórico'],
                    'Valor Recebido (Bruto)': float(rec['Valor']), 'Valor Retido p/ Saldo Anterior': float(saldo_anterior),
                    'Motivo': 'Liquidou o restante do Saldo Anterior'
                })
                credito_disponivel -= saldo_anterior
                saldo_anterior = Decimal('0.00')
            else:
                baixas_saldo_anterior.append({
                    'Data Recebimento': rec['Data'].strftime('%d/%m/%Y'), 'Histórico Bancário': rec['Histórico'],
                    'Valor Recebido (Bruto)': float(rec['Valor']), 'Valor Retido p/ Saldo Anterior': float(credito_disponivel),
                    'Motivo': 'Abatimento Parcial do Saldo Anterior'
                })
                saldo_anterior -= credito_disponivel
                credito_disponivel = Decimal('0.00')
                
        # 2. O que sobra vai para o Cofre (Pool)
        pool_creditos += credito_disponivel
        
        # 3. Motor Atômico: Só dá baixa se o cofre cobrir 100% da venda mais antiga
        while idx_venda < total_vendas:
            venda_atual = vendas[idx_venda]
            valor_venda = venda_atual['Valor']
            
            if pool_creditos >= valor_venda:
                pool_creditos -= valor_venda
                venda_atual['Status'] = 'Conciliado'
                idx_venda += 1
            else:
                # O cofre não tem dinheiro suficiente. Para e espera o próximo recebimento, sem desmembrar.
                break

    vendas_conciliadas = []
    vendas_pendentes = []
    
    for v in vendas:
        if v.get('Status') == 'Conciliado':
            vendas_conciliadas.append({
                'Data Venda': v['Data'].strftime('%d/%m/%Y'), 'Histórico': v['Histórico'],
                'Valor Original': float(v['Valor']), 'Motivo': 'Conciliado Integralmente'
            })
        else:
            vendas_pendentes.append({
                'Data Venda': v['Data'].strftime('%d/%m/%Y'), 'Histórico': v['Histórico'],
                'Valor Original': float(v['Valor']), 'Motivo': 'Pendente (Aguardando Recebimento Total)'
            })

    # Se sobrar dinheiro no cofre no fim do mês
    recebimentos_orfaos = []
    if pool_creditos > Decimal('0.00'):
        recebimentos_orfaos.append({
            'Data Recebimento': 'Acumulado no Período', 'Valor Órfão': float(pool_creditos),
            'Histórico': 'Saldo residual de créditos', 'Motivo': 'Créditos que sobraram após baixar vendas integrais'
        })

    total_gerado = sum(v['Valor'] for v in vendas)
    total_pago = sum(r['Valor'] for r in recebimentos)
    
    return float(total_gerado), float(total_pago), float(saldo_anterior), baixas_saldo_anterior, vendas_conciliadas, vendas_pendentes, recebimentos_orfaos, float(pool_creditos)

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
                ["1. Fornecedores (Cruzamento Exato e Agrupado)", "2. Cartões / Contas sem ID (FIFO Atômico Sem Desmembramento)"])

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
        st.success(f"✔️ Saldo Anterior de R$ {float(saldo_abertura_var):,.2f} capturado automaticamente do Balancete.".replace(",", "X").replace(".", ",").replace("X", "."))
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
            nfs, pags, antecipados, juros, conciliados, saldo_ant_restante = processar_fornecedores(df_base_geral, conta_input, saldo_abertura_var)
            
            excel_data = gerar_excel_memoria({
                'Conciliados': conciliados, 'NFs Abertas': nfs, 
                'Pagamentos (Sobra)': pags, 'Pagos Antecipados': antecipados, 'Divergência Juros': juros
            })
            
            st.download_button(label="📥 Baixar Relatório (Fornecedores)", data=excel_data, 
                               file_name=f"Auditoria_Fornecedor_{conta_input}.xlsx", 
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            
            col1, col2 = st.columns(2)
            with col1:
                st.warning(f"NFs Abertas (Falta Pagamento): {len(nfs)} registros")
            with col2:
                st.error(f"Pagamentos Descasados: {len(pags)} registros")
                
        else:
            t_gerado, t_pago, saldo_ant_pendente, baixas_ant, v_conciliadas, v_pendentes, r_orfaos, pool_sobra = processar_cartoes_fifo(df_base_geral, conta_input, saldo_abertura_var)
            
            excel_data = gerar_excel_memoria({
                'Baixas do Ano Anterior': baixas_ant, 
                'Vendas Conciliadas': v_conciliadas, 
                'Vendas Pendentes Integrais': v_pendentes, 
                'Créditos Sobrando (Pool)': r_orfaos
            })
            
            st.download_button(label="📥 Baixar Relatório (Cartões)", data=excel_data, 
                               file_name=f"Auditoria_Cartoes_{conta_input}.xlsx", 
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            
            st.write("---")
            st.subheader("Balanço Sintético do Período")
            st.write(f"**Total Lançado (Novas Vendas):** R$ {t_gerado:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            st.write(f"**Total Baixado (Créditos/Taxas):** R$ {t_pago:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            
            # Cálculo Contábil Perfeito
            saldo_real_periodo = sum(item['Valor Original'] for item in v_pendentes)
            saldo_final_acumulado = saldo_ant_pendente + saldo_real_periodo - pool_sobra
            
            st.warning(f"**Saldo Residual a Receber (Cálculo Financeiro):** R$ {saldo_final_acumulado:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            
            st.write("---")
            st.markdown("### Composição do Saldo (Sem Desmembramentos)")
            st.error(f"🔹 **Vendas 100% Pendentes (Intactas):** R$ {saldo_real_periodo:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            if pool_sobra > 0:
                st.success(f"🔹 **Créditos Sobrando (Não alocados - Abatem do total):** R$ {pool_sobra:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            if saldo_ant_pendente > 0:
                st.info(f"🔹 **Saldo Anterior não liquidado:** R$ {saldo_ant_pendente:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            
    except Exception as e:
        st.error(f"Falha na execução. Detalhe técnico: {e}")
