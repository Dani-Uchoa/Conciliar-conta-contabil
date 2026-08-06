import streamlit as st
import pandas as pd
import itertools
from decimal import Decimal, ROUND_HALF_UP

st.set_page_config(page_title="Conciliador Contábil", layout="wide")

def formatar_moeda(v):
    try:
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except:
        return Decimal('0.00')

def processar_conciliacao(df_raw, conta_alvo):
    # Limpeza e padronização da extração da Domínio
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

    # Separação por conta alvo
    pagamentos = df_main[df_main['Débito'] == conta_alvo].to_dict('records')
    notas_fiscais = df_main[df_main['Crédito'] == conta_alvo].to_dict('records')

    ids_conciliados_pag = set()
    ids_conciliados_nf = set()
    relacoes_validadas = []

    # FASE 1: Cruzamento Exato (1 para 1)
    for i, pag in enumerate(pagamentos):
        for j, nf in enumerate(notas_fiscais):
            if j not in ids_conciliados_nf and pag['Valor'] == nf['Valor']:
                ids_conciliados_pag.add(i)
                ids_conciliados_nf.add(j)
                relacoes_validadas.append({'pags': [pag], 'nfs': [nf]})
                break

    # FASE 2: Combinação de Subconjuntos (1 Pagamento para Múltiplas NFs)
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

    # --- CLASSIFICAÇÃO DOS 4 CENÁRIOS DE FEEDBACK ---
    
    # 1. Pagamentos Antecipados (Dentro dos que fecharam a matemática)
    pagos_antecipados = []
    for rel in relacoes_validadas:
        data_nf_mais_antiga = min([nf['Data'] for nf in rel['nfs']])
        data_pag_mais_antiga = min([pag['Data'] for pag in rel['pags']])
        if data_pag_mais_antiga < data_nf_mais_antiga:
            pagos_antecipados.append({
                'data_pag': data_pag_mais_antiga, 'valor_pag': rel['pags'][0]['Valor'],
                'data_nf': data_nf_mais_antiga, 'doc': rel['nfs'][0]['Documento']
            })

    # 2. Tolerância para Multa e Juros (15% de margem)
    divergencia_juros = []
    ids_pag_juros = set()
    ids_nf_juros = set()

    for i, pag in enumerate(pag_sobra):
        if i in ids_pag_juros: continue
        for j, nf in enumerate(nf_sobra):
            if j in ids_nf_juros: continue
            
            if pag['Data'] >= nf['Data'] and pag['Valor'] > nf['Valor']:
                diferenca = pag['Valor'] - nf['Valor']
                limite_aceitavel = nf['Valor'] * Decimal('0.15') 
                if diferenca <= limite_aceitavel:
                    divergencia_juros.append({
                        'doc': nf['Documento'], 'nf_valor': nf['Valor'], 
                        'pag_valor': pag['Valor'], 'juros_calc': diferenca
                    })
                    ids_pag_juros.add(i)
                    ids_nf_juros.add(j)
                    break

    # 3. e 4. Sobras reais não identificadas
    nfs_abertas_reais = [nf for j, nf in enumerate(nf_sobra) if j not in ids_nf_juros]
    pags_orfaos_reais = [pag for i, pag in enumerate(pag_sobra) if i not in ids_pag_juros]

    return nfs_abertas_reais, pags_orfaos_reais, pagos_antecipados, divergencia_juros

# --- INTERFACE DE USUÁRIO (WEB) ---
st.title("Conciliação Contábil - Domínio Sistemas")
st.markdown("Identificador analítico de divergências, pagamentos órfãos e antecipações.")

conta_input = st.number_input("Digite a conta contábil do Fornecedor para análise (Ex: 1059)", value=1059, step=1)
arquivo_anexado = st.file_uploader("Anexe o relatório da Domínio (.xlsx)", type=["xlsx"])

if arquivo_anexado:
    try:
        df_bruto = pd.read_excel(arquivo_anexado, header=5)
        nfs, pags, antecipados, juros = processar_conciliacao(df_bruto, conta_input)
        
        st.success("Análise matemática concluída.")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.warning(f"1. Consta a entrada, mas não consta o pagamento: {len(nfs)} NF(s)")
            for nf in nfs:
                st.write(f"-> Doc: {nf['Documento']} | Emissão: {nf['Data'].strftime('%d/%m/%Y')} | R$ {nf['Valor']}")
                
            st.info(f"3. Notas pagas ANTES do lançamento da nota: {len(antecipados)} ocorrência(s)")
            for p in antecipados:
                st.write(f"-> NF {p['doc']} lançada em {p['data_nf'].strftime('%d/%m/%Y')}. Pago antes em {p['data_pag'].strftime('%d/%m/%Y')} (R$ {p['valor_pag']})")

        with col2:
            st.error(f"2. Notas pagas que não constam entradas (Sobras de Débito): {len(pags)} pagamento(s)")
            for pag in pags:
                st.write(f"-> Saída: {pag['Data'].strftime('%d/%m/%Y')} | R$ {pag['Valor']}")
                
            st.warning(f"4. Divergência de valores (Possível Juros por atraso): {len(juros)} caso(s)")
            for j in juros:
                st.write(f"-> NF {j['doc']} (R$ {j['nf_valor']}) | Pagamento: R$ {j['pag_valor']} | Lançar R$ {j['juros_calc']} como Juros")
                
    except Exception as e:
        st.error(f"Erro ao processar a planilha. Verifique se a estrutura da Domínio está íntegra. Detalhe: {e}")