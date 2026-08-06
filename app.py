import streamlit as st
import pandas as pd
import itertools
import io
from decimal import Decimal, ROUND_HALF_UP

st.set_page_config(page_title="Auditoria Contábil - Domínio Sistemas", layout="wide")

def formatar_moeda(v):
    try:
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except:
        return Decimal('0.00')

def limpar_dados_dominio(df_raw):
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

def processar_fornecedores(df_main, conta_alvo, saldo_anterior_informado):
    pagamentos = df_main[df_main['Débito'] == conta_alvo].to_dict('records')
    notas_fiscais = df_main[df_main['Crédito'] == conta_alvo].to_dict('records')

    ids_conciliados_pag = set()
    ids_conciliados_nf = set()
    relacoes_validadas = []

    # FASE 1: Exato
    for i, pag in enumerate(pagamentos):
        for j, nf in enumerate(notas_fiscais):
            if j not in ids_conciliados_nf and pag['Valor'] == nf['Valor']:
                ids_conciliados_pag.add(i)
                ids_conciliados_nf.add(j)
                relacoes_validadas.append({'pags': [pag], 'nfs': [nf]})
                break

    # FASE 2: Combinações
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

    # CLASSIFICAÇÃO: Antecipações
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

    # CLASSIFICAÇÃO: Juros
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

    # CLASSIFICAÇÃO: NFs em Aberto
    nfs_abertas_reais = [{
        'Doc': nf['Documento'], 'Emissão': nf['Data'].strftime('%d/%m/%Y'), 
        'Valor': float(nf['Valor']), 'Histórico': nf['Histórico'],
        'Motivo': 'Nota Fiscal sem pagamento correspondente'
    } for j, nf in enumerate(nf_sobra) if j not in ids_nf_juros]
    
    # CLASSIFICAÇÃO: Absorção de Saldo Anterior e Pagamentos Órfãos
    pags_brutos = [pag for i, pag in enumerate(pag_sobra) if i not in ids_pag_juros]
    pags_brutos.sort(key=lambda x: x['Data']) # Ordenação Cronológica
    
    saldo_ant = Decimal(str(saldo_anterior_informado))
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

    # CLASSIFICAÇÃO: Conciliados Exatos
    conciliados_exp = []
    for rel in relacoes_validadas:
        pag = rel['pags'][0]
        for nf in rel['nfs']:
            conciliados_exp.append({
                'Data Pagamento': pag['Data'].strftime('%d/%m/%Y'), 'Valor Pagamento': float(pag['Valor']),
                'Data NF': nf['Data'].strftime('%d/%m/%Y'), 'Doc NF': nf['Documento'], 'Valor NF': float(nf['Valor']),
                'Motivo': 'Conciliação Exata/Agrupada'
            })

    return nfs_abertas_reais, pags_orfaos_finais, pagos_antecipados, divergencia_juros, conciliados_exp, float(saldo_ant)

def processar_cartoes_fifo(df_main, conta_alvo, saldo_anterior_informado):
    df_alvo = df_main[(df_main['Débito'] == conta_alvo) | (df_main['Crédito'] == conta_alvo)].copy()
    
    vendas = df_alvo[df_alvo['Débito'] == conta_alvo].sort_values('Data').to_dict('records')
    recebimentos = df_alvo[df_alvo['Crédito'] == conta_alvo].sort_values('Data').to_dict('records')
    
    for v in vendas:
        v['Saldo_Pendente'] = v['Valor']

    saldo_anterior = Decimal(str(saldo_anterior_informado))
    idx_venda = 0
    total_vendas = len(vendas)
    recebimentos_orfaos = []

    for rec in recebimentos:
        credito_disponivel = rec['Valor']
        
        # Abate do Saldo Anterior
        if saldo_anterior > Decimal('0.00'):
            if credito_disponivel >= saldo_anterior:
                credito_disponivel -= saldo_anterior
                saldo_anterior = Decimal('0.00')
            else:
                saldo_anterior -= credito_disponivel
                credito_disponivel = Decimal('0.00')
                
        # Baixa FIFO nas vendas atuais
        while credito_disponivel > Decimal('0.00') and idx_venda < total_vendas:
            venda_atual = vendas[idx_venda]
            if venda_atual['Saldo_Pendente'] <= credito_disponivel:
                credito_disponivel -= venda_atual['Saldo_Pendente']
                venda_atual['Saldo_Pendente'] = Decimal('0.00')
                idx_venda += 1
            else:
                venda_atual['Saldo_Pendente'] -= credito_disponivel
                credito_disponivel = Decimal('0.00')
                
        # Se sobrou crédito e não há mais vendas
        if credito_disponivel > Decimal('0.00') and idx_venda >= total_vendas:
            recebimentos_orfaos.append({
                'Data Recebimento': rec['Data'].strftime('%d/%m/%Y'), 'Valor Órfão': float(credito_disponivel),
                'Histórico': rec['Histórico'], 'Motivo': 'Recebimento sem Venda (Possível Antecipação ou Erro)'
            })

    # Categorização Final de Vendas
    vendas_conciliadas = []
    vendas_pendentes = []
    
    for v in vendas:
        if v['Saldo_Pendente'] == Decimal('0.00'):
            vendas_conciliadas.append({
                'Data Venda': v['Data'].strftime('%d/%m/%Y'), 'Histórico': v['Histórico'],
                'Valor Original': float(v['Valor']), 'Motivo': 'Conciliado (Baixa FIFO 100%)'
            })
        else:
            motivo = 'Parcialmente Conciliado (Aguardando Restante)' if v['Saldo_Pendente'] < v['Valor'] else 'Não Conciliado (Saldo Intacto)'
            vendas_pendentes.append({
                'Data Venda': v['Data'].strftime('%d/%m/%Y'), 'Histórico': v['Histórico'],
                'Valor Original': float(v['Valor']), 'Saldo Pendente': float(v['Saldo_Pendente']), 'Motivo': motivo
            })

    total_gerado = sum(v['Valor'] for v in vendas)
    total_pago = sum(r['Valor'] for r in recebimentos)
    
    return float(total_gerado), float(total_pago), float(saldo_anterior), vendas_conciliadas, vendas_pendentes, recebimentos_orfaos

def gerar_excel_memoria(dfs_dict):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for sheet_name, data in dfs_dict.items():
            if data: pd.DataFrame(data).to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()

# --- INTERFACE WEB (STREAMLIT) ---
st.title("Auditoria Contábil - Domínio Sistemas")
st.markdown("Ferramenta técnica para identificação de divergências, antecipações e baixas.")

modo = st.radio("Selecione o Modelo de Regra de Negócio:", 
                ["1. Fornecedores (Cruzamento Exato e Agrupado)", "2. Cartões / Contas sem ID (Baixa FIFO)"])

col_conta, col_saldo = st.columns(2)
with col_conta:
    conta_input = st.number_input("Digite a conta contábil alvo (Ex: 1059 ou 808)", value=0, step=1)
with col_saldo:
    saldo_abertura = st.number_input("Saldo em Aberto Anterior (R$)", value=0.00, step=100.00)

arquivo_anexado = st.file_uploader("Anexe o relatório bruto (.xlsx)", type=["xlsx"])

if arquivo_anexado and conta_input != 0:
    try:
        df_bruto = pd.read_excel(arquivo_anexado, header=5)
        df_limpo = limpar_dados_dominio(df_bruto)
        
        st.success("Planilha higienizada com sucesso. Executando motor matemático...")
        
        if "1. Fornecedores" in modo:
            nfs, pags, antecipados, juros, conciliados, saldo_ant_restante = processar_fornecedores(df_limpo, conta_input, saldo_abertura)
            
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
                st.info(f"Pagamentos Antecipados: {len(antecipados)} ocorrências")
            with col2:
                st.error(f"Pagamentos Descasados (Inclui Baixa de Saldo Ant.): {len(pags)} registros")
                st.warning(f"Divergência de Valores (Juros): {len(juros)} casos")
                
        else:
            t_gerado, t_pago, saldo_ant_pendente, v_conciliadas, v_pendentes, r_orfaos = processar_cartoes_fifo(df_limpo, conta_input, saldo_abertura)
            
            excel_data = gerar_excel_memoria({
                'Vendas Conciliadas': v_conciliadas, 
                'Vendas Pendentes': v_pendentes, 
                'Créditos Órfãos': r_orfaos
            })
            
            st.download_button(label="📥 Baixar Relatório (Cartões)", data=excel_data, 
                               file_name=f"Auditoria_Cartoes_{conta_input}.xlsx", 
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            
            st.write("---")
            st.subheader("Balanço Sintético do Período")
            
            if saldo_ant_pendente > 0:
                st.error(f"**Alerta:** O banco não liquidou todo o Saldo Anterior. Restam R$ {saldo_ant_pendente:,.2f} em aberto do passado.".replace(",", "X").replace(".", ",").replace("X", "."))
            else:
                st.success("Saldo Anterior totalmente liquidado pelos recebimentos deste período.")

            st.write(f"**Total Lançado (Novas Vendas):** R$ {t_gerado:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            st.write(f"**Total Baixado (Créditos/Taxas):** R$ {t_pago:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            
            saldo_real_periodo = sum(item['Saldo Pendente'] for item in v_pendentes)
            saldo_final_acumulado = saldo_ant_pendente + saldo_real_periodo
            
            st.warning(f"**Saldo Residual a Receber (Novo Acumulado):** R$ {saldo_final_acumulado:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            
    except Exception as e:
        st.error(f"Falha na execução. Detalhe técnico: {e}")
