import streamlit as st
import pandas as pd
import itertools
import io
from decimal import Decimal, ROUND_HALF_UP

st.set_page_config(page_title="Conciliador Contábil", layout="wide")

def formatar_moeda(v):
    try:
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except:
        return Decimal('0.00')

def processar_conciliacao(df_raw, conta_alvo):
    # Limpeza e padronização
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

    pagamentos = df_main[df_main['Débito'] == conta_alvo].to_dict('records')
    notas_fiscais = df_main[df_main['Crédito'] == conta_alvo].to_dict('records')

    ids_conciliados_pag = set()
    ids_conciliados_nf = set()
    relacoes_validadas = []

    # FASE 1: Cruzamento Exato
    for i, pag in enumerate(pagamentos):
        for j, nf in enumerate(notas_fiscais):
            if j not in ids_conciliados_nf and pag['Valor'] == nf['Valor']:
                ids_conciliados_pag.add(i)
                ids_conciliados_nf.add(j)
                relacoes_validadas.append({'pags': [pag], 'nfs': [nf]})
                break

    # FASE 2: Agrupamento
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

    # CLASSIFICAÇÃO DOS CENÁRIOS
    pagos_antecipados = []
    for rel in relacoes_validadas:
        data_nf_mais_antiga = min([nf['Data'] for nf in rel['nfs']])
        data_pag_mais_antiga = min([pag['Data'] for pag in rel['pags']])
        if data_pag_mais_antiga < data_nf_mais_antiga:
            pagos_antecipados.append({
                'Data Pagamento': data_pag_mais_antiga.strftime('%d/%m/%Y'), 
                'Valor Pagamento': float(rel['pags'][0]['Valor']),
                'Data NF': data_nf_mais_antiga.strftime('%d/%m/%Y'), 
                'Doc NF': rel['nfs'][0]['Documento']
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
                        'Valor Pagamento': float(pag['Valor']), 'Juros Calculado': float(diferenca)
                    })
                    ids_pag_juros.add(i)
                    ids_nf_juros.add(j)
                    break

    # Formatando as sobras reais para exportação
    nfs_abertas_reais = [{
        'Doc': nf['Documento'], 'Emissão': nf['Data'].strftime('%d/%m/%Y'), 
        'Valor': float(nf['Valor']), 'Histórico': nf['Histórico']
    } for j, nf in enumerate(nf_sobra) if j not in ids_nf_juros]
    
    pags_orfaos_reais = [{
        'Saída Bancária': pag['Data'].strftime('%d/%m/%Y'), 'Valor': float(pag['Valor']), 
        'Histórico': pag['Histórico']
    } for i, pag in enumerate(pag_sobra) if i not in ids_pag_juros]

    # Formatando os conciliados para exportação
    lista_conciliados = []
    for rel in relacoes_validadas:
        pag = rel['pags'][0]
        for nf in rel['nfs']:
            lista_conciliados.append({
                'Data Pagamento': pag['Data'].strftime('%d/%m/%Y'),
                'Valor Pagamento': float(pag['Valor']),
                'Data NF': nf['Data'].strftime('%d/%m/%Y'),
                'Doc NF': nf['Documento'],
                'Valor NF': float(nf['Valor'])
            })

    return nfs_abertas_reais, pags_orfaos_reais, pagos_antecipados, divergencia_juros, lista_conciliados

def gerar_excel_memoria(nfs, pags, antecipados, juros, conciliados):
    """Gera um arquivo Excel na memória RAM (BytesIO) com abas separadas."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if conciliados: pd.DataFrame(conciliados).to_excel(writer, index=False, sheet_name='Conciliados')
        if nfs: pd.DataFrame(nfs).to_excel(writer, index=False, sheet_name='NFs Abertas')
        if pags: pd.DataFrame(pags).to_excel(writer, index=False, sheet_name='Pagamentos sem NF')
        if antecipados: pd.DataFrame(antecipados).to_excel(writer, index=False, sheet_name='Pagos Antecipados')
        if juros: pd.DataFrame(juros).to_excel(writer, index=False, sheet_name='Divergência Juros')
    return output.getvalue()

# --- INTERFACE WEB ---
st.title("Conciliação Contábil - Domínio Sistemas")
st.markdown("Identificador analítico de divergências, pagamentos órfãos e antecipações.")

conta_input = st.number_input("Digite a conta contábil do Fornecedor para análise (Ex: 1059)", value=1059, step=1)
arquivo_anexado = st.file_uploader("Anexe o relatório da Domínio (.xlsx)", type=["xlsx"])

if arquivo_anexado:
    try:
        df_bruto = pd.read_excel(arquivo_anexado, header=5)
        nfs, pags, antecipados, juros, conciliados = processar_conciliacao(df_bruto, conta_input)
        
        st.success("Análise matemática concluída.")
        
        # Geração do arquivo para download
        excel_data = gerar_excel_memoria(nfs, pags, antecipados, juros, conciliados)
        st.download_button(
            label="📥 Baixar Relatório Completo (Excel)",
            data=excel_data,
            file_name=f"Relatorio_Conciliacao_Conta_{conta_input}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        st.markdown("---")
        
        # Resumo visual na tela
        col1, col2 = st.columns(2)
        
        with col1:
            st.warning(f"1. Consta a entrada, mas não consta o pagamento: {len(nfs)} NF(s)")
            for nf in nfs:
                st.write(f"-> Doc: {nf['Doc']} | Emissão: {nf['Emissão']} | R$ {nf['Valor']}")
                
            st.info(f"3. Notas pagas ANTES do lançamento da nota: {len(antecipados)} ocorrência(s)")
            for p in antecipados:
                st.write(f"-> NF {p['Doc NF']} (R$ {p['Valor Pagamento']}) paga em {p['Data Pagamento']}, mas lançada em {p['Data NF']}.")

        with col2:
            st.error(f"2. Notas pagas que não constam entradas (Sobras de Débito): {len(pags)} pagamento(s)")
            for pag in pags:
                st.write(f"-> Saída: {pag['Saída Bancária']} | R$ {pag['Valor']}")
                
            st.warning(f"4. Divergência de valores (Possível Juros por atraso): {len(juros)} caso(s)")
            for j in juros:
                st.write(f"-> NF {j['Doc NF']} (R$ {j['Valor NF']}) | Pagamento: R$ {j['Valor Pagamento']} | Lançar R$ {j['Juros Calculado']} como Juros")
                
    except Exception as e:
        st.error(f"Erro ao processar a planilha. Verifique se a estrutura da Domínio está íntegra. Detalhe: {e}")
